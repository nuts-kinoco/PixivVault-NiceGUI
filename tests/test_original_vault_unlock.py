"""Z01 original password-unlock contract, with generated encrypted data only."""
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from required_network_guard import no_network
from pixivvault_nicegui.external_vault import ExternalVaultService, VaultError, LegacyVaultCrypto
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault import FixtureTransport
from test_required_vault_controls import legacy_packet
from test_oauth_send_boundary import make_vault


PASSWORD = 'generated-only-password'


def new_vault(tmp_path):
    return ExternalVaultService(tmp_path/'vault', transport=FixtureTransport())


def saved_data(vault, key=None):
    return vault.crypto.decrypt(vault._load()[4], key or vault._key)


@pytest.mark.asyncio
async def test_password_unlock_defaults_off_and_clears_ui(tmp_path, monkeypatch):
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    epoch, data = vault._snapshot()
    data['slots']['17'].update(consecutive_failures=4, rejected_refresh_hashes=['a'*64])
    vault._publish(epoch, data)
    before = saved_data(vault)
    vault.lock()
    starts = []
    monkeypatch.setattr(vault, 'start_keeper', lambda **kw: starts.append(True))
    client = Client(ui.page('/_original_unlock'))
    try:
        with client:
            view = ExternalVaultView(SimpleNamespace(external_vault=vault))
            view.password.set_value(PASSWORD)
            await view.action('unlock')
            assert vault.status()['open'] and not vault.status()['auto_unlock']
            assert not vault.status()['auto_unlock_save_failed']
            assert not view.auto_unlock.value
            assert view.password.value == ''
            assert starts == [True] and vault.transport.calls == []
            assert saved_data(vault) == before
            view.close()
        vault.close()
        vault = new_vault(tmp_path)
        assert not vault.status()['open']
        assert not vault.try_auto_unlock()
        assert vault.transport.calls == []
    finally:
        client.delete()
        vault.close()


def test_wrong_password_never_remembers_key_or_changes_current(tmp_path):
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    vault.lock()
    before = (vault.root/'CURRENT').read_bytes()
    try:
        with pytest.raises(VaultError, match='unlock-failed'):
            vault.unlock('generated-wrong')
        assert not vault.status()['open']
        assert (vault.root/'CURRENT').read_bytes() == before
        assert 'autounlock' not in vault._load()[1]
        assert vault.transport.calls == []
    finally:
        vault.close()


@pytest.mark.parametrize('enabled', (False, True))
def test_internal_reopen_is_readonly_and_does_not_turn_on_autounlock(tmp_path, enabled):
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    if enabled:
        vault.configure_auto_unlock(True)
    before = (vault.root/'CURRENT').read_bytes()
    meta_before = vault._load()[1]
    try:
        vault.reopen_saved_state(PASSWORD)
        assert vault.status()['open']
        assert vault.status()['auto_unlock'] is enabled
        assert (vault.root/'CURRENT').read_bytes() == before
        assert vault._load()[1] == meta_before
        assert vault._keeper_stop.is_set() and vault._keeper is None
        assert vault.transport.calls == []
    finally:
        vault.close()


def test_explicit_off_survives_restart_recheck_and_normal_unlock(tmp_path):
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    vault.lock()
    vault.unlock(PASSWORD)
    vault.lock_explicitly()
    vault.close()
    vault = new_vault(tmp_path)
    try:
        assert not vault.try_auto_unlock()
        vault.reopen_saved_state(PASSWORD)
        assert not vault.status()['auto_unlock']
        vault.lock()
        assert not vault.try_auto_unlock()
        vault.unlock(PASSWORD)
        assert not vault.status()['auto_unlock']
        vault.configure_auto_unlock(True)
        vault.lock()
        assert vault.try_auto_unlock()
        assert vault.transport.calls == []
    finally:
        vault.close()


@pytest.mark.parametrize('saved_pending', (False, True))
def test_remember_uses_current_not_old_memory_and_keeps_shared_stop(tmp_path, saved_pending):
    vault, wire, scope = make_vault(tmp_path, [], crypto=LegacyVaultCrypto())
    try:
        scope.gate.trip(429)
        stop_before = scope.gate.snapshot()
        data = saved_data(vault)
        data['slots']['1'].update(access_token='generated-current',
                                 oauth_send_pending=saved_pending,
                                 rejected_refresh_hashes=['b'*64])
        vault._commit(data, vault._key, vault._meta, vault._revision)
        vault._data['slots']['1'].update(access_token='generated-stale',
                                       oauth_send_pending=not saved_pending)
        vault.unlock('generated-password')
        assert not vault.status()['auto_unlock']
        assert saved_data(vault) == data
        assert vault.status()['oauth_review_required'] is saved_pending
        assert scope.gate.snapshot() == stop_before and wire.calls == []
        assert vault._keeper is None
        if saved_pending:
            with pytest.raises(VaultError, match='pixiv-outcome-uncertain'):
                vault.keeper_tick()
            assert wire.calls == []
    finally:
        vault.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ('protect', 'before_publish', 'after_publish'))
async def test_opt_in_failure_preserves_unlock_and_reports_only_unconfirmed_state(tmp_path, monkeypatch, failure):
    import pixivvault_nicegui.external_vault_autounlock as module
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    epoch, data = vault._snapshot()
    data['slots']['17'].update(oauth_send_pending=True, refresh_token='generated-refresh')
    vault._publish(epoch, data)
    before = saved_data(vault);pointer=(vault.root/'CURRENT').read_bytes()
    vault.lock();starts=[]
    monkeypatch.setattr(vault, 'start_keeper', lambda **kw: starts.append(True))
    real_replace=module.os.replace;real_dpapi=module._dpapi
    def interrupted(source,target):
        if target.name=='autounlock-currentuser.json':
            if failure=='after_publish':real_replace(source,target)
            raise OSError('generated-only failure')
        real_replace(source,target)
    def bad_protect(*args,**kwargs):
        if not kwargs.get('decrypt'):raise OSError('generated-only failure')
        return real_dpapi(*args,**kwargs)
    client=Client(ui.page('/_opt_in_failure'))
    try:
        with client:
            view=ExternalVaultView(SimpleNamespace(external_vault=vault))
            view.password.set_value(PASSWORD);view.auto_unlock.set_value(True)
            with monkeypatch.context() as injection:
                if failure=='protect':injection.setattr(module,'_dpapi',bad_protect)
                else:injection.setattr(module.os,'replace',interrupted)
                await view.action('unlock')
            assert vault.status()['open']
            assert vault.status()['auto_unlock_save_failed'] is (failure!='after_publish')
            assert vault.status()['auto_unlock'] is (failure=='after_publish')
            assert vault.status()['oauth_review_required']
            assert view.password.value=='' and not view.auto_unlock.value
            assert starts==[] and vault.transport.calls==[]
            assert saved_data(vault)==before and (vault.root/'CURRENT').read_bytes()==pointer
            view.close()
        vault.close();vault=new_vault(tmp_path)
        assert vault.try_auto_unlock() is (failure=='after_publish')
        assert vault.transport.calls==[]
    finally:client.delete();vault.close()


def test_import_activate_false_keeps_locked_without_auto_key_or_keeper(tmp_path):
    vault = new_vault(tmp_path)
    salt, verify, ciphertext = legacy_packet()
    try:
        vault.import_legacy(PASSWORD, salt=salt, verify=verify,
                            ciphertext=ciphertext, activate=False)
        assert not vault.status()['open']
        assert 'autounlock' not in vault._load()[1]
        assert not vault.try_auto_unlock()
        assert vault.transport.calls == [] and vault._keeper is None
    finally:
        vault.close()


def test_failed_auto_readback_keeps_vault_revision_and_reports_uncertainty(tmp_path,monkeypatch):
    from pixivvault_nicegui.external_vault_autounlock import AutoUnlockRecord
    vault=new_vault(tmp_path);vault.initialize(PASSWORD,'17','generated-secret');vault.lock()
    pointer=(vault.root/'CURRENT').read_bytes();before=vault._load()[4]
    real_read=AutoUnlockRecord.read;reads=[]
    def first_only(self,*args):
        reads.append(True)
        if len(reads)>1:raise OSError('generated-only unreadable state')
        return real_read(self,*args)
    try:
        with monkeypatch.context() as injection:
            injection.setattr(AutoUnlockRecord,'read',first_only)
            vault.unlock(PASSWORD,auto_unlock=True)
            assert vault.status()['open'] and vault.status()['auto_unlock_save_failed']
            assert vault.status()['auto_unlock'] is None
        assert (vault.root/'CURRENT').read_bytes()==pointer and vault._load()[4]==before
        assert vault.status()['auto_unlock'] is True
        vault.reopen_saved_state(PASSWORD)
        assert vault.transport.calls==[] and vault._keeper is None
    finally:vault.close()


def test_socket_unverified_still_rejects_password_unlock_before_meta_write(tmp_path):
    vault = new_vault(tmp_path)
    vault.initialize(PASSWORD, '17', 'generated-secret')
    vault.lock()
    before = (vault.root/'CURRENT').read_bytes()
    vault.transport.revocation_unverified = True
    try:
        with pytest.raises(VaultError, match='socket-shutdown-unverified'):
            vault.unlock(PASSWORD)
        assert not vault.status()['open']
        assert (vault.root/'CURRENT').read_bytes() == before
        assert 'autounlock' not in vault._load()[1]
        assert vault.transport.calls == []
    finally:
        vault.transport.revocation_unverified = False
        vault.close()
