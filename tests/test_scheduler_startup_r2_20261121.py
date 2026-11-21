import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.bootstrap import _wire_followup
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy, ManagedNetworkStopped
from pixivvault_nicegui.managed_scheduler import ScheduleError
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.following_sync_view import FollowingSyncView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ac_library_search_20261119 import until
from test_cookie_account_followup_20261117 import FakeAuthReader, FakeFollowReader, make
from test_cookie_auth_status_20261117 import cookie_file
from test_w_account_separation_20261119 import A, B, X, Reader


class LiveReader(Reader):
    def __init__(self, account):
        super().__init__(None)
        self.account = account
        self.attempts = 0
        self.busy = False

    def verify_identity(self):
        self.attempts += 1
        if self.busy:
            raise ManagedNetworkGateBusy('synthetic busy')
        self._verified_identity = self.account
        return self.account

    require_identity = verify_identity

    def close(self):
        pass


@contextmanager
def native(tmp_path, monkeypatch, *, account=A):
    binding, _, _ = make(tmp_path, monkeypatch, uid=account, with_store=False)
    binding.launch_snapshot = binding.store.snapshot()
    reader = LiveReader(account)
    services = ApplicationServices(live_client=reader, pixiv_download_root=tmp_path / 'managed',
                                   following_sync_root=tmp_path / 'following', scheduler_wait_for_auth=True)
    services.native_profile = binding
    _wire_followup(binding, services)
    try:
        yield binding, services, reader
    finally:
        services.close()
        binding.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ('startup-auth', 'first-sync', 'after-account-switch'))
async def test_manual_following_recovers_timer_after_startup_failure(tmp_path, monkeypatch, failure):
    with native(tmp_path, monkeypatch, account=B if failure == 'after-account-switch' else A) as (binding, services, reader):
        if failure == 'after-account-switch':
            services.following_sync.sync(Reader(A))
            await binding.refresh_auth_status(startup=True)
            assert binding.followup.pending is not None

        class FailedFollowing(FakeFollowReader):
            def get_following_users_detailed(self, *args):
                raise ManagedNetworkStopped(429)

        binding.followup.reader_factory = lambda _: FailedFollowing(reader.account)
        FakeAuthReader.invalid = failure == 'startup-auth'
        if failure == 'after-account-switch':
            await binding.followup.confirm_account_switch()
        else:
            await binding.refresh_auth_status(startup=True)
        assert services._scheduler_waiting_for_auth
        assert services.following_sync.last_synced_account() is None
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        client = Client(ui.page('/_r2_following'))
        try:
            with client:
                view = FollowingSyncView(services)
                view.build()
                assert await view.sync_now()
                thread = services.managed_schedule_runner._thread
                assert thread is not None and not services._scheduler_waiting_for_auth
                assert await view.sync_now()
                assert services.managed_schedule_runner._thread is thread
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('existing_owner', (False, True), ids=('first-sync', 'fresh-following'))
async def test_settings_reauth_recovers_after_startup_auth_failed(tmp_path, monkeypatch, existing_owner):
    with native(tmp_path, monkeypatch) as (binding, services, _):
        if existing_owner:
            services.following_sync.sync(Reader(A))
        FakeAuthReader.invalid = True
        await binding.refresh_auth_status(startup=True)
        assert services._scheduler_waiting_for_auth
        FakeAuthReader.invalid = False
        binding.followup.reader_factory = lambda _: FakeFollowReader(A)
        draft_root = tmp_path / 'draft'
        draft_root.mkdir()
        draft = cookie_file(draft_root, 20)
        client = Client(ui.page('/_r2_reauth'))
        try:
            with client:
                view = binding.create_view()
                view.build()
                view.draft_cookie = draft
                assert await view.check_auth()
                assert services._scheduler_waiting_for_auth
                view.draft_cookie = binding.launch_snapshot.cookie_file
                assert await view.check_auth()
        finally:
            client.delete()
        await until(lambda: not services._scheduler_waiting_for_auth)
        assert services.following_sync.last_synced_account() == A


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ('saved', 'browser'))
async def test_replaced_cookie_holds_timer_until_relaunch(tmp_path, monkeypatch, source):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        alternative = tmp_path / 'replacement'
        alternative.mkdir()
        new_file = cookie_file(alternative, 20)

        def save_cookie():
            state = binding.store.snapshot()
            binding.store.update(cookie_file=new_file, expected_revision=state.revision)

        if source == 'saved':
            save_cookie()
            binding.request_cookie_check(imported=True)
            await asyncio.gather(*binding._background_tasks)
        else:
            async def adopt():
                save_cookie()
                return 'adopted'
            monkeypatch.setattr(binding, '_extraction_wanted', lambda *_: True)
            monkeypatch.setattr(binding, 'adopt_browser_cookies', adopt)
            await binding.refresh_auth_status(startup=True)
        assert binding.cookie_replaced_since_launch()
        assert services._scheduler_waiting_for_auth and services.managed_schedule_runner._thread is None
        assert reader.attempts == 0
        reader._verified_identity = A  # stale proof from the launch reader
        client = Client(ui.page('/_r2_restart_required'))
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                assert '再起動' in view.status.text
                assert not view.confirm_account_button.visible and not view.run_button.enabled
                assert not view.authors.visible and not view.history_label.visible
                view.close()
        finally:
            client.delete()

        from pixivvault_nicegui.native_startup import NativeProfileBinding
        services.close()
        relaunched = NativeProfileBinding(binding.store, launch_snapshot=binding.store.snapshot())
        fresh = ApplicationServices(live_client=LiveReader(A), pixiv_download_root=tmp_path / 'managed',
                                    following_sync_root=tmp_path / 'following', scheduler_wait_for_auth=True)
        try:
            fresh.native_profile = relaunched
            _wire_followup(relaunched, fresh)
            await relaunched.refresh_auth_status(startup=True)
            assert not fresh._scheduler_waiting_for_auth and fresh.managed_schedule_runner._thread is not None
        finally:
            fresh.close()
            relaunched.close()


@pytest.mark.asyncio
async def test_running_timer_preserves_due_slot_after_cookie_replacement(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        runner = services.managed_schedule_runner
        assert runner._thread is not None
        replacement_root = tmp_path / 'replacement'
        replacement_root.mkdir()
        state = binding.store.snapshot()
        binding.store.update(cookie_file=cookie_file(replacement_root, 20), expected_revision=state.revision)
        claims = []
        monkeypatch.setattr(runner.ledger, '_claim_due', lambda *_, **__: claims.append(True))
        assert runner._claim_reserved(datetime.now(timezone.utc), startup=True) is None
        # A slot may already have been reserved when the settings commit lands.
        with pytest.raises(ScheduleError):
            runner.dispatch('fixture-slot', Event())
        assert all(entry.level != 'error' for entry in services.shared_log.snapshot())
        assert not claims and reader.attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('accept', (False, True), ids=('cancel', 'confirm'))
async def test_account_switch_only_releases_after_confirmed_sync(tmp_path, monkeypatch, accept):
    with native(tmp_path, monkeypatch, account=B) as (binding, services, _):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        assert services._scheduler_waiting_for_auth and binding.followup.pending is not None
        if accept:
            await binding.followup.confirm_account_switch()
            assert not services._scheduler_waiting_for_auth
        else:
            binding.followup.cancel_account_switch()
            await binding.refresh_auth_status(imported=True)
            assert services._scheduler_waiting_for_auth and services.managed_schedule_runner._thread is None
            assert services.following_sync.last_synced_account() == A


@pytest.mark.asyncio
@pytest.mark.parametrize('close_first', (False, True), ids=('release-first', 'close-first'))
async def test_release_and_close_leave_no_timer(tmp_path, monkeypatch, close_first):
    with native(tmp_path, monkeypatch) as (binding, services, _):
        store = services.following_sync
        store.sync(Reader(A))

        def redundant_sync(_):
            pytest.fail('a following commit younger than 24 hours must skip sync')

        monkeypatch.setattr(binding.followup, 'reader_factory', redundant_sync)
        runner = services.managed_schedule_runner
        if close_first:
            entered, release = Event(), Event()
            resources = services._close_resources

            def close_resources():
                entered.set()
                assert release.wait(5)
                resources()

            monkeypatch.setattr(services, '_close_resources', close_resources)
            close_task = asyncio.create_task(asyncio.to_thread(services.close))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                await binding.followup.after_confirmed(A)
                assert runner._thread is None
            finally:
                release.set()
                await close_task
        else:
            await binding.refresh_auth_status(startup=True)
            assert runner._thread is not None
            await asyncio.to_thread(services.close)
            assert not runner._thread.is_alive()
        assert services.closed and runner.stop_event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ('match', 'mismatch', 'stopped'))
async def test_scheduler_view_explicitly_confirms_the_live_reader(tmp_path, monkeypatch, outcome):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        store = services.following_sync
        store.sync(Reader(A))
        store.set_favorite(A, X, True)
        client = Client(ui.page('/_r2_scheduler'))
        try:
            with client:
                view = SchedulerView(services)
                await view.refresh()
                assert '認証・フォロー情報の確認待ち' in view.status.text
                await binding.refresh_auth_status(startup=True)
                assert binding.auth_status.status.ok and reader._verified_identity is None
                await view.refresh()
                assert not view.run_button.enabled and view.authors.value == '' and reader.attempts == 0
                assert view.confirm_account_button.enabled
                if outcome == 'mismatch':
                    reader.account = B
                if outcome == 'stopped':
                    from types import SimpleNamespace

                    def blocked():
                        raise ManagedNetworkStopped(429)

                    services.managed_network_stop = SimpleNamespace(require_allowed=blocked)
                await view.confirm_account()
                if outcome == 'match':
                    assert view.authors.value == X and view.run_button.enabled and view.save_button.enabled
                    assert '（未紐づけ）' in view.account_label.text
                else:
                    assert not view.authors.visible and not view.history_label.visible and not view.run_button.enabled
                    assert services.managed_scheduler._bound_account() is None
                assert reader.attempts == (0 if outcome == 'stopped' else 1)
                view.close()
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_configured_view_disables_open_even_when_first_refresh_fails(tmp_path, monkeypatch):
    with native(tmp_path, monkeypatch) as (_, services, _):
        def unavailable():
            raise RuntimeError('synthetic unavailable')

        monkeypatch.setattr(services, 'reconcile_managed_scheduler', unavailable)
        client = Client(ui.page('/_r2_scheduler_failed_refresh'))
        try:
            with client:
                view = SchedulerView(services)
                assert not view.open_button.enabled
                await view.refresh()
                assert not view.open_button.enabled
                assert '状態を取得できませんでした' in view.status.text
                view.close()
        finally:
            client.delete()


@pytest.mark.parametrize('after_wait', ('retry', 'cancel', 'close'))
def test_verify_identity_busy_wait_releases_dispatch_lock(tmp_path, monkeypatch, after_wait):
    with native(tmp_path, monkeypatch) as (_, services, reader):
        services.following_sync.sync(Reader(A))
        reader.busy = True
        cancellation = Event()
        dispatched = []
        monkeypatch.setattr(services, 'dispatch_author_check', lambda *_, **__: dispatched.append(True))

        def wait(timeout):
            assert timeout == 3.0
            assert services._scheduler_dispatch_lock.acquire(blocking=False)
            services._scheduler_dispatch_lock.release()
            reader.busy = False
            if after_wait == 'cancel':
                cancellation.set()
            elif after_wait == 'close':
                services.closing = True
            return cancellation.is_set()

        monkeypatch.setattr(cancellation, 'wait', wait)
        runner = services.managed_schedule_runner
        if after_wait != 'retry':
            with pytest.raises(ManagedNetworkGateBusy):
                runner.dispatch('fixture-slot', cancellation)
            assert reader.attempts == 1 and not dispatched
        else:
            runner.dispatch('fixture-slot', cancellation)
            assert reader.attempts == 2 and dispatched == [True]


def test_busy_during_admission_reports_phase_without_retry(tmp_path, monkeypatch, caplog):
    with native(tmp_path, monkeypatch) as (_, services, reader):
        services.following_sync.sync(Reader(A))

        def busy(*_, **__):
            raise ManagedNetworkGateBusy('synthetic private fixture')

        monkeypatch.setattr(services, 'dispatch_author_check', busy)
        with pytest.raises(ManagedNetworkGateBusy):
            services.managed_schedule_runner.dispatch('fixture-slot', Event())
        message = services.shared_log.snapshot()[-1].message
        assert 'チェックの受付・実行' in message and 'ManagedNetworkGateBusy' in message
        assert message in caplog.text and 'synthetic private fixture' not in caplog.text
        assert reader.attempts == 1
