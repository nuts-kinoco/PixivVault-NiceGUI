from pathlib import Path

import pytest

from pixivvault_nicegui import bootstrap, managed_network_stop_shared_scope as shared
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
from test_stage5_r1_native_first_draft_shared import _stub_native


def workspace(tmp_path):
    (tmp_path / '.pixivvault-isolated-test-workspace').write_bytes(
        b'PixivVault isolated source test workspace v1\n')
    return tmp_path


def test_exact_empty_test_workspace_opens_normal_page_on_both_launches(tmp_path, monkeypatch):
    calls, recovered, registered = _stub_native(monkeypatch)
    with isolated_test_workspace(workspace(tmp_path)):
        for _ in range(2):
            assert bootstrap.main(['--native', '--profile-root', str(tmp_path/'profile')]) == 0
            assert not (tmp_path/'shared-stop-owner').exists()
        assert len(calls) == len(registered) == 2 and not recovered
        for kwargs in registered:
            assert kwargs['pixiv_actions_mode'] == 'preferences'
            binding = kwargs['native_profile']
            assert binding.auth_stop_required and binding.auth_shared_stop_required
            assert binding.shared_network_stop_scope is None
        snap = NativeProfileStore.peek(tmp_path/'profile')
        assert snap.revision == 0 and snap.cookie_file is None and snap.save_parent is None


@pytest.mark.parametrize('failure', ['empty-owner', 'permission', 'io'])
def test_existing_or_unreadable_owner_is_not_absent(tmp_path, monkeypatch, failure):
    calls, recovered, registered = _stub_native(monkeypatch)
    root = tmp_path/'shared-stop-owner'
    root.mkdir()
    if failure != 'empty-owner':
        actual = Path.lstat
        def lstat(path, *a, **kw):
            if path == root:
                raise PermissionError('synthetic') if failure == 'permission' else OSError('synthetic')
            return actual(path, *a, **kw)
        monkeypatch.setattr(Path, 'lstat', lstat)
    with isolated_test_workspace(workspace(tmp_path)):
        assert bootstrap.main(['--native', '--profile-root', str(tmp_path/'profile')]) == 0
    assert len(calls) == len(recovered) == 1 and not registered
    assert recovered[0][1] == {'stop_migration_required': True}


@pytest.mark.asyncio
async def test_absent_shared_owner_never_authorizes_draft_auth(tmp_path, monkeypatch):
    calls, recovered, registered = _stub_native(monkeypatch)
    with isolated_test_workspace(workspace(tmp_path)):
        assert bootstrap.main(['--native', '--profile-root', str(tmp_path/'profile')]) == 0
        binding = registered[0]['native_profile']
        # main closes the binding at exit; reopen only the synthetic profile.
        from pixivvault_nicegui.native_startup import NativeProfileBinding
        from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
        fresh = NativeProfileBinding(NativeProfileStore(tmp_path/'profile'))
        fresh.auth_stop_required = fresh.auth_shared_stop_required = True
        fresh.managed_network_stop_scope = ManagedProfileNetworkStopScope(tmp_path/'profile')
        try:
            assert not await fresh.verify_auth(tmp_path/'missing-synthetic-cookie.txt')
            assert not (tmp_path/'shared-stop-owner').exists()
        finally:
            fresh.close()


def test_first_local_start_connects_real_services_and_settings(tmp_path, monkeypatch):
    from nicegui import app, ui, Client
    from nicegui.page import page as Page
    from pixivvault_nicegui.services import _APP_KEY
    original_register = bootstrap.register_pages
    calls, recovered, registered = _stub_native(monkeypatch)
    monkeypatch.setattr(bootstrap, 'register_pages', original_register)
    inspected = []
    def run(**kwargs):
        service = getattr(app.state, _APP_KEY)
        assert service.native_profile is not None
        assert service.live_client is None
        client = Client(Page('/first-local-settings'))
        view = None
        try:
            with client:
                view = service.native_profile.create_view()
                view.build()
            inspected.append(True)
        finally:
            if view is not None:
                view.close()
            client.delete()
    monkeypatch.setattr(ui, 'run', run)
    with isolated_test_workspace(workspace(tmp_path)):
        assert bootstrap.main(['--native', '--profile-root', str(tmp_path/'profile')]) == 0
        assert not (tmp_path/'shared-stop-owner').exists()
    assert inspected == [True] and recovered == []


@pytest.mark.parametrize('status', [401, 403, 429])
def test_local_screen_does_not_clear_existing_profile_stop(tmp_path, monkeypatch, status):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    calls, recovered, registered = _stub_native(monkeypatch)
    with isolated_test_workspace(workspace(tmp_path)):
        root = tmp_path/'profile'
        NativeProfileStore(root).close()
        scope = ManagedProfileNetworkStopScope(root)
        scope.gate.trip(status)
        before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert bootstrap.main(['--native', '--profile-root', str(root)]) == 0
        state = ManagedProfileNetworkStopScope(root).gate.snapshot()
        assert state.blocked and state.status_code == status
        after = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert before == {name:after[name] for name in before}
        assert set(after) - set(before) <= {'.pixivvault-native-instance-v1.json'}
        assert not (tmp_path/'shared-stop-owner').exists()
    assert len(registered) == 1 and recovered == []


def test_damaged_profile_history_still_requires_recovery(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
    calls, recovered, registered = _stub_native(monkeypatch)
    with isolated_test_workspace(workspace(tmp_path)):
        root = tmp_path/'profile'
        NativeProfileStore(root).close()
        scope = ManagedProfileNetworkStopScope(root)
        scope.gate.path.write_bytes(b'generated damaged history')
        before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert bootstrap.main(['--native', '--profile-root', str(root)]) == 0
        after = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
        assert before == {name:after[name] for name in before}
        assert set(after) - set(before) <= {'.pixivvault-native-instance-v1.json'}
    assert not registered and recovered[0][1] == {'stop_migration_required': True}
