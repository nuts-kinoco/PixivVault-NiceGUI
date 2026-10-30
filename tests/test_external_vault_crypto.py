"""Real original crypto format; optional existing interpreter only, no installs."""
import json
import pytest
pytest.importorskip('cryptography',reason='Product S environment has no optional AES-GCM dependency')
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pixivvault_nicegui.external_vault import ExternalVaultService,LegacyVaultCrypto,VaultError,VERIFY,ENCRYPT

def test_exact_original_key_derivation_ciphertext_both_directions(tmp_path):
    crypto=LegacyVaultCrypto();salt=bytes(range(16));password='synthetic-互換-password'
    stretched=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=600000).derive(password.encode('utf-8'))
    original=tuple(HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=info).derive(stretched) for info in (VERIFY,ENCRYPT))
    assert crypto.derive(password,salt)==original
    verify,key=original;legacy={'secret_key':'synthetic-key','shaft_uid':17,'access_token':'synthetic-access'}
    nonce=b'0'*12;ciphertext=nonce+AESGCM(key).encrypt(nonce,json.dumps(legacy,ensure_ascii=False).encode('utf-8'),None)
    assert crypto.decrypt(ciphertext,key)==legacy
    encoded=crypto.encrypt(legacy,key)
    assert json.loads(AESGCM(key).decrypt(encoded[:12],encoded[12:],None))==legacy
    assert b'synthetic-access' not in encoded
    with pytest.raises(VaultError):crypto.decrypt(encoded[:-1]+bytes([encoded[-1]^1]),key)
    root=tmp_path/'crypto-vault';service=ExternalVaultService(root)
    service.import_legacy(password,salt=salt,verify=verify,ciphertext=ciphertext);service.close()
    reopened=ExternalVaultService(root)
    try:
        reopened.unlock(password);assert reopened.status()['open'];reopened.close()
        for file in root.rglob('*'):
            if file.is_file():assert b'synthetic-access' not in file.read_bytes() and b'synthetic-key' not in file.read_bytes()
    finally:reopened.close()

def test_original_device_bound_autounlock_opt_in(tmp_path):
    crypto=LegacyVaultCrypto();binding=b'synthetic-machine';material=binding+b'PixivVault-nana7mi-autounlock-2026-08-22'
    expected=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b'nana7mi-vault-autounlock-v1').derive(material)
    assert crypto._device_key(binding)==expected
    root=tmp_path/'autounlock';v=ExternalVaultService(root);v.initialize('fixture-password','17','fixture-key')
    v.configure_auto_unlock(True,binding=binding);v.close()
    v=ExternalVaultService(root)
    try:
        assert not v.try_auto_unlock(binding=b'other') and v.try_auto_unlock(binding=binding)
        v.configure_auto_unlock(False);v.lock();assert not v.try_auto_unlock(binding=binding)
    finally:v.close()
