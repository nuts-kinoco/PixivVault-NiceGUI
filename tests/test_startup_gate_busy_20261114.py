from types import SimpleNamespace

from nicegui import Client, app, ui
from nicegui.page import page as Page

from pixivvault_nicegui import bootstrap, native_startup
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
from pixivvault_nicegui.native_profile import NativeProfileStore
from test_stage5_r1_native_first_draft_shared import _stub_native, _generated_shared_owner


def test_busy_shared_gate_uses_offline_retry_not_migration(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared

    def busy():
        raise ManagedNetworkGateBusy('generated contention')

    monkeypatch.setattr(shared, 'open_existing_windows_user_stop', busy)
    calls, recovered, registered = _stub_native(monkeypatch)
    root = tmp_path / 'generated-profile'
    store = NativeProfileStore(root)
    store.close()
    assert bootstrap.main(['--native', '--profile-root', str(root)]) == 0
    assert len(calls) == len(recovered) == 1 and registered == []
    assert recovered[0][1] == {'stop_gate_busy': True}


def test_busy_page_disables_auth_and_explains_retry(monkeypatch):
    pages, allowed, labels = {}, [], []
    monkeypatch.setattr(ui, 'page', lambda route: lambda fn: pages.setdefault(route, fn))
    monkeypatch.setattr(app, 'get', lambda *a, **kw: lambda fn: fn)
    monkeypatch.setattr(app, 'on_shutdown', lambda fn: None)
    original = ui.label
    def capture(text=''):
        labels.append(text)
        return original(text)
    monkeypatch.setattr(ui, 'label', capture)
    binding = SimpleNamespace(create_view=lambda *, allow_auth: (
        allowed.append(allow_auth) or SimpleNamespace(build=lambda: None, close=lambda: None)),
        close=lambda: None, shared_network_stop_scope=None)
    native_startup.register_profile_recovery(binding, stop_gate_busy=True)
    client = Client(Page('/generated-busy-recovery'))
    try:
        with client:
            pages['/']()
        assert allowed == [False]
        assert any('使用中' in s and '再起動' in s for s in labels)
        assert not any('正式な移行' in s for s in labels)
    finally:
        client.delete()


def test_uncertain_shared_get_opens_existing_recovery(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_shared_scope as shared
    scope = _generated_shared_owner(tmp_path)
    monkeypatch.setattr(shared, 'open_existing_windows_user_stop', lambda: scope)
    calls, recovered, registered = _stub_native(monkeypatch)
    def uncertain(**kwargs):
        raise shared.SharedGetOutcomeUncertain()
    monkeypatch.setattr(bootstrap, 'register_pages', uncertain)
    root = tmp_path / 'generated-profile-uncertain'
    store = NativeProfileStore(root)
    store.close()
    assert bootstrap.main(['--native', '--profile-root', str(root)]) == 0
    assert len(calls) == len(recovered) == 1 and registered == []
    assert recovered[0][1] == {'stop_get_uncertain': True}
    assert recovered[0][0].shared_network_stop_scope is scope
