import sys
from pathlib import Path
import pytest
import pixivvault_nicegui.external_vault as m
from pixivvault_nicegui.ui.premium_gate_view import vault_unlock_error_message

@pytest.mark.parametrize('failure,code',[(PermissionError(),'vault-permission-denied'),(OSError(),'vault-io-error'),(FileNotFoundError(),'invalid-generation'),(ValueError(),'invalid-generation')])
def test_load_classification(monkeypatch,failure,code):
    v=m.ExternalVaultService.__new__(m.ExternalVaultService); v.root=Path('synthetic'); v._pointer=lambda:'a'*32
    monkeypatch.setattr(m,'_safe_path',lambda *a,**k:None)
    def reject(*a,**k): raise failure
    monkeypatch.setattr(m,'_read_bounded',reject)
    with pytest.raises(m.VaultError) as e: v._load()
    assert e.value.code==code
    assert vault_unlock_error_message(code)
    if isinstance(failure,PermissionError) or type(failure) is OSError: assert code not in m.VAULT_INTEGRITY_ERRORS

def test_unknown_and_lock():
    assert vault_unlock_error_message(None) is None
    assert 'パスワード' in vault_unlock_error_message('unlock-failed')
    assert '復号' in vault_unlock_error_message('invalid-ciphertext')
