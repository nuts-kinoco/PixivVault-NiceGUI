"""WU-1: run ledger and the following_sync last_downloaded writer. Synthetic data only."""
import sqlite3
from datetime import datetime, timezone

import pytest

from types import SimpleNamespace

from pixivvault_nicegui.account_scope import AccountScope, OwnedListing
from pixivvault_nicegui.batch_runs import (
    BatchRunConflict, BatchRunError, BatchRunLedger, saved_counts_by_author)
from pixivvault_nicegui.following_sync import FollowingSyncError, FollowingSyncStore

ACCOUNT = '55'

def listing(author_id):
    reader = SimpleNamespace(_verified_identity=ACCOUNT)
    return OwnedListing(AccountScope(ACCOUNT, reader), author_id, 'both', ())



class Reader:
    def __init__(self, rows):
        self.rows = rows

    def require_identity(self):
        return ACCOUNT

    def get_following_users_detailed(self, uid, rest):
        return () if rest == 'hide' else tuple(
            dict(user_id=u, name='Generated ' + u, account='', profile_img='') for u in self.rows)


@pytest.fixture
def store(tmp_path):
    value = FollowingSyncStore(tmp_path / 'follow')
    value.sync(Reader(('41', '42', '43')))
    yield value
    value.close()


@pytest.fixture
def ledger(tmp_path):
    value = BatchRunLedger(tmp_path / 'runs')
    yield value
    value.close()


def row(snapshot, uid):
    return next(a for a in snapshot.authors if a.user_id == uid)


# ---- following_sync writers -------------------------------------------------

def test_record_downloaded_stamps_only_the_named_existing_author(store):
    before = store.snapshot(ACCOUNT)
    after = store.record_downloaded(ACCOUNT, '41', '2026-11-18T01:02:03+00:00')
    assert row(after, '41').last_downloaded == '2026-11-18T01:02:03+00:00'
    assert row(after, '42').last_downloaded is None
    # the follow list itself is unchanged: no revision bump, so a running follow
    # sync is not turned into a conflict by every finished author
    assert after.revision == before.revision


def test_record_downloaded_for_unknown_author_or_account_is_a_noop(store):
    before = store.snapshot(ACCOUNT)
    assert store.record_downloaded(ACCOUNT, '999', '2026-11-18T01:02:03+00:00') == before
    assert store.record_downloaded('77', '41', '2026-11-18T01:02:03+00:00').authors == ()
    assert store.snapshot(ACCOUNT) == before
    assert store.snapshot('77').revision == 0  # no phantom account row


@pytest.mark.parametrize('bad', ['yesterday', '2026-11-18T01:02:03+09:00', '', 5])
def test_record_downloaded_rejects_bad_stamp(store, bad):
    with pytest.raises(FollowingSyncError):
        store.record_downloaded(ACCOUNT, '41', bad)


def test_record_downloaded_honours_expected_revision(store):
    from pixivvault_nicegui.following_sync import FollowingSyncConflict
    with pytest.raises(FollowingSyncConflict):
        store.record_downloaded(ACCOUNT, '41', expected_revision=store.snapshot(ACCOUNT).revision + 1)


def test_set_favorite_round_trip(store):
    revision = store.snapshot(ACCOUNT).revision
    assert row(store.set_favorite(ACCOUNT, '42', True), '42').is_favorite
    assert store.set_favorite(ACCOUNT, '42', True).revision == revision + 1  # idempotent, no bump
    assert not row(store.set_favorite(ACCOUNT, '42', False), '42').is_favorite
    assert store.set_favorite(ACCOUNT, '999', True).revision == revision + 2  # unknown: no-op
    with pytest.raises(FollowingSyncError):
        store.set_favorite(ACCOUNT, '42', 1)


def test_resync_keeps_last_downloaded_favorite_and_zipped(tmp_path, store):
    """Characterisation (design 11): sync() must not wipe user-owned columns."""
    store.record_downloaded(ACCOUNT, '41', '2026-11-18T01:02:03+00:00')
    store.set_favorite(ACCOUNT, '41', True)
    with sqlite3.connect(tmp_path / 'follow' / 'following.sqlite3') as raw:
        raw.execute("UPDATE following_authors SET is_zipped=1 WHERE user_id='41'")
    again = store.sync(Reader(('41', '43')))  # 42 unfollowed meanwhile
    kept = row(again, '41')
    assert (kept.last_downloaded, kept.is_favorite, kept.is_zipped) == (
        '2026-11-18T01:02:03+00:00', True, True)
    assert not row(again, '42').is_following
    again = store.sync(Reader(('41', '42', '43')))  # refollow: columns survive
    assert row(again, '42').is_following


def test_stamp_survives_reopen(tmp_path):
    root = tmp_path / 'follow'
    first = FollowingSyncStore(root)
    first.sync(Reader(('41',)))
    first.record_downloaded(ACCOUNT, '41', '2026-11-18T01:02:03+00:00')
    first.close()
    second = FollowingSyncStore(root)
    try:
        assert row(second.snapshot(ACCOUNT), '41').last_downloaded == '2026-11-18T01:02:03+00:00'
    finally:
        second.close()


# ---- run ledger -------------------------------------------------------------

def test_run_lifecycle_and_reopen(tmp_path):
    root = tmp_path / 'runs'
    ledger = BatchRunLedger(root)
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42', '41'), account_id=ACCOUNT)
    record = ledger.run(run_id)
    assert (record.state, record.authors_total, record.kind, record.finished_utc) == ('running', 2, 'authors', None)
    assert [a.state for a in ledger.authors(run_id)] == ['pending', 'pending']
    ledger.update_author(run_id, '41', state='listing')
    ledger.update_author(run_id, '41', state='downloading', counters={'n_new': 3, 'n_updated': 1})
    ledger.record_item(run_id, '41', '100', 'illustration', 'a' * 32, job_id='b' * 32)
    ledger.record_item(run_id, '41', '100', 'illustration', 'a' * 32, state='done')
    ledger.complete_author(run_id, '41', {'n_new': 3, 'n_updated': 1, 'n_failed': 1}, listing=listing('41'))
    ledger.set_run_state(run_id, 'done')
    ledger.close()
    ledger = BatchRunLedger(root)  # reopen: everything is still there
    try:
        first = ledger.authors(run_id)[0]
        assert (first.state, first.n_new, first.n_updated, first.n_failed) == ('done', 3, 1, 1)
        assert first.started_utc and first.finished_utc
        assert [(i.state, i.job_id) for i in ledger.items(run_id)] == [('done', 'b' * 32)]
        assert ledger.run(run_id).state == 'done' and ledger.run(run_id).finished_utc
        assert ledger.runs()[0].run_id == run_id
    finally:
        ledger.close()


def test_second_open_of_the_same_folder_is_refused(tmp_path):
    first = BatchRunLedger(tmp_path / 'runs')
    try:
        with pytest.raises(BatchRunError):
            BatchRunLedger(tmp_path / 'runs')
    finally:
        first.close()
    BatchRunLedger(tmp_path / 'runs').close()  # released on close


def test_foreign_or_corrupt_folders_are_rejected(tmp_path):
    (tmp_path / 'plain').mkdir()
    with pytest.raises(BatchRunError):
        BatchRunLedger(tmp_path / 'plain')  # no marker
    ledger = BatchRunLedger(tmp_path / 'runs')
    ledger.close()
    (tmp_path / 'runs' / 'stray.txt').write_text('x')
    with pytest.raises(BatchRunError):
        BatchRunLedger(tmp_path / 'runs')
    with pytest.raises(BatchRunError):
        BatchRunLedger('relative/path')


def test_only_one_active_run_at_a_time(ledger):
    first = ledger.begin_run('authors', 'manual', 'both', ('41',))
    with pytest.raises(BatchRunConflict):
        ledger.begin_run('authors', 'auto', 'both', ('42',))
    assert ledger.active_run().run_id == first
    ledger.set_run_state(first, 'stopped')
    assert ledger.active_run() is None
    ledger.begin_run('bookmarks', 'manual', '', ())


def test_terminal_run_state_is_final(ledger):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41',))
    ledger.set_run_state(run_id, 'failed', 'x_failed')
    with pytest.raises(BatchRunConflict):
        ledger.set_run_state(run_id, 'running')
    ledger.set_run_state(run_id, 'failed')  # same state again is harmless
    assert ledger.run(run_id).error_code == 'x_failed'


def test_recover_interrupted_marks_active_runs_and_midflight_authors(ledger):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=ACCOUNT)
    ledger.update_author(run_id, '41', state='downloading')
    assert ledger.recover_interrupted() == (run_id,)
    assert ledger.run(run_id).state == 'interrupted'
    assert [a.state for a in ledger.authors(run_id)] == ['stopped', 'pending']
    assert ledger.active_run() is None and ledger.recover_interrupted() == ()


# ---- last_downloaded rule ---------------------------------------------------

def test_done_author_stamps_last_downloaded(ledger, store):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=ACCOUNT)
    stamp = ledger.complete_author(run_id, '41', {'n_new': 2}, following=store, listing=listing('41'))
    assert row(store.snapshot(ACCOUNT), '41').last_downloaded == stamp
    assert row(store.snapshot(ACCOUNT), '42').last_downloaded is None
    author = ledger.authors(run_id)[0]
    assert author.last_downloaded_utc == stamp and author.stamp_written
    datetime.fromisoformat(stamp).astimezone(timezone.utc)


@pytest.mark.parametrize('outcome', ['failed', 'stopped', 'skipped'])
def test_failed_stopped_or_skipped_author_never_stamps(ledger, store, outcome):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41',), account_id=ACCOUNT)
    ledger.finish_author(run_id, '41', outcome, error_code='boom')
    assert row(store.snapshot(ACCOUNT), '41').last_downloaded is None
    with pytest.raises(BatchRunConflict):  # a settled author cannot be completed afterwards
        ledger.complete_author(run_id, '41', following=store, listing=listing('41'))
    assert row(store.snapshot(ACCOUNT), '41').last_downloaded is None


def test_stopping_run_never_stamps_even_if_the_step_returned(ledger, store):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41',), account_id=ACCOUNT)
    ledger.set_run_state(run_id, 'stopping')
    assert ledger.complete_author(run_id, '41', following=store, listing=listing('41')) is None
    assert row(store.snapshot(ACCOUNT), '41').last_downloaded is None
    assert ledger.authors(run_id)[0].state == 'done'  # the work is recorded, the stamp is not


def test_bookmarks_run_never_stamps(ledger, store):
    run_id = ledger.begin_run('bookmarks', 'manual', '', ('41',), account_id=ACCOUNT)
    assert ledger.complete_bookmark_row(run_id, '41') is None
    assert row(store.snapshot(ACCOUNT), '41').last_downloaded is None


def test_author_that_is_not_followed_is_recorded_without_stamping(ledger, store):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('999',), account_id=ACCOUNT)
    ledger.complete_author(run_id, '999', {'n_new': 1}, following=store, listing=listing('999'))
    assert ledger.authors(run_id)[0].state == 'done'
    assert store.snapshot(ACCOUNT).revision == 1 and all(a.last_downloaded is None for a in store.snapshot(ACCOUNT).authors)


def test_no_account_or_no_store_does_not_stamp(ledger, store):
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42'))
    assert ledger.complete_author(run_id, '41', following=store, listing=listing('41')) is None
    assert ledger.complete_author(run_id, '42', listing=listing('42')) is None
    assert all(a.last_downloaded is None for a in store.snapshot(ACCOUNT).authors)


# ---- saved count per author -------------------------------------------------

def test_saved_counts_by_author():
    receipts = [('r/1', {'author_id': '41'}, 'd', None), ('r/2', {'author_id': '41'}, 'd', None),
                ('r/3', {'author_id': '42'}, 'd', None), ('r/4', {'author_id': 'x'}, 'd', None)]
    assert saved_counts_by_author(receipts) == {'41': 2, '42': 1}
    assert saved_counts_by_author(()) == {}
