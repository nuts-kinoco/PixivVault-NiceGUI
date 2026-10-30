"""Explicit legacy failed_queue copy -> verified managed queue, without DB writes."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3
import time
import threading
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from nicegui import Client, ui
from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.failed_records_repository import read_failed_records
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui.ui.job_views import QueueView
from pixivvault_nicegui.ui.shell import DemoShell


class OfflinePixiv:
    def __init__(self, *, wrong_author=False, delayed=None):
        self.calls = []
        self.wrong_author = wrong_author
        self.delayed = delayed
        self.closed = False

    def verify_identity(self):
        self.calls.append('identity')
        return '12'

    def get_artwork_download_record(self, work_id):
        self.calls.append(('artwork', work_id))
        if self.delayed is not None:
            assert self.delayed.wait(5)
        return RemoteBookmark(work_id, 'illustration', 'show',
                              '合成作品', '99' if self.wrong_author else '23',
                              '合成作者', (), '', '2026', 1,
                              1 if work_id == '302' else 0, 0)

    def get_artwork_original_pages(self, work_id):
        self.calls.append(('original-pages', work_id))
        return (f'https://i.pximg.net/synthetic/{work_id}_p0.png',)

    def get_image_bytes(self, url):
        self.calls.append('page')
        buffer = BytesIO()
        Image.new('RGB', (20, 10), (55, 80, 110)).save(buffer, format='PNG')
        return buffer.getvalue()

    def close(self):
        self.closed = True


def make_snapshot(tmp_path):
    # The writable managed root must NEVER be nested beneath the protected
    # old DB's parent. Use distinct sibling roots for both frozen source copies
    # and keep each test's original/Cookie hash assertions unchanged.
    source_root=tmp_path/'original-source'
    frozen_root=tmp_path/'frozen-old-copy'
    source_root.mkdir();frozen_root.mkdir()
    original, copied = (source_root / 'legacy-original.sqlite3',
                        frozen_root / 'static-failures.sqlite3')
    with sqlite3.connect(original) as conn:
        conn.execute('CREATE TABLE failed_queue (work_id TEXT, user_id TEXT, title TEXT, '
                     'work_type TEXT, error_reason TEXT, failed_at TEXT, retry_count INTEGER)')
        conn.executemany('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)', (
            ('301', '23', '旧イラスト', 'illust', 'Timeout', '2026-10-23 10:00:00', 1),
            ('302', '23', '旧漫画', 'manga', '404', '2026-10-23 09:00:00', 0),
            ('303', '23', '未対応小説', 'novel', '403', '2026-10-23 08:00:00', 2),
            ('304', '23', '未対応うごイラ', 'ugoira', 'Timeout', '2026-10-23 07:00:00', 0),
        ))
    shutil.copyfile(original, copied)
    return original, copied


def digest(path):
    return sha256(path.read_bytes()).digest()


def finished(service, job_id):
    with service.jobs._condition:
        assert service.jobs._condition.wait_for(
            lambda: any(job.job_id == job_id and job.state in ('done', 'error', 'stopped')
                        for job in service.jobs.snapshot()), timeout=8)
    return next(job for job in service.jobs.snapshot() if job.job_id == job_id)


@pytest.mark.asyncio
async def test_q03_managed_queue_author_name_filter_survives_restart(tmp_path):
    """Author search must use frozen accepted metadata, never demo author IDs."""
    original, frozen = make_snapshot(tmp_path)
    original_sha, frozen_sha = digest(original), digest(frozen)
    root = tmp_path / 'managed-q03'
    service = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=root,
                                  failed_queue_readonly_db=frozen)
    client = Client(ui.page('/_q03_managed_author_search'))
    try:
        with client:
            failure_view = FailedRecordsView(service)
            await failure_view._refresh(failure_view.state.begin_refresh())
            selected = next(row for row in failure_view.state.records if row.work_id == '301')
            assert await failure_view._retry_records((selected,))
            assert finished(service, service.jobs.snapshot()[0].job_id).state == 'done'
            queue = QueueView(service)
            queue.search('合成作者')
            assert [job.target_id for job in queue._matching()] == ['301']
            queue.search('301')
            assert [job.target_id for job in queue._matching()] == ['301']
            queue.search('別の作者')
            assert queue._matching() == []
            queue.close()
        assert (digest(original), digest(frozen)) == (original_sha, frozen_sha)
    finally:
        client.delete()
        service.close()
    reopened = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=root,
                                   failed_queue_readonly_db=frozen)
    second_client = Client(ui.page('/_q03_managed_author_search_restart'))
    try:
        with second_client:
            queue = QueueView(reopened)
            queue.search('合成作者')
            assert [job.target_id for job in queue._matching()] == ['301']
            queue.close()
        assert (digest(original), digest(frozen)) == (original_sha, frozen_sha)
    finally:
        second_client.delete()
        reopened.close()


def test_managed_storage_nested_in_frozen_copy_parent_is_rejected_before_writes(tmp_path):
    """Separate fixture roots are mandatory; overlap is still a hard refusal."""
    original, copied = make_snapshot(tmp_path)
    protected = (digest(original), digest(copied))
    unsafe = copied.parent / 'managed-under-protected-copy'
    with pytest.raises(ValueError, match='media storage roots must be disjoint'):
        ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=unsafe,
                            failed_queue_readonly_db=copied)
    assert not unsafe.exists()
    assert (digest(original), digest(copied)) == protected


@pytest.mark.asyncio
async def test_optin_real_ui_retries_one_original_and_recovers_without_legacy_mutation(tmp_path, monkeypatch):
    old, copied = make_snapshot(tmp_path)
    before = digest(old), digest(copied)
    live = OfflinePixiv()
    root = tmp_path / 'new-managed'
    service = ApplicationServices(live_client=live, pixiv_download_root=root,
                                  failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_managed_one'))
    try:
        assert service.legacy_failed_retry_available and not live.calls
        from pixivvault_nicegui.ui import failed_records_view
        # NiceGUI core.loop is set by ui.run, not by Client test instances.
        # Run the very same view coroutine on this test's live asyncio loop.
        monkeypatch.setattr(failed_records_view.background_tasks, 'create',
                            lambda coroutine: asyncio.create_task(coroutine))
        with client:
            shell = DemoShell(service)
            shell.show('failed')
            view = shell.failed_records
            assert isinstance(view, FailedRecordsView)
            for _ in range(80):
                if view._loaded and not view.state.loading:
                    break
                await asyncio.sleep(.01)
            assert view._loaded and not view.state.loading
            assert len(view.state.records) == 4
            assert view.retry_all.enabled
            assert len(view.state.matched_records) == 4
            selected = next(item for item in view.state.records if item.work_id == '301')
            assert await view._retry_records((selected,))
            assert '1 件' in view.retry_notice.text
            assert not any(call == ('artwork', '303') or call == ('artwork', '304')
                           for call in live.calls)
        jobs = service.jobs.snapshot()
        assert len(jobs) == 1 and finished(service, jobs[0].job_id).state == 'done'
        assert len(service.managed_downloads.saved_receipts()) == 1
        assert (digest(old), digest(copied)) == before
        assert not any(Path(str(copied) + extra).exists()
                       for extra in ('-wal', '-shm', '-journal'))
    finally:
        client.delete()
        service.close()
    restarted_source = OfflinePixiv()
    reopened = ApplicationServices(live_client=restarted_source, pixiv_download_root=root,
                                   failed_queue_readonly_db=copied)
    try:
        assert len(reopened.managed_downloads.saved_receipts()) == 1
        assert any(job.state == 'done' for job in reopened.jobs.snapshot())
        assert not restarted_source.calls, 'restart must not call Pixiv'
        assert (digest(old), digest(copied)) == before
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_filtered_batch_retries_supported_only_and_preserves_failure_copy(tmp_path):
    old, copied = make_snapshot(tmp_path)
    before = digest(old), digest(copied)
    source = OfflinePixiv()
    app = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_managed_batch'))
    try:
        with client:
            view = FailedRecordsView(app)
            await view._refresh(view.state.begin_refresh())
            assert await view._retry_records(tuple(item for item in view.state.records
                                                  if view._retry_supported(item)))
            assert not any(call in (('artwork', '303'), ('artwork', '304'))
                           for call in source.calls)
        jobs = app.jobs.snapshot()
        assert len(jobs) == 2
        for job in jobs:
            assert finished(app, job.job_id).state == 'done'
        assert {receipt['work_id'] for _, receipt, _ in app.managed_downloads.saved_receipts()} == {'301', '302'}
        assert (digest(old), digest(copied)) == before
    finally:
        client.delete()
        app.close()


@pytest.mark.asyncio
async def test_stale_copy_row_rejected_before_remote_get_or_job_acceptance(tmp_path):
    _, copied = make_snapshot(tmp_path)
    app = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    try:
        original = next(item for item in await app.read_failed_records() if item.work_id == '301')
        with sqlite3.connect(copied) as connection:
            connection.execute("UPDATE failed_queue SET retry_count=5 WHERE work_id='301'")
        with pytest.raises(ValueError, match='copy changed'):
            await app.lookup_legacy_failed_original(original, owner='a' * 32)
        assert not app.live_client.calls and not app.jobs.snapshot()
    finally:
        app.close()


@pytest.mark.asyncio
async def test_wrong_author_and_unsupported_kinds_cannot_enqueue(tmp_path):
    _, copied = make_snapshot(tmp_path)
    source = OfflinePixiv(wrong_author=True)
    app = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    try:
        rows = await app.read_failed_records()
        for row in (item for item in rows if item.work_type in ('novel', 'ugoira')):
            with pytest.raises(ValueError, match='not supported'):
                await app.lookup_legacy_failed_original(row, owner='a' * 32)
        illustration = next(item for item in rows if item.work_type == 'illust')
        with pytest.raises(ValueError, match='identity changed'):
            await app.lookup_legacy_failed_original(illustration, owner='a' * 32)
        assert not app.jobs.snapshot()
    finally:
        app.close()


def test_failed_copy_optin_cli_and_invalid_storage_gates(tmp_path):
    _, copied = make_snapshot(tmp_path)
    credential_dir = tmp_path / 'credentials'
    credential_dir.mkdir()
    cookie = credential_dir / 'synthetic-cookie-file.txt'
    cookie.write_text('synthetic-only', encoding='utf-8')
    root = tmp_path / 'managed'
    flags = ['--pixiv-cookie-file', str(cookie), '--pixiv-download-root', str(root),
             '--failed-queue-readonly-db', str(copied)]
    assert parse_args(flags).failed_queue_readonly_db == copied
    with pytest.raises(SystemExit):
        parse_args(['--failed-queue-readonly-db', str(copied)])
    with pytest.raises(ValueError):
        ApplicationServices(failed_queue_readonly_db=copied)
    Path(str(copied) + '-wal').write_bytes(b'incomplete-wal')
    with pytest.raises(SystemExit):
        parse_args(flags)
    with pytest.raises(ValueError):
        ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=root,
                            failed_queue_readonly_db=copied)
    assert not root.exists()


def test_immutable_source_never_treats_wal_as_empty_or_creates_shm(tmp_path):
    original, copied = make_snapshot(tmp_path)
    before = digest(copied)
    assert len(read_failed_records(copied, immutable=True)) == 4
    assert before == digest(copied)
    assert not Path(str(copied) + '-shm').exists()
    Path(str(copied) + '-wal').write_bytes(b'fake')
    with pytest.raises(ValueError, match='standalone copy'):
        read_failed_records(copied, immutable=True)


@pytest.mark.asyncio
async def test_same_row_second_explicit_retry_after_terminal_creates_new_job(tmp_path):
    class RetrySource(OfflinePixiv):
        fail_page = True

        def get_image_bytes(self, url):
            if self.fail_page:
                raise OSError('synthetic page GET failure')
            return super().get_image_bytes(url)

    _, copied = make_snapshot(tmp_path)
    source = RetrySource()
    app = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_same_row_again'))
    try:
        with client:
            view = FailedRecordsView(app)
            await view._refresh(view.state.begin_refresh())
            row = next(r for r in view.state.records if r.work_id == '301')
            assert await view._retry_records((row,))
            first = app.jobs.snapshot()[0]
            assert finished(app, first.job_id).state == 'error'
            source.fail_page = False
            assert await view._retry_records((row,))
            assert len(app.jobs.snapshot()) == 2
            second = next(j for j in app.jobs.snapshot() if j.job_id != first.job_id)
            assert finished(app, second.job_id).state == 'done'
            # A fresh *explicit* click must rotate the operation after a
            # terminal job, never misreport an old error as accepted again.
            assert app.jobs.operation_ids(first.job_id) != app.jobs.operation_ids(second.job_id)
            assert await view._retry_records((row,))
            third = next(j for j in app.jobs.snapshot() if j.job_id not in
                         (first.job_id, second.job_id))
            assert finished(app, third.job_id).state == 'done'
            assert len(app.managed_downloads.saved_receipts()) == 1
    finally:
        client.delete()
        app.close()


def test_one_owner_can_accept_three_batches_without_sticky_128_pins(tmp_path, monkeypatch):
    _, copied = make_snapshot(tmp_path)
    with sqlite3.connect(copied) as conn:
        conn.execute('DELETE FROM failed_queue')
        conn.executemany('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)', (
            (str(wid), '23', 'Synthetic', 'illust', 'Timeout', '2026-10-23', 0)
            for wid in range(1001, 1131)))
    app = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    owner = uuid4().hex
    accepted = []
    def committed(record, operation_id, *, owner):
        accepted.append(record.remote_id)
        return SimpleNamespace(job_id=uuid4().hex)
    monkeypatch.setattr(app, 'enqueue_managed_download', committed)
    try:
        rows = read_failed_records(copied, immutable=True)
        from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
        assert len(rows) == 130
        for row in rows:
            record = RemoteBookmark(row.work_id, 'illustration', 'show', 'Synthetic',
                                    row.user_id, 'Synthetic', (), '', '2026', 1, 0, 0)
            app.enqueue_legacy_failed_original(row, record, uuid4().hex, owner=owner)
            assert owner not in app._direct_download_candidates
            assert owner not in app._author_owned_pins
        assert len(accepted) == 130 and len(set(accepted)) == 130
    finally:
        app.close()


@pytest.mark.asyncio
async def test_copy_change_in_unrelated_row_blocks_selected_work(tmp_path):
    original, copied = make_snapshot(tmp_path)
    before_original = digest(original)
    source = OfflinePixiv()
    app = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    try:
        row = next(item for item in await app.read_failed_records() if item.work_id == '301')
        # Selected row 301 remains byte-for-byte identical; any *other* source
        # mutation invalidates the signed snapshot, even if row check passes.
        with sqlite3.connect(copied) as conn:
            conn.execute("UPDATE failed_queue SET retry_count=8 WHERE work_id='304'")
        with pytest.raises(ValueError, match='copy changed'):
            await app.lookup_legacy_failed_original(row, owner='a' * 32)
        with pytest.raises(ValueError, match='copy changed'):
            await app.read_failed_records()
        assert not source.calls and not app.jobs.snapshot()
        assert digest(original) == before_original
    finally:
        app.close()


@pytest.mark.asyncio
async def test_changed_remote_intent_after_uncertain_accept_rotates_collision_id(tmp_path, monkeypatch):
    class ChangingPixiv(OfflinePixiv):
        current_title = 'Previous synthetic title'

        def get_artwork_download_record(self, work_id):
            return replace(super().get_artwork_download_record(work_id),
                           title=self.current_title)

    old, copied = make_snapshot(tmp_path)
    expected = digest(old), digest(copied)
    pixiv = ChangingPixiv()
    service = ApplicationServices(live_client=pixiv, pixiv_download_root=tmp_path / 'managed',
                                  failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_collision_recovery'))
    try:
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            row = next(item for item in view.state.records if item.work_id == '301')
            prior = service.jobs.enqueue
            def uncommitted(*_args, **_kwargs):
                raise OSError('synthetic rejected SQLite transaction')
            monkeypatch.setattr(service.jobs, 'enqueue', uncommitted)
            assert not await view._retry_records((row,))
            assert not service.jobs.snapshot()
            assert len(view._retry_ops) == 1
            assert len(list((service.pixiv_download_root / 'requests').glob('*.json'))) == 1

            pixiv.current_title = 'Updated synthetic title'
            monkeypatch.setattr(service.jobs, 'enqueue', prior)
            assert not await view._retry_records((row,))
            assert not view._retry_ops, 'collision must release the never-accepted key'
            assert not service.jobs.snapshot()
            assert await view._retry_records((row,))
            job = service.jobs.snapshot()[0]
            assert finished(service, job.job_id).state == 'done'
            assert len(service.managed_downloads.saved_receipts()) == 1
            assert (digest(old), digest(copied)) == expected
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_sqlite_enqueue_failure_is_not_false_success_and_retry_recovers(tmp_path, monkeypatch):
    old, copied = make_snapshot(tmp_path)
    original_hash, snapshot_hash = digest(old), digest(copied)
    app = ApplicationServices(live_client=OfflinePixiv(), pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_managed_sqlite_fault'))
    try:
        with client:
            view = FailedRecordsView(app)
            await view._refresh(view.state.begin_refresh())
            row = next(item for item in view.state.records if item.work_id == '301')
            original = app.jobs.enqueue

            def reject(*_args, **_kwargs):
                raise OSError('private database failure that must not be displayed')

            monkeypatch.setattr(app.jobs, 'enqueue', reject)
            assert not await view._retry_records((row,))
            assert 'private' not in view.retry_notice.text
            assert not app.jobs.snapshot()
            assert not app._managed_job_records
            assert (digest(old), digest(copied)) == (original_hash, snapshot_hash)
            monkeypatch.setattr(app.jobs, 'enqueue', original)
            assert await view._retry_records((row,))
        assert len(app.jobs.snapshot()) == 1
        assert finished(app, app.jobs.snapshot()[0].job_id).state == 'done'
        assert len(app.managed_downloads.saved_receipts()) == 1
        assert (digest(old), digest(copied)) == (original_hash, snapshot_hash)
    finally:
        client.delete()
        app.close()


@pytest.mark.asyncio
async def test_legacy_source_changed_during_remote_lookup_is_not_accepted(tmp_path):
    _, copied = make_snapshot(tmp_path)
    release = threading.Event()
    pixiv = OfflinePixiv(delayed=release)
    app = ApplicationServices(live_client=pixiv, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_managed_stale'))
    try:
        with client:
            view = FailedRecordsView(app)
            await view._refresh(view.state.begin_refresh())
            row = next(item for item in view.state.records if item.work_id == '301')
            task = asyncio.create_task(view._retry_records((row,)))
            for _ in range(60):
                if ('artwork', '301') in pixiv.calls:
                    break
                await asyncio.sleep(.01)
            assert ('artwork', '301') in pixiv.calls
            # Test-controlled fixture mutation only; never edit a real DB.
            with sqlite3.connect(copied) as conn:
                conn.execute("UPDATE failed_queue SET retry_count=7 WHERE work_id='301'")
            release.set()
            assert not await asyncio.wait_for(task, 5)
            assert not app.jobs.snapshot()
            assert '0 件を管理キュー' in view.retry_notice.text
    finally:
        release.set()
        client.delete()
        app.close()


@pytest.mark.asyncio
async def test_client_tab_departure_never_accepts_late_unverified_lookup(tmp_path):
    _, copied = make_snapshot(tmp_path)
    release = threading.Event()
    pixiv = OfflinePixiv(delayed=release)
    app = ApplicationServices(live_client=pixiv, pixiv_download_root=tmp_path / 'managed',
                              failed_queue_readonly_db=copied)
    client = Client(ui.page('/_failed_managed_tab_away'))
    try:
        with client:
            view = FailedRecordsView(app)
            await view._refresh(view.state.begin_refresh())
            row = next(item for item in view.state.records if item.work_id == '301')
            task = asyncio.create_task(view._retry_records((row,)))
            for _ in range(60):
                if ('artwork', '301') in pixiv.calls:
                    break
                await asyncio.sleep(.01)
            assert ('artwork', '301') in pixiv.calls
            view.set_active(False)
            view.set_active(True)
            release.set()
            assert not await asyncio.wait_for(task, 5)
            assert not app.jobs.snapshot()
    finally:
        release.set()
        client.delete()
        app.close()
