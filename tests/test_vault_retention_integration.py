"""Bound normal known-result retention without discarding failed/unknown evidence."""
from required_network_guard import no_network
from pathlib import Path
import pytest
from pixivvault_nicegui.external_vault import LegacyVaultCrypto,VaultError
from pixivvault_nicegui.external_vault_transport import VaultResponse,OAUTH
from test_oauth_send_boundary import make_vault,payout,reopened
from test_vault_known_result_recovery import interrupted_result
from test_vault_known_result_safety import record_bytes

def success(reuse=False):
    # Every renewal begins with payout; preserve the OAuth outcome and
    # retention assertions while matching the original product's sequence.
    return [payout(),VaultResponse(200,{'access_token':'generated-a','refresh_token':'generated-r','expires_in':3600}),VaultResponse(200,{'ok':True})]

def test_successful_record_growth_stops_at_sixteen_across_restart(tmp_path):
    v,wire,_=make_vault(tmp_path,[],crypto=LegacyVaultCrypto())
    now=[1000];v.clock=lambda:now[0]
    try:
        for i in range(18):
            wire.responses.extend(success(reuse=bool(v._snapshot()[1]['slots']['1'].get('refresh_token'))));v.refresh(1);now[0]+=4000
            if i==8:
                v=reopened(v);v.clock=lambda:now[0]
        assert len(record_bytes(v))==32
        assert not v.status()['oauth_review_required'] and wire.calls.count(OAUTH)==18
    finally:v.close()

def full_budget(v,monkeypatch):
    # Exercise product admission; the helper's actual sixteen-entry bound is
    # tested separately with real encrypted files.
    monkeypatch.setattr(v._known_result_retention,'can_record',lambda *_:False)

def test_full_normal_budget_publishes_without_new_record_and_survives_restart(tmp_path,monkeypatch):
    v,wire,_=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    try:
        full_budget(v,monkeypatch)
        v.refresh(1)
        assert not record_bytes(v) and not v.status()['oauth_review_required']
        v=reopened(v)
        assert v._snapshot()[1]['slots']['1']['access_token']=='generated-a'
        assert wire.calls.count(OAUTH)==1
    finally:v.close()

@pytest.mark.parametrize('after_current',[False,True])
def test_full_budget_preserves_known_record_on_primary_save_failure(tmp_path,monkeypatch,after_current):
    v,wire,scope=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    publish=v._publish
    def fail(epoch,data):
        if data['slots']['1'].get('oauth_send_pending') is False:
            if after_current:publish(epoch,data)
            raise OSError('generated primary save failure')
        return publish(epoch,data)
    try:
        full_budget(v,monkeypatch)
        with monkeypatch.context() as patch:
            patch.setattr(v,'_publish',fail)
            with pytest.raises(VaultError):v.refresh(1)
        saved=record_bytes(v);assert len(saved)==2
        calls=list(wire.calls);scope.gate.trip(403);stop=scope.gate.snapshot()
        v=reopened(v)
        if not after_current:v.recover_known_result('generated-password')
        assert not v.status()['oauth_review_required']
        assert record_bytes(v)==saved and wire.calls==calls and scope.gate.snapshot()==stop
    finally:v.close()

def test_full_budget_keeps_invalid_grant_record_outside_normal_limit(tmp_path,monkeypatch):
    v,wire,_=make_vault(tmp_path,[payout(),VaultResponse(400,{'error':'invalid_grant'}),
        VaultResponse(200,{'ok':True}),VaultResponse(429,{'quotas':[{'remaining':0,'resetsAt':4000000}]})],crypto=LegacyVaultCrypto())
    try:
        full_budget(v,monkeypatch)
        with pytest.raises(VaultError,match='rate-limited'):v.refresh(1)
        assert len(record_bytes(v))==2 and not v.status()['oauth_review_required']
        assert v._snapshot()[1]['slots']['1']['backoff_until']==4000
    finally:v.close()

def test_full_budget_post_oauth_followup_failure_keeps_record(tmp_path,monkeypatch):
    v,wire,_=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    try:
        full_budget(v,monkeypatch)
        monkeypatch.setattr(v.transport,'online_tick',lambda *_:(_ for _ in ()).throw(OSError('generated followup failure')))
        with pytest.raises(VaultError):v.refresh(1)
        assert len(record_bytes(v))==2
        assert not v.status()['oauth_review_required'] and wire.calls.count(OAUTH)==1
    finally:v.close()

def test_retention_metadata_failure_never_blocks_primary_result_or_discards_record(tmp_path,monkeypatch):
    v,wire,_=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    try:
        monkeypatch.setattr(v._known_result_retention,'mark_success',lambda *_:(_ for _ in ()).throw(OSError('generated index failure')))
        v.refresh(1)
        assert len(record_bytes(v))==2 and not v.status()['oauth_review_required']
        assert wire.calls.count(OAUTH)==1
    finally:v.close()

def test_budget_read_failure_defers_normal_but_still_preserves_failed_result(tmp_path,monkeypatch):
    v,wire,_=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    publish=v._publish
    def fail(epoch,data):
        if data['slots']['1'].get('oauth_send_pending') is False:raise OSError('generated publish failure')
        return publish(epoch,data)
    try:
        monkeypatch.setattr(v._known_result_retention,'can_record',lambda *_:(_ for _ in ()).throw(ValueError('generated unreadable index')))
        with monkeypatch.context() as patch:
            patch.setattr(v,'_publish',fail)
            with pytest.raises(VaultError):v.refresh(1)
        assert len(record_bytes(v))==2 and v.status()['oauth_review_required']
    finally:v.close()

def test_full_budget_unhandled_interruption_keeps_pending_without_inventing_record(tmp_path,monkeypatch):
    class Interrupted(BaseException):pass
    v,wire,scope=make_vault(tmp_path,success(),crypto=LegacyVaultCrypto())
    publish=v._publish
    def cut(epoch,data):
        if data['slots']['1'].get('oauth_send_pending') is False:raise Interrupted()
        return publish(epoch,data)
    try:
        full_budget(v,monkeypatch)
        with monkeypatch.context() as patch:
            patch.setattr(v,'_publish',cut)
            with pytest.raises(Interrupted):v.refresh(1)
        calls=list(wire.calls)
        v=reopened(v)
        assert v.status()['oauth_review_required'] and not record_bytes(v)
        with pytest.raises(VaultError):v.recover_known_result('generated-password')
        assert wire.calls==calls
    finally:v.close()

@pytest.mark.parametrize('failure',['completion-mark','index-stage-write'])
def test_persistent_retention_write_failure_cannot_allow_unbounded_normal_records(tmp_path,monkeypatch,failure):
    from pixivvault_nicegui import external_vault_result_retention as retention
    v,wire,_=make_vault(tmp_path,[],crypto=LegacyVaultCrypto())
    now=[1000];v.clock=lambda:now[0]
    def fail(*_):raise OSError('generated persistent accounting failure')
    try:
        if failure=='completion-mark':monkeypatch.setattr(v._known_result_retention,'mark_success',fail)
        else:monkeypatch.setattr(retention,'_write',fail)
        for _ in range(18):
            wire.responses.extend(success(reuse=bool(v._snapshot()[1]['slots']['1'].get('refresh_token'))));v.refresh(1);now[0]+=4000
        assert len(record_bytes(v)) <= (32 if failure=='completion-mark' else 0)
        assert not v.status()['oauth_review_required'] and wire.calls.count(OAUTH)==18
    finally:v.close()

