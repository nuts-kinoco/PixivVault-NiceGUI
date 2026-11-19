from threading import RLock
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ScheduleAccountError
from pixivvault_nicegui.services import ApplicationServices
from test_v_dl_priority_20261117 import ACCOUNT, sync


def test_unbound_legacy_settings_and_pending_are_not_imported_or_deleted(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    ledger = ManagedScheduler(root)
    ledger.set_favourites(('1001',), expected_revision=ledger.snapshot().revision, account_id=None)
    ledger._db.execute("INSERT INTO deferred_work VALUES ('9999','100','2020',1,0,'2020')")
    ledger.close()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    ledger = ManagedScheduler(root)
    try:
        book = FavouriteBook(following, scheduler=ledger)
        ledger.attach_favourites(book)
        assert ledger.favourites(ACCOUNT) == ()
        assert ledger.legacy_favourites() == ('1001',)
        assert book.ids(ACCOUNT) == ()
        assert ledger.deferred_count() == 1
        with pytest.raises(ScheduleAccountError):
            ledger.bind_account(book.account())
        assert book.ids(ACCOUNT) == () and ledger.deferred_count() == 1
    finally:
        ledger.close()
        following.close()


def test_star_selection_rejects_the_wrong_displayed_account(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    ledger = ManagedScheduler(root)
    try:
        ledger.bind_account('999')
        book = FavouriteBook(following, scheduler=ledger)
        ledger.attach_favourites(book)
        with pytest.raises(ScheduleAccountError):
            ledger.set_favourites(('1001',), expected_revision=ledger.snapshot().revision, account_id='999')
        assert book.ids(ACCOUNT) == ()
    finally:
        ledger.close()
        following.close()


def test_dispatch_selects_verified_account_stars_before_queue(monkeypatch):
    from pixivvault_nicegui import managed_scheduler
    calls = []
    ledger = SimpleNamespace(sync_favourites=lambda ids: calls.append(('sync', ids)))
    service = SimpleNamespace(batch_runs=None,
        scheduler_favourite_authors=lambda **kwargs: calls.append(('verify', kwargs['ledger'])) or ('7', '8'))
    monkeypatch.setattr(managed_scheduler, 'submit_author_batch',
                        lambda services, authors, **kwargs: calls.append(('submit', authors)) or {'authors': len(authors)})
    result = ApplicationServices.dispatch_author_check(service, ledger, 'slot', SimpleNamespace())
    assert result['authors'] == 2
    assert calls == [('verify', ledger), ('sync', ('7', '8')), ('submit', ('7', '8'))]


def test_unbound_existing_schedule_settings_are_not_adopted(tmp_path):
    from datetime import datetime, timezone
    ledger = ManagedScheduler(tmp_path)
    initial = ledger.snapshot()
    ledger.configure(interval_hours=6, startup_check=True, expected_revision=initial.revision,
                     now=datetime.now(timezone.utc))
    ledger._db.execute('ALTER TABLE schedule DROP COLUMN settings_origin')
    before = ledger.snapshot()
    ledger.close()
    ledger = ManagedScheduler(tmp_path)
    try:
        with pytest.raises(ScheduleAccountError):
            ledger.bind_account('55')
        assert ledger.snapshot() == before
    finally:
        ledger.close()


def test_scheduler_batch_refuses_when_verified_account_differs_from_latest_sync():
    class Engine:
        account_id = None
        def start(self, authors, **kwargs):
            assert authors == ('7',)
            assert kwargs['account_id'] == '55'
            return 'run'
    service = SimpleNamespace(batch_runs=Engine(), closing=False,
        following_sync=SimpleNamespace(last_synced_account=lambda: '99', _lock=RLock()),
        live_client=SimpleNamespace(_verified_identity='55'))
    with pytest.raises(RuntimeError, match='account changed'):
        ApplicationServices.start_batch_run(service, ('7',), origin='auto',
                                            target_type='both', account_id='55')


def test_scheduler_passes_bound_account_to_batch_engine(monkeypatch):
    from pixivvault_nicegui import managed_scheduler
    calls = []
    ledger = SimpleNamespace(_bound_account=lambda: '55')
    service = SimpleNamespace(batch_runs=object())
    monkeypatch.setattr(managed_scheduler, 'run_author_check',
                        lambda *args, **kwargs: calls.append(kwargs['account_id']) or {'authors': 1})
    result = ApplicationServices.dispatch_author_check(service, ledger, 'slot', SimpleNamespace(), authors=('7',))
    assert result['authors'] == 1 and calls == ['55']


def test_unknown_account_does_not_reconcile_existing_jobs(tmp_path):
    ledger = ManagedScheduler(tmp_path)
    ledger._account_guard = True
    ledger._db.execute("INSERT INTO deferred_work VALUES ('9','100','2020',1,0,'2020')")
    def forbidden():
        raise AssertionError('unbound scheduler must not reconcile')
    service = SimpleNamespace(closing=False, managed_scheduler=ledger,
                              managed_downloads=object(), jobs=SimpleNamespace(snapshot=forbidden),
                              live_client=SimpleNamespace(_verified_identity=None))
    try:
        assert ApplicationServices.reconcile_managed_scheduler(service) is False
        assert ledger.deferred_count() == 1
    finally:
        ledger.close()


def test_current_account_stars_are_visible_and_bound_before_dispatch(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    following.set_favorite('55', '1001', True)
    ledger = ManagedScheduler(root)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    reader = SimpleNamespace(_verified_identity='55', verify_identity=lambda: '55')
    service = SimpleNamespace(closing=False, following_sync=following, live_client=reader,
                              managed_network_stop=None, managed_scheduler=ledger)
    try:
        assert ledger.favourites(ACCOUNT) == ('1001',)
        assert ApplicationServices.scheduler_favourite_authors(service, ledger=ledger) == ('1001',)
        assert ledger._bound_account() == '55'
        ledger.set_favourites(('1002',), expected_revision=ledger.snapshot().revision, account_id=ACCOUNT)
        assert book.ids(ACCOUNT) == ('1002',)
    finally:
        ledger.close()
        following.close()


@pytest.mark.asyncio
async def test_first_scheduler_form_save_updates_verified_account_stars(tmp_path):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    following.set_favorite('55', '1001', True)
    ledger = ManagedScheduler(root)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    reader = SimpleNamespace(_verified_identity='55', verify_identity=lambda: '55')
    service = SimpleNamespace(closing=False, following_sync=following, live_client=reader,
                              managed_network_stop=None, managed_scheduler=ledger,
                              managed_schedule_runner=SimpleNamespace(settings_changed=lambda: None))
    service.scheduler_favourite_authors = lambda **kw: ApplicationServices.scheduler_favourite_authors(service, **kw)
    client = Client(ui.page('/merge-scheduler-form'))
    try:
        with client:
            view = SchedulerView(service)
        view._load(ledger)
        assert view.authors.value == '1001'
        view.authors.set_value('1002,9999')
        await view.save()
        assert book.ids(ACCOUNT) == ('1002',)
        assert ledger._bound_account() is None
        assert '9999' in view.action_notice.text
    finally:
        client.delete()
        ledger.close()
        following.close()


def test_first_due_check_binds_account_after_claim_without_losing_stars(tmp_path):
    from datetime import datetime, timedelta, timezone
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    following.set_favorite('55', '1001', True)
    ledger = ManagedScheduler(root)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    reader = SimpleNamespace(_verified_identity='55', verify_identity=lambda: '55')
    service = SimpleNamespace(closing=False, following_sync=following, live_client=reader,
                              managed_network_stop=None, managed_scheduler=ledger)
    try:
        assert ledger.snapshot().interval_hours == 24
        assert ledger.claim_due(datetime.now(timezone.utc) + timedelta(hours=25)) is not None
        assert ApplicationServices.scheduler_favourite_authors(service, ledger=ledger) == ('1001',)
        assert ledger._bound_account() == '55' and book.ids(ACCOUNT) == ('1001',)
    finally:
        ledger.close()
        following.close()


def test_verified_account_star_save_failure_rolls_back_following_ledger(tmp_path):
    from datetime import datetime, timezone
    root = tmp_path / 'managed'
    root.mkdir()
    following = FollowingSyncStore(tmp_path / 'following')
    sync(following, 3)
    following.set_favorite('55', '1001', True)
    ledger = ManagedScheduler(root)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    ledger.bind_account('55')
    ledger._db.execute("CREATE TRIGGER reject_schedule BEFORE UPDATE ON schedule BEGIN SELECT RAISE(ABORT, 'test'); END")
    try:
        with pytest.raises(Exception):
            ledger.configure_all(author_ids=('1002',), interval_hours=6, startup_check=False,
                                 expected_revision=ledger.snapshot().revision, now=datetime.now(timezone.utc), account_id=ACCOUNT)
        assert book.ids(ACCOUNT) == ('1001',)
    finally:
        ledger.close()
        following.close()


def test_scheduler_batch_rejects_account_change_before_start():
    def forbidden(*args, **kwargs):
        raise AssertionError('batch must not start under another account')
    service = SimpleNamespace(batch_runs=SimpleNamespace(account_id=None, start=forbidden),
        closing=False, following_sync=None, live_client=SimpleNamespace(_verified_identity='99'))
    with pytest.raises(RuntimeError, match='account changed'):
        ApplicationServices.start_batch_run(service, ('7',), origin='auto',
                                            target_type='both', account_id='55')
