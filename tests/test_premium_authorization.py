"""Generated passwords and keys only; production password is never a fixture."""
import asyncio
import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pixivvault_nicegui.external_vault import ExternalVaultService,VaultError
from pixivvault_nicegui.premium_gate import PremiumAuthorization,PremiumGateError
from test_external_vault import FixtureCrypto,FixtureTransport

TEST_PASSWORD='generated-activation-passphrase'
SALT=b'generated-salt-for-test-only-32!!'
CHECK=(SALT,hashlib.pbkdf2_hmac('sha256',TEST_PASSWORD.encode(),SALT,600000),600000)

class Protection:
    """Authenticated, root-bound test substitute for CurrentUser DPAPI."""
    def __init__(self):self._cipher=AESGCM(os.urandom(32))
    def __call__(self,data,entropy,*,decrypt=False):
        if decrypt:return self._cipher.decrypt(data[:12],data[12:],entropy)
        nonce=os.urandom(12);return nonce+self._cipher.encrypt(nonce,data,entropy)

def create(root,protection,now):
    t=FixtureTransport();v=ExternalVaultService(root,crypto=FixtureCrypto(),transport=t,clock=lambda:now[0])
    gate=PremiumAuthorization(v,protect=protection,clock=lambda:now[0],verifier=CHECK)
    v.premium_authorization=gate
    return v,gate,t

def enable(gate,remember=False):
    gate.authenticate(TEST_PASSWORD);assert not gate.status()['enabled']
    gate.confirm(remember)

def regenerated_vault(root, protection, now):
    v,g,_=create(root,protection,now)
    enable(g,True);g.initialize_vault('1','generated-secret')
    old=g._read_protected('material.dpapi')
    salt=os.urandom(16);verify,key=v.crypto.derive(TEST_PASSWORD,salt)
    meta=dict(version=1,salt=base64.b64encode(salt).decode(),verify=base64.b64encode(verify).decode())
    v._commit(v._data,key,meta,v._revision)
    g.close();v.close()
    return old

def test_stale_material_password_recaptures_only_on_confirm(tmp_path):
    root=tmp_path/'v';p=Protection();now=[1000]
    old=regenerated_vault(root,p,now)
    v,g,_=create(root,p,now)
    try:
        assert g.authenticate(TEST_PASSWORD)
        assert g._read_protected('material.dpapi')==old
        g.confirm(True)
        assert g._read_protected('material.dpapi')!=old
        v.lock()
        assert g.restore_vault() and v.status()['open']
    finally:g.close();v.close()

def test_stale_material_startup_preserves_permission(tmp_path):
    root=tmp_path/'v';p=Protection();now=[1000]
    regenerated_vault(root,p,now)
    v,g,_=create(root,p,now)
    try:
        assert g.status()['error']=='premium-material-stale'
        assert not g.status()['enabled']
        assert (root/'premium-activation-state').read_bytes()==b'on\n'
    finally:g.close();v.close()

def test_stale_material_message_is_shown_at_startup(tmp_path):
    from nicegui import Client,ui
    from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog,vault_unlock_error_message
    root=tmp_path/'v';p=Protection();now=[1000]
    regenerated_vault(root,p,now)
    v,g,_=create(root,p,now);client=Client(ui.page('/_stale_material'));dialog=None
    try:
        with client:dialog=PremiumGateDialog(g,default_uid_path=None)
        assert dialog.notice.text==vault_unlock_error_message('premium-material-stale')
        assert 'パスワード' in dialog.notice.text
    finally:
        if dialog:dialog.close()
        client.delete();g.close();v.close()

def test_stale_material_wrong_password_counts_failure(tmp_path):
    root=tmp_path/'v';p=Protection();now=[1000]
    regenerated_vault(root,p,now)
    v,g,_=create(root,p,now)
    try:
        with pytest.raises(PremiumGateError,match='premium-password-rejected'):
            g.authenticate('generated-wrong')
        assert g._attempts()['failures']==1 and g.status()['retry_after']==1
        assert not g.status()['enabled']
    finally:g.close();v.close()

def test_default_off_even_if_token_vault_is_locally_unlocked(tmp_path):
    v,g,t=create(tmp_path/'v',Protection(),[1000])
    try:
        v.initialize('generated-local-password','1','generated-secret')
        assert not g.status()['enabled']
        for operation in (v.refresh,lambda:v.search('sample'),lambda:v.ranking(date='20200102'),v.start_keeper):
            with pytest.raises(VaultError):operation()
        assert t.calls==[]
    finally:g.close();v.close()

def test_real_transport_requires_permission_before_dns(monkeypatch):
    import socket
    from pixivvault_nicegui.external_vault_transport import default_transport
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:pytest.fail('OFF must not resolve a host'))
    with pytest.raises(VaultError,match='premium-disabled'):
        default_transport().fetch_account(1,'generated-signing-key',{},1000)

def test_application_binds_default_off_gate_and_account_quota_policy(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.external_vault_transport import ShaftTransport
    from test_external_vault import Request,profile
    request=Request([]);transport=ShaftTransport(request,oauth_profiles=[profile()])
    services=ApplicationServices(external_vault_root=tmp_path/'app-vault',external_vault_transport=transport,external_vault_crypto=FixtureCrypto())
    try:
        assert not services.premium_authorization.status()['enabled']
        assert transport.borrowed_429_scope=='account'
        with pytest.raises(VaultError,match='premium-disabled'):transport.fetch_account(1,'generated',{},1000)
        assert request.calls==[]
    finally:services.close()

def test_wrong_password_delays_attempts_and_never_enables(tmp_path):
    protection=Protection();now=[1000];v,g,t=create(tmp_path/'v',protection,now)
    try:
        for failure in range(3):
            with pytest.raises(PremiumGateError,match='premium-password-rejected'):g.authenticate('generated-wrong')
            assert not g.status()['enabled'] and g.status()['retry_after']==2**failure
            with pytest.raises(PremiumGateError,match='premium-password-wait'):g.authenticate(TEST_PASSWORD)
            now[0]+=2**failure
        assert t.calls==[]
    finally:g.close();v.close()

@pytest.mark.parametrize('remember',[False,True])
def test_saved_permission_and_session_only_restart(tmp_path,remember):
    root=tmp_path/'v';protection=Protection();now=[1000]
    v,g,t=create(root,protection,now)
    try:
        enable(g,remember);assert g.status()['enabled']
        g.initialize_vault('1\n2','generated-secret');v.refresh(1)
        assert len(t.calls)==1
    finally:g.close();v.close()
    v,g,t=create(root,protection,now)
    try:
        assert g.status()['enabled'] is remember
        assert v.status()['open'] is remember
        assert t.calls==[]
        if not remember:
            with pytest.raises(VaultError):v.search('sample')
            enable(g);assert g.restore_vault()
        assert t.calls==[]
    finally:g.close();v.close()

def test_explicit_off_revokes_saved_permission_without_deleting_token_vault(tmp_path):
    root=tmp_path/'v';p=Protection();now=[1000];v,g,t=create(root,p,now)
    enable(g,True);g.initialize_vault('1','generated-secret');v.refresh()
    pointer=(root/'CURRENT').read_bytes();g.disable()
    assert not g.status()['enabled'] and not v.status()['open']
    assert (root/'CURRENT').read_bytes()==pointer
    assert g._read_protected('activation.dpapi')['enabled'] is False
    g.close();v.close();v,g,t=create(root,p,now)
    try:
        assert not g.status()['enabled'] and t.calls==[]
        enable(g);assert g.restore_vault()
        assert v._snapshot()[1]['slots']['1']['access_token']=='synthetic-access-1'
    finally:g.close();v.close()

def test_off_fence_survives_failed_dpapi_revocation(tmp_path,monkeypatch):
    root=tmp_path/'v';p=Protection();now=[1000];v,g,t=create(root,p,now)
    enable(g,True);g.initialize_vault('1','generated-secret')
    with monkeypatch.context() as patch:
        patch.setattr(g,'_write_protected',lambda *a:(_ for _ in ()).throw(OSError('generated write failure')))
        with pytest.raises(OSError):g.disable()
    g.close();v.close();v,g,t=create(root,p,now)
    try:assert not g.status()['enabled'] and not v.status()['open'] and t.calls==[]
    finally:g.close();v.close()

def test_late_dialog_cannot_reverse_explicit_off(tmp_path):
    v,g,t=create(tmp_path/'v',Protection(),[1000])
    try:
        epoch=g.begin_prompt();g.disable()
        with pytest.raises(PremiumGateError,match='stale-session'):g.authenticate(TEST_PASSWORD,expected_epoch=epoch)
        assert not g.status()['enabled'] and t.calls==[]
    finally:g.close();v.close()

def test_permission_files_have_no_test_password_and_are_profile_bound(tmp_path):
    p=Protection();v,g,t=create(tmp_path/'v',p,[1000])
    try:
        enable(g,True)
        other=tmp_path/'other';other.mkdir()
        from pixivvault_nicegui.external_vault_autounlock import _dpapi
        assert g._read_protected('activation.dpapi')['enabled']
        with pytest.raises(Exception):p((v.root/'premium-activation.dpapi').read_bytes(),b'other-profile',decrypt=True)
    finally:g.close();v.close()
    for file in (tmp_path/'v').rglob('*'):
        if file.is_file():assert TEST_PASSWORD.encode() not in file.read_bytes()

@pytest.mark.skipif(os.name!='nt',reason='CurrentUser DPAPI requires Windows')
def test_currentuser_dpapi_real_roundtrip_uses_generated_credentials(tmp_path):
    v=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=FixtureTransport())
    gate=PremiumAuthorization(v,verifier=CHECK);v.premium_authorization=gate
    try:
        enable(gate,True);gate.initialize_vault('1','generated-secret')
    finally:gate.close();v.close()
    v=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=FixtureTransport())
    gate=PremiumAuthorization(v,verifier=CHECK)
    try:assert gate.status()['enabled'] and v.status()['open']
    finally:gate.close();v.close()

@pytest.mark.asyncio
async def test_ui_one_password_then_always_kept_on_and_no_off_network(tmp_path,monkeypatch):
    from nicegui import Client,ui,core
    from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    v,g,t=create(tmp_path/'v',Protection(),[1000])
    # The popup hands the signing key and UIDs to the vault and then starts the
    # keeper, which talks to the transport on its own thread. Replace only the
    # keeper start so "no traffic before ON" is judged on the popup path itself.
    started=[];monkeypatch.setattr(v,'start_keeper',lambda **kw:started.append(1))
    before_on=[];real_confirm=g.confirm
    def spy(*a,**k):before_on.append((list(t.calls),list(started)));return real_confirm(*a,**k)
    monkeypatch.setattr(g,'confirm',spy)
    client=Client(ui.page('/_premium_permission'));dialog=None;task=None
    try:
        with client:dialog=PremiumGateDialog(g,default_uid_path=None)
        assert not g.status()['enabled'] and t.calls==[]
        task=asyncio.create_task(dialog.request_enable());await asyncio.sleep(0)
        assert len([x for x in dialog.password_dialog.descendants() if isinstance(x,ui.input)])==2
        dialog.password.set_value(TEST_PASSWORD);dialog.secret.set_value('fixture-key');dialog._load_uid_bytes(b'1');dialog.password_dialog.submit(True)
        await asyncio.wait_for(task,5)
        assert dialog.password.value==''
        assert before_on==[([],[])]  # nothing was sent or started before ON was confirmed
        # ON is always kept for the next launch (no separate save question).
        assert g.status()['enabled'] and g.status()['remembered'] and t.calls==[] and started==[1]
    finally:
        if dialog:dialog.close()
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        client.delete();g.close();v.close()
