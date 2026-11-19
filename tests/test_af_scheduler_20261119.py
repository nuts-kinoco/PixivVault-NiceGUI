from types import SimpleNamespace
from threading import Event

import pytest

from account_test_support import observed_scope
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ManagedScheduleRunner, ScheduleError, submit_author_batch


def test_scheduler_targets_all_stars_from_current_account_and_checks_gate():
    calls = []
    class Reader:
        def verify_identity(self):
            calls.append('identity')
            self._verified_identity = '42'
            return self._verified_identity
    class Store:
        def snapshot(self, account):
            assert account == '42'
            return SimpleNamespace(account_id=account, authors=tuple(
                SimpleNamespace(user_id=str(i), is_favorite=i != 2, is_following=False)
                for i in range(1, 132)))
    service = SimpleNamespace(live_client=Reader(), following_sync=Store(), closing=False,
                              managed_network_stop=SimpleNamespace(require_allowed=lambda: calls.append('gate')))
    authors = ApplicationServices.scheduler_favourite_authors(service)
    assert len(authors) == 130 and '2' not in authors and '131' in authors
    assert calls == ['gate', 'identity']


def test_scheduler_fails_closed_without_favourite_store():
    service = SimpleNamespace(live_client=SimpleNamespace(_verified_identity='42', verify_identity=lambda: '42'),
                              following_sync=None, managed_network_stop=None, closing=False)
    with pytest.raises(RuntimeError):
        ApplicationServices.scheduler_favourite_authors(service)


def test_dispatch_allows_more_than_128_starred_authors():
    seen = []
    service = SimpleNamespace(managed_network_stop=None, author_media=None,
        managed_downloads=SimpleNamespace(saved_receipts_with_dates=lambda: ()),
        jobs=SimpleNamespace(snapshot=lambda: ()), closing=False, release_managed_owner=lambda owner: None,
        live_client=SimpleNamespace(_verified_identity='42', verify_identity=lambda: '42',
            get_author_artworks_complete=lambda aid: seen.append(aid) or ()))
    result = submit_author_batch(service, tuple(str(i) for i in range(1, 132)), cancellation=Event())
    assert result['authors'] == 131
    assert len(seen) == 131


def test_default_schedule_has_next_due_and_can_be_disabled(tmp_path):
    from datetime import datetime, timezone
    ledger = ManagedScheduler(tmp_path)
    try:
        state = ledger.snapshot()
        assert state.interval_hours == 24 and state.next_due_utc is not None
        disabled = ledger.configure(interval_hours=0, startup_check=False,
                                    expected_revision=state.revision, now=datetime.now(timezone.utc))
        assert disabled.next_due_utc is None
    finally:
        ledger.close()


def test_scheduler_refreshes_star_cache_without_losing_settings_revision(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    try:
        revision = ledger.snapshot().revision
        ledger.bind_account('42', scope=observed_scope('42'))
        ledger._sync_favourites(tuple(str(i) for i in range(1, 132)))
        assert len(ledger.favourites()) == 131
        ledger._sync_favourites(('9',))
        assert ledger.favourites() == ('9',)
        assert ledger.snapshot().revision == revision
    finally:
        ledger.close()


def test_unbound_existing_state_is_preserved_and_cannot_be_adopted(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    ledger.set_favourites(('9',), expected_revision=ledger.snapshot().revision, account_id=None)
    ledger._db.execute("INSERT INTO deferred_work VALUES ('99','100','2020',1,0,'2020')")
    ledger.close()
    ledger = ManagedScheduler(tmp_path)
    try:
        assert ledger.favourites() == ('9',)
        assert ledger.deferred_count() == 1
        with pytest.raises(ScheduleError):
            ledger.bind_account('42', scope=observed_scope('42'))
        assert ledger.favourites() == ('9',) and ledger.deferred_count() == 1
    finally:
        ledger.close()


def test_bound_account_rejects_other_account_without_changing_work(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    try:
        ledger.bind_account('42', scope=observed_scope('42'))
        ledger._sync_favourites(('9',))
        ledger.defer_work(author_id='9', work={'id':'100','update_date':'2020','page_count':1,'type':0}, now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc), account_id='42')
        with pytest.raises(ScheduleError):
            ledger.bind_account('43', scope=observed_scope('43'))
        ledger._sync_favourites(())
        assert ledger.deferred_count() == 1
    finally:
        ledger.close()


def test_off_schedule_does_not_dispatch_deferred_backlog(tmp_path):
    from datetime import datetime, timezone
    ledger = ManagedScheduler(tmp_path)
    ledger.bind_account('42', scope=observed_scope('42'))
    ledger._sync_favourites(('9',))
    ledger.defer_work(author_id='9', work={'id':'100','update_date':'2020','page_count':1,'type':0},
                      now=datetime.now(timezone.utc), account_id='42')
    ledger._disable(expected_revision=ledger.snapshot().revision)
    fired = Event()
    runner = ManagedScheduleRunner(ledger, lambda *_: fired.set())
    try:
        runner.start()
        assert not fired.wait(6)
        assert ledger.deferred_count() == 1
    finally:
        runner.stop()
        ledger.close()


def test_existing_never_run_default_schedule_gets_due_on_reopen(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    ledger._db.execute('UPDATE schedule SET next_due_utc=NULL')
    ledger.close()
    ledger = ManagedScheduler(tmp_path)
    try:
        assert ledger.snapshot().next_due_utc is not None
    finally:
        ledger.close()


def test_account_binding_failures_have_distinct_safe_messages():
    from pixivvault_nicegui.managed_scheduler import ScheduleAccountError
    from pixivvault_nicegui.ui.scheduler_view import check_failure_message
    unknown = check_failure_message(ScheduleAccountError('unknown'))
    mismatch = check_failure_message(ScheduleAccountError('mismatch'))
    assert '出所' in unknown and '保持' in unknown and '停止' in unknown
    assert '別' in mismatch and '保持' in mismatch and '停止' in mismatch


async def test_unsynced_following_never_uses_another_accounts_scheduler_stars():
    from threading import Lock
    reader = SimpleNamespace(_verified_identity='42', verify_identity=lambda: '42', get_following_users=lambda uid, visibility:
        [{'user_id':'9','name':'author'}] if visibility == 'show' else [])
    service = SimpleNamespace(_direct_owner=lambda owner: owner, closing=False,
        managed_downloads=object(), live_client=reader, following_sync=None,
        managed_scheduler=SimpleNamespace(favourites=lambda: ('9',)),
        _managed_target_lock=Lock(), _follow_account_by_owner={})
    authors = await ApplicationServices.followed_download_authors(service, owner='test')
    assert len(authors) == 1 and authors[0].is_favorite is None
