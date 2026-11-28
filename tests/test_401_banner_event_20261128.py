import asyncio
from contextlib import nullcontext
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
from test_h2_401_recovery import recovery, IDENTITY  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['recovered', 'recovered_restart', 'failed'])
async def test_selected_recovery_event_does_not_reinspect(recovery, monkeypatch, outcome):
    binding, _, shared, _, chosen, sends, responses, _ = recovery
    shared.gate.trip(401)
    if outcome == 'recovered':
        binding._cookie_expiry_coordinator = SimpleNamespace(
            lock=Lock(), prepare_verified_recovery=lambda *a: nullcontext(lambda: None), close=lambda: None)
    if outcome == 'failed':
        responses[IDENTITY] = HTTPStatusRejected(401)
    client = Client(ui.page('/_401_event'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.recovered_401, can_check_auth=True)
            await view.inspect_summary()
            assert view.summary.visible
            calls = []
            monkeypatch.setattr(shared, 'preview_uncertain_get_recovery', lambda: calls.append('unexpected'))
            binding.auth_status.publish(binding.auth_status.status)
            await asyncio.sleep(0)
            assert calls == [] and view.summary.visible
            result = await binding.check_selected_authentication(chosen, _publish=False)
            await asyncio.sleep(0)
            assert (result.code == outcome) if outcome != 'failed' else not result.ok
            assert view.summary.visible is (outcome == 'failed')
            assert calls == []
            view.close()
            assert not binding.recovered_401._listeners
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_stale_401_read_does_not_restore_banner(recovery, monkeypatch):
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    started, release = Event(), Event()
    client = Client(ui.page('/_401_event_race'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.recovered_401, can_check_auth=True)
            await view.inspect_summary()
            preview = shared.preview_uncertain_get_recovery
            def delayed():
                try:
                    preview()
                except ManagedNetworkStopped as error:
                    assert error.gate_revision is not None
                    started.set()
                    assert release.wait(10)
                    raise
            monkeypatch.setattr(shared, 'preview_uncertain_get_recovery', delayed)
            task = asyncio.create_task(view.inspect_summary())
            assert await asyncio.to_thread(started.wait, 5)
            result = await binding.check_selected_authentication(chosen, _publish=False)
            assert result.code == 'recovered_restart' and binding.recovered_401.revision is not None
            await asyncio.sleep(0)
            release.set()
            await task
            assert not view.summary.visible
            client.delete()
            assert not binding.recovered_401._listeners
    finally:
        release.set()
        if not client.is_deleted:
            client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 429])
async def test_clear_event_preserves_other_stop_and_client_cleanup(recovery, status):
    binding, _, shared, _, _, _, _, _ = recovery
    shared.gate.trip(status)
    client = Client(ui.page('/_401_event_403'))
    with client:
        view = SharedGetRecoveryView(shared, binding.recovered_401)
        await view.inspect_summary()
        before = view.summary.text
        binding.recovered_401.publish(2)
        await asyncio.sleep(0)
        assert view.summary.visible and view.summary.text == before
    client.delete()
    assert view.closed and not binding.recovered_401._listeners


@pytest.mark.asyncio
async def test_cookie_rejected_page_receives_clear_event(recovery, monkeypatch):
    from nicegui import app
    from nicegui.page import page as Page
    from pixivvault_nicegui import native_startup
    from pixivvault_nicegui.ui import shared_get_recovery_view
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    pages, views, allowed = {}, [], []
    class Capture(SharedGetRecoveryView):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            views.append(self)
    monkeypatch.setattr(shared_get_recovery_view, 'SharedGetRecoveryView', Capture)
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *a, **k: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    monkeypatch.setattr(binding, 'create_view', lambda **kw: (
        allowed.append(kw['allow_auth']) or SimpleNamespace(build=lambda: None, close=lambda: None)))
    native_startup.register_profile_recovery(binding, cookie_rejected=True)
    client = Client(Page('/_cookie_rejected_401'))
    try:
        with client:
            pages['/']()
            view, = views
            await view.inspect_summary()
            assert allowed == [True] and '選択したCookieの認証を確認' in view.summary.text
            assert (await binding.check_selected_authentication(chosen, _publish=False)).ok
            await asyncio.sleep(0)
            assert not view.summary.visible
    finally:
        client.delete()
    assert not binding.recovered_401._listeners


@pytest.mark.asyncio
async def test_recheck_view_receives_unpublished_recovery(recovery, monkeypatch):
    from pixivvault_nicegui.ui import shared_get_recovery_view, stop_recovery_view
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    views = []
    class Capture(SharedGetRecoveryView):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            views.append(self)
    monkeypatch.setattr(shared_get_recovery_view, 'SharedGetRecoveryView', Capture)
    from pixivvault_nicegui.native_auth_result import NativeAuthResult
    monkeypatch.setattr(binding, 'startup_stop_recheck_result', lambda: (shared, 'stop_rechecked'))
    original_check = binding.check_selected_authentication
    async def uncertain(*args, **kwargs):
        return NativeAuthResult('uncertain')
    monkeypatch.setattr(binding, 'check_selected_authentication', uncertain)
    profile_view = SimpleNamespace(verify_auth=None)
    profile_view.set_auth_verifier = lambda fn: setattr(profile_view, 'verify_auth', fn)
    client = Client(ui.page('/_recheck_401'))
    try:
        with client:
            stop_recovery_view.build_startup_stop_recheck(binding, ui.label(), profile_view)
            assert (await profile_view.verify_auth(chosen)).code == 'uncertain'
            view, = views
            await view.inspect_summary()
            assert '選択したCookieの認証を確認' in view.summary.text
            monkeypatch.setattr(binding, 'check_selected_authentication', original_check)
            assert (await profile_view.verify_auth(chosen)).ok
            await asyncio.sleep(0)
            assert not view.summary.visible
    finally:
        client.delete()
    assert not binding.recovered_401._listeners


@pytest.mark.asyncio
async def test_notification_failure_cannot_fail_recovery(recovery, monkeypatch):
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    monkeypatch.setattr(binding.recovered_401, 'publish', lambda *_: (_ for _ in ()).throw(RuntimeError()))
    assert (await binding.check_selected_authentication(chosen, _publish=False)).ok


@pytest.mark.asyncio
async def test_initial_timer_does_not_read_after_recovery(recovery, monkeypatch):
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    client = Client(ui.page('/_401_initial_after_clear'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.recovered_401)
            assert (await binding.check_selected_authentication(chosen, _publish=False)).ok
            calls = []
            monkeypatch.setattr(shared, 'preview_uncertain_get_recovery', lambda: calls.append(True))
            await view._initial_check.callback()
            assert calls == [] and not view.summary.visible
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_late_clear_does_not_hide_a_new_401(recovery):
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    client = Client(ui.page('/_401_new_stop'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.recovered_401)
            result = await binding.check_selected_authentication(chosen, _publish=False)
            assert result.code == 'recovered_restart' and binding.recovered_401.revision is not None
            await asyncio.sleep(0)
            shared.gate.trip(401)
            await view.inspect_summary()
            view._clear_recovered_401(binding.recovered_401.generation, binding.recovered_401.revision)
            assert view.summary.visible and 'HTTP 401' in view.summary.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_new_stop_before_recovery_notification_is_preserved(recovery, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_auth_recovery as auth_recovery
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    finished, release = Event(), Event()
    original = auth_recovery.recover_draft_auth_401
    def delayed(*args):
        uid = original(*args)
        finished.set()
        assert release.wait(10)
        return uid
    monkeypatch.setattr(auth_recovery, 'recover_draft_auth_401', delayed)
    client = Client(ui.page('/_401_new_before_notification'))
    try:
        with client:
            view = SharedGetRecoveryView(shared, binding.recovered_401)
            await view.inspect_summary()
            task = asyncio.create_task(binding.check_selected_authentication(chosen, _publish=False))
            assert await asyncio.to_thread(finished.wait, 5)
            shared.gate.trip(401)
            await view.inspect_summary()
            before = view.summary.text
            release.set()
            assert (await task).ok
            await asyncio.sleep(0)
            assert view.summary.visible and view.summary.text == before
    finally:
        release.set()
        client.delete()


@pytest.mark.asyncio
async def test_recheck_removes_guidance_when_auth_becomes_unavailable(recovery, monkeypatch):
    from pixivvault_nicegui.native_auth_result import NativeAuthResult
    from pixivvault_nicegui.ui import shared_get_recovery_view, stop_recovery_view
    binding, _, shared, _, chosen, _, _, _ = recovery
    shared.gate.trip(401)
    views = []
    class Capture(SharedGetRecoveryView):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            views.append(self)
    monkeypatch.setattr(shared_get_recovery_view, 'SharedGetRecoveryView', Capture)
    monkeypatch.setattr(binding, 'startup_stop_recheck_result', lambda: (shared, 'stop_rechecked'))
    responses = iter(('uncertain', 'profile_changed'))
    async def check(*args, **kwargs):
        return NativeAuthResult(next(responses))
    monkeypatch.setattr(binding, 'check_selected_authentication', check)
    profile = SimpleNamespace(verify_auth=None)
    profile.set_auth_verifier = lambda fn: setattr(profile, 'verify_auth', fn)
    client = Client(ui.page('/_401_profile_changed'))
    try:
        with client:
            stop_recovery_view.build_startup_stop_recheck(binding, ui.label(), profile)
            verify = profile.verify_auth
            await verify(chosen)
            view, = views
            await view.inspect_summary()
            assert '選択したCookieの認証を確認' in view.summary.text
            await verify(chosen)
            assert profile.verify_auth is None and view.summary.visible
            assert '選択したCookieの認証を確認' not in view.summary.text
    finally:
        client.delete()
