import asyncio
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.desktop_integration import DesktopActionRejected, DesktopIntegration, WindowsProtocolRegistration
from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from test_display_recovery_batch import Clock
from test_external_vault_ui import drain_personal_view, setup
from test_shared_get_recovery import rows
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner


class MemoryRegistry:
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {'Software': {}, 'Software\\Classes': {}}

    def OpenKey(self, _root, path, *_options):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return nullcontext(path)

    def CreateKeyEx(self, _root, path, *_options):
        segments = path.split('\\')
        for count in range(1, len(segments) + 1):
            self.keys.setdefault('\\'.join(segments[:count]), {})
        return nullcontext(path)

    def QueryInfoKey(self, path):
        prefix = path + '\\'
        children = sum(key.startswith(prefix) and '\\' not in key[len(prefix):] for key in self.keys)
        return children, len(self.keys[path]), 0

    def QueryValueEx(self, path, name):
        try:
            return self.keys[path][name]
        except KeyError:
            raise FileNotFoundError(name) from None

    def SetValueEx(self, path, name, _reserved, kind, value):
        self.keys[path][name] = (value, kind)

    def DeleteValue(self, path, name):
        self.QueryValueEx(path, name)
        del self.keys[path][name]

    def DeleteKey(self, _root, path):
        assert self.QueryInfoKey(path)[0] == 0
        del self.keys[path]


def test_uri_registration_dry_run_restores_previous_command_and_refuses_external_change(tmp_path):
    registry = MemoryRegistry()
    protocol = WindowsProtocolRegistration(f'"{tmp_path / "generated.exe"}" "%1"', registry=registry)
    command_key = protocol.KEY + r'\shell\open\command'
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, command_key) as key:
        registry.SetValueEx(key, '', 0, registry.REG_SZ, 'generated legacy command')
    original = deepcopy(registry.keys)
    desktop = DesktopIntegration(protocol=protocol)
    assert not protocol.is_registered()
    with pytest.raises(DesktopActionRejected):
        desktop.set_uri_registration(True)
    assert registry.keys == original
    desktop.set_uri_registration(True, confirmed=True)
    receipt = deepcopy(registry.keys)
    assert protocol.is_registered()
    assert registry.QueryValueEx(command_key, '') == (protocol.command, registry.REG_SZ)
    registry.SetValueEx(command_key, '', 0, registry.REG_SZ, 'generated external change')
    foreign = deepcopy(registry.keys)
    with pytest.raises(DesktopActionRejected):
        desktop.set_uri_registration(False, confirmed=True)
    assert registry.keys == foreign
    registry.keys = receipt
    desktop.set_uri_registration(False, confirmed=True)
    assert not protocol.is_registered() and registry.keys == original


@pytest.mark.asyncio
async def test_settings_429_wait_reaches_guarded_ranking_get(tmp_path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []

    def wire(*_):
        calls.append(len(calls) + 1)
        if len(calls) % 2:
            raise HTTPStatusRejected(429)
        return json.dumps({'contents': rows()}).encode()

    reader = scope.create_reader(cookie, transport=wire)
    reader._verified_identity = '55'
    services = ApplicationServices(1, preferences_root=tmp_path / 'preferences')
    client = Client(ui.page('/_p4_429_settings'))
    try:
        services.live_client = reader
        services._configure_request_preferences()
        clock = Clock()
        services._request_retry.clock = lambda: clock.now
        services._request_retry.sleep = clock.sleep
        with client:
            view = PreferencesView(services.preferences, enabled_keys=services.preference_consumers())
            view.open()
            for value in ('3.0', '10.0'):
                clock.waits.clear()
                view.controls['api_retry_wait'].set_value(value)
                assert view.save()
                assert len(reader.get_ranking()) == 50
                assert sum(clock.waits) == pytest.approx(float(value))
            assert calls == [1, 2, 3, 4]
            assert not scope.gate.snapshot().blocked
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(('code', 'expected'), [('busy', '準備中'),
                                                  ('pixiv-network-stopped', '通信の停止状態'),
                                                  ('transport-failed', '理由: transport-failed')])
async def test_personal_search_distinguishes_failure_and_keeps_last_result(tmp_path, monkeypatch, code, expected):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault, transport, pins = setup(tmp_path)
    client = Client(ui.page('/_p4_z04_failure_' + code))
    view = initial_task = None
    try:
        with client:
            view = PersonalRankingView(services)
            view.set_active(True)
            initial_task = view._context_task
            view.add_tag('tag')
            await view.load()
            result = view.page
            assert result is not None and '123' in view.gallery.cards

            def fail(*_args, **_kwargs):
                raise VaultError(code)

            monkeypatch.setattr(view.source, 'fetch', fail)
            await view.load()
            assert expected in view.notice.text
            assert view.page is result and '123' in view.gallery.cards
    finally:
        try:
            await drain_personal_view(view, initial_task)
        finally:
            client.delete()
            vault.close()
            services.close()
