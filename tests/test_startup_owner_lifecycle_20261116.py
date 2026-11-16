"""Normal product entry with generated credentials and owned fake transport."""
import asyncio
import json
from pathlib import Path
from threading import Event
import time

import pytest

from pixivvault_nicegui import bootstrap
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopped, ManagedNetworkStopGate)
from pixivvault_nicegui.managed_network_stop_shared_scope import (
    open_existing_windows_user_stop, _GATE_FILE, _OWNER_FILE)
from pixivvault_nicegui.managed_network_stop_startup import (
    open_or_initialize_windows_user_stop, attach_selected_histories)
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from pixivvault_nicegui.test_owner_runtime import isolated_test_workspace
from test_stage5_r1_native_first_draft_shared import _stub_native


@pytest.fixture
def isolated(tmp_path):
    (tmp_path / '.pixivvault-isolated-test-workspace').write_bytes(
        b'PixivVault isolated source test workspace v1\n')
    with isolated_test_workspace(tmp_path):
        yield tmp_path


@pytest.fixture
def cookie(isolated):
    folder = isolated / 'credentials'
    folder.mkdir()
    path = folder / 'cookies.txt'
    path.write_text('# Netscape HTTP Cookie File\n' +
        f'.pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generated\n', encoding='utf-8')
    return path


@pytest.fixture
def remote(monkeypatch):
    calls = []
    class GeneratedOwnedTransport:
        revocation_unverified = False
        def __call__(self, request, *_):
            calls.append(request.full_url)
            assert request.get_method() == 'GET' and request.data is None
            if request.full_url.endswith('/ajax/user/extra'):
                return b'{"error":false,"body":{"userId":"55"}}'
            if request.full_url == 'https://www.pixiv.net/':
                return b'<meta id="meta-global-data" content=\'{"userData":{"id":"55"}}\'>'
            return b'generated image'
        def close(self): pass
    from pixivvault_nicegui import managed_network_stop_reader
    monkeypatch.setattr(managed_network_stop_reader, '_OwnedReadTransport', GeneratedOwnedTransport)
    return calls


def test_normal_cli_initializes_and_uses_guarded_reader(isolated, cookie, remote, monkeypatch):
    from nicegui import ui
    inspected = []
    def register(**kwargs):
        assert kwargs['shared_network_stop_scope'] is not None
        client, *_ = bootstrap._build_owned_media_readers(
            pixiv_cookie_file=cookie, pixiv_download_root=isolated/'images',
            novel_download_root=None, novel_fixture_root=None, novel_source=None,
            ugoira_root=None, ugoira_fixture_root=None, live_client=None,
            managed_network_stop_scope=None,
            shared_network_stop_scope=kwargs['shared_network_stop_scope'])
        try:
            assert client.require_identity() == '55'
            assert client.get_image_bytes('https://i.pximg.net/generated/a.png') == b'generated image'
            inspected.append(True)
        finally:
            client.close()
    monkeypatch.setattr(bootstrap, 'register_pages', register)
    monkeypatch.setattr(ui, 'run', lambda **_: None)
    for _ in range(2):
        assert bootstrap.main(['--pixiv-cookie-file', str(cookie),
            '--pixiv-download-root', str(isolated/'images')]) == 0
    assert inspected == [True, True] and len(remote) == 6
    assert not open_existing_windows_user_stop().gate.snapshot().blocked


def test_new_native_draft_save_restart_borrow_entry(isolated, cookie, remote, monkeypatch):
    actual_register = bootstrap.register_pages
    calls, recovery, registrations = _stub_native(monkeypatch)
    profile = isolated/'profile'
    parent = isolated/'saved'; parent.mkdir()
    assert bootstrap.main(['--native','--profile-root',str(profile)]) == 0
    assert recovery == [] and len(registrations) == 1
    assert registrations[0]['native_profile'].shared_network_stop_scope is not None
    store = NativeProfileStore(profile)
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope = ManagedProfileNetworkStopScope(profile)
    binding.shared_network_stop_scope = open_existing_windows_user_stop()
    try:
        result = asyncio.run(binding.check_authentication(cookie))
        assert result.ok and result.code == 'verified'
        assert store.snapshot().revision == 0  # Draft check never saves secretly.
        store.update(cookie_file=cookie, save_parent=parent, expected_revision=0)
    finally:
        binding.close()
    from pixivvault_nicegui.services import _APP_KEY
    from nicegui import Client, app, ui, core
    from nicegui.page import page as Page
    from pixivvault_nicegui.ui.shell import DemoShell
    from types import SimpleNamespace
    inspected = []
    def register(**config):
        registrations.append(config)
        assert config['pixiv_cookie_file'] == cookie
        assert config['external_vault_root'].is_relative_to(parent)
        actual_register(**config)
    def run(**_):
        service = getattr(app.state, _APP_KEY)
        assert service.data_mode == 'pixiv-readonly'
        assert service.live_client.require_identity() == '55'
        client = Client(Page('/startup-borrow'))
        shell = None
        try:
            with client:
                shell = DemoShell(service)
            assert shell.borrow_switch.enabled and shell.borrow_dialog is not None
            async def prompt_and_cancel():
                monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
                task = asyncio.create_task(shell.borrow_changed(SimpleNamespace(value=True)))
                try:
                    await asyncio.sleep(0)
                    assert shell.borrow_dialog.password_dialog.value
                    shell.borrow_dialog.password_dialog.submit(False)
                    await asyncio.wait_for(task, 2)
                    assert not service.premium_authorization.status()['enabled']
                finally:
                    if not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
            asyncio.run(prompt_and_cancel())
            assert len(remote) == 4  # Own auth only; no borrowed-account login GET.
            inspected.append(True)
        finally:
            if shell is not None: shell.close()
            client.delete()
    monkeypatch.setattr(bootstrap, 'register_pages', register)
    monkeypatch.setattr(bootstrap, '_registered', False)
    monkeypatch.setattr(ui, 'run', run)
    assert bootstrap.main(['--native','--profile-root',str(profile)]) == 0
    assert recovery == [] and len(registrations) == 2 and inspected == [True]


@pytest.mark.parametrize('status', [401, 403, 429])
def test_selected_old_stop_preserved_before_first_owner_publication(isolated, cookie, remote, status):
    from pixivvault_nicegui.managed_downloads import MARKER, IDENTITY
    root = isolated/'old-images'; root.mkdir()
    (root/MARKER).write_text(json.dumps(IDENTITY))
    gate = ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3')
    gate.trip(status)
    before = {p.name:p.read_bytes() for p in root.iterdir()}
    owner = open_or_initialize_windows_user_stop(selected_roots=(('images', root),))
    assert owner.gate.snapshot().status_code == status
    reader = owner.create_reader(cookie)
    try:
        with pytest.raises(ManagedNetworkStopped): reader.get_image_bytes('https://i.pximg.net/generated/a.png')
        assert remote == []
        assert before == {p.name:p.read_bytes() for p in root.iterdir()}
    finally: reader.close()


def test_later_old_writer_stop_is_seen_without_reset(isolated, cookie, remote):
    from pixivvault_nicegui.managed_downloads import MARKER, IDENTITY
    root = isolated/'old-images'; root.mkdir()
    (root/MARKER).write_text(json.dumps(IDENTITY))
    gate = ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3')
    owner = open_or_initialize_windows_user_stop(selected_roots=(('images', root),))
    reader = owner.create_reader(cookie)
    try:
        gate.trip(403)
        with pytest.raises(ManagedNetworkStopped): reader.get_image_bytes('https://i.pximg.net/generated/a.png')
        assert remote == [] and owner.gate.snapshot().status_code == 403
    finally: reader.close()


@pytest.mark.parametrize('damage', ['owner-directory', 'both-gate-files', 'startup-record', 'parent-anchor', 'both-records'])
def test_normal_owner_loss_never_reinitializes(isolated, damage):
    owner = open_or_initialize_windows_user_stop()
    if damage == 'owner-directory': owner.root.rename(isolated/'preserved-owner')
    elif damage == 'both-gate-files':
        owner.gate.path.unlink(); owner.gate.lock_path.unlink()
    elif damage in ('startup-record', 'both-records'):
        (owner.root/'startup-record.json').unlink()
        if damage == 'both-records': (owner.root.parent/'.pixivvault-user-stop-startup.v1.json').unlink()
    else: (owner.root.parent/'.pixivvault-user-stop-startup.v1.json').unlink()
    with pytest.raises(ManagedNetworkGateStorageError): open_or_initialize_windows_user_stop()


def test_uncertain_get_survives_restart(isolated, cookie, remote):
    owner = open_or_initialize_windows_user_stop()
    marker = owner.gate.inflight_path
    marker.write_bytes(b'pixivvault-user-pixiv-get-inflight-v1\n')
    restarted = open_or_initialize_windows_user_stop()
    with pytest.raises(ManagedNetworkGateStorageError): restarted.require_allowed()
    assert marker.exists() and remote == []


@pytest.mark.parametrize('condition,expected', [
    ('missing-owner','owner_absent'), ('bad-cookie','cookie_unreadable'),
    ('stopped','stopped'), ('uncertain','uncertain'),
        ('denied','remote_denied'), ('anonymous','identity_unverified'), ('rate-limit','rate_limited')])
def test_authentication_diagnostics_are_distinct(isolated, cookie, remote, monkeypatch, condition, expected):
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, HTTPStatusRejected, AuthenticationMissing
    store = NativeProfileStore(isolated/'profile')
    binding = NativeProfileBinding(store)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    binding.managed_network_stop_scope = ManagedProfileNetworkStopScope(store.root)
    binding.shared_network_stop_scope = open_or_initialize_windows_user_stop()
    if condition == 'missing-owner': binding.shared_network_stop_scope = None
    if condition == 'bad-cookie': cookie.write_text('generated invalid Cookie')
    if condition == 'stopped': binding.shared_network_stop_scope.gate.trip(403)
    if condition == 'uncertain': binding.shared_network_stop_scope.gate.inflight_path.write_bytes(b'uncertain')
    if condition == 'anonymous':
        monkeypatch.setattr(LivePixivReadClient,'require_identity',
            lambda self: (_ for _ in ()).throw(AuthenticationMissing()))
    if condition in ('denied', 'rate-limit'):
        from pixivvault_nicegui import managed_network_stop_reader
        class RejectingTransport:
            revocation_unverified = False
            def __call__(self, request, *_):
                remote.append('generated refusal')
                raise HTTPStatusRejected(403 if condition == 'denied' else 429)
            def close(self): pass
        monkeypatch.setattr(managed_network_stop_reader,'_OwnedReadTransport',RejectingTransport)
        if condition == 'rate-limit':
            from test_display_recovery_batch import Clock
            binding.auth_retry_policy = Clock().policy()
    try:
        result = asyncio.run(binding.check_authentication(cookie))
        assert result.code == expected and not result.ok
        if condition not in ('denied','anonymous','rate-limit'): assert remote == []
        assert str(cookie) not in result.message and '55_generated' not in result.message
    finally: binding.close()


def test_busy_old_writer_does_not_poison_first_setup(isolated):
    from pixivvault_nicegui.managed_downloads import MARKER, IDENTITY
    from pixivvault_nicegui.managed_stop_migration import _FileLock
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateBusy
    root = isolated/'old-images'; root.mkdir()
    (root/MARKER).write_text(json.dumps(IDENTITY))
    gate = ManagedNetworkStopGate(root/'.pixivvault-network-stop.sqlite3')
    ticket = _FileLock(gate.lock_path)
    try:
        with pytest.raises(ManagedNetworkGateBusy):
            open_or_initialize_windows_user_stop(selected_roots=(('images',root),))
        assert not (isolated/'.pixivvault-user-stop-startup.v1.json').exists()
        assert not (isolated/'shared-stop-owner').exists()
    finally: ticket.close()
    owner = open_or_initialize_windows_user_stop(selected_roots=(('images',root),))
    assert not owner.gate.snapshot().blocked


def test_new_result_is_rendered_by_real_settings_view(isolated, cookie):
    from nicegui import Client
    from nicegui.page import page as Page
    from pixivvault_nicegui.native_auth_result import NativeAuthResult
    from pixivvault_nicegui.ui.native_profile_view import NativeProfileView
    store = NativeProfileStore(isolated/'profile')
    result = NativeAuthResult('owner_absent')
    async def verify(_): return result
    client = Client(Page('/startup-auth-result'))
    view = None
    try:
        with client:
            view = NativeProfileView(store, verify_auth=verify)
            view.build()
            view.draft_cookie = cookie
            assert asyncio.run(view.check_auth()) is False
            assert view.status.text == result.message
    finally:
        if view is not None: view.close()
        client.delete(); store.close()


@pytest.mark.parametrize('failure', ['bad-cookie', 'missing-established-owner'])
def test_cli_startup_failures_have_bounded_cause_and_next_action(isolated, cookie, monkeypatch, capsys, failure):
    calls = []
    monkeypatch.setattr(bootstrap, 'register_pages', lambda **_: calls.append(True))
    if failure == 'bad-cookie': cookie.write_text('generated invalid Cookie')
    else:
        owner = open_or_initialize_windows_user_stop()
        owner.root.rename(isolated/'preserved-owner')
    assert bootstrap.main(['--pixiv-cookie-file', str(cookie)]) == 2
    diagnostic = capsys.readouterr().err
    assert ('Cookieを読み込めません' if failure == 'bad-cookie' else 'Pixivの認証失敗ではありません') in diagnostic
    assert str(cookie) not in diagnostic and '55_generated' not in diagnostic and calls == []


def test_native_local_owner_failure_keeps_its_cause_for_recovery_screen(isolated, monkeypatch):
    calls, recovery, registered = _stub_native(monkeypatch)
    owner = open_or_initialize_windows_user_stop()
    owner.root.rename(isolated/'preserved-owner')
    assert bootstrap.main(['--native','--profile-root',str(isolated/'profile')]) == 0
    assert not registered and len(recovery) == 1
    result = recovery[0][0].auth_startup_result
    assert result.code == 'local_unavailable'
    assert 'Pixivの認証失敗ではありません' in result.message
