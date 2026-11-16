import json
import os
import pytest
from required_network_guard import no_network
from pixivvault_nicegui import external_vault_autounlock as module
from pixivvault_nicegui.external_vault_autounlock import AutoUnlockRecord,AutoUnlockError,_dpapi

def test_dpapi_currentuser_round_trip_and_wrong_binding():
    key=b'generated-key-material'*2
    wrapped=_dpapi(key,b'synthetic-vault-a')
    assert wrapped!=key
    assert _dpapi(wrapped,b'synthetic-vault-a',decrypt=True)==key
    with pytest.raises(AutoUnlockError):_dpapi(wrapped,b'synthetic-vault-b',decrypt=True)

def test_standalone_on_off_readback_and_vault_binding(tmp_path):
    store=AutoUnlockRecord(tmp_path)
    assert store.read('synthetic-salt') is None
    state=store.write('synthetic-salt',enabled=True,key=b'k'*32,inherited=True)
    assert store.read('synthetic-salt')==state
    assert b'kkkkkkkk' not in store.path.read_bytes()
    with pytest.raises(AutoUnlockError):store.read('other-vault')
    store.write('synthetic-salt',enabled=False)
    assert store.read('synthetic-salt')['enabled'] is False

def test_failed_prepublication_keeps_previous_record(tmp_path,monkeypatch):
    store=AutoUnlockRecord(tmp_path);store.write('v',enabled=False)
    before=store.path.read_bytes()
    def denied(*_):raise PermissionError('generated write denial')
    monkeypatch.setattr(module,'_write',denied)
    with pytest.raises(AutoUnlockError):store.write('v',enabled=True,key=b'g'*32)
    assert store.path.read_bytes()==before

def test_corrupt_record_never_becomes_missing_or_off(tmp_path):
    store=AutoUnlockRecord(tmp_path);store.write('v',enabled=False)
    store.path.write_bytes(b'{')
    with pytest.raises(AutoUnlockError):store.read('v')

def test_after_replace_error_succeeds_only_after_exact_readback(tmp_path,monkeypatch):
    store=AutoUnlockRecord(tmp_path);original=module.os.replace
    def after(source,target):
        original(source,target)
        raise OSError('generated after replace')
    monkeypatch.setattr(module.os,'replace',after)
    state=store.write('v',enabled=True,key=b'g'*32)
    assert store.read('v')==state

def test_failed_readback_is_not_reported_as_success(tmp_path,monkeypatch):
    store=AutoUnlockRecord(tmp_path)
    monkeypatch.setattr(store,'read',lambda *_:(_ for _ in ()).throw(OSError('generated read failure')))
    with pytest.raises(OSError):store.write('v',enabled=True,key=b'g'*32)
    # New instance confirms publication; the failed caller did not claim success.
    assert AutoUnlockRecord(tmp_path).read('v')['enabled'] is True

def test_protect_failure_keeps_old_packet(tmp_path,monkeypatch):
    store=AutoUnlockRecord(tmp_path);store.write('v',enabled=False);before=store.path.read_bytes()
    original=module._dpapi
    def fail_protect(*args,**kwargs):
        if not kwargs.get('decrypt'):raise AutoUnlockError('generated protect failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'_dpapi',fail_protect)
    with pytest.raises(AutoUnlockError):store.write('v',enabled=True,key=b'g'*32)
    assert store.path.read_bytes()==before
