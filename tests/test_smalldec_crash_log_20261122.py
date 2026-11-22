import sys

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
