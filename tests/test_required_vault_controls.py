"""Z01/Z02: real crypto, actual component callbacks, generated credentials only."""
import asyncio
from required_network_guard import no_network
import base64
from types import SimpleNamespace

import pytest
from nicegui import Client, ui
from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto, VaultError
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault import FixtureTransport

PASSWORD = 'generated-only-password'
BINDING = b'generated-device'


def service(tmp_path):
    return ExternalVaultService(tmp_path/'vault', transport=FixtureTransport())


def legacy_packet():
    crypto = LegacyVaultCrypto()
    salt = bytes(range(16))
    verify, key = crypto.derive(PASSWORD, salt)
    data = {'secret_key':'generated-secret', 'shaft_uids':[17], 'slots':{
        '17': {'access_token':'generated-access', 'refresh_token':'generated-refresh',
               'oauth_send_pending':True, 'rejected_refresh_hashes':['a'*64]}}}
    return salt, verify, crypto.encrypt(data, key)


def event(payload):
    async def read():
        return payload
    return SimpleNamespace(file=SimpleNamespace(name='selected.enc', read=read))


@pytest.mark.parametrize('pending', (False, True))
def test_explicit_ui_lock_revokes_autounlock_without_changing_saved_slot(tmp_path, monkeypatch, pending):
    vault = service(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    epoch, data = vault._snapshot()
    data['slots']['17'].update(oauth_send_pending=pending, refresh_token='generated-refresh',
                               rejected_refresh_hashes=['b'*64])
    vault._publish(epoch, data)
    vault.configure_auto_unlock(True, binding=BINDING)
    before = vault.crypto.decrypt(vault._load()[4], vault._key)
    client = Client(ui.page('/_required_lock'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            view.lock()
            assert not vault.status()['open']
            view.close()
        vault.close()
        vault = service(tmp_path)
        assert not vault.try_auto_unlock(binding=BINDING)
        vault.unlock(PASSWORD)
        assert vault.crypto.decrypt(vault._load()[4], vault._key) == before
        assert vault.status()['oauth_review_required'] is pending
        assert vault.transport.calls == []
    finally:
        client.delete()
        vault.close()


def test_explicit_lock_uses_persisted_generation_not_stale_memory(tmp_path):
    vault = service(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    vault.configure_auto_unlock(True, binding=BINDING)
    # Same-process E2 state: CURRENT contains the known result, memory is stale.
    saved = vault.crypto.decrypt(vault._load()[4], vault._key)
    saved['slots']['17'].update(access_token='generated-known', oauth_send_pending=False)
    vault._commit(saved, vault._key, vault._meta, vault._revision)
    vault._data['slots']['17'].update(access_token='generated-old', oauth_send_pending=True)
    client = Client(ui.page('/_required_lock_current'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            view.lock()
            view.close()
        assert not vault.try_auto_unlock(binding=BINDING)
        vault.unlock(PASSWORD)
        assert vault._snapshot()[1] == saved
        assert vault.transport.calls == []
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
async def test_legacy_upload_requires_explicit_import_preserves_pending_and_never_starts_keeper(tmp_path, monkeypatch):
    vault = service(tmp_path)
    starts = []
    monkeypatch.setattr(vault, 'start_keeper', lambda: starts.append(True))
    salt, verify, ciphertext = legacy_packet()
    client = Client(ui.page('/_required_import'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            await view.pick_legacy_file(event(ciphertext))
            assert not (vault.root/'CURRENT').exists()
            assert vault.transport.calls == []
            view.legacy_salt.set_value(base64.b64encode(salt).decode())
            view.legacy_verify.set_value(base64.b64encode(verify).decode())
            view.legacy_password.set_value(PASSWORD)
            await view.import_legacy()
            assert (vault.root/'CURRENT').exists()
            assert not vault.status()['open']
            assert not view._legacy_ciphertext
            assert view.legacy_password.value == view.legacy_salt.value == view.legacy_verify.value == ''
            assert starts == [] and vault.transport.calls == []
            view.close()
        vault.close()
        vault = service(tmp_path)
        vault.unlock(PASSWORD)
        assert vault.status()['oauth_review_required']
        assert vault._snapshot()[1]['slots']['17']['rejected_refresh_hashes'] == ['a'*64]
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ('password', 'ciphertext', 'salt', 'verify'))
async def test_invalid_legacy_import_never_publishes_or_displays_input(tmp_path, failure):
    vault = service(tmp_path)
    salt, verify, ciphertext = legacy_packet()
    client = Client(ui.page('/_required_import_bad'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            await view.pick_legacy_file(event(ciphertext[:-1]+bytes([ciphertext[-1]^1]) if failure=='ciphertext' else ciphertext))
            view.legacy_salt.set_value('private-invalid' if failure=='salt' else base64.b64encode(salt).decode())
            view.legacy_verify.set_value('private-invalid' if failure=='verify' else base64.b64encode(verify).decode())
            view.legacy_password.set_value('private-wrong' if failure=='password' else PASSWORD)
            await view.import_legacy()
            assert not (vault.root/'CURRENT').exists()
            assert not vault.status()['open'] and vault.transport.calls == []
            assert not view._legacy_ciphertext
            assert 'private-' not in view.legacy_notice.text
            view.close()
    finally:
        client.delete()
        vault.close()


@pytest.mark.asyncio
async def test_upload_finished_after_client_close_is_not_retained(tmp_path):
    vault = service(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def read():
        entered.set()
        await release.wait()
        return b'0'*28
    client = Client(ui.page('/_required_import_late'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            task = asyncio.create_task(view.pick_legacy_file(SimpleNamespace(file=SimpleNamespace(name='selected.enc', read=read))))
            await entered.wait()
            view.close()
            release.set()
            await task
            assert not view._legacy_ciphertext
            assert not (vault.root/'CURRENT').exists()
    finally:
        client.delete()
        vault.close()


@pytest.mark.parametrize('after_replace', (False, True))
def test_explicit_lock_interrupted_commit_keeps_saved_data_and_reports_uncertainty(tmp_path, monkeypatch, after_replace):
    import pixivvault_nicegui.external_vault_autounlock as module
    vault = service(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    epoch, data = vault._snapshot()
    data['slots']['17']['oauth_send_pending'] = True
    vault._publish(epoch, data)
    vault.configure_auto_unlock(True, binding=BINDING)
    saved = vault.crypto.decrypt(vault._load()[4], vault._key)
    real_replace = module.os.replace
    def interrupted(source, target):
        if target.name == 'autounlock-currentuser.json':
            if after_replace:real_replace(source, target)
            raise OSError('generated-only failure')
        real_replace(source, target)
    client = Client(ui.page('/_required_lock_interruption'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            with monkeypatch.context() as injection:
                injection.setattr(module.os, 'replace', interrupted)
                view.lock()
            assert not vault.status()['open']
            if not after_replace:assert '確認できません' in view.notice.text
            view.close()
        assert vault.try_auto_unlock(binding=BINDING) is (not after_replace)
        vault.lock()
        vault.unlock(PASSWORD)
        assert vault._snapshot()[1] == saved
        assert vault.transport.calls == []
    finally:
        client.delete()
        vault.close()
