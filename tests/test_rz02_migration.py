"""Read-only review probes against generated Vaults only."""
import json
from pathlib import Path
import pytest
from required_network_guard import no_network
from pixivvault_nicegui.external_vault import ExternalVaultService
from test_z02_policy import PolicyCrypto,legacy_on

def make(tmp_path):
    v=ExternalVaultService(tmp_path/'vault',crypto=PolicyCrypto())
    v.initialize('generated-password','17','generated-secret')
    return v

def test_legacy_on_is_preserved_by_reopen_and_config_commit(tmp_path):
    v=make(tmp_path)
    try:
        old_path,old_bytes=legacy_on(v)
        v.lock()
        v.reopen_saved_state('generated-password')
        assert v.status()['auto_unlock'] is True
        v.update_config(secret_key='generated-updated-secret')
        assert old_path.read_bytes()==old_bytes
        assert v.status()['auto_unlock'] is True, 'unrelated config commit silently turns inherited ON into OFF'
    finally:v.close()

def test_password_recovery_after_transient_migration_failure_keeps_existing_on(tmp_path,monkeypatch):
    v=make(tmp_path)
    try:
        old_path,old_bytes=legacy_on(v);v.lock()
        with monkeypatch.context() as m:
            m.setattr(v,'_write_auto',lambda *a,**k:(_ for _ in ()).throw(OSError('generated one-shot migration error')))
            assert not v.try_auto_unlock()
        assert v.status()['auto_unlock'] is True
        v.reopen_saved_state('generated-password')
        assert v.status()['open'] and v.status()['auto_unlock'] is True
        assert not v.status()['auto_unlock_save_failed']
        v.update_config(uid_text='17\n18')
        assert old_path.read_bytes()==old_bytes
        assert v.status()['auto_unlock'] is True, 'inherited ON is lost without an explicit OFF'
    finally:v.close()

def test_persistent_migration_failure_preserves_current_and_password_access(tmp_path,monkeypatch):
    from pixivvault_nicegui.external_vault import VaultError
    v=make(tmp_path)
    try:
        old,old_bytes=legacy_on(v);v.lock();v.reopen_saved_state('generated-password')
        pointer=(v.root/'CURRENT').read_bytes();before=v._snapshot()[1]
        with monkeypatch.context() as m:
            m.setattr(v,'_write_auto',lambda *a,**k:(_ for _ in ()).throw(OSError('generated persistent failure')))
            with pytest.raises(VaultError,match='autounlock-migration-unconfirmed'):
                v.update_config(uid_text='17\n18')
            assert (v.root/'CURRENT').read_bytes()==pointer
            assert v._snapshot()[1]==before
            assert v.status()['auto_unlock'] and v.status()['auto_unlock_save_failed']
            v.lock();v.unlock('generated-password')
            assert v.status()['open'] and v.status()['auto_unlock']
        v.update_config(uid_text='17\n18')
        assert v.status()['auto_unlock'] and v.status()['auto_unlock_inherited']
        assert not v.status()['auto_unlock_save_failed']
        assert 'autounlock' not in v._load()[1] and old.read_bytes()==old_bytes
    finally:v.close()

def test_explicit_off_record_wins_over_legacy_metadata_during_commit(tmp_path):
    v=make(tmp_path)
    try:
        old,before=legacy_on(v)
        v.configure_auto_unlock(False)
        v.update_config(secret_key='generated-updated-secret')
        assert not v.status()['auto_unlock']
        assert not v.status()['auto_unlock_inherited']
        assert 'autounlock' not in v._load()[1] and old.read_bytes()==before
    finally:v.close()
