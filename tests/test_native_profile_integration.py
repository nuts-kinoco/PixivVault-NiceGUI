"""Native entry/profile integration without OS dialogs, network or user storage."""

import asyncio
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.native_startup import NativeProfileBinding, native_entry_arguments


def test_only_plain_executable_launch_uses_profile_and_never_creates_it(tmp_path, monkeypatch):
    root = tmp_path / 'profile'
    monkeypatch.setenv('PIXIVVAULT_PROFILE_ROOT', str(root))
    assert native_entry_arguments([]) == ['--profile-root', str(root)]
    for argv in (['--check'], ['--port', '55050'], ['--native'], ['--check-output', 'x']):
        assert native_entry_arguments(argv) == argv
    assert not root.exists()
    monkeypatch.setenv('PIXIVVAULT_PROFILE_ROOT', 'relative-profile')
    with pytest.raises(ValueError):
        native_entry_arguments([])
    assert not root.exists()


def test_plain_native_entry_forwards_profile_without_precreating_files(tmp_path, monkeypatch):
    import sys
    from tools import native_entry

    root = tmp_path / 'profile'
    monkeypatch.setenv('PIXIVVAULT_PROFILE_ROOT', str(root))
    monkeypatch.setattr(sys, 'argv', ['app.exe'])
    monkeypatch.setattr(native_entry.multiprocessing, 'freeze_support', lambda: None)
    calls = []
    monkeypatch.setattr(bootstrap, 'main', lambda args: calls.append(args) or 0)
    assert native_entry.main() == 0
    assert calls == [['--profile-root', str(root), '--native']]
    assert not root.exists()


def test_packaged_native_entry_reports_argparse_refusal_without_leaking_detail(monkeypatch):
    """Warm URI profile peek may reject during an SQLite sidecar update.

    argparse raises SystemExit(2), which must not disappear silently when
    the packaged executable has no console.
    """
    import sys
    from tools import native_entry

    calls=[]
    monkeypatch.setattr(native_entry.multiprocessing,'freeze_support',lambda:None)
    monkeypatch.setattr(sys,'argv',['PixivVault.exe'])
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(native_entry,'_regular_entry',
                        lambda _: (_ for _ in ()).throw(SystemExit(2)))
    monkeypatch.setattr(native_entry,'_report_startup_rejected',
                        lambda: calls.append('fixed-error-dialog') or 2)
    assert native_entry.main()==2
    assert calls==['fixed-error-dialog']


def test_frozen_native_runtime_oserror_releases_owner_and_is_not_startup(tmp_path,monkeypatch):
    import sys
    from nicegui import ui
    from pixivvault_nicegui import native_instance, native_profile

    root=tmp_path/'owned-native-profile'
    native_profile.NativeProfileStore(root).close()
    _fake_native(monkeypatch)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(bootstrap,'register_pages',lambda **kw:None)
    monkeypatch.setattr(ui,'run',lambda **kw:(_ for _ in ()).throw(
        OSError('synthetic private runtime detail')))
    with pytest.raises(native_instance.NativeAppRuntimeError):
        bootstrap.main(['--native','--profile-root',str(root)])
    restarted=native_instance.NativeInstance(root)
    try:
        assert restarted.acquire()
    finally:
        restarted.close()


def test_check_profile_has_no_creation_or_secret_output(tmp_path, capsys):
    root = tmp_path / 'profile'
    assert bootstrap.main(['--native', '--profile-root', str(root), '--check']) == 0
    assert not root.exists()
    assert str(root) not in capsys.readouterr().out


def test_persisted_profile_passes_existing_storage_fences_without_provision(tmp_path):
    from pixivvault_nicegui.native_profile import NativeProfileStore

    root = tmp_path / 'profile'
    cookie = tmp_path / 'credentials' / 'cookies.txt'
    cookie.parent.mkdir()
    cookie.write_text('# synthetic reference only\n', encoding='utf-8')
    parent = tmp_path / 'selected'
    parent.mkdir()
    store = NativeProfileStore(root)
    try:
        saved = store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
        expected = store.native_kwargs(saved)
    finally:
        store.close()
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    config = bootstrap.parse_args(['--native', '--profile-root', str(root), '--check'])
    assert config.pixiv_cookie_file == cookie
    assert config.profile_revision == saved.revision
    assert config.novel_download_root is not None and config.ugoira_root is not None
    assert config.discovery_root is not None and config.save_export_root is not None
    assert all(getattr(config, key) == value for key, value in expected.items())
    assert not list(parent.iterdir())
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert cookie.read_text(encoding='utf-8') == '# synthetic reference only\n'


def test_profile_and_explicit_fixture_are_rejected_before_creation(tmp_path):
    root, fixture = tmp_path / 'profile', tmp_path / 'fixture'
    with pytest.raises(SystemExit):
        bootstrap.parse_args(['--native', '--profile-root', str(root),
                              '--download-sandbox', str(fixture)])
    assert not root.exists() and not fixture.exists()


def _fake_native(monkeypatch):
    from nicegui import app, ui
    import pixivvault_nicegui.native_layout as layout

    actual_find_spec = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(bootstrap.importlib.util, 'find_spec',
                        lambda name, *a, **kw: object() if name == 'webview'
                        else actual_find_spec(name, *a, **kw))
    monkeypatch.setattr(layout, 'current_startup_layout',
                        lambda: SimpleNamespace(width=960, height=720, x=0, y=0))
    monkeypatch.setattr(app.native, 'window_args', {})
    monkeypatch.setattr(app.native, 'settings', {})
    calls = []
    monkeypatch.setattr(ui, 'run', lambda **kwargs: calls.append(kwargs))
    return calls


def test_first_launch_registers_editable_profile_and_closes_its_owner(tmp_path, monkeypatch):
    calls = _fake_native(monkeypatch)
    registrations = []
    monkeypatch.setattr(bootstrap, 'register_pages', lambda **kwargs: registrations.append(kwargs))
    monkeypatch.delenv('PIXIVVAULT_PROFILE_ROOT', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    arguments = native_entry_arguments([])
    root = Path(arguments[1])
    assert root.parent == tmp_path and not root.exists()
    assert bootstrap.main(arguments + ['--native']) == 0
    assert len(registrations) == len(calls) == 1
    binding = registrations[0]['native_profile']
    assert binding._closed and calls[0]['title'] == 'PixivVault'
    assert registrations[0]['desktop_enabled'] is True
    assert 'pixiv_cookie_file' not in registrations[0]
    assert 'pixiv_download_root' not in registrations[0]
    assert root.is_dir()


def test_profile_changed_during_start_does_not_provision_or_start(tmp_path, monkeypatch):
    from pixivvault_nicegui.native_profile import NativeProfileStore

    root, parent = tmp_path / 'profile', tmp_path / 'selected'
    parent.mkdir()
    store = NativeProfileStore(root)
    store.update(save_parent=parent, expected_revision=0)
    store.close()
    calls = _fake_native(monkeypatch)
    monkeypatch.setattr(bootstrap, 'parse_args', lambda _: bootstrap.RunConfig(
        native=True, profile_root=root, profile_revision=0))
    registered = []
    monkeypatch.setattr(bootstrap, 'register_pages', lambda **kw: registered.append(kw))
    with pytest.raises(ValueError, match='変更'):
        bootstrap.main([])
    assert not registered and not calls and not list(parent.iterdir())


def test_missing_selected_cookie_opens_configuration_without_starting_media(tmp_path, monkeypatch):
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui import native_startup

    root, parent = tmp_path / 'profile', tmp_path / 'selected'
    parent.mkdir()
    cookie = tmp_path / 'credentials' / 'missing-later.txt'
    cookie.parent.mkdir()
    cookie.write_text('synthetic only', encoding='utf-8')
    store = NativeProfileStore(root)
    saved = store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
    store.close()
    cookie.unlink()
    args = ['--native', '--profile-root', str(root)]
    config = bootstrap.parse_args(args)
    assert config.profile_reference_unavailable and config.profile_revision == saved.revision
    assert config.pixiv_cookie_file is None and config.pixiv_download_root is None
    calls = _fake_native(monkeypatch)
    monkeypatch.setattr(bootstrap, 'register_pages', lambda **_: pytest.fail('media must not start'))
    recovery = []
    monkeypatch.setattr(native_startup, 'register_profile_recovery',
                        lambda binding, **kw: recovery.append((binding, kw)))
    assert bootstrap.main(args) == 0
    assert len(calls) == len(recovery) == 1
    assert recovery[0][0]._closed and recovery[0][1] == {}
    assert not list(parent.iterdir())
    assert NativeProfileStore.peek(root).cookie_file == cookie


def test_invalid_cookie_format_has_editable_recovery_only_for_profile_launch(tmp_path, monkeypatch):
    from pixivvault_nicegui import native_startup
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.pixiv_read_client import CookieSourceRejected

    root = tmp_path / 'profile'
    cookie = tmp_path / 'credentials' / 'wrong-format.txt'
    cookie.parent.mkdir()
    cookie.write_text('synthetic only', encoding='utf-8')
    store = NativeProfileStore(root)
    store.update(cookie_file=cookie, expected_revision=0)
    store.close()
    calls = _fake_native(monkeypatch)
    def reject_cookie(**kwargs):
        assert kwargs['pixiv_cookie_file'] == cookie
        raise CookieSourceRejected()
    monkeypatch.setattr(bootstrap, 'register_pages', reject_cookie)
    recovery = []
    monkeypatch.setattr(native_startup, 'register_profile_recovery',
                        lambda binding, **kw: recovery.append((binding, kw)))
    assert bootstrap.main(['--native', '--profile-root', str(root)]) == 0
    assert len(calls) == len(recovery) == 1
    assert recovery[0][1] == {'cookie_rejected': True}
    with pytest.raises(CookieSourceRejected):
        bootstrap.main(['--native', '--pixiv-cookie-file', str(cookie)])
    assert len(calls) == len(recovery) == 1


@pytest.mark.asyncio
async def test_auth_cancellation_closes_only_its_reader_and_suppresses_success(tmp_path, monkeypatch):
    from pixivvault_nicegui import pixiv_read_client
    from pixivvault_nicegui.native_profile import NativeProfileStore

    entered, released, exited = Event(), Event(), Event()
    readers = []
    class Reader:
        def __init__(self, cookie):
            self.cookie = cookie
            self.closed = False
            readers.append(self)
        def require_identity(self):
            entered.set()
            assert released.wait(3)
            exited.set()
            return 'synthetic-identity-not-displayed'
        def close(self):
            self.closed = True
            released.set()
    monkeypatch.setattr(pixiv_read_client, 'LivePixivReadClient', Reader)
    closed = []
    store = NativeProfileStore(tmp_path / 'profile')
    close_store = store.close
    def close_profile():
        closed.append(True)
        close_store()
    monkeypatch.setattr(store, 'close', close_profile)
    binding = NativeProfileBinding(store)
    task = asyncio.create_task(binding.verify_auth(tmp_path / 'synthetic-cookie.txt'))
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert readers[0].closed
    assert await asyncio.to_thread(exited.wait, 3)
    binding.close()
    binding.close()
    assert closed == [True]
    assert await binding.verify_auth(tmp_path / 'other.txt') is False
    assert len(readers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('with_preferences', [False, True])
async def test_settings_reaches_profile_and_detaches_on_close(tmp_path, with_preferences, monkeypatch):
    from nicegui import Client, core, ui
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())

    store = NativeProfileStore(tmp_path / 'profile')
    binding = NativeProfileBinding(store)
    services = ApplicationServices(preferences_root=tmp_path / 'preferences' if with_preferences else None)
    services.native_profile = binding
    client = Client(ui.page('/_profile_integration_' + str(with_preferences)))
    try:
        with client:
            shell = DemoShell(services)
            settings = next(e for e in client.elements.values()
                            if e.props.get('data-testid') == 'preferences-open')
            assert settings.props.get('disable') is not True
            shell.open_preferences()
            assert shell.profile_view is not None and shell.profile_view._active
            if with_preferences:
                await shell.following_view._initial_task
                assert shell.preferences_view.native_profile is shell.profile_view
                shell.preferences_view.close_dialog()
                assert not shell.profile_view._active
            shell.close()
            assert shell.profile_view._closed
    finally:
        client.delete()
        services.close()
        binding.close()
