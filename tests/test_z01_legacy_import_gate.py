"""Z01: legacy-import inputs remain usable while the premium gate is ON."""
import base64
import secrets
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault import FixtureTransport
from test_required_vault_controls import event


class EnabledGate:
    def status(self):
        return {'enabled': True, 'remembered': False, 'error': None}


def packet():
    password = secrets.token_urlsafe(24)
    crypto = LegacyVaultCrypto()
    salt = secrets.token_bytes(16)
    verify, key = crypto.derive(password, salt)
    data = {'secret_key': secrets.token_urlsafe(24), 'shaft_uids': [17], 'slots': {}}
    return password, salt, verify, crypto.encrypt(data, key)


@pytest.mark.asyncio
async def test_gate_on_exposes_separate_masked_legacy_password(tmp_path):
    vault = ExternalVaultService(tmp_path/'vault', transport=FixtureTransport())
    client = Client(ui.page('/_z01_import_visible'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault, premium_authorization=EnabledGate()))
            assert not view.password.visible  # Existing gate/unlock contract is unchanged.
            field = getattr(view, 'legacy_password', None)
            assert bool(field is not None and field.visible and field.enabled)
            assert field.props.get('type') == 'password'
            view.close()
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
async def test_gate_on_form_then_file_imports_and_reopens_without_network(tmp_path):
    password, salt, verify, ciphertext = packet()
    transport = FixtureTransport()
    vault = ExternalVaultService(tmp_path/'vault', transport=transport)
    client = Client(ui.page('/_z01_import_form_order'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault, premium_authorization=EnabledGate()))
            field = getattr(view, 'legacy_password', None)
            assert bool(field is not None and field.visible)
            field.set_value(password)
            view.legacy_salt.set_value(base64.b64encode(salt).decode())
            view.legacy_verify.set_value(base64.b64encode(verify).decode())
            # Match the actual GUI sequence: fill inputs BEFORE selecting the file.
            await view.pick_legacy_file(event(ciphertext))
            assert bool(field.value == password)
            assert bool(view.legacy_salt.value and view.legacy_verify.value)
            await view.import_legacy()
            assert vault.status()['initialized'] and not vault.status()['open']
            assert '取り込みました' in view.legacy_notice.text
            assert field.value == view.legacy_salt.value == view.legacy_verify.value == ''
            assert not view._legacy_ciphertext and transport.calls == []
            assert vault._keeper is None
            view.close()
        vault.close()
        vault = ExternalVaultService(tmp_path/'vault', transport=transport)
        vault.unlock(password)
        assert vault.status()['open']
        vault.lock()
        assert not vault.status()['open'] and transport.calls == []
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
async def test_empty_import_password_explains_and_never_calls_service(tmp_path, monkeypatch):
    vault = ExternalVaultService(tmp_path/'vault', transport=FixtureTransport())
    calls = []
    monkeypatch.setattr(vault, 'import_legacy', lambda *a, **kw: calls.append(True))
    client = Client(ui.page('/_z01_import_empty'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault, premium_authorization=EnabledGate()))
            await view.pick_legacy_file(event(b'x'*28))
            view.legacy_salt.set_value(base64.b64encode(bytes(16)).decode())
            view.legacy_verify.set_value(base64.b64encode(bytes(32)).decode())
            await view.import_legacy()
            assert calls == []
            assert 'パスワードを入力' in view.legacy_notice.text
            assert bool(view._legacy_ciphertext)  # Correct the missing input without re-uploading.
            view.close()
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['wrong-password', 'corrupt', 'truncated'])
async def test_gate_on_import_rejects_invalid_input_and_scrubs_password(tmp_path, failure):
    password, salt, verify, ciphertext = packet()
    if failure == 'corrupt':
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    elif failure == 'truncated':
        ciphertext = ciphertext[:-1]
    entered = secrets.token_urlsafe(24) if failure == 'wrong-password' else password
    vault = ExternalVaultService(tmp_path/'vault', transport=FixtureTransport())
    client = Client(ui.page('/_z01_import_invalid'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault, premium_authorization=EnabledGate()))
            field = getattr(view, 'legacy_password', None)
            assert bool(field is not None and field.visible)
            await view.pick_legacy_file(event(ciphertext))
            view.legacy_salt.set_value(base64.b64encode(salt).decode())
            view.legacy_verify.set_value(base64.b64encode(verify).decode())
            field.set_value(entered)
            await view.import_legacy()
            assert not vault.status()['initialized'] and not vault.status()['open']
            assert '取り込みを完了できませんでした' in view.legacy_notice.text
            assert field.value == '' and not view._legacy_ciphertext
            assert bool(entered not in view.legacy_notice.text)
            assert vault.transport.calls == []
            view.close()
    finally:
        client.delete()
        vault.close()
