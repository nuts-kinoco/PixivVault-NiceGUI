import pytest
from urllib.parse import parse_qs
from test_oauth_send_boundary import make_vault,payout,reopened
from pixivvault_nicegui.external_vault_transport import VaultResponse,ACCOUNT,OAUTH

@pytest.mark.parametrize('status',[400,403])
def test_oauth_client_rejection_advances_profile_without_latching(tmp_path,status):
    v,wire,scope=make_vault(tmp_path,[payout(),VaultResponse(status,{'error':{'code':918}}),
        VaultResponse(200,{'access_token':'generated-a','refresh_token':'generated-r','expires_in':3600}),VaultResponse(200,{})])
    try:
        v.refresh(1)
        assert wire.calls[:3]==[ACCOUNT,OAUTH,OAUTH]
        assert not scope.gate.snapshot().blocked
        assert not v.status()['oauth_review_required']
        assert not (scope.root/'.pixivvault-user-stop-inflight.v1').exists()
        v=reopened(v)
        assert not v.status()['oauth_review_required']
    finally:v.close()

def test_short_unexpired_payout_refreshes_instead_of_discarding(tmp_path):
    short=VaultResponse(200,{'expired':False,'expiresAt':1200000,'account':{
        'access_token':'generated-short','refresh_token':'generated-r','user':{'id':42}}})
    v,wire,scope=make_vault(tmp_path,[short,VaultResponse(200,{
        'access_token':'generated-new','refresh_token':'generated-new-r','expires_in':3600}),VaultResponse(200,{})])
    try:
        v.refresh(1)
        assert wire.calls[:2]==[ACCOUNT,OAUTH]
        assert not v.status()['oauth_review_required']
    finally:v.close()


def test_complete_oauth_server_errors_do_not_leave_pending(tmp_path):
    from pixivvault_nicegui.external_vault import VaultError
    v,wire,scope=make_vault(tmp_path,[payout(),VaultResponse(503,{}),VaultResponse(503,{}),
        payout(),VaultResponse(503,{}),VaultResponse(503,{})])
    try:
        with pytest.raises(VaultError):v.refresh(1)
        assert not v.status()['oauth_review_required']
        assert not scope.gate.snapshot().blocked
        assert wire.calls.count(OAUTH)==4
    finally:v.close()


def test_oauth_wire_uses_only_original_headers_and_five_form_fields(tmp_path):
    v,wire,scope=make_vault(tmp_path,[payout(),VaultResponse(200,{
        'access_token':'generated-a','refresh_token':'generated-r','expires_in':3600}),VaultResponse(200,{})])
    original=wire.fenced_request;seen=[]
    def capture(method,url,**kwargs):
        if url==OAUTH:
            assert set(k.lower() for k in kwargs['headers'])=={'user-agent','content-type'}
            assert set(parse_qs(kwargs['body'].decode()))=={'grant_type','refresh_token','client_id','client_secret','include_policy'}
            seen.append(True)
        return original(method,url,**kwargs)
    wire.fenced_request=capture
    try:
        v.refresh(1)
        assert seen==[True]
    finally:v.close()


def test_first_search_fetches_once_then_reads_without_manual_refresh(tmp_path):
    from pixivvault_nicegui.external_vault_transport import SEARCH
    fresh=VaultResponse(200,{'account':{'access_token':'generated-a','user':{'id':42}},'expiresAt':4600000})
    v,wire,scope=make_vault(tmp_path,[fresh,VaultResponse(200,{'illusts':[]})])
    try:
        assert v.search('sample')['illusts']==[]
        assert wire.calls[0]==ACCOUNT and wire.calls.count(ACCOUNT)==1
        assert wire.calls[1].split('?')[0]==SEARCH
        assert not v.status()['oauth_review_required']
    finally:v.close()
