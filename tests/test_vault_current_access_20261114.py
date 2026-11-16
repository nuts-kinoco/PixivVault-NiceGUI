from pathlib import Path
from types import SimpleNamespace
import pytest
import pixivvault_nicegui.external_vault as m
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault import FixtureCrypto

@pytest.mark.parametrize('failure,code',[(PermissionError(),'vault-permission-denied'),(OSError('synthetic'),'vault-io-error')])
def test_current_access_error_is_not_missing(tmp_path,monkeypatch,failure,code):
    root=tmp_path/'v'; root.mkdir(); (root/'EXTERNAL_VAULT_V1').write_bytes(b'PixivVault external vault sidecar v1\n')
    before={p.name:p.read_bytes() for p in root.iterdir()}; original=m._read_bounded
    def read(path,limit):
        if Path(path).name=='CURRENT':raise failure
        return original(path,limit)
    monkeypatch.setattr(m,'_read_bounded',read)
    v=m.ExternalVaultService(root,crypto=FixtureCrypto())
    try:
        assert v.status()['inspection_error']==code and v.status()['has_artifact']
        with pytest.raises(m.VaultError,match=code):v.initialize('pw','1','secret')
        view=ExternalVaultView(SimpleNamespace(external_vault=v)); view.render()
        assert not view.create_button.enabled and not view._can_import()
        view.close()
    finally:v.close()
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before

def test_current_missing_and_invalid(tmp_path):
    root=tmp_path/'v'; root.mkdir(); (root/'EXTERNAL_VAULT_V1').write_bytes(b'PixivVault external vault sidecar v1\n')
    v=m.ExternalVaultService(root,crypto=FixtureCrypto()); assert v.status()['inspection_error'] is None; v.close()
    (root/'CURRENT').write_bytes(b'\xff')
    before={p.name:p.read_bytes() for p in root.iterdir()}; v=m.ExternalVaultService(root,crypto=FixtureCrypto())
    assert v.status()['inspection_error']=='invalid-generation' and v.status()['has_artifact']; v.close()
    assert {p.name:p.read_bytes() for p in root.iterdir()}==before
