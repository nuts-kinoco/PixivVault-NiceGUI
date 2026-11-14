"""M1/M3 synthetic encrypted-file regressions; no external requests."""
import copy
import hashlib
from pathlib import Path

import pytest
import pixivvault_nicegui.external_vault as module
import test_vault_known_result_recovery as recovery
from test_vault_known_result_recovery import no_network, interrupted_result, rewrite_authenticated_result
from test_oauth_send_boundary import make_vault, payout, reopened
from pixivvault_nicegui.external_vault import LegacyVaultCrypto, VaultError, LIMIT
from pixivvault_nicegui.external_vault_rejections import ARCHIVE_FIELD
from pixivvault_nicegui.external_vault_transport import VaultResponse, OAUTH


def seeded_result(tmp_path, monkeypatch, archive=False):
    original = recovery.make_vault
    def seeded(*args, **kwargs):
        v, wire, scope = original(*args, **kwargs)
        epoch, data = v._snapshot()
        data['slots']['2'].update(access_token='generated-other-access', consecutive_failures=7)
        data['slots']['1'].update(consecutive_failures=4, backoff_until=0,
                                  rejected_refresh_hashes=[hashlib.sha256(b'generated-old-rejected').hexdigest()])
        if archive:v._compact_rejections(data, v._key, force=True)
        v._publish(epoch, data)
        return v, wire, scope
    monkeypatch.setattr(recovery, 'make_vault', seeded)
    return interrupted_result(tmp_path, monkeypatch)


def refuses(v, wire):
    before, calls = (v.root/'CURRENT').read_bytes(), list(wire.calls)
    with pytest.raises(VaultError, match='known-result-unavailable'):
        v.recover_known_result('generated-password')
    assert (v.root/'CURRENT').read_bytes() == before and wire.calls == calls
    v.unlock('generated-password')
    assert v.status()['oauth_review_required']


@pytest.mark.parametrize('field,value', [('access_token','generated-forged'), ('consecutive_failures',0),
                                        ('backoff_until',9000), ('oauth_send_pending',False)])
def test_m1_other_slot_requires_complete_base_equality(tmp_path, monkeypatch, field, value):
    v, wire, _ = seeded_result(tmp_path, monkeypatch)
    try:
        rewrite_authenticated_result(v, lambda d:d['slots']['2'].update({field:value}))
        refuses(v, wire)
    finally:v.close()


@pytest.mark.parametrize('field,value', [('consecutive_failures',0), ('backoff_until',9000),
    ('last_fetch_at',9999), ('last_refresh_attempt_at',9999), ('reported_refresh_hash','generated-forged'),
    ('unexpected_management','generated-forged')])
def test_m1_target_rejects_fields_outside_callback_allowlist(tmp_path, monkeypatch, field, value):
    v, wire, _ = seeded_result(tmp_path, monkeypatch)
    try:
        rewrite_authenticated_result(v, lambda d:d['slots']['1'].update({field:value}))
        refuses(v, wire)
    finally:v.close()


@pytest.mark.parametrize('change', ['remove','branch','older'])
def test_m1_archive_must_retain_exact_base_head(tmp_path, monkeypatch, change):
    v, wire, _ = seeded_result(tmp_path, monkeypatch, archive=True)
    try:
        base = copy.deepcopy(v._data)
        def alter(data):
            if change == 'remove':data.pop(ARCHIVE_FIELD)
            else:
                # An independently authenticated chain with identical hashes is
                # still not an extension of the base head.
                data.pop(ARCHIVE_FIELD)
                data['slots']['1']['rejected_refresh_hashes'] = list(v._rejections.read(base,v._key,v.crypto,LIMIT))
                v._compact_rejections(data,v._key,force=True)
                if change == 'older':
                    # Base is advanced below; candidate retains the old head.
                    data[ARCHIVE_FIELD] = base[ARCHIVE_FIELD]
        if change == 'older':
            # Bind the proof to a newer synthetic pending base, then roll its
            # candidate archive back to the original authenticated head.
            epoch, advanced = v._snapshot()
            advanced['slots']['1']['rejected_refresh_hashes'] = [hashlib.sha256(b'generated-new-rejected').hexdigest()]
            v._compact_rejections(advanced,v._key,force=True)
            old_dir = v.root/'known-results'/v._revision
            result_bytes = (old_dir/'result.enc').read_bytes()
            v._publish(epoch,advanced)
            new_dir = v.root/'known-results'/v._revision
            new_dir.mkdir()
            (new_dir/'result.enc').write_bytes(result_bytes)
            source = (v.root/'generations'/v._revision/'vault.enc').read_bytes()
            proof = {'purpose':'oauth-known-result-v1','base':v._revision,'uid':1,
                     'source_sha256':hashlib.sha256(source).hexdigest(),
                     'result_sha256':hashlib.sha256(result_bytes).hexdigest()}
            (new_dir/'proof.enc').write_bytes(v.crypto.encrypt(proof,v._key))
        rewrite_authenticated_result(v, alter)
        refuses(v, wire)
    finally:v.close()


@pytest.mark.parametrize('field,value', [('secret_key','generated-changed'), ('shaft_uids',[2,1])])
def test_m1_configuration_binding(tmp_path, monkeypatch, field, value):
    v, wire, _ = interrupted_result(tmp_path, monkeypatch)
    try:
        rewrite_authenticated_result(v, lambda d:d.update({field:value}))
        refuses(v, wire)
    finally:v.close()


@pytest.mark.parametrize('extend', [False, True])
def test_m1_normal_result_preserves_persisted_failures_and_archive(tmp_path, monkeypatch, extend):
    v, wire, _ = seeded_result(tmp_path, monkeypatch, archive=True)
    try:
        source = v.crypto.decrypt((v.root/'generations'/v._revision/'vault.enc').read_bytes(),v._key)
        if extend:
            def add(data):
                data['slots']['1']['rejected_refresh_hashes'] = [hashlib.sha256(b'generated-more-rejected').hexdigest()]
                v._compact_rejections(data,v._key,force=True)
            rewrite_authenticated_result(v, add)
        calls = list(wire.calls)
        v.recover_known_result('generated-password')
        result = v._snapshot()[1]
        assert result['slots']['2'] == source['slots']['2']
        assert result['slots']['1']['consecutive_failures'] == 4
        assert result['slots']['1']['access_token'] == 'generated-recovered-access'
        assert wire.calls == calls and not v.status()['oauth_review_required']
    finally:v.close()


@pytest.mark.parametrize('fault', ['result-before','result-partial','result-after',
    'proof-before','proof-partial','proof-after','mkdir','directory-sync','parent-sync'])
@pytest.mark.parametrize('rejected', [False, True])
def test_m3_record_failure_does_not_block_known_result_publish(tmp_path, monkeypatch, fault, rejected):
    response = (VaultResponse(400, {'error':'invalid_grant'}) if rejected else
        VaultResponse(200, {'access_token':'generated-success','refresh_token':'generated-rotated','expires_in':3600}))
    tail = ([VaultResponse(200, {'ok':True}), VaultResponse(429, {'quotas':[{'remaining':0,'resetsAt':4000000}]})]
            if rejected else [VaultResponse(200, {'ok':True})])
    v, wire, _ = make_vault(tmp_path,[payout(),response]+tail,crypto=LegacyVaultCrypto())
    write, mkdir, sync = module._write, Path.mkdir, module._fsync_dir
    hits = []
    def fail():
        hits.append(fault)
        raise OSError('generated journal failure')
    def broken_write(path, payload):
        if path.parent.parent.name == 'known-results' and fault.startswith(path.stem+'-'):
            if fault.endswith('partial'):write(path,payload[:len(payload)//2])
            if fault.endswith('after'):write(path,payload)
            fail()
        return write(path,payload)
    def broken_mkdir(path,*args,**kwargs):
        if fault == 'mkdir' and path.parent.name == 'known-results':fail()
        return mkdir(path,*args,**kwargs)
    def broken_sync(path):
        if ((fault=='directory-sync' and path.parent.name=='known-results') or
                (fault=='parent-sync' and path.name=='known-results')):fail()
        return sync(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module,'_write',broken_write)
            patch.setattr(Path,'mkdir',broken_mkdir)
            patch.setattr(module,'_fsync_dir',broken_sync)
            if rejected:
                with pytest.raises(VaultError,match='rate-limited'):v.refresh(1)
            else:v.refresh(1)
        assert hits == [fault] and wire.calls.count(OAUTH)==1
        v = reopened(v)
        slot = v._snapshot()[1]['slots']['1']
        assert not v.status()['oauth_review_required']
        if rejected:
            assert hashlib.sha256(b'generated-refresh').hexdigest() in slot['rejected_refresh_hashes']
            assert slot['backoff_until']==4000
        else:assert slot['access_token']=='generated-success'
    finally:v.close()
