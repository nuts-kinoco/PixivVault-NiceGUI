"""Synthetic key behavior; no real credentials or network."""
import pytest
from pixivvault_nicegui.external_vault import ExternalVaultService,VaultError
from pixivvault_nicegui.external_vault_transport import ShaftTransport,VaultResponse
from test_external_vault import FixtureCrypto,FixtureTransport,Request,profile

def test_empty_key_does_not_create_vault_or_call_transport(tmp_path):
    t=FixtureTransport();v=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=t)
    try:
        with pytest.raises(VaultError):v.initialize('generated-password','1','')
        assert not v.status()['open'] and not t.calls
    finally:v.close()

def test_rejected_signature_is_not_success():
    request=Request([VaultResponse(403,{})]);t=ShaftTransport(request,oauth_profiles=[profile()])
    with pytest.raises(VaultError,match='payout-failed'):t.fetch_account(1,'synthetic-wrong-key',{},1000)
    assert len(request.calls)==1

def test_encrypted_prior_generation_still_contains_old_key_after_replacement(tmp_path):
    v=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=FixtureTransport())
    try:
        v.initialize('generated-password','1','synthetic-previous-key')
        old=v._revision;v.update_config(secret_key='synthetic-replacement-key')
        assert v._snapshot()[1]['secret_key']=='synthetic-replacement-key'
        retained=v.crypto.decrypt((v.root/'generations'/old/'vault.enc').read_bytes(),v._key)
        assert retained['secret_key']=='synthetic-previous-key'
    finally:v.close()


def test_replacement_uses_new_key_for_payout_and_keeps_old_generation_for_recovery(tmp_path):
    import hashlib, hmac
    class RecordingTransport(FixtureTransport):
        def fetch_account(self, uid, secret, slot, now):
            self.signature = hmac.new(secret.encode(), b"synthetic-payout", hashlib.sha256).digest()
            return super().fetch_account(uid, secret, slot, now)
    transport = RecordingTransport()
    vault = ExternalVaultService(tmp_path / "vault", crypto=FixtureCrypto(), transport=transport)
    old, new = "synthetic-previous-key", "synthetic-replacement-key"
    try:
        vault.initialize("synthetic-password", "1", old)
        previous = vault._revision
        vault.update_config(secret_key=new)
        vault.refresh(1)
        assert transport.signature == hmac.new(new.encode(), b"synthetic-payout", hashlib.sha256).digest()
        assert transport.signature != hmac.new(old.encode(), b"synthetic-payout", hashlib.sha256).digest()
        recovered = vault.crypto.decrypt((vault.root / "generations" / previous / "vault.enc").read_bytes(), vault._key)
        assert recovered["secret_key"] == old
        vault.lock()
        vault.unlock("synthetic-password")
        assert vault._snapshot()[1]["secret_key"] == new
    finally:
        vault.close()
