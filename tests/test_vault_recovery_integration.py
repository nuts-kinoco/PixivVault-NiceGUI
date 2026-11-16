"""Integration of audited recovery with Z01/Z02; generated files and transports."""
from required_network_guard import no_network
from pathlib import Path
import pytest
from test_vault_known_result_recovery import interrupted_result
from test_oauth_send_boundary import make_vault, payout
from pixivvault_nicegui.external_vault import ExternalVaultService, LegacyVaultCrypto, VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse

PASSWORD='generated-password'

def records(v):
    return {p.relative_to(v.root).as_posix():p.read_bytes() for p in (v.root/'known-results').rglob('*.enc')}

def test_recovery_authentication_does_not_advance_pending_or_enable_auto_unlock(tmp_path,monkeypatch):
    v,wire,scope=interrupted_result(tmp_path,monkeypatch)
    try:
        before=records(v);calls=list(wire.calls);scope.gate.trip(429);stop=scope.gate.snapshot()
        v.recover_known_result(PASSWORD)
        assert not v.status()['oauth_review_required'] and not v.status()['auto_unlock']
        assert records(v)==before and wire.calls==calls and scope.gate.snapshot()==stop
        assert v._keeper is None and v._keeper_stop.is_set()
    finally:v.close()

@pytest.mark.parametrize('operation',['unlock','unlock-lock','configure'])
def test_metadata_changes_preserve_authenticated_result_without_copying_records(tmp_path,monkeypatch,operation):
    v,wire,scope=interrupted_result(tmp_path,monkeypatch)
    try:
        original=records(v);calls=list(wire.calls)
        if operation in ('unlock','unlock-lock'):v.unlock(PASSWORD,auto_unlock=True)
        else:v.configure_auto_unlock(True)
        assert v.status()['oauth_review_required']
        if operation=='unlock-lock':v.lock_explicitly()
        root,transport,crypto=v.root,v.transport,v.crypto;v.close()
        v=ExternalVaultService(root,transport=transport,crypto=crypto)
        assert v.try_auto_unlock() is (operation!='unlock-lock')
        v.recover_known_result(PASSWORD)
        assert not v.status()['oauth_review_required']
        assert v._snapshot()[1]['slots']['1']['refresh_token']=='generated-recovered-refresh'
        assert v.status()['auto_unlock'] is (operation!='unlock-lock')
        assert records(v)==original and wire.calls==calls and v._keeper is None
    finally:v.close()

@pytest.mark.parametrize('known',[True,False])
def test_legacy_auto_unlock_does_not_enable_premium_or_start_keeper(tmp_path,monkeypatch,known):
    from pixivvault_nicegui.services import ApplicationServices
    if known:v,wire,_=interrupted_result(tmp_path,monkeypatch)
    else:
        v,wire,_=make_vault(tmp_path,[payout(),VaultError('transport-failed')],crypto=LegacyVaultCrypto())
        with pytest.raises(VaultError):v.refresh(1)
    v.unlock(PASSWORD,auto_unlock=True)
    root,transport,crypto=v.root,v.transport,v.crypto;calls=list(wire.calls);v.close()
    services=None
    try:
        services=ApplicationServices(external_vault_root=root,
            external_vault_transport=transport,external_vault_crypto=crypto)
        # Old auto-unlock is not a saved premium-permission proof. Default OFF
        # wins; explicit local legacy-password inspection still works.
        assert not services.premium_authorization.status()['enabled']
        assert not services.external_vault.status()['open']
        services.external_vault.unlock(PASSWORD)
        assert services.external_vault.status()['oauth_review_required']
        assert services.external_vault._keeper is None and wire.calls==calls
        assert not services.closed
    finally:
        if services is not None:services.close()

@pytest.mark.parametrize('fault',['link-partial','replace-after'])
def test_metadata_commit_interruption_keeps_old_or_linked_recovery(tmp_path,monkeypatch,fault):
    import pixivvault_nicegui.external_vault as module
    v,wire,_=interrupted_result(tmp_path,monkeypatch)
    old=v._revision;before=records(v);calls=list(wire.calls)
    write,replace=module._write,module.os.replace;hits=[]
    def broken_write(path,data):
        if fault=='link-partial' and path.name=='known-result-origin.enc':
            write(path,data[:len(data)//2]);hits.append(True);raise OSError('generated link interruption')
        return write(path,data)
    def broken_replace(source,target):
        result=replace(source,target)
        if fault=='replace-after' and target.name=='CURRENT':
            hits.append(True);raise OSError('generated post-CURRENT interruption')
        return result
    try:
        v.unlock(PASSWORD)
        epoch, data = v._snapshot()
        with monkeypatch.context() as patch:
            patch.setattr(module,'_write',broken_write);patch.setattr(module.os,'replace',broken_replace)
            with pytest.raises(OSError):
                v._publish(epoch,data)
        assert hits
        assert (v._pointer()!=old)==(fault=='replace-after')
        # A post-CURRENT failure leaves in-memory revision stale. Reopen the
        # actually published generation before recovering; never replay.
        if fault=='replace-after':
            v.reopen_saved_state(PASSWORD)
        v.recover_known_result(PASSWORD)
        assert records(v)==before and wire.calls==calls and not v.status()['oauth_review_required']
    finally:v.close()

@pytest.mark.parametrize('change',['corrupt-link','different-pending-data'])
def test_invalid_origin_never_recovers_or_replays(tmp_path,monkeypatch,change):
    v,wire,_=interrupted_result(tmp_path,monkeypatch)
    try:
        v.unlock(PASSWORD)
        if change=='corrupt-link':
            epoch,data=v._snapshot()
            v._publish(epoch,data)
            path=v.root/'generations'/v._revision/'known-result-origin.enc'
            raw=bytearray(path.read_bytes());raw[-1]^=1;path.write_bytes(raw)
        else:
            epoch,data=v._snapshot();data['slots']['2']['backoff_until']=7777
            v._publish(epoch,data)
        before=records(v);pointer=(v.root/'CURRENT').read_bytes();calls=list(wire.calls)
        with pytest.raises(VaultError):v.recover_known_result(PASSWORD)
        assert (v.root/'CURRENT').read_bytes()==pointer and records(v)==before and wire.calls==calls
        v.reopen_saved_state(PASSWORD)
        assert v.status()['oauth_review_required']
    finally:v.close()

