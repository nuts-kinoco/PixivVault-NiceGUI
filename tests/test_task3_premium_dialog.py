import asyncio
from types import SimpleNamespace
import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog
from pixivvault_nicegui.ui.shell import NAVIGATION, AVAILABLE_TABS
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.preferences import PreferencesStore
from test_premium_authorization import create, Protection, TEST_PASSWORD


def test_navigation_has_no_external():
    assert 'external' not in dict(NAVIGATION) and 'external' not in AVAILABLE_TABS


@pytest.mark.asyncio
async def test_dialog_initializes_updates_and_rejects_wrong_password(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    from pixivvault_nicegui import external_vault_autounlock
    protection=Protection()
    monkeypatch.setattr(external_vault_autounlock, '_dpapi', protection)
    vault, gate, transport = create(tmp_path/'vault', protection, [1000])
    monkeypatch.setattr(vault, 'start_keeper', lambda **kw: None)
    client=Client(ui.page('/_task3_dialog')); dialog=None
    async def submit(password, secret, payload, remember=False):
        task=asyncio.create_task(dialog.request_enable()); await asyncio.sleep(0)
        dialog.password.set_value(password); dialog.secret.set_value(secret)
        assert dialog._load_uid_bytes(payload)
        dialog.password_dialog.submit(True)
        for _ in range(500):
            if task.done() or dialog.save_dialog.value: break
            await asyncio.sleep(.01)
        if dialog.save_dialog.value: dialog.save_dialog.submit(remember)
        await asyncio.wait_for(task, 10)
    try:
        with client: dialog=PremiumGateDialog(gate, default_uid_path=None)
        await submit(TEST_PASSWORD, 'first-key', b'12345\n67890')
        assert vault.status()['open'] and gate.status()['enabled']
        assert vault._snapshot()[1]['shaft_uids']==[12345,67890]
        assert vault._snapshot()[1]['secret_key']=='first-key'
        gate.disable()
        await submit(TEST_PASSWORD, 'second-key', b'54321')
        assert vault._snapshot()[1]['shaft_uids']==[54321]
        assert vault._snapshot()[1]['secret_key']=='second-key'
        gate.disable()
        await submit('incorrect', 'bad-key', b'99999')
        assert not gate.status()['enabled'] and not vault.status()['open']
        assert '\u4e00\u81f4\u3057\u307e\u305b\u3093' in dialog.notice.text
        assert not transport.calls
        assert not dialog._load_uid_bytes(b'12345\nsecret-value')
        assert '2' in dialog.uid_notice.text and 'secret-value' not in dialog.uid_notice.text
    finally:
        if dialog: dialog.close()
        client.delete();gate.close();vault.close()


def test_preferences_status_displays_only_budget(tmp_path):
    class Vault:
        def status(self): return {'open': True, 'initialized': True, 'secret_key':'secret-value'}
        def premium_pool_status(self): return [dict(index=1,payouts=2,payout_limit=5,borrowed=1,borrow_limit=4,wait=0,uid='123456')]
    client=Client(ui.page('/_task3_status')); view=None
    try:
        with client:
            view=PreferencesView(PreferencesStore(tmp_path/'prefs'),vault=Vault())
            view.open()
        texts=[getattr(element,'text','') for element in view.dialog.descendants()]
        visible=' '.join(str(text) for text in texts)
        assert '\u501f\u7528\u30d7\u30ec\u30df\u30a2\u30e0\u306e\u72b6\u614b' in visible and '2/5' in visible
        assert 'secret-value' not in visible and '123456' not in visible
    finally:
        if view:view.close()
        client.delete()


@pytest.mark.asyncio
async def test_uid_default_and_upload_show_count_only(tmp_path):
    from test_n45a_f26a_recovery import fixture
    password, now, vault, gate, transport=fixture(tmp_path)
    path=tmp_path/'list.txt';path.write_bytes(b'12345\n67890\n')
    client=Client(ui.page('/_task3_uid')); dialog=None
    class Upload:
        name='list.txt'
        async def read(self): return b'77777\n88888'
    try:
        with client:dialog=PremiumGateDialog(gate,default_uid_path=path)
        dialog._load_default_uids()
        assert '2' in dialog.uid_notice.text and '12345' not in dialog.uid_notice.text
        await dialog.pick_uid_file(SimpleNamespace(file=Upload()))
        assert dialog._uids==[77777,88888]
        assert '2' in dialog.uid_notice.text and '77777' not in dialog.uid_notice.text
    finally:
        if dialog:dialog.close()
        client.delete();gate.close();vault.close()
