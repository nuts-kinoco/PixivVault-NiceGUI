import subprocess
import sys

import pytest

from pixivvault_nicegui import bootstrap, crash_entry, crash_evidence
from pixivvault_nicegui.native_profile import NativeProfileStore


def owned_profile(path):
    store = NativeProfileStore(path)
    store.close()
    return path


def test_crash_entry_uses_profile_logs_before_bootstrap(tmp_path, monkeypatch):
    profile = owned_profile(tmp_path / 'profile')
    monkeypatch.delenv('PIXIVVAULT_CRASH_LOG_PATH', raising=False)
    monkeypatch.setattr(sys, 'argv', ['app', '--native', '--profile-root', str(profile)])
    expected = profile / 'logs' / 'crash_log.txt'

    def application():
        assert expected.is_file()
        return 0

    monkeypatch.setattr(bootstrap, 'main', application)
    try:
        assert crash_entry.main() == 0
        assert not (profile.parent / 'logs').exists()
    finally:
        if crash_evidence._active is not None:
            crash_evidence._active.close()


def test_crash_log_path_override_precedence(tmp_path, monkeypatch):
    profile = owned_profile(tmp_path / 'profile')
    environment_path = tmp_path / 'env' / 'evidence.log'
    command_path = tmp_path / 'cli' / 'evidence.log'
    monkeypatch.setenv('PIXIVVAULT_CRASH_LOG_PATH', str(environment_path))
    monkeypatch.setattr(sys, 'argv', ['app', '--native', '--profile-root', str(profile)])
    capture = crash_evidence.install(command_path)
    assert capture is not None
    capture.close()
    assert command_path.is_file()
    assert not environment_path.exists()

    monkeypatch.setattr(sys, 'argv', ['app'])
    capture = crash_evidence.install()
    assert capture is not None
    capture.close()
    assert environment_path.is_file()
    assert not (profile / 'logs').exists()


def test_uncreatable_crash_log_warns_and_app_continues(tmp_path, monkeypatch, capsys):
    profile = owned_profile(tmp_path / 'profile')
    (profile / 'logs').write_bytes(b'occupied')
    old_hook = sys.excepthook
    monkeypatch.delenv('PIXIVVAULT_CRASH_LOG_PATH', raising=False)
    monkeypatch.setattr(sys, 'argv', ['app', '--native', '--profile-root=' + str(profile)])
    monkeypatch.setattr(bootstrap, 'main', lambda: 17)
    try:
        assert crash_entry.main() == 17
        assert sys.excepthook is old_hook
        warning = capsys.readouterr().err
        assert 'クラッシュ記録を保存できません' in warning
        assert str(profile) not in warning
    finally:
        if crash_evidence._active is not None:
            crash_evidence._active.close()


@pytest.mark.parametrize('override', ['argument', 'environment'])
def test_crash_override_rejects_junction(override, tmp_path, monkeypatch, capsys):
    destination = tmp_path / 'real'
    destination.mkdir()
    link = tmp_path / 'junction'
    result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(destination)],
                            capture_output=True)
    assert result.returncode == 0
    requested = link / 'crash_log.txt'
    path = requested if override == 'argument' else None
    if path is None:
        monkeypatch.setenv('PIXIVVAULT_CRASH_LOG_PATH', str(requested))
    capture = crash_evidence.install(path)
    if capture is not None:
        capture.close()
    assert capture is None
    assert not (destination / 'crash_log.txt').exists()
    assert 'クラッシュ記録を保存できません' in capsys.readouterr().err


def test_first_profile_launch_creates_its_crash_log(tmp_path, monkeypatch):
    from test_first_local_start_20261114 import workspace, _stub_native
    from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace

    profile = tmp_path / 'new-profile'
    _stub_native(monkeypatch)
    monkeypatch.delenv('PIXIVVAULT_CRASH_LOG_PATH', raising=False)
    monkeypatch.setattr(sys, 'argv', ['app', '--native', '--profile-root', str(profile)])
    try:
        with isolated_test_workspace(workspace(tmp_path)):
            assert crash_entry.main() == 0
            assert NativeProfileStore.peek(profile).revision == 0
            assert (profile / 'logs' / 'crash_log.txt').is_file()
    finally:
        if crash_evidence._active is not None:
            crash_evidence._active.close()
