from types import SimpleNamespace

import pytest
from nicegui import Client, ui
from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog, vault_unlock_error_message
from pixivvault_nicegui.premium_read import unavailable_message
from test_premium_authorization import Protection, create, enable


@pytest.mark.asyncio
async def test_header_off_is_for_current_launch_and_saved_borrow_restores(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell

    root = tmp_path / 'vault'
    protection = Protection()
    vault, gate, transport = create(root, protection, [1000])
    enable(gate, True)
    gate.initialize_vault('1', 'generated-signing-key')
    services = ApplicationServices()
    services.external_vault = vault
    services.premium_authorization = gate
    services.default_uid_path = None
    client = Client(ui.page('/smalldec-header-pause'))
    try:
        with client:
            shell = DemoShell(services)
            try:
                assert shell.borrow_switch.value
                await shell.borrow_changed(SimpleNamespace(value=False))
                assert not shell.borrow_switch.value
                assert not vault.status()['open']
                assert '次回起動時は ON に戻ります' in shell.borrow_dialog.notice.text
                assert transport.calls == []
            finally:
                shell.close()
    finally:
        client.delete()
        gate.close()
        services.close()

    vault, gate, transport = create(root, protection, [1000])
    try:
        assert gate.status()['enabled']
        assert vault.status()['open']
        assert transport.calls == []
    finally:
        gate.close()
        vault.close()


def test_benchmark_startup_override_preserves_saved_borrow_configuration(tmp_path, monkeypatch):
    root = tmp_path / 'vault'
    protection = Protection()
    vault, gate, _ = create(root, protection, [1000])
    enable(gate, True)
    gate.initialize_vault('1', 'generated-signing-key')
    gate.close()
    vault.close()
    monkeypatch.setenv('PIXIVVAULT_PREMIUM_STARTUP', 'off')
    vault, gate, transport = create(root, protection, [1000])
    try:
        assert not gate.status()['enabled']
        assert not vault.status()['open']
        assert transport.calls == []
    finally:
        gate.close()
        vault.close()
    monkeypatch.delenv('PIXIVVAULT_PREMIUM_STARTUP')
    vault, gate, transport = create(root, protection, [1000])
    try:
        assert gate.status()['enabled'] and vault.status()['open']
        assert transport.calls == []
    finally:
        gate.close()
        vault.close()


@pytest.mark.asyncio
async def test_missing_configuration_shows_an_entry_without_opening_popup(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.shell import DemoShell

    vault, gate, transport = create(tmp_path / 'vault', Protection(), [1000])
    services = ApplicationServices()
    services.external_vault = vault
    services.premium_authorization = gate
    services.default_uid_path = None
    client = Client(ui.page('/smalldec-header-setup'))
    try:
        with client:
            shell = DemoShell(services)
            try:
                dialog = shell.borrow_dialog
                assert not shell.borrow_switch.value
                assert not dialog.password_dialog.value
                assert '借用プレミアムを使うには初期設定が必要です' in dialog.notice.text
                entry = next(element for element in shell.root.descendants()
                             if element.props.get('data-testid') == 'premium-setup-shell')
                assert entry.visible
                assert transport.calls == []
            finally:
                shell.close()
    finally:
        client.delete()
        gate.close()
        services.close()


@pytest.mark.parametrize('code', ['busy', 'payout-limit', 'account-cooldown', 'borrow-accounts-cooling-down'])
def test_busy_and_budget_guidance_asks_user_to_retry_later(code):
    assert 'しばらくしてから再試行' in unavailable_message(code)
    assert 'しばらくしてから再試行' in vault_unlock_error_message(code)
