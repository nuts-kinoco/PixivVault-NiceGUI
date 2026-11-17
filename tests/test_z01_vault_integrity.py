"""Z01: authenticated corruption is not a separately protected legacy vault."""
import asyncio
import hashlib
import os
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto, VaultError
from pixivvault_nicegui.premium_gate import PremiumAuthorization
from test_external_vault import FixtureTransport
from test_premium_authorization import Protection


def prepared(tmp_path, *, remember=False):
    password=os.urandom(24).hex()
    salt=os.urandom(32)
    check=(salt,hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000),600000)
    transport=FixtureTransport()
    vault=ExternalVaultService(tmp_path/'vault',crypto=LegacyVaultCrypto(),transport=transport)
    gate=PremiumAuthorization(vault,protect=Protection(),verifier=check)
    vault.premium_authorization=gate
    gate.authenticate(password);gate.confirm(remember)
    gate.initialize_vault('17','synthetic-signing-key')
    vault.lock()
    gate._enabled=False
    return vault,gate,transport,password


def damage(vault,kind):
    path=vault.root/'generations'/vault._revision/'vault.enc'
    if kind=='missing':path.rename(path.with_suffix('.held'))
    elif kind=='corrupt':
        raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
    else:path.write_bytes(vault.crypto.encrypt({},vault.crypto._test_key))


@pytest.mark.parametrize('kind,code',[('corrupt','invalid-ciphertext'),('missing','invalid-generation'),('payload','invalid-vault')])
@pytest.mark.parametrize('derived',[False,True])
def test_integrity_failure_cannot_confirm_or_enable(tmp_path,kind,code,derived):
    vault,gate,transport,password=prepared(tmp_path,remember=derived)
    try:
        vault.crypto._test_key=__import__('base64').b64decode(gate._material['key'])
        if not derived:gate._material=None
        damage(vault,kind)
        for _ in range(2):
            with pytest.raises(VaultError,match=code):gate.authenticate(password)
            state=gate.status()
            assert not state['enabled'] and not state['pending'] and state['error']==code
            with pytest.raises(VaultError):gate.confirm(False)
            assert not vault.status()['open'] and transport.calls==[]
        assert (vault.root/'premium-activation-state').read_bytes()==b'off\n'
    finally:gate.close();vault.close()


@pytest.mark.parametrize('kind,code',[('corrupt','invalid-ciphertext'),('missing','invalid-generation')])
def test_saved_activation_restore_preserves_integrity_reason(tmp_path,kind,code):
    vault,gate,transport,password=prepared(tmp_path,remember=True)
    try:
        damage(vault,kind)
        restored=PremiumAuthorization(vault,protect=gate.protect,verifier=gate._verifier)
        try:
            assert not restored.status()['enabled'] and restored.status()['error']==code
            assert not vault.status()['open'] and transport.calls==[]
        finally:restored.close()
    finally:gate.close();vault.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,code',[('corrupt','invalid-ciphertext'),('missing','invalid-generation'),('wrong','premium-password-rejected')])
async def test_activation_gui_retains_reason_and_off_after_render(tmp_path,monkeypatch,kind,code):
    from nicegui import Client,ui,core
    from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    vault,gate,transport,password=prepared(tmp_path)
    client=Client(ui.page('/_z01_'+kind));view=None;task=None
    try:
        gate._material=None
        if kind!='wrong':damage(vault,kind)
        with client:view=ExternalVaultView(SimpleNamespace(external_vault=vault,premium_authorization=gate))
        task=asyncio.create_task(view.gate_dialog.request_enable());await asyncio.sleep(0)
        view.gate_dialog.password.set_value(os.urandom(24).hex() if kind=='wrong' else password)
        view.gate_dialog.password_dialog.submit(True)
        if kind=='wrong':
            for _ in range(300):
                if view.gate_dialog.dialog_error.text:break
                await asyncio.sleep(.01)
            assert not task.done() and 'パスワードが違います' in view.gate_dialog.dialog_error.text
            view.gate_dialog.password_dialog.submit(False)
            await asyncio.wait_for(task,10)
            assert 'OFF' in view.permission_status.text
            assert not gate.status()['pending'] and not vault.status()['open'] and transport.calls==[]
            assert view.gate_dialog.password.value==''
            return
        await asyncio.wait_for(task,10)
        expected='パスワードが一致しません' if kind=='wrong' else {'invalid-ciphertext':'鍵ファイルの復号を確認できません','invalid-generation':'保管庫の構造を確認できません'}[code]
        assert expected in view.gate_dialog.notice.text
        for _ in range(3):
            view.render()
            assert 'OFF' in view.permission_status.text
            assert expected in (view.gate_dialog.notice.text if kind=='wrong' else view.notice.text)
        assert not gate.status()['pending'] and not vault.status()['open'] and transport.calls==[]
        assert view.gate_dialog.password.value==''
    finally:
        if view:view.close()
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        client.delete();gate.close();vault.close()


@pytest.mark.asyncio
async def test_manual_legacy_unlock_corruption_stays_visible_and_disables_gate(tmp_path,monkeypatch):
    from nicegui import Client,ui,core
    from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    vault,gate,transport,password=prepared(tmp_path)
    client=Client(ui.page('/_z01_manual'));view=None
    try:
        damage(vault,'corrupt');gate._enabled=True
        with client:view=ExternalVaultView(SimpleNamespace(external_vault=vault,premium_authorization=gate))
        view.password.set_value(password)
        await view.action('unlock')
        for _ in range(3):
            view.render()
            assert '鍵ファイルの復号を確認できません' in view.notice.text
            assert 'OFF' in view.permission_status.text
        assert view.password.value=='' and transport.calls==[]
    finally:
        if view:view.close()
        client.delete();gate.close();vault.close()


def test_encrypted_old_json_payload_is_normalized_on_first_unlock(tmp_path):
    """Old JSON schema inside ciphertext; this is not plaintext-file discovery."""
    import base64
    vault,gate,transport,password=prepared(tmp_path)
    try:
        path=vault.root/'generations'/vault._revision/'vault.enc'
        key=base64.b64decode(gate._material['key'])
        old=dict(secret_key='synthetic-signing-key',shaft_uid=17,access_token='synthetic-token')
        path.write_bytes(vault.crypto.encrypt(old,key))
        gate._material=None
        gate.authenticate(password);gate.confirm(False)
        assert gate.restore_vault() and vault._data['shaft_uids']==[17]
        assert vault._data['slots']['17']['access_token']=='synthetic-token'
        assert 'shaft_uid' not in vault._data and transport.calls==[]
    finally:gate.close();vault.close()
