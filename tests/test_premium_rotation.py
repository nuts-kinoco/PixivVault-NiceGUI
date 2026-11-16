"""Finite, synthetic pool rotation and durable per-identity accounting."""
import json
from datetime import datetime,timezone
from urllib.parse import urlsplit
import pytest
from pixivvault_nicegui.external_vault import ExternalVaultService,LegacyVaultCrypto,VaultError
from pixivvault_nicegui.external_vault_transport import ShaftTransport,VaultResponse,ACCOUNT,SEARCH
from pixivvault_nicegui.premium_read import unavailable_message
from test_external_vault import Request,profile
from test_premium_authorization import Protection,CHECK,TEST_PASSWORD
from pixivvault_nicegui.premium_gate import PremiumAuthorization

def payout(uid):
    return VaultResponse(200,dict(uid=1,account=dict(access_token='generated-'+str(uid),
        user=dict(id=uid,is_premium=True)),expiresAt=9000000))

def pool(tmp_path,responses,**budget):
    now=[1000];request=Request(responses);transport=ShaftTransport(request,oauth_profiles=[profile()],clock=lambda:datetime.fromtimestamp(now[0],timezone.utc))
    v=ExternalVaultService(tmp_path/'v',crypto=LegacyVaultCrypto(),transport=transport,clock=lambda:now[0])
    protection=Protection();gate=PremiumAuthorization(v,protect=protection,clock=lambda:now[0],verifier=CHECK)
    v.premium_authorization=gate;transport.bind_premium_authorization(gate.require_enabled)
    gate.authenticate(TEST_PASSWORD);gate.confirm(True);gate.initialize_vault('1\n2','generated-signing-key')
    v.enable_request_budget(min_interval=0,**budget)
    return v,gate,request,now,protection

def test_capped_account_is_skipped_and_all_capped_accounts_stop(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100),VaultResponse(200,{'illusts':[]}),VaultResponse(200,{'illusts':[]})],borrow_limit=1)
    try:
        v.refresh(1);v.refresh(2);v.search('sample');v._round=0;v.search('sample')
        searches=[c for c in r.calls if c[1].startswith(SEARCH)]
        assert [c[2]['headers']['Authorization'] for c in searches]==['Bearer generated-99','Bearer generated-100']
        count=len(r.calls)
        with pytest.raises(VaultError,match='borrow-accounts-cooling-down') as error:v.search('sample')
        assert len(r.calls)==count and '通信を止め' in unavailable_message(error.value.code)
        assert [s['borrowed'] for s in v.premium_pool_status()]==[1,1]
        assert b'account:99' not in (v.root/'premium-request-budget.enc').read_bytes()
    finally:g.close();v.close()

def test_same_borrowed_identity_cannot_escape_limit_through_two_request_uids(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(99),VaultResponse(200,{'illusts':[]})],borrow_limit=1)
    try:
        v.refresh(1);v.refresh(2);v.search('sample')
        with pytest.raises(VaultError,match='borrow-accounts-cooling-down'):v.search('sample')
        assert len([c for c in r.calls if c[1].startswith(SEARCH)])==1
    finally:g.close();v.close()

def test_shaft_request_uid_429_rotates_once_to_next_request_uid(tmp_path):
    quota=VaultResponse(429,{'quotas':[dict(remaining=0,resetsAt=2000000)]})
    v,g,r,now,p=pool(tmp_path,[quota,payout(100),VaultResponse(200,{'illusts':[]})])
    try:
        v.search('sample')
        pay=[c for c in r.calls if c[1]==ACCOUNT]
        assert [json.loads(c[2]['body'])['uid'] for c in pay]==[1,2]
        assert len(r.calls)==3 and v._snapshot()[1]['slots']['1']['backoff_until']==2000
        assert [s['payouts'] for s in v.premium_pool_status()]==[1,1]
    finally:g.close();v.close()

def test_all_shaft_quotas_exhausted_stop_without_borrowed_get(tmp_path):
    quota=VaultResponse(429,{'quotas':[dict(remaining=0,resetsAt=2000000)]})
    v,g,r,now,p=pool(tmp_path,[quota,quota])
    try:
        with pytest.raises(VaultError,match='borrow-accounts-unavailable') as error:v.search('sample')
        assert [c[1] for c in r.calls]==[ACCOUNT,ACCOUNT]
        assert '取得枠' in unavailable_message(error.value.code)
    finally:g.close();v.close()

def test_payout_limit_persists_across_restart(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99)],payout_limit=1)
    v.refresh(1);g.close();v.close()
    request=Request([]);t=ShaftTransport(request,oauth_profiles=[profile()])
    v=ExternalVaultService(tmp_path/'v',crypto=LegacyVaultCrypto(),transport=t,clock=lambda:now[0])
    g=PremiumAuthorization(v,protect=p,clock=lambda:now[0],verifier=CHECK);v.premium_authorization=g
    t.bind_premium_authorization(g.require_enabled);v.enable_request_budget(payout_limit=1,min_interval=0)
    try:
        with pytest.raises(VaultError,match='payout-limit'):v.refresh(1)
        assert request.calls==[] and v.premium_pool_status()[0]['payouts']==1
    finally:g.close();v.close()

def test_budget_corruption_fails_closed_without_losing_tokens(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99)])
    try:
        v.refresh(1);before=(v.root/'CURRENT').read_bytes()
        (v.root/'premium-request-budget.enc').write_bytes(b'generated-corruption')
        with pytest.raises(VaultError,match='premium-budget-unavailable'):v.search('sample')
        assert len(r.calls)==1 and (v.root/'CURRENT').read_bytes()==before
    finally:g.close();v.close()

def test_original_point_three_second_global_cadence(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100),VaultResponse(200,{'illusts':[]}),VaultResponse(200,{'illusts':[]})])
    try:
        v.refresh(1);v.refresh(2)
        waits=[]
        def sleep(delay):waits.append(delay);now[0]+=delay
        v._request_budget.min_interval=.3;v._request_budget.sleep=sleep
        v.search('sample');v.search('sample')
        assert sum(waits)==pytest.approx(.3)
    finally:g.close();v.close()

def account_policy(v,tmp_path):
    from test_oauth_send_boundary import owned_scope
    scope=owned_scope(tmp_path)
    v.transport.bind_shared_pixiv_stop(scope)
    v.transport.set_borrowed_429_scope('account')
    return scope

@pytest.mark.parametrize('seconds',[30,90000])
def test_account_policy_429_cools_one_actor_and_uses_next_without_clearing_stop(tmp_path,seconds):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100),VaultResponse(429,{}, {'Retry-After':str(seconds)}),VaultResponse(200,{'illusts':[]})])
    scope=account_policy(v,tmp_path)
    try:
        v.refresh(1);v.refresh(2);v.search('sample')
        assert len(r.calls)==4 and not scope.gate.snapshot().blocked
        assert v.premium_pool_status()[0]['wait']==seconds
        assert r.calls[-1][2]['headers']['Authorization']=='Bearer generated-100'
    finally:g.close();v.close()

def test_account_policy_all_429_stop_after_one_pass(tmp_path):
    quota=VaultResponse(429,{}, {'Retry-After':'30'})
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100),quota,quota])
    scope=account_policy(v,tmp_path)
    try:
        v.refresh(1);v.refresh(2)
        with pytest.raises(VaultError,match='borrow-accounts-cooling-down'):v.search('sample')
        assert len(r.calls)==4 and not scope.gate.snapshot().blocked
        with pytest.raises(VaultError):v.search('sample')
        assert len(r.calls)==4
    finally:g.close();v.close()

def test_account_policy_never_clears_preexisting_common_stop(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100)])
    scope=account_policy(v,tmp_path)
    try:
        v.refresh(1);v.refresh(2);scope.gate.trip(429)
        with pytest.raises(VaultError,match='pixiv-network-stopped'):v.search('sample')
        assert len(r.calls)==2 and scope.gate.snapshot().blocked
    finally:g.close();v.close()

def test_account_policy_challenge_429_stops_all_without_rotation(tmp_path):
    v,g,r,now,p=pool(tmp_path,[payout(99),payout(100),VaultResponse(429,{'error':'captcha required'})])
    scope=account_policy(v,tmp_path)
    try:
        v.refresh(1);v.refresh(2)
        with pytest.raises(VaultError,match='pixiv-network-stopped'):v.search('sample')
        assert len(r.calls)==3 and scope.gate.snapshot().blocked
    finally:g.close();v.close()
