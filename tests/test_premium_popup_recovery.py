"""Premium ON popup recovery: default UID file, uncertain send, legacy vault password."""
import asyncio
import copy
import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.premium_gate import PremiumGateError
from pixivvault_nicegui.ui.premium_gate_view import (
    PremiumGateDialog, DEFAULT_UID_PATH, vault_unlock_error_message)
from test_premium_authorization import create, Protection, TEST_PASSWORD

LEGACY_PASSWORD='generated-earlier-vault-password'


class Harness:
    def __init__(self, tmp_path, monkeypatch, default_text=b'11111\n'):
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        from pixivvault_nicegui import external_vault_autounlock
        self.protection = Protection()
        monkeypatch.setattr(external_vault_autounlock, '_dpapi', self.protection)
        self.now = [1000]
        self.vault, self.gate, self.transport = create(tmp_path/'vault', self.protection, self.now)
        self.keeper = []
        monkeypatch.setattr(self.vault, 'start_keeper', lambda **kw: self.keeper.append(1))
        self.default = tmp_path/'default.txt'
        self.default.write_bytes(default_text)
        self.client = Client(ui.page('/_recovery_%d' % id(self)))
        self.dialog = None

    def open_dialog(self, default_uid_path='unset'):
        path = self.default if default_uid_path == 'unset' else default_uid_path
        with self.client:
            self.dialog = PremiumGateDialog(self.gate, default_uid_path=path)
        return self.dialog

    async def wait_for(self, condition, task):
        for _ in range(500):
            if task.done() or condition():
                return
            await asyncio.sleep(.01)

    async def submit(self, password, secret='', payload=None, legacy=None, remember=False):
        d = self.dialog
        task = asyncio.create_task(d.request_enable())
        await asyncio.sleep(0)
        d.password.set_value(password)
        d.secret.set_value(secret)
        if payload is not None:
            assert d._load_uid_bytes(payload)
        d.password_dialog.submit(True)
        if legacy is not None:
            await self.wait_for(lambda: d.legacy_box.visible, task)
            assert d.legacy_box.visible and not d.main_box.visible
            d.legacy.set_value(legacy)
            d.password_dialog.submit(True)
        await asyncio.wait_for(task, 10)

    def close(self):
        if self.dialog:
            self.dialog.close()
        self.client.delete()
        self.gate.close()
        self.vault.close()


@pytest.mark.asyncio
async def test_default_file_replaces_saved_uids_of_initialized_vault_like_the_original(tmp_path, monkeypatch):
    # Reverses the earlier "never overwrite" rule: the original treats the fixed
    # UID file as the authority every time the dialog opens (gui.py:859 and the dialog).
    h = Harness(tmp_path, monkeypatch)
    try:
        h.open_dialog()  # production path: default_uid_path is a real, readable file (11111)
        await h.submit(TEST_PASSWORD, 'first-key', b'12345\n67890')
        assert h.vault._snapshot()[1]['shaft_uids'] == [12345, 67890]  # a picked file wins on first setup
        h.gate.disable()
        await h.submit(TEST_PASSWORD)  # user only types the password
        after = h.vault._snapshot()[1]
        assert h.gate.status()['enabled'] and h.vault.status()['open']
        assert after['shaft_uids'] == [11111] and sorted(after['slots']) == ['11111']
        assert after['secret_key'] == 'first-key'
    finally:
        h.close()


@pytest.mark.asyncio
async def test_default_file_is_read_every_time_the_popup_opens(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.open_dialog()
        await h.submit(TEST_PASSWORD, 'first-key', b'12345')
        h.gate.disable()
        calls = []
        original = h.dialog._load_default_uids
        monkeypatch.setattr(h.dialog, '_load_default_uids', lambda: calls.append(1) or original())
        await h.submit(TEST_PASSWORD)
        assert calls == [1]
    finally:
        h.close()


@pytest.mark.asyncio
async def test_default_file_prefills_first_time_initialization_only(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, default_text=b'11111\n22222\n')
    try:
        h.open_dialog()
        await h.submit(TEST_PASSWORD, 'first-key')  # no file picked: default pre-fill is used
        assert h.vault._snapshot()[1]['shaft_uids'] == [11111, 22222]
    finally:
        h.close()


@pytest.mark.asyncio
async def test_explicit_signing_key_without_file_keeps_saved_uids(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.open_dialog(None)  # no fixed UID file: the saved UIDs stay
        await h.submit(TEST_PASSWORD, 'first-key', b'12345\n67890')
        h.gate.disable()
        await h.submit(TEST_PASSWORD, 'second-key')
        data = h.vault._snapshot()[1]
        assert data['secret_key'] == 'second-key' and data['shaft_uids'] == [12345, 67890]
    finally:
        h.close()


def test_default_uid_path_is_one_overridable_constant():
    import inspect
    from pixivvault_nicegui.ui import shell
    assert DEFAULT_UID_PATH == r'S:\Projects\PixivVault\nana7mi_uids.txt'
    assert DEFAULT_UID_PATH not in inspect.getsource(shell)
    assert inspect.signature(PremiumGateDialog).parameters['default_uid_path'].default == DEFAULT_UID_PATH


@pytest.mark.asyncio
async def test_signing_key_input_is_recreated_each_time_popup_opens(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        d = h.open_dialog(None)
        ids = {d.secret.id}
        for _ in range(2):
            task = asyncio.create_task(d.request_enable())
            await asyncio.sleep(0)
            ids.add(d.secret.id)
            d.password.set_value('wrong')
            d.secret.set_value('typed-signing-key')
            d.password_dialog.submit(True)
            await Harness.wait_for(None, lambda: 'パスワードが違います' in d.dialog_error.text, task)
            assert not task.done() and d.secret.value == 'typed-signing-key'  # kept while the dialog stays open
            d.password_dialog.submit(False)
            await asyncio.wait_for(task, 10)
            assert d.secret.value == ''
            h.gate._atomic('attempts.json', b'{"failures":0,"next_at":0}')
        assert len(ids) >= 3
    finally:
        h.close()


@pytest.mark.asyncio
async def test_uncertain_send_does_not_block_on_and_explains_when_it_does(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.open_dialog()
        await h.submit(TEST_PASSWORD, 'first-key', b'12345\n67890')
        with h.vault._mutex:
            data = copy.deepcopy(h.vault._data)
            data['slots']['12345']['oauth_send_pending'] = True
            h.vault._commit(data, h.vault._key, h.vault._meta, h.vault._revision)
            h.vault._data = data
        h.gate.disable()
        h.keeper.clear()
        await h.submit(TEST_PASSWORD)  # plain ON works and starts nothing
        assert h.gate.status()['enabled'] and h.vault.status()['open'] and h.keeper == []
        assert '停止したまま' in h.dialog.notice.text
        assert h.vault._snapshot()[1]['slots']['12345']['oauth_send_pending'] is True
        h.gate.disable()
        await h.submit(TEST_PASSWORD, 'new-key')  # explicit change is refused with an explanation
        assert not h.gate.status()['enabled']
        text = h.dialog.notice.text
        assert 'OFFのまま' in text and '変更を加えていません' in text and text.startswith(vault_unlock_error_message('pixiv-outcome-uncertain'))
        h.vault.unlock(TEST_PASSWORD)  # inspect stored state locally; nothing was discarded
        assert h.vault._snapshot()[1]['slots']['12345']['oauth_send_pending'] is True
        assert h.vault._snapshot()[1]['secret_key'] == 'first-key'
    finally:
        h.close()


@pytest.mark.asyncio
async def test_legacy_vault_password_second_field_enables(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.vault.initialize(LEGACY_PASSWORD, '12345', 'legacy-key')
        h.vault.lock()
        d = h.open_dialog(None)
        assert not d.legacy_box.visible
        await h.submit(TEST_PASSWORD, legacy=LEGACY_PASSWORD)
        assert h.gate.status()['enabled'] and h.vault.status()['open']
        assert h.vault._snapshot()[1]['secret_key'] == 'legacy-key'
        assert not d.legacy_box.visible and d.main_box.visible and d.legacy is None
        assert 'ON' in d.notice.text
        # OFF removes the derived material, so the earlier password is asked again
        h.gate.disable()
        await h.submit(TEST_PASSWORD, legacy=LEGACY_PASSWORD)
        assert h.gate.status()['enabled']
    finally:
        h.close()


@pytest.mark.asyncio
async def test_legacy_wrong_vault_password_is_specific_and_backs_off(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.vault.initialize(LEGACY_PASSWORD, '12345', 'legacy-key')
        h.vault.lock()
        d = h.open_dialog(None)
        await h.submit(TEST_PASSWORD, legacy='generated-wrong-vault-password')
        assert not h.gate.status()['enabled'] and not h.vault.status()['open']
        assert d.notice.text.startswith(vault_unlock_error_message('premium-vault-password-rejected'))
        assert 'generated-wrong-vault-password' not in d.notice.text and TEST_PASSWORD not in d.notice.text
        assert h.gate.status()['retry_after'] > 0 and h.gate._attempts()['failures'] == 1
        assert not h.transport.calls and not d.legacy_box.visible and d.legacy is None
    finally:
        h.close()


@pytest.mark.asyncio
async def test_legacy_wrong_vault_password_backoff_escalates(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.vault.initialize(LEGACY_PASSWORD, '12345', 'legacy-key')
        h.vault.lock()
        h.open_dialog(None)
        waits = []
        for expected in (1, 2, 3):
            await h.submit(TEST_PASSWORD, legacy='generated-wrong-vault-password')
            assert h.gate._attempts()['failures'] == expected and not h.gate.status()['enabled']
            waits.append(h.gate.status()['retry_after'])
            h.now[0] += 1000
        assert waits[0] < waits[1] < waits[2]
        await h.submit(TEST_PASSWORD, legacy=LEGACY_PASSWORD)
        assert h.gate.status()['enabled'] and h.vault.status()['open'] and h.gate._attempts()['failures'] == 0
    finally:
        h.close()


@pytest.mark.asyncio
async def test_legacy_cancel_leaves_everything_off(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    try:
        h.vault.initialize(LEGACY_PASSWORD, '12345', 'legacy-key')
        h.vault.lock()
        d = h.open_dialog(None)
        task = asyncio.create_task(d.request_enable())
        await asyncio.sleep(0)
        d.password.set_value(TEST_PASSWORD)
        d.password_dialog.submit(True)
        await h.wait_for(lambda: d.legacy_box.visible, task)
        assert d.legacy_box.visible
        d.password_dialog.submit(False)
        await asyncio.wait_for(task, 10)
        assert not h.gate.status()['enabled'] and not h.vault.status()['open']
    finally:
        h.close()


def test_unlock_legacy_requires_prior_authentication(tmp_path):
    v, g, _ = create(tmp_path/'v', Protection(), [1000])
    try:
        v.initialize(LEGACY_PASSWORD, '12345', 'k')
        v.lock()
        with pytest.raises(PremiumGateError, match='premium-confirmation-required'):
            g.unlock_legacy(LEGACY_PASSWORD)
        assert not v.status()['open']
    finally:
        g.close()
        v.close()


def test_messages_no_longer_point_to_removed_shaft_tab():
    from pixivvault_nicegui.premium_read import unavailable_message
    codes = ('not-premium', 'expired-payout', 'borrow-accounts-unavailable', 'borrow-accounts-cooling-down',
             'pixiv-network-stopped', 'pixiv-outcome-uncertain')
    for code in codes:
        text = unavailable_message(code)
        assert text and 'Shaft連携' not in text and '外部連携' not in text
        assert '借用プレミアム' in text
    # shown while the switch may be ON: must not claim the switch is OFF
    assert 'OFFのまま' not in unavailable_message('pixiv-outcome-uncertain')
    text = unavailable_message('borrow-accounts-unavailable', reason='not-premium')
    assert 'Shaft連携' not in text and '借用プレミアム' in text
