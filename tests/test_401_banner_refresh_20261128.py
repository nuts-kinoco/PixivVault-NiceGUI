"""The shared STOP banner follows selected authentication without network reads."""
import pytest
from contextlib import nullcontext
from threading import Lock
from types import SimpleNamespace
from nicegui import Client, ui

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
from test_h2_401_recovery import recovery, HOME, IDENTITY  # noqa: F401
from test_b3_recovery_guidance_20261116 import fingerprint
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


@pytest.mark.asyncio
async def test_startup_recovery_page_keeps_shared_view(recovery, monkeypatch):
    from nicegui import app
    from nicegui.page import page as Page
    from pixivvault_nicegui import native_startup
    from pixivvault_nicegui.ui import shared_get_recovery_view

    _, _, shared, _, _, _, _, _ = recovery
    shared.gate.trip(401)
    pages, views = {}, []
    class Capture(SharedGetRecoveryView):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            views.append(self)
    monkeypatch.setattr(shared_get_recovery_view, 'SharedGetRecoveryView', Capture)
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *args, **kwargs: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    binding = SimpleNamespace(shared_network_stop_scope=shared, close=lambda: None,
        create_view=lambda **kwargs: SimpleNamespace(build=lambda: None, close=lambda: None))
    native_startup.register_profile_recovery(binding, stop_get_uncertain=True)
    client = Client(Page('/_startup_banner_401'))
    try:
        with client:
            pages['/']()
            view, = views
            await view.inspect_summary()
            assert view.summary.visible and '選択したCookieの認証を確認' in view.summary.text
        client.delete()
        assert view.closed and view._initial_check._is_canceled
    finally:
        if not client.is_deleted:
            client.delete()


@pytest.mark.asyncio
async def test_shell_connects_auth_holder_and_closes_view(recovery, monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell

    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    services = ApplicationServices(1)
    services.native_profile = binding
    services.managed_network_stop = shared
    client = Client(ui.page('/_banner_401_shell'))
    try:
        with client:
            shell = DemoShell(services)
            view = shell.get_recovery_view
            await view.inspect_summary()
            assert view.summary.visible
            assert (await binding.check_selected_authentication(chosen)).ok
            timer = view._refresh_timer
            assert timer is not None
            async def connected():
                return True
            monkeypatch.setattr(timer, '_can_start', connected)
            monkeypatch.setattr(timer, '_should_stop', lambda: False)
            timer.interval = 0
            await timer._run_once()
            assert not view.summary.visible
            shell.close()
            assert not binding.auth_status._listeners
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['recovered', 'recovered_restart', 'failed'])
async def test_auth_notification_refreshes_banner(recovery, monkeypatch, outcome):
    binding, local, shared, _, chosen, sends, responses, _ = recovery
    shared.gate.trip(401)
    if outcome == 'recovered':
        binding._cookie_expiry_coordinator = SimpleNamespace(
            lock=Lock(), prepare_verified_recovery=lambda *args: nullcontext(lambda: None),
            close=lambda: None)
    if outcome == 'failed':
        responses[IDENTITY] = HTTPStatusRejected(401)
    client = Client(ui.page('/_banner_401_notification'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.auth_status)
            await view.inspect_summary()
            assert view.summary.visible
            assert '選択したCookieの認証を確認' in view.summary.text
            result = await binding.check_selected_authentication(chosen)
            assert result.code == outcome or (outcome == 'failed' and not result.ok)
            before = list(sends)
            files_before = fingerprint(shared.root)
            timer = view._refresh_timer
            assert timer is not None
            await timer.callback()
            timer.cancel()
            assert view.summary.visible is (outcome == 'failed')
            assert sends == before == [HOME, IDENTITY]
            assert fingerprint(shared.root) == files_before
            view.close()
            assert not binding.auth_status._listeners
            assert view._refresh_timer is None
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_busy_preserves_banner_and_retries_then_close(recovery, monkeypatch):
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    client = Client(ui.page('/_banner_401_busy'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.auth_status)
            await view.inspect_summary()
            previous = view.summary.text
            assert (await binding.check_selected_authentication(chosen)).ok
            preview = shared.preview_uncertain_get_recovery
            monkeypatch.setattr(shared, 'preview_uncertain_get_recovery',
                                lambda: (_ for _ in ()).throw(ManagedNetworkGateBusy()))
            timer = view._refresh_timer
            await timer.callback()
            timer.cancel()
            assert view.summary.visible and view.summary.text == previous
            assert view._refresh_timer.active
            monkeypatch.setattr(shared, 'preview_uncertain_get_recovery', preview)
            timer = view._refresh_timer
            await timer.callback()
            timer.cancel()
            assert not view.summary.visible
            binding.auth_status.publish(binding.auth_status.status)
            pending = view._refresh_timer
            view.close()
            assert pending._is_canceled and view._initial_check._is_canceled
            assert view._refresh_timer is None and not binding.auth_status._listeners
    finally:
        client.delete()
