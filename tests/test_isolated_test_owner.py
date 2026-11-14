from pathlib import Path
from types import SimpleNamespace
import pytest
from test_formal_stop_migration import setup, fingerprint
from pixivvault_nicegui import managed_network_stop_shared_scope as scope
from pixivvault_nicegui.managed_stop_migration import StopMigrationService
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped, ManagedNetworkGateStorageError
from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace, validate_config

def mark(root):
    (root / '.pixivvault-isolated-test-workspace').write_bytes(b'PixivVault isolated source test workspace v1\n')

@pytest.mark.parametrize('status', [None, 401, 403, 429])
def test_migration_and_startup_share_owner_without_opening_real_owner(tmp_path, monkeypatch, status):
    mark(tmp_path)
    store, images, gate = setup(tmp_path, status=status)
    def forbidden(): raise AssertionError('real LocalAppData must not be queried')
    monkeypatch.setattr(scope, '_windows_local_app_data', forbidden)
    try:
        with isolated_test_workspace(tmp_path):
            service = StopMigrationService(store)
            before = fingerprint(images)
            result = service.execute(service.preview([('images', images)]), confirmed=True)
            assert result.status == 'completed'
            assert service.target_root == tmp_path / 'shared-stop-owner'
            assert fingerprint(images) == before
            owner = scope.open_existing_windows_user_stop()
            assert owner.root == service.target_root
            assert owner.gate.snapshot().status_code == status
            if status:
                with pytest.raises(ManagedNetworkStopped): owner.require_allowed()
            else: owner.require_allowed()
            owner.gate.lock_path.rename(owner.gate.lock_path.with_suffix(".retained"))
            owner.gate.lock_path.write_bytes(b'replaced')
            with pytest.raises(ManagedNetworkGateStorageError): owner.require_allowed()
    finally: store.close()

def test_default_resolution_is_unchanged_and_selection_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(scope, '_windows_local_app_data', lambda: tmp_path / 'real-sentinel')
    expected = tmp_path / 'real-sentinel' / 'PixivVault-NiceGUI' / 'managed-pixiv-get-stop-v1'
    assert scope.shared_owner_root() == expected
    mark(tmp_path)
    with isolated_test_workspace(tmp_path):
        assert scope.shared_owner_root() == tmp_path / 'shared-stop-owner'
    assert scope.shared_owner_root() == expected
    assert not (tmp_path / 'real-sentinel').exists()

def test_test_workspace_rejects_external_paths_and_ordinary_cli_override(tmp_path):
    from pixivvault_nicegui.bootstrap import parse_args
    mark(tmp_path)
    with isolated_test_workspace(tmp_path):
        with pytest.raises(ValueError): validate_config(SimpleNamespace(cookie=tmp_path.parent / 'foreign-cookie'))
        with pytest.raises(ValueError): validate_config(SimpleNamespace(extension_bridge=True))
    with pytest.raises(SystemExit): parse_args(['--test-workspace', str(tmp_path)])

def test_unmarked_workspace_is_refused_and_no_empty_owner_is_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        with isolated_test_workspace(tmp_path): pass
    mark(tmp_path)
    with isolated_test_workspace(tmp_path):
        with pytest.raises(ManagedNetworkGateStorageError): scope.open_existing_windows_user_stop()
        assert not (tmp_path / 'shared-stop-owner').exists()

def test_migration_rejects_external_source(tmp_path):
    mark(tmp_path)
    store, _, _ = setup(tmp_path)
    try:
        with isolated_test_workspace(tmp_path):
            with pytest.raises(Exception):
                StopMigrationService(store).preview([('images', tmp_path.parent / 'external')])
    finally: store.close()


def test_actual_main_check_with_slots_config(tmp_path, monkeypatch):
    from pixivvault_nicegui.bootstrap import main
    mark(tmp_path)
    monkeypatch.setattr(scope, '_windows_local_app_data', lambda: pytest.fail('real owner query'))
    with isolated_test_workspace(tmp_path):
        assert main(['--check']) == 0

def test_native_saved_paths_and_selection_are_checked_before_access(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileSnapshot, native_kwargs, _absolute
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    mark(tmp_path)
    external = tmp_path.parent / 'unread-external-cookie'
    with isolated_test_workspace(tmp_path):
        with pytest.raises(ValueError): _absolute(external, kind='file')
        with pytest.raises(ValueError): native_kwargs(NativeProfileSnapshot(0, library_readonly_db=external), tmp_path / 'profile')
        with pytest.raises(ValueError): NativeProfileBinding._verify_auth(None, external, None)
