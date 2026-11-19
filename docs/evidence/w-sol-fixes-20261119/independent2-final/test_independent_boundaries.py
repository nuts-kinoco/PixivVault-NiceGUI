from datetime import datetime, timezone
from pathlib import Path
import asyncio
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_scheduler import ManagedScheduler
from stage5_generated_shared_owner import generated_shared_scope
from test_managed_network_stop_integration import _cookie
from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish
from test_w_review_bookmark_resume_20261118 import services_with_runs
from test_w_review_favourites_20261118 import create_stars


CRASH_EXIT = 79


def test_live_reader_rejects_a_previously_seen_journal_rollback(tmp_path):
    root = make_root(tmp_path)
    writer = mg.ManagedGenerations(root)
    writer.prepare_for_publication()
    publish(writer, root, '1000')
    journal, mark = journal_of(root), mark_of(root)
    early_journal, early_mark = journal.read_bytes(), mark.read_bytes()
    publish(writer, root, '1001')
    assert set(writer.entries()) == {'1000', '1001'}
    journal.write_bytes(early_journal)
    mark.write_bytes(early_mark)
    with pytest.raises(ManagedDownloadError):
        writer.entries()


@pytest.mark.asyncio
async def test_bookmark_resume_closed_gate_dispatches_no_request(tmp_path):
    row_id = VISIBILITY_ROWS['show']
    first = services_with_runs(tmp_path)
    try:
        old_run = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row_id,))
        first.batch_run_ledger.update_author(old_run, row_id, state='downloading')
    finally:
        await asyncio.to_thread(first.close)
    calls = []
    scope = generated_shared_scope(tmp_path)
    reader = scope.create_reader(_cookie(tmp_path), transport=lambda *_args: calls.append('unexpected'))
    reopening = services_with_runs(tmp_path, reader, shared_network_stop_scope=scope)
    try:
        scope.gate.trip(429)
        new_run = reopening.resume_batch_run(old_run)
        assert await asyncio.to_thread(reopening.batch_runs.join, 12)
        assert calls == []
        assert reopening.batch_run_ledger.run(new_run).state != 'done'
        assert reopening.batch_runs.resumable_run() is not None
        assert reopening.batch_run_ledger.unfinished_authors(new_run) == (row_id,)
    finally:
        await asyncio.to_thread(reopening.close)


@pytest.mark.asyncio
async def test_bookmark_resume_only_processes_unfinished_visibility(tmp_path):
    show, hide = (VISIBILITY_ROWS[p] for p in ('show', 'hide'))
    first = services_with_runs(tmp_path)
    try:
        old_run = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (hide, show))
        first.batch_run_ledger.complete_author(old_run, hide, {}, following=None)
        first.batch_run_ledger.update_author(old_run, show, state='downloading')
    finally:
        await asyncio.to_thread(first.close)
    reopening = services_with_runs(tmp_path)
    try:
        new_run = reopening.resume_batch_run(old_run)
        assert await asyncio.to_thread(reopening.batch_runs.join, 12)
        run = reopening.batch_run_ledger.run(new_run)
        assert (run.kind, run.target_type, run.state) == ('bookmarks', 'both', 'done')
        assert [author.author_id for author in reopening.batch_run_ledger.authors(new_run)] == [show]
        assert [item.work_key for item in reopening.batch_run_ledger.items(new_run, show)] == ['99']
    finally:
        await asyncio.to_thread(reopening.close)


@pytest.mark.parametrize('when', ['before_commit', 'after_commit'])
def test_attach_process_exit_recovers_both_ledgers(tmp_path, when):
    following, scheduler, book = create_stars(tmp_path)
    try:
        book.set('1000', True)
        before = scheduler.snapshot()
        assert scheduler._db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert following._connection.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    finally:
        scheduler.close()
        following.close()
    child = subprocess.run([sys.executable, '-B', __file__, str(tmp_path), when], timeout=20)
    assert child.returncode == CRASH_EXIT
    following, scheduler, book = reopen(tmp_path)
    try:
        assert scheduler._db.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert following._connection.execute('PRAGMA quick_check').fetchone() == ('ok',)
        state = scheduler.snapshot()
        if when == 'before_commit':
            assert state == before
            assert book.ids() == ('1000',)
        else:
            assert (state.revision, state.interval_hours) == (before.revision + 1, 6)
            assert book.ids() == ('1001',)
    finally:
        scheduler.close()
        following.close()


def reopen(parent):
    following = FollowingSyncStore(parent / 'follow')
    scheduler = ManagedScheduler(parent / 'managed')
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    return following, scheduler, book


def crash(parent, when):
    following, scheduler, book = reopen(parent)
    before = scheduler.snapshot()

    def trace(sql):
        if when == 'before_commit' and sql.strip().upper() == 'COMMIT':
            os._exit(CRASH_EXIT)
        if when == 'after_commit' and sql.strip().upper() == 'DETACH DATABASE FOLLOWING_SETTINGS':
            os._exit(CRASH_EXIT)

    scheduler._db.set_trace_callback(trace)
    scheduler.configure_all(author_ids=('1001',), interval_hours=6, startup_check=False,
                            expected_revision=before.revision, now=datetime.now(timezone.utc))


if __name__ == '__main__':
    crash(Path(sys.argv[1]), sys.argv[2])
