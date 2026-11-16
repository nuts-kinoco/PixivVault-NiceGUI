import base64
import json
from pathlib import Path
from threading import Thread,Event
import pytest
from required_network_guard import no_network
from pixivvault_nicegui.external_vault import ExternalVaultService,VaultError
from test_external_vault import FixtureCrypto
class PolicyCrypto(FixtureCrypto):
    # State-machine fixture only, not an AES compatibility claim.
    def wrap_key(self,key,**_):return b'generated-wrap:'+key
    def unwrap_key(self,wrapped,**_):
        if not wrapped.startswith(b'generated-wrap:'):raise VaultError('autounlock-failed')
        return wrapped[len(b'generated-wrap:'):]


def vault(tmp_path):
    service=ExternalVaultService(tmp_path/'vault',crypto=PolicyCrypto())
    service.initialize('generated-password','17','generated-secret')
    return service

def legacy_on(service):
    path=service.root/'generations'/service._pointer()/'settings.json'
    meta=json.loads(path.read_bytes())
    meta['autounlock']=base64.b64encode(service.crypto.wrap_key(service._key)).decode()
    path.write_text(json.dumps(meta))
    return path,path.read_bytes()

def test_new_default_off_manual_unlock_never_overwrites_off(tmp_path):
    v=vault(tmp_path)
    try:
        assert v.status()['auto_unlock'] is False
        v.lock();v.unlock('generated-password')
        assert v.status()['auto_unlock'] is False
        v.configure_auto_unlock(True);v.configure_auto_unlock(False)
        v.lock();v.unlock('generated-password')
        assert not v.status()['auto_unlock']
        for p in v.root.glob('generations/*/settings.json'):
            assert 'autounlock' not in json.loads(p.read_bytes())
    finally:v.close()

def test_explicit_on_persists_across_manual_unlock_and_reopen(tmp_path):
    v=vault(tmp_path);root=v.root
    v.unlock('generated-password',auto_unlock=True)
    v.close()
    v=ExternalVaultService(root,crypto=PolicyCrypto())
    try:
        assert v.status()['auto_unlock'] is True and not v.status()['open']
        assert v.try_auto_unlock()
        v.lock();v.unlock('generated-password')
        assert v.status()['auto_unlock'] is True
        v.lock_explicitly()
        assert not v.status()['open'] and not v.status()['auto_unlock']
        assert not v.try_auto_unlock()
    finally:v.close()

def test_click_epoch_fences_queued_unlock_and_key_save(tmp_path):
    v=vault(tmp_path);epoch=v.unlock_request_epoch();started=Event();errors=[]
    try:
        v.configure_auto_unlock(True)
        def worker():
            started.set()
            try:v.unlock('generated-password',auto_unlock=True,expected_epoch=epoch)
            except VaultError as exc:errors.append(exc.code)
        with v._mutex:
            t=Thread(target=worker);t.start();assert started.wait(3)
            v.lock_explicitly()
        t.join(5)
        assert not t.is_alive()
        assert errors==['stale-session']
        assert not v.status()['open'] and not v.status()['auto_unlock']
    finally:v.close()

def test_legacy_on_migrates_without_rewriting_generations(tmp_path):
    v=vault(tmp_path)
    try:
        path,before=legacy_on(v);v.lock()
        assert v.status()['auto_unlock_inherited']
        assert v.try_auto_unlock()
        assert v.status()['auto_unlock_state']=='on' and v.status()['auto_unlock_inherited']
        assert path.read_bytes()==before
        v.unlock('generated-password',auto_unlock=True)
        assert not v.status()['auto_unlock_inherited']
        v.update_config(uid_text='17\n18')
        assert 'autounlock' not in json.loads((v.root/'generations'/v._pointer()/'settings.json').read_bytes())
        assert path.read_bytes()==before
    finally:v.close()

def test_migration_failure_keeps_password_unlock_and_old_bytes(tmp_path,monkeypatch):
    v=vault(tmp_path)
    try:
        path,before=legacy_on(v);v.lock()
        def failed(*args,**kwargs):raise OSError('generated migration failure')
        monkeypatch.setattr(v,'_write_auto',failed)
        assert not v.try_auto_unlock()
        v.unlock('generated-password')
        assert v.status()['open'] and v.status()['auto_unlock_save_failed']
        assert path.read_bytes()==before
    finally:v.close()

def test_disable_failure_does_not_claim_off(tmp_path,monkeypatch):
    v=vault(tmp_path)
    try:
        v.configure_auto_unlock(True)
        monkeypatch.setattr(v,'_write_auto',lambda *a,**k:(_ for _ in ()).throw(OSError('generated denial')))
        with pytest.raises(VaultError):v.lock_explicitly()
        assert not v.status()['open'] and v.status()['auto_unlock'] is True
        assert v.status()['auto_unlock_save_failed']
    finally:v.close()

def test_pending_and_shared_stop_remain_no_network(tmp_path):
    from test_external_vault import FixtureTransport
    v=vault(tmp_path);wire=FixtureTransport();v.transport=wire
    try:
        epoch,data=v._snapshot();data['slots']['17']['oauth_send_pending']=True
        v._publish(epoch,data);v.lock()
        v.unlock('generated-password')
        assert v.status()['oauth_review_required'] and wire.calls==[]
        with pytest.raises(VaultError,match='pixiv-outcome-uncertain'):v.start_keeper()
        with pytest.raises(VaultError,match='pixiv-outcome-uncertain'):v.keeper_tick()
        v.configure_auto_unlock(True)
        assert v.status()['auto_unlock'] is True
        assert wire.calls==[] and v._keeper is None
    finally:v.close()

def test_late_keeper_start_cannot_use_a_newer_session(tmp_path):
    v=vault(tmp_path);epoch=v.unlock_request_epoch()
    try:
        v.lock_explicitly();v.unlock('generated-password')
        with pytest.raises(VaultError,match='stale-session'):v.start_keeper(expected_epoch=epoch)
        assert v._keeper is None
    finally:v.close()

def test_auto_unlock_keeps_shared_429_stop_and_sends_nothing(tmp_path):
    from test_oauth_send_boundary import make_vault
    v,wire,scope=make_vault(tmp_path,[],crypto=PolicyCrypto())
    try:
        scope.gate.trip(429);before=scope.gate.snapshot()
        v.configure_auto_unlock(True);v.lock()
        assert v.try_auto_unlock()
        assert scope.gate.snapshot()==before and wire.calls==[] and v._keeper is None
    finally:v.close()
