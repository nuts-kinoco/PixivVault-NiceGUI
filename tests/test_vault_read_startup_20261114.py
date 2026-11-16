from pathlib import Path
import hashlib
import pytest
from pixivvault_nicegui.external_vault import ExternalVaultService, VaultError
from test_external_vault import FixtureCrypto

def inventory(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

def test_absent_status_close_and_existing_read(tmp_path):
    root=tmp_path/'vault'; v=ExternalVaultService(root,crypto=FixtureCrypto()); assert not v.status()['initialized']; v.close(); assert not root.exists()
    v=ExternalVaultService(root,crypto=FixtureCrypto()); v.initialize('pw','1','secret'); v.close(); before=inventory(root)
    v=ExternalVaultService(root,crypto=FixtureCrypto()); assert v.status()['initialized']; v.close(); assert inventory(root)==before

def test_explicit_unlock_and_exclusive_writer(tmp_path):
    root=tmp_path/'vault'; v=ExternalVaultService(root,crypto=FixtureCrypto()); v.initialize('pw','1','secret')
    with pytest.raises(Exception): ExternalVaultService(root,crypto=FixtureCrypto())
    v.close()
    v=ExternalVaultService(root,crypto=FixtureCrypto()); v.unlock('pw'); assert v.status()['open']; v.close()

def test_orphan_rejected(tmp_path):
    root=tmp_path/'vault'; (root/'generations'/'orphan').mkdir(parents=True); (root/'EXTERNAL_VAULT_V1').write_bytes(b'PixivVault external vault sidecar v1\n')
    before=inventory(root)
    v=ExternalVaultService(root,crypto=FixtureCrypto())
    try:
        assert v.status()['inspection_error']=='invalid-generation' and v.status()['has_artifact']
        with pytest.raises(VaultError,match='invalid-generation'):v.initialize('pw','1','secret')
    finally:v.close()
    assert inventory(root)==before


def test_uninitialized_premium_records_survive_restart(tmp_path):
    root=tmp_path/'vault'; v=ExternalVaultService(root,crypto=FixtureCrypto())
    v._ensure_writable(None); (root/'premium-activation-state').write_bytes(b'off\n'); v.close()
    before=inventory(root); v=ExternalVaultService(root,crypto=FixtureCrypto())
    assert v.status()['inspection_error'] is None and not v.status()['initialized']
    v.close(); assert inventory(root)==before
