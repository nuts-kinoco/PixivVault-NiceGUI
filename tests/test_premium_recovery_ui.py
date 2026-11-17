"""Leaving the unconfirmed-result state from the borrow popup and from Settings.

The Shaft tab that used to hold "re-check saved state / restore recorded result /
update account" is gone; these tests drive the same operations from the popup.
"""
import asyncio
import hashlib
import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.premium_gate import PremiumAuthorization
from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog
from test_premium_authorization import Protection
from test_oauth_send_boundary import reopened
from test_vault_known_result_recovery import interrupted_result, no_network  # noqa: F401  (autouse guard)

PASSWORD = 'generated-password'
SALT = b'generated-salt-for-test-only-32!!'
VERIFIER = (SALT, hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), SALT, 600000), 600000)


async def wait_for(condition, task=None, limit=500):
    for _ in range(limit):
        if (task is not None and task.done()) or condition():
            return
        await asyncio.sleep(.01)


class Setup:
    def __init__(self, tmp_path, monkeypatch, *, recoverable=True):
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        from pixivvault_nicegui import external_vault_autounlock
        self.protection = Protection()
        monkeypatch.setattr(external_vault_autounlock, '_dpapi', self.protection)
        v, self.wire, self.scope = interrupted_result(tmp_path, monkeypatch)
        self.vault = reopened(v)
        self.gate = PremiumAuthorization(self.vault, protect=self.protection, clock=lambda: 1000, verifier=VERIFIER)
        self.vault.premium_authorization = self.gate
        self.keeper = []
        monkeypatch.setattr(self.vault, 'start_keeper', lambda **kw: self.keeper.append(1))
        self.calls = list(self.wire.calls)
        self.client = Client(ui.page('/_recovery_ui_%d' % id(self)))
        with self.client:
            self.dialog = PremiumGateDialog(self.gate, default_uid_path=None)

    async def turn_on(self):
        task = asyncio.create_task(self.dialog.request_enable())
        await asyncio.sleep(0)
        self.dialog.password.set_value(PASSWORD)
        self.dialog.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)

    def close(self):
        self.dialog.close()
        self.client.delete()
        self.gate.close()
        self.vault.close()


@pytest.mark.asyncio
async def test_unconfirmed_then_recover_then_on_again_starts_keeper(tmp_path, monkeypatch):
    s = Setup(tmp_path, monkeypatch)
    try:
        assert s.vault.status()['oauth_review_required']
        await s.turn_on()
        # ON is allowed but traffic stays stopped, and the popup offers the way out.
        assert s.gate.status()['enabled'] and s.keeper == []
        assert '停止したまま' in s.dialog.notice.text
        panel = s.dialog.recovery
        assert panel.recover_button.enabled and panel.reopen_button.enabled
        assert not panel.refresh_button.enabled  # the original blocks the update while unconfirmed
        # a wrong password changes nothing and keeps the permission and the open vault
        panel.password.set_value('generated-wrong')
        await panel.action('recover_known')
        assert s.vault.status()['oauth_review_required'] and s.gate.status()['enabled']
        assert s.vault.status()['open'] and panel.password.value == ''
        # restoring the recorded result clears the unconfirmed state without sending anything
        panel.password.set_value(PASSWORD)
        await panel.action('recover_known')
        assert not s.vault.status()['oauth_review_required']
        assert '復元しました' in panel.result.text and list(s.wire.calls) == s.calls
        assert s.keeper == []  # recovery itself starts nothing
        assert not panel.recover_button.enabled and panel.refresh_button.enabled
        # turning it OFF and ON again now starts the keeper
        s.gate.disable()
        await s.turn_on()
        assert s.gate.status()['enabled'] and s.vault.status()['open'] and s.keeper == [1]
        assert '停止したまま' not in s.dialog.notice.text
    finally:
        s.close()


@pytest.mark.asyncio
async def test_reopen_saved_state_keeps_unconfirmed_when_still_pending(tmp_path, monkeypatch):
    s = Setup(tmp_path, monkeypatch)
    try:
        await s.turn_on()
        panel = s.dialog.recovery
        panel.password.set_value(PASSWORD)
        await panel.action('reopen_saved')
        assert s.vault.status()['oauth_review_required'] and s.vault.status()['open']
        assert '未確認のまま' in panel.result.text and s.keeper == [] and list(s.wire.calls) == s.calls
    finally:
        s.close()


@pytest.mark.asyncio
async def test_recovery_while_off_leaves_the_vault_locked(tmp_path, monkeypatch):
    s = Setup(tmp_path, monkeypatch)
    try:
        assert not s.gate.status()['enabled']
        panel = s.dialog.recovery
        panel.password.set_value(PASSWORD)
        await panel.action('recover_known')
        assert not s.vault.status()['oauth_review_required']
        assert not s.vault.status()['open'] and not s.gate.status()['enabled']
        await s.turn_on()
        assert s.keeper == [1]
    finally:
        s.close()


@pytest.mark.asyncio
async def test_popup_and_toggle_show_recovery_only_while_unconfirmed(tmp_path, monkeypatch):
    s = Setup(tmp_path, monkeypatch)
    try:
        d = s.dialog
        assert d.recovery_visible()
        panel = d.recovery
        panel.password.set_value(PASSWORD)
        await panel.action('recover_known')
        assert not d.recovery_visible()
    finally:
        s.close()


@pytest.mark.asyncio
async def test_settings_section_offers_the_same_operations(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    s = Setup(tmp_path, monkeypatch)
    try:
        class Store:
            def snapshot(self):
                raise RuntimeError('settings are not needed here')
        with s.client:
            view = PreferencesView(Store(), vault=s.vault)
            view.open()
        panel = view.vault_recovery
        assert panel.recover_button.enabled and panel.reopen_button.enabled and not panel.refresh_button.enabled
        panel.password.set_value(PASSWORD)
        await panel.action('recover_known')
        assert not s.vault.status()['oauth_review_required'] and list(s.wire.calls) == s.calls
        assert not panel.recover_button.enabled and not s.vault.status()['open']  # OFF keeps it locked, so no update either
        state = [x for x in view.dialog.descendants() if getattr(x, 'props', {}).get('data-testid') == 'premium-vault-state']
        assert state and '確認が必要' not in state[0].text
        view.closed = True
    finally:
        s.close()


@pytest.mark.asyncio
async def test_header_shows_recovery_button_only_while_unconfirmed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pixivvault_nicegui.ui.shell import DemoShell
    from test_external_vault_ui import setup
    services, v, t, _ = setup(tmp_path)
    s = None
    client = None
    try:
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        from pixivvault_nicegui import external_vault_autounlock
        protection = Protection()
        monkeypatch.setattr(external_vault_autounlock, '_dpapi', protection)
        v.lock()
        monkeypatch.setattr(v, 'start_keeper', lambda **kw: None)
        gate = PremiumAuthorization(v, protect=protection, clock=lambda: 1000, verifier=VERIFIER)
        v.premium_authorization = gate
        services.premium_authorization = gate
        services.default_uid_path = None
        client = Client(ui.page('/_recovery_header'))
        with client:
            shell = DemoShell(services)
        button = shell.borrow_recovery_button
        assert button is not None and not button.visible
        with v._mutex:
            import copy
            v.unlock('fixture-password') if not v.status()['open'] else None
        data = copy.deepcopy(v._snapshot()[1])
        data['slots'][str(data['shaft_uids'][0])]['oauth_send_pending'] = True
        v._publish(v._snapshot()[0], data)
        shell.sync_borrow()
        assert button.visible
        s = shell
    finally:
        if s:
            s.close()
        if client:
            client.delete()
        v.close()
