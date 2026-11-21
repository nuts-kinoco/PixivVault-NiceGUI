import asyncio
from datetime import datetime, timezone
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy, ManagedNetworkStopped
from pixivvault_nicegui.managed_scheduler import ManagedScheduleRunner, ManagedScheduler
from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.services import ApplicationServices, _APP_KEY
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import until
from test_cookie_account_followup_20261117 import age, make
from test_w_account_separation_20261119 import A, B, X, Reader, resources, switch


@pytest.mark.asyncio
@pytest.mark.parametrize('startup', (True, False), ids=('startup-only', 'periodic-due'))
async def test_native_dispatch_waits_for_auth_and_committed_following(tmp_path, monkeypatch, startup):
    binding, _, _ = make(tmp_path, monkeypatch, uid=A, with_store=False)
    binding.auth_stop_required = True
    auth_entered, release_auth = Event(), Event()
    following_entered, release_following = Event(), Event()
    dispatched = Event()
    attempted = []

    from pixivvault_nicegui.native_auth_result import NativeAuthResult

    def verify_auth(_file, _stop):
        auth_entered.set()
        assert release_auth.wait(5)
        return NativeAuthResult('verified'), (A, 20)

    class FollowReader(Reader):
        def get_following_users_detailed(self, account, visibility):
            following_entered.set()
            assert release_following.wait(5)
            return super().get_following_users_detailed(account, visibility)

        def close(self):
            pass

    class LiveReader(Reader):
        def verify_identity(self):
            attempted.append('identity')
            self._verified_identity = None
            if not release_following.is_set():
                raise ManagedNetworkGateBusy('synthetic competing request')
            self._verified_identity = A
            return A

        def close(self):
            pass

    def dispatch(services, ledger, slot, cancellation, *, authors):
        attempted.append((authors, ledger._bound_account()))
        dispatched.set()

    def start_now(runner):
        started = start(runner)
        runner.settings_changed()
        return started

    root = tmp_path / 'managed'
    writer = ManagedPixivDownloads(root, Reader(A), resolve_record=lambda _: None)
    writer.close()
    following_root = tmp_path / 'following'
    following = FollowingSyncStore(following_root)
    try:
        following.sync(Reader(A))
        following.set_favorite(A, X, True)
        age(following, A, days=2)
    finally:
        following.close()
    with_seed = ManagedScheduler(root)
    try:
        state = with_seed.snapshot()
        # The regular slot is already due when the synthetic app starts.
        now = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with_seed.configure(interval_hours=0 if startup else 6, startup_check=startup,
                            expected_revision=state.revision, now=now, account_id=None)
    finally:
        with_seed.close()
    start = ManagedScheduleRunner.start
    monkeypatch.setattr(ManagedScheduleRunner, 'start', start_now)
    monkeypatch.setattr(ApplicationServices, 'dispatch_author_check', dispatch)
    monkeypatch.setattr(binding, '_verify_auth_result', verify_auth)
    binding.followup.reader_factory = lambda _file: FollowReader(A)
    app = SimpleNamespace(state=SimpleNamespace(), on_startup=lambda _: None, on_shutdown=lambda _: None,
                          get=lambda *_, **__: lambda func: func)
    monkeypatch.setattr('nicegui.app', app)
    monkeypatch.setattr(ui, 'page', lambda _: lambda func: func)
    monkeypatch.setattr('pixivvault_nicegui.media_routes.register_media_routes', lambda *_: None)
    monkeypatch.setattr(bootstrap, '_registered', False)
    task = None
    try:
        bootstrap.register_pages(native_profile=binding, live_client=LiveReader(None),
                                 pixiv_download_root=root, following_sync_root=following_root)
        services = getattr(app.state, _APP_KEY)
        runner = services.managed_schedule_runner
        task = binding.start_startup_auth_check()
        assert await asyncio.to_thread(auth_entered.wait, 5)
        assert runner._thread is None
        assert services.managed_scheduler.snapshot().last_status == 'never'
        release_auth.set()
        assert await asyncio.to_thread(following_entered.wait, 5)
        assert binding.auth_status.status.ok
        assert not attempted and runner._thread is None
        release_following.set()
        await task
        assert await asyncio.to_thread(dispatched.wait, 5)
        await until(lambda: services.managed_scheduler.snapshot().last_status == 'queued')
        assert attempted == ['identity', ((X,), A)]
        messages = [entry.message for entry in services.shared_log.snapshot()]
        assert messages.index('起動時自動同期完了 (2人の作者)') < messages.index(
            '☆ お気に入り自動チェックを開始します (1人の作者)')
    finally:
        release_auth.set()
        release_following.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if hasattr(app.state, _APP_KEY):
            getattr(app.state, _APP_KEY).close()
        binding.close()


def test_dispatch_error_logs_only_exception_type_and_safe_reason(tmp_path, monkeypatch, caplog):
    private = 'synthetic-marker fixture-value 998877665544'

    def fail():
        raise RuntimeError(private)

    monkeypatch.setattr(ManagedScheduleRunner, 'start', lambda _: True)
    service = SimpleNamespace(_close_lock=Lock(), _managed_scheduler_lock=Lock(), _scheduler_dispatch_lock=Lock(),
                              closing=False, managed_downloads=SimpleNamespace(root=tmp_path), live_client=object(),
                              managed_scheduler=None, reconcile_managed_scheduler=lambda: None,
                              scheduler_favourite_authors=fail, shared_log=QueueLog())
    ledger = ApplicationServices.enable_managed_scheduler(service)
    try:
        with pytest.raises(RuntimeError):
            service.managed_schedule_runner.dispatch('synthetic-slot', Event())
        entry, = service.shared_log.snapshot()
        assert entry.level == 'error'
        assert 'RuntimeError' in entry.message and '☆一覧' in entry.message
        assert entry.message in caplog.text
        assert private not in entry.message and private not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        ledger.close()


@pytest.mark.parametrize('outcome', ('success', 'stopped', 'still-busy'))
def test_auto_check_retries_only_transient_preflight(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(ManagedScheduleRunner, 'start', lambda _: True)
    with resources(tmp_path) as (root, store, old_ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        old_ledger.close()
        services.managed_scheduler = None
        services.managed_downloads = SimpleNamespace(root=root)
        services._close_lock, services._managed_scheduler_lock, services._scheduler_dispatch_lock = (
            Lock(), Lock(), Lock())
        services.reconcile_managed_scheduler = lambda: None
        services.shared_log = QueueLog()
        attempts, accepted = [], []

        def gate():
            attempts.append('admission')
            if len(attempts) == 1 or outcome == 'still-busy':
                raise ManagedNetworkGateBusy('synthetic busy')
            if outcome == 'stopped':
                raise ManagedNetworkStopped(429)

        services.managed_network_stop = SimpleNamespace(require_allowed=gate)
        services.dispatch_author_check = lambda *_, authors: accepted.append(authors)
        cancellation = Event()
        monkeypatch.setattr(cancellation, 'wait', lambda _: False)
        ledger = ApplicationServices.enable_managed_scheduler(services)
        try:
            if outcome == 'success':
                services.managed_schedule_runner.dispatch('synthetic-slot', cancellation)
                assert accepted == [(X,)]
            else:
                failure = ManagedNetworkStopped if outcome == 'stopped' else ManagedNetworkGateBusy
                with pytest.raises(failure):
                    services.managed_schedule_runner.dispatch('synthetic-slot', cancellation)
                assert accepted == [] and ledger._bound_account() is None
            assert attempts == ['admission', 'admission']
        finally:
            ledger.close()


@pytest.mark.asyncio
async def test_scheduler_view_distinguishes_unverified_reader_and_other_history(tmp_path):
    with resources(tmp_path) as (_, store, ledger, reader, services):
        store.sync(reader)
        store.set_favorite(A, X, True)
        services.scheduler_favourite_authors(ledger=ledger)
        reader._verified_identity = None
        services.reconcile_managed_scheduler = lambda: None
        client = Client(ui.page('/_scheduler_startup_status'))
        try:
            with client:
                view = SchedulerView(services)
                assert '未起動' not in view.status.text
                await view.refresh()
                assert 'ログイン' in view.star_notice.text and 'フォロー' in view.star_notice.text
                assert not view.authors.visible and not view.history_label.visible and not view.run_button.enabled
                reader._verified_identity = A
                await view.refresh()
                assert '紐づけアカウント ID' in view.account_label.text
                assert view.authors.value == X and view.run_button.enabled
                assert view.interval.enabled and not view.open_button.enabled
                switch(store, reader)
                assert reader._verified_identity == B
                await view.refresh()
                assert '別のアカウント' in view.star_notice.text
                assert not view.history_label.visible and not view.run_button.enabled
                assert ledger._bound_account() == A
                view.close()
        finally:
            client.delete()
