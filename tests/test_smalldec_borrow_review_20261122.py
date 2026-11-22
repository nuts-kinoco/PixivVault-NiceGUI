import asyncio
import threading
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui import external_vault_autounlock, premium_gate
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell
from test_external_vault import FixtureCrypto, FixtureTransport
from test_premium_authorization import CHECK, Protection, create, enable


class PayoutTransport(FixtureTransport):
    def __init__(self):
        super().__init__()
        self.paid = threading.Event()

    def bind_request_budget(self, reserve):
        self.reserve = reserve

    def fetch_account(self, uid, secret, slot, now):
        self.reserve('payout', uid)
        result = super().fetch_account(uid, secret, slot, now)
        self.paid.set()
        return result


@pytest.fixture
def app_vault(tmp_path, monkeypatch):
    root = tmp_path / 'vault'
    protection = Protection()
    monkeypatch.delenv('PIXIVVAULT_PREMIUM_STARTUP', raising=False)
    monkeypatch.setattr(premium_gate, 'SALT', CHECK[0])
    monkeypatch.setattr(premium_gate, 'VERIFIER', CHECK[1])
    monkeypatch.setattr(premium_gate, 'ITERATIONS', CHECK[2])
    monkeypatch.setattr(premium_gate, '_dpapi', protection)
    monkeypatch.setattr(external_vault_autounlock, '_dpapi', protection)

    def launch():
        transport = PayoutTransport()
        services = ApplicationServices(external_vault_root=root, external_vault_transport=transport,
                                       external_vault_crypto=FixtureCrypto())
        services.default_uid_path = None
        return services, transport

    return root, protection, launch


@pytest.mark.parametrize('session_only', [True, False], ids=['session', 'persistent'])
@pytest.mark.asyncio
async def test_shell_off_choice_stops_real_service_keeper_and_controls_restart(app_vault, session_only):
    root, protection, launch = app_vault
    vault, gate, _ = create(root, protection, [1000])
    enable(gate, True)
    gate.initialize_vault('1', 'fixture-signing-key')
    gate.close()
    vault.close()

    services, transport = launch()
    vault = services.external_vault
    keeper = vault._keeper
    client = Client(ui.page('/review-premium-off'))
    try:
        assert keeper is not None
        assert await asyncio.to_thread(transport.paid.wait, 5)
        assert vault._request_budget.payout_limit == 10
        assert vault._request_budget.payout_window == 5 * 3600
        with client:
            shell = DemoShell(services)
            try:
                assert shell.borrow_switch.value
                await shell.borrow_changed(SimpleNamespace(value=False))
                assert shell.borrow_switch.value
                dialog = shell.borrow_dialog
                assert dialog.off_choices.visible
                await dialog.request_disable(session_only=session_only)
                assert not dialog.off_choices.visible
                assert not shell.borrow_switch.value and not vault.status()['open']
                await asyncio.to_thread(keeper.join, 5)
                assert not keeper.is_alive()
                assert len(transport.calls) == 1
                if session_only:
                    await asyncio.wait_for(shell.borrow_changed(SimpleNamespace(value=True)), 5)
                    assert shell.borrow_switch.value and not dialog.password_dialog.value
                    resumed_keeper = vault._keeper
                    assert resumed_keeper is not keeper and resumed_keeper.is_alive()
                    await shell.borrow_changed(SimpleNamespace(value=False))
                    await dialog.request_disable(session_only=True)
                    await asyncio.to_thread(resumed_keeper.join, 5)
                    assert not resumed_keeper.is_alive()
                    assert len(transport.calls) == 1
            finally:
                shell.close()
    finally:
        services.close()
        client.delete()
        await asyncio.to_thread(keeper.join, 5)

    services, transport = launch()
    try:
        assert services.premium_authorization.status()['enabled'] is session_only
        if session_only:
            assert services.external_vault._keeper is not None
        else:
            assert services.external_vault._keeper is None and transport.calls == []
            assert not (root / 'premium-material.dpapi').exists()
    finally:
        vault = services.external_vault
        services.close()
        if vault._keeper is not None:
            await asyncio.to_thread(vault._keeper.join, 5)


@pytest.mark.parametrize('mode', ['off', ' OFF ', '0'])
def test_benchmark_override_never_starts_service_keeper(app_vault, monkeypatch, mode):
    root, protection, launch = app_vault
    vault, gate, _ = create(root, protection, [1000])
    enable(gate, True)
    gate.initialize_vault('1', 'fixture-signing-key')
    gate.close()
    vault.close()
    monkeypatch.setenv('PIXIVVAULT_PREMIUM_STARTUP', mode)
    services, transport = launch()
    try:
        assert not services.premium_authorization.status()['enabled']
        assert services.external_vault._keeper is None and transport.calls == []
        assert (root / 'premium-material.dpapi').is_file()
    finally:
        vault = services.external_vault
        services.close()
        if vault._keeper is not None:
            vault._keeper.join(5)


@pytest.mark.parametrize('revoked', [False, True], ids=['missing-proof', 'old-off'])
@pytest.mark.asyncio
async def test_locked_initialized_vault_off_has_non_modal_setup(app_vault, revoked):
    root, protection, launch = app_vault
    vault, gate, _ = create(root, protection, [1000])
    vault.initialize('fixture-vault-password', '1', 'fixture-signing-key')
    if revoked:
        gate.disable()
    gate.close()
    vault.close()
    services, transport = launch()
    client = Client(ui.page('/review-premium-setup'))
    try:
        with client:
            shell = DemoShell(services)
            try:
                assert not shell.borrow_switch.value
                assert not shell.borrow_dialog.password_dialog.value
                assert shell.borrow_setup_button.visible
                assert shell.borrow_dialog.notice.text
                assert services.external_vault._keeper is None and transport.calls == []
            finally:
                shell.close()
    finally:
        client.delete()
        services.close()
