"""Explicit action capability policy; every credential and transport is synthetic."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.pixiv_actions import ActionNeedsReconciliation
from pixivvault_nicegui.preferences import PreferencesStore, SPEC_BY_KEY
from pixivvault_nicegui.services import ApplicationServices, get_services
from test_pixiv_actions import intent
from test_pixiv_actions_services import environment


def saved_preferences(root, enabled):
    store = PreferencesStore(root / 'preferences.sqlite3')
    try:
        initial = store.snapshot()
        assert initial.values['pixiv_actions_enabled'] == '0'
        if enabled != '0':
            store.update({'pixiv_actions_enabled': enabled},
                         expected_revision=initial.revision)
    finally:
        store.close()


def test_preference_has_a_readonly_default_and_explicit_boolean_choices(tmp_path):
    spec = SPEC_BY_KEY['pixiv_actions_enabled']
    assert spec.default == '0' and spec.choices == ('0', '1')
    store = PreferencesStore(tmp_path / 'preferences' / 'preferences.sqlite3')
    try:
        assert store.get_setting('pixiv_actions_enabled') == '0'
    finally:
        store.close()


@pytest.mark.parametrize('saved', ('0', '1'))
def test_existing_readonly_entry_never_creates_writer_even_with_saved_enable(environment, saved):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, saved)
    service = ApplicationServices(live_client=env.reader,
        preferences_root=root, following_sync_root=env.root)
    env.created_services.append(service)
    assert service.pixiv_actions_mode == 'readonly'
    assert service.pixiv_actions is None
    assert 'pixiv_actions_enabled' not in service.preference_consumers()
    assert env.created_actions == []
    assert env.http.reads == env.http.posts == []


@pytest.mark.parametrize('saved', ('0', '1'))
def test_profile_policy_reads_saved_choice_once_without_get(environment, saved):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, saved)
    service = env.build(preferences_root=root, pixiv_actions_mode='preferences')
    assert (service.pixiv_actions is not None) is (saved == '1')
    assert 'pixiv_actions_enabled' in service.preference_consumers()
    assert len(env.created_actions) == int(saved)
    assert env.http.reads == env.http.posts == []


def test_explicit_cli_policy_ignores_saved_disable_and_disables_preference_control(environment):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, '0')
    service = env.build(preferences_root=root, pixiv_actions_mode='enabled')
    assert isinstance(service.pixiv_actions, env.actions_type)
    assert 'pixiv_actions_enabled' not in service.preference_consumers()
    assert service.preferences.get_setting('pixiv_actions_enabled') == '0'
    assert env.http.reads == env.http.posts == []


def test_profile_without_cookie_stays_synthetic_even_when_preference_is_enabled(environment):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, '1')
    service = ApplicationServices(preferences_root=root, pixiv_actions_mode='preferences')
    env.created_services.append(service)
    assert service.pixiv_actions is None and service.data_mode == 'synthetic'
    assert 'pixiv_actions_enabled' in service.preference_consumers()
    assert service.preferences.get_setting('pixiv_actions_enabled') == '1'
    assert env.created_actions == [] and env.http.reads == env.http.posts == []


def test_enable_preference_does_not_construct_owner_until_new_startup(environment):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, '0')
    service = env.build(preferences_root=root, pixiv_actions_mode='preferences')
    snapshot = service.preferences.snapshot()
    changed = service.preferences.update({'pixiv_actions_enabled': '1'},
                                         expected_revision=snapshot.revision)
    service.apply_preferences(changed)
    assert service.pixiv_actions is None and env.created_actions == []
    service.close()
    reopened = env.build(live_client=env.new_reader(), preferences_root=root,
                         pixiv_actions_mode='preferences')
    assert isinstance(reopened.pixiv_actions, env.actions_type)
    assert len(env.created_actions) == 1
    assert env.http.reads == env.http.posts == []


@pytest.mark.asyncio
async def test_disable_preference_keeps_owner_and_unknown_until_shutdown(environment):
    env = environment
    root = env.path / 'preferences'
    saved_preferences(root, '1')
    service = env.build(preferences_root=root, pixiv_actions_mode='preferences')
    owner = service.pixiv_actions
    env.http.post_answer = TimeoutError('synthetic unknown response')
    request = intent('preference-unknown')
    assert (await owner.execute(request)).requires_reconciliation
    before_reads, before_posts = len(env.http.reads), len(env.http.posts)
    changed = service.preferences.update({'pixiv_actions_enabled': '0'},
                                         expected_revision=service.preferences.snapshot().revision)
    service.apply_preferences(changed)
    assert service.pixiv_actions is owner
    assert owner.state('55', 'illust', '123').unknown
    with pytest.raises(ActionNeedsReconciliation):
        await owner.execute(intent('preference-must-not-resend'))
    assert (len(env.http.reads), len(env.http.posts)) == (before_reads, before_posts)
    service.close()
    reopened = env.build(live_client=env.new_reader(), preferences_root=root,
                         pixiv_actions_mode='preferences')
    assert reopened.pixiv_actions is None
    assert len(env.created_actions) == 1
    # This checks owner stability within the running app. The operation journal
    # is not claimed to survive a restart.


def test_cached_owner_cannot_change_policy_but_no_argument_reaccess_is_safe(environment):
    env = environment
    app = SimpleNamespace(state=SimpleNamespace())
    service = get_services(app, live_client=env.reader, following_sync_root=env.root,
                           pixiv_actions_mode='enabled')
    env.created_services.append(service)
    owner = service.pixiv_actions
    assert get_services(app) is service
    with pytest.raises(RuntimeError, match='action policy'):
        get_services(app, pixiv_actions_mode='readonly')
    assert service.pixiv_actions is owner and len(env.created_actions) == 1
    assert env.http.reads == env.http.posts == []


def test_unknown_policy_is_rejected_before_creating_any_owned_storage(environment):
    env = environment
    root = env.path / 'must-not-create'
    with pytest.raises(ValueError, match='action policy'):
        env.build(preferences_root=root, pixiv_actions_mode='automatic')
    assert not root.exists() and not env.root.exists()
    assert env.created_actions == [] and env.http.reads == env.http.posts == []


def test_cookie_cli_remains_readonly_and_enable_requires_explicit_cookie(tmp_path):
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    cookie = credentials / 'synthetic.txt'
    cookie.write_text('synthetic reference only', encoding='utf-8')
    before = cookie.read_bytes()
    default = bootstrap.parse_args(['--pixiv-cookie-file', str(cookie), '--check'])
    enabled = bootstrap.parse_args(['--pixiv-cookie-file', str(cookie),
                                    '--enable-pixiv-actions', '--check'])
    assert default.enable_pixiv_actions is False
    assert enabled.enable_pixiv_actions is True
    with pytest.raises(SystemExit):
        bootstrap.parse_args(['--enable-pixiv-actions', '--check'])
    with pytest.raises(SystemExit):
        bootstrap.parse_args(['--pixiv-cookie-file', str(cookie),
            '--novel-download-root', str(tmp_path / 'novels'), '--enable-pixiv-actions'])
    assert cookie.read_bytes() == before and not (tmp_path / 'novels').exists()


@pytest.mark.parametrize('mode', ('readonly', 'enabled', 'preferences'))
def test_registration_forwards_the_fixed_action_policy(environment, monkeypatch, mode):
    env = environment
    from pixivvault_nicegui import services as service_module
    captured = []
    class StopBeforeServiceConstruction(Exception):
        pass
    def capture(_app, **kwargs):
        captured.append(kwargs)
        raise StopBeforeServiceConstruction()
    monkeypatch.setattr(bootstrap, '_registered', False)
    monkeypatch.setattr(service_module, 'get_services', capture)
    with pytest.raises(StopBeforeServiceConstruction):
        bootstrap.register_pages(live_client=env.reader, pixiv_actions_mode=mode)
    assert captured[0]['pixiv_actions_mode'] == mode
    assert captured[0]['live_client'] is env.reader
    assert env.http.reads == env.http.posts == []


def stub_server_and_native(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import native_layout
    captured = []
    monkeypatch.setattr(bootstrap, 'register_pages', lambda **kwargs: captured.append(kwargs))
    monkeypatch.setattr(ui, 'run', lambda **_kwargs: None)
    original_find = bootstrap.importlib.util.find_spec
    monkeypatch.setattr(bootstrap.importlib.util, 'find_spec',
        lambda name: object() if name == 'webview' else original_find(name))
    monkeypatch.setattr(native_layout, 'current_startup_layout',
        lambda: SimpleNamespace(width=1280, height=720, x=0, y=0))
    monkeypatch.setattr(app.native, 'window_args', dict(app.native.window_args))
    monkeypatch.setattr(app.native, 'settings', dict(app.native.settings))
    return captured


@pytest.mark.parametrize('mode', ('readonly', 'enabled', 'preferences'))
def test_main_selects_policy_from_explicit_cli_or_normal_profile(tmp_path, monkeypatch, mode):
    captured = stub_server_and_native(monkeypatch)
    if mode == 'preferences':
        root = tmp_path / 'profile'
        profile = NativeProfileStore(root)
        profile.close()
        args = ['--native', '--profile-root', str(root)]
    else:
        cookie = tmp_path / 'synthetic-cookie.txt'
        cookie.write_text('synthetic reference', encoding='utf-8')
        args = ['--pixiv-cookie-file', str(cookie)]
        if mode == 'enabled':
            args.append('--enable-pixiv-actions')
    assert bootstrap.main(args) == 0
    assert len(captured) == 1
    assert captured[0].get('pixiv_actions_mode', 'readonly') == mode


def test_uri_relaunch_keeps_explicit_cli_capability(tmp_path, monkeypatch):
    captured = stub_server_and_native(monkeypatch)
    from pixivvault_nicegui import uri_launch
    uri_arguments = []
    def command(_executable, **kwargs):
        uri_arguments.extend(kwargs['startup_args'])
        return 'synthetic protocol command'
    monkeypatch.setattr(uri_launch, 'build_protocol_command', command)
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    cookie = credentials / 'synthetic.txt'
    cookie.write_text('synthetic reference', encoding='utf-8')
    managed = tmp_path / 'managed'
    assert bootstrap.main(['--native', '--pixiv-cookie-file', str(cookie),
        '--pixiv-download-root', str(managed), '--enable-pixiv-actions']) == 0
    assert captured[0]['pixiv_actions_mode'] == 'enabled'
    assert '--enable-pixiv-actions' in uri_arguments
    assert not managed.exists()  # Registration was intercepted before all resources.
