import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopGate
from pixivvault_nicegui.managed_scheduler import ScheduleConfirmationUnavailable
from pixivvault_nicegui.pixiv_actions import PixivActions, _Observation
from pixivvault_nicegui.pixiv_read_client import ClientClosed
from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteHTTPResponse, WriteIntent
from pixivvault_nicegui.request_policy import request_checkpoint
from pixivvault_nicegui.services import ApplicationServices, _APP_KEY
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from test_ag_z04_20261119 import _home, reader as expiry_reader, setup as expiry_setup
from test_scheduler_startup_r2_20261121 import LiveReader, native
from test_w_account_separation_20261119 import A, Reader


def test_batch_shutdown_requires_confirmed_join_before_closing_borrowed_dependencies():
    services = ApplicationServices(1)
    services.batch_runs = SimpleNamespace(_shutdown=Mock(side_effect=(False, True)))
    services.live_client = SimpleNamespace(close=Mock())
    try:
        with pytest.raises(RuntimeError, match='batch run shutdown unverified'):
            services.close()
        services.live_client.close.assert_not_called()
        assert services.closing and not services.closed
    finally:
        services.close()
    services.live_client.close.assert_called_once()
    assert services.closed


@pytest.mark.asyncio
async def test_close_finishes_dispatched_action_before_revoking_reader(tmp_path, monkeypatch):
    account = '777'  # same synthetic account as the expiry fixture
    expiry_root = tmp_path / 'expiry'
    expiry_root.mkdir()
    _, cookie, profile, store, coordinator = expiry_setup(expiry_root)
    reader, _ = expiry_reader(cookie, coordinator, [])
    reader._transport = lambda *args: _home()
    dispatched, respond, confirm_entered, stop_actions, revoked = (Event() for _ in range(5))

    def post(*_):
        dispatched.set()
        assert respond.wait(8)
        return WriteHTTPResponse(200, b'{"error":false,"body":{}}')

    actions = PixivActions(reader, writer=PixivWriteClient(reader, transport=post))
    monkeypatch.setattr(actions, '_observe', lambda *_: _Observation(respond.is_set(), 0))
    original_stop, original_close = actions.close, reader.close

    def close_actions():
        stop_actions.set()
        return original_stop()

    def close_reader():
        revoked.set()
        original_close()

    monkeypatch.setattr(actions, 'close', close_actions)
    monkeypatch.setattr(reader, 'close', close_reader)
    with native(tmp_path, monkeypatch, account=account) as (binding, services, _):
        services.following_sync.sync(Reader(account))
        await binding.refresh_auth_status(startup=True)
        services.live_client, services.pixiv_actions = reader, actions
        action = asyncio.create_task(actions.execute(WriteIntent('shutdown-action', account, '91', 'follow')))
        confirmation = closing = None
        try:
            assert await asyncio.to_thread(dispatched.wait, 4)

            def confirm():
                confirm_entered.set()
                with coordinator.lock:
                    request_checkpoint()
                    return account

            monkeypatch.setattr(reader, 'require_identity', confirm)
            confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
            assert await asyncio.to_thread(confirm_entered.wait, 4)
            closing = asyncio.create_task(asyncio.to_thread(services.close))
            assert await asyncio.to_thread(stop_actions.wait, 2)
            assert not revoked.is_set() and not closing.done()
            respond.set()
            assert (await action).status == 'confirmed'
            with pytest.raises(ScheduleConfirmationUnavailable) as cancelled:
                await confirmation
            assert cancelled.value.reason == 'closing'
            await closing
            assert revoked.is_set() and services.closed
        finally:
            respond.set()
            await asyncio.gather(*(t for t in (action, confirmation, closing) if t is not None),
                                 return_exceptions=True)
            original_stop()
            original_close()
            coordinator.close()
            store.close()
            profile.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('worker', ('novel_jobs', 'ugoira'))
async def test_close_stops_download_owner_before_waiting_on_confirmation(tmp_path, monkeypatch, worker):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        coordinator = CookieExpiryCoordinator(binding.store, binding._open_cookie_store)
        download_active, stop_download, confirm_entered = (Event() for _ in range(3))
        download_finished, reader_closed = Event(), Event()

        def download():
            with coordinator.lock:
                download_active.set()
                assert stop_download.wait(8)
            download_finished.set()

        def close_download(*, timeout):
            stop_download.set()
            assert download_finished.wait(2)

        def confirm():
            confirm_entered.set()
            with coordinator.lock:
                request_checkpoint()
                return A

        def close_reader():
            assert download_finished.is_set(), 'shared reader revoked before the download worker stopped'
            reader_closed.set()

        monkeypatch.setattr(services, worker, SimpleNamespace(close=close_download))
        monkeypatch.setattr(reader, 'require_identity', confirm)
        monkeypatch.setattr(reader, 'close', close_reader)
        download_task = asyncio.create_task(asyncio.to_thread(download))
        confirmation = None
        try:
            assert await asyncio.to_thread(download_active.wait, 4)
            confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
            assert await asyncio.to_thread(confirm_entered.wait, 4)
            await asyncio.to_thread(services.close)
            assert reader_closed.is_set() and services.closed
            with pytest.raises(ScheduleConfirmationUnavailable) as cancelled:
                await confirmation
            assert cancelled.value.reason == 'closing'
        finally:
            stop_download.set()
            await asyncio.gather(*(t for t in (download_task, confirmation) if t is not None),
                                 return_exceptions=True)
            coordinator.close()


@pytest.mark.asyncio
async def test_asgi_handler_closes_profile_after_confirmation_timeout_with_coordinator_busy(tmp_path, monkeypatch):
    hooks = []
    app = SimpleNamespace(state=SimpleNamespace(), on_startup=lambda _: None, on_shutdown=hooks.append,
                          get=lambda *_, **__: lambda func: func)
    monkeypatch.setattr('nicegui.app', app)
    monkeypatch.setattr(ui, 'page', lambda _: lambda func: func)
    monkeypatch.setattr('pixivvault_nicegui.media_routes.register_media_routes', lambda *_: None)
    monkeypatch.setattr(bootstrap, '_registered', False)
    # Separate isolated services for the actual registered shutdown hook.
    with native(tmp_path, monkeypatch) as (binding, _, __):
        reader = LiveReader(A)
        bootstrap.register_pages(native_profile=binding, live_client=reader,
                                 pixiv_download_root=tmp_path / 'hook-managed',
                                 following_sync_root=tmp_path / 'hook-following')
        services = getattr(app.state, _APP_KEY)
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        coordinator = CookieExpiryCoordinator(binding.store, binding._open_cookie_store)
        reader._closed, reader._expiry_coordinator, reader._cookie_user_id = False, None, None
        coordinator.register(reader)
        monkeypatch.setattr(reader, 'close', lambda: setattr(reader, '_closed', True))
        binding._cookie_expiry_coordinator = coordinator
        gate = ManagedUserNetworkStopGate(tmp_path / 'stop' / 'shared.sqlite3')
        services.managed_network_stop = SimpleNamespace(require_allowed=gate.snapshot)
        entered, released = Event(), Event()
        resources = []
        services.tray = SimpleNamespace(close=lambda: resources.append('tray'))
        following_close = Mock(wraps=services.following_sync.close)
        scheduler_close = Mock(wraps=services.managed_scheduler.close)
        monkeypatch.setattr(services.following_sync, 'close', following_close)
        monkeypatch.setattr(services.managed_scheduler, 'close', scheduler_close)
        close_store = binding.store.close

        def close_profile_store():
            close_store()
            resources.append('profile')

        def confirm():
            with coordinator.lock, gate.request():
                entered.set()
                assert released.wait(8)
                request_checkpoint()
                raise ClientClosed()

        monkeypatch.setattr(binding.store, 'close', close_profile_store)
        monkeypatch.setattr(reader, 'require_identity', confirm)
        monkeypatch.setattr('pixivvault_nicegui.services._SCHEDULER_CONFIRMATION_DRAIN_SECONDS', 0.1)
        confirmation = asyncio.create_task(asyncio.to_thread(services.confirm_scheduler_account))
        shutdown = None
        try:
            assert await asyncio.to_thread(entered.wait, 4)
            shutdown = asyncio.create_task(hooks[0]())
            done, _ = await asyncio.wait((shutdown,), timeout=2)
            assert done, 'profile close waited again on the confirmation worker'
            with pytest.raises(RuntimeError, match='scheduler confirmation shutdown unverified'):
                await shutdown
            assert services.closed and coordinator._closed
            assert resources == ['tray', 'profile'] and not confirmation.done()
            following_close.assert_called_once()
            scheduler_close.assert_called_once()
            assert gate.inflight_path.is_file()
        finally:
            released.set()
            await asyncio.gather(*(t for t in (confirmation, shutdown) if t is not None),
                                 return_exceptions=True)
            await asyncio.to_thread(services.close)
        assert not gate.inflight_path.exists() and not gate.snapshot().blocked


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ('own-refresh', 'stale-click', 'busy-exception'))
async def test_confirmation_view_follows_current_owner_and_result(tmp_path, monkeypatch, phase):
    with native(tmp_path, monkeypatch) as (binding, services, reader):
        services.following_sync.sync(Reader(A))
        await binding.refresh_auth_status(startup=True)
        entered, finish, confirmed = Event(), Event(), Event()
        original_confirm = services.confirm_scheduler_account

        def require_identity():
            entered.set()
            assert finish.wait(8)
            return reader.verify_identity()

        def confirm():
            owner = not entered.is_set()
            try:
                return original_confirm()
            except ScheduleConfirmationUnavailable as error:
                assert error.reason == 'busy'
                finish.set()
                assert confirmed.wait(4)
                raise
            finally:
                if owner:
                    confirmed.set()

        monkeypatch.setattr(reader, 'require_identity', require_identity)
        monkeypatch.setattr(services, 'confirm_scheduler_account', confirm)
        clients = [Client(ui.page('/_scheduler_r5_first')), Client(ui.page('/_scheduler_r5_second'))]
        views, first = [], None
        try:
            for client in clients:
                with client:
                    view = SchedulerView(services)
                    await view.refresh()
                    views.append(view)
            active, other = views
            first = asyncio.create_task(active.confirm_account())
            assert await asyncio.to_thread(entered.wait, 4)
            if phase == 'own-refresh':
                await active.refresh()  # same path as the five-second timer
                await other.refresh()
                assert '別の画面' not in active.status.text
                assert '定期チェックの認証を確認中' in active.status.text
                assert '別の画面' in other.status.text
                assert not active.confirm_account_button.enabled and not other.confirm_account_button.enabled
                return

            blocker = other._confirmation_blocker

            def race():
                monkeypatch.setattr(other, '_confirmation_blocker', blocker)
                if phase == 'busy-exception':
                    return None  # admitted just before the other tab took the lock
                value = blocker()
                assert value == 'busy'
                finish.set()
                assert confirmed.wait(4)
                return value

            monkeypatch.setattr(other, '_confirmation_blocker', race)
            await other.confirm_account()
            assert reader._verified_identity == A
            assert other.authors.visible and other.run_button.enabled
            assert not other.confirm_account_button.visible and not other.action_notice.text
        finally:
            finish.set()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
            for view in views:
                view.close()
            for client in clients:
                client.delete()
