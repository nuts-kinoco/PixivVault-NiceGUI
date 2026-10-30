"""Synthetic external vault policies; no network, Cookie, or user data."""
import base64
from contextlib import contextmanager
import hashlib
import errno
import json
import threading
import pytest
from pixivvault_nicegui.external_vault import (ExternalVaultService,LegacyVaultCrypto,VaultError,CryptoUnavailable,
    parse_uid_text,normalize_data,keeper_plan,proactive_wait)
from pixivvault_nicegui.external_vault_transport import (ShaftTransport,VaultResponse,OAuthProfile,ACCOUNT,OAUTH,INVALID,ONLINE,SEARCH,
    search_params,canonical,build_online_payload,default_transport,RateLimited)

class FixtureCrypto:
    """Test-only serialization, never a production fallback or AES claim."""
    available=True
    def derive(self,password,salt):return hashlib.sha256(password.encode()+salt).digest(),b'K'*32
    def encrypt(self,data,key):return b'fixture-only:'+base64.b64encode(json.dumps(data).encode())
    def decrypt(self,data,key):return json.loads(base64.b64decode(data.removeprefix(b'fixture-only:')))

class FixtureTransport:
    def __init__(self):self.calls=[];self.response={'illusts':[]};self.error=None
    def fetch_account(self,uid,secret,slot,now):
        self.calls.append(('refresh',uid))
        if self.error:raise self.error
        return {'access_token':'synthetic-access-'+str(uid),'refresh_token':'synthetic-refresh','expires_in':3600,'token_expires_at_ms':(now+3600)*1000,'account_uid':uid+100}
    @contextmanager
    def session_guard(self,valid):
        if not valid():raise VaultError('stale-session')
        yield
    def clear_pending(self):pass
    def search(self,token,params):self.calls.append(('search',token,params));return self.response

@pytest.fixture
def vault(tmp_path):
    transport=FixtureTransport();service=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=transport,clock=lambda:1000)
    service.initialize('fixture-password','1\n2','fixture-secret')
    yield service,transport
    service.close()

def test_restart_unlock_migration_and_safe_status(tmp_path):
    root=tmp_path/'v';crypto=FixtureCrypto();salt=b'0'*16;verify,key=crypto.derive('pass',salt)
    old={'secret_key':'synthetic-secret','shaft_uid':17,'access_token':'synthetic-token','user_email':'must-not-persist'}
    v=ExternalVaultService(root,crypto=crypto);v.import_legacy('pass',salt=salt,verify=verify,ciphertext=crypto.encrypt(old,key))
    assert old['shaft_uid']==17
    assert '17' not in repr(v.status()) and 'synthetic' not in repr(v.status())
    v.close();v=ExternalVaultService(root,crypto=crypto)
    try:
        assert not v.status()['open']
        with pytest.raises(VaultError,match='unlock-failed'):v.unlock('wrong')
        v.unlock('pass');assert v.status()['open']
        _,data=v._snapshot();assert 'user_email' not in data and data['shaft_uids']==[17]
    finally:v.close()

def test_owner_foreign_root_and_generation_conflict(vault):
    v,t=vault
    with pytest.raises(Exception):ExternalVaultService(v.root,crypto=FixtureCrypto())
    v.refresh();first=v._revision
    (v.root/'CURRENT').write_text('a'*32)
    with pytest.raises(VaultError,match='generation-conflict'):v.refresh(2)
    assert (v.root/'generations'/first/'vault.enc').exists()

def test_crypto_missing_has_no_plaintext_fallback(tmp_path):
    class Missing(FixtureCrypto):available=False
    v=ExternalVaultService(tmp_path/'v',crypto=Missing())
    try:
        with pytest.raises(CryptoUnavailable):v.initialize('p','1','s')
        assert list((v.root/'generations').iterdir())==[]
    finally:v.close()

def test_foreign_root_untouched(tmp_path):
    root=tmp_path/'foreign';root.mkdir();(root/'user.txt').write_text('original')
    with pytest.raises(VaultError,match='foreign-root'):ExternalVaultService(root)
    assert list(root.iterdir())==[root/'user.txt']

@pytest.mark.parametrize('text', ['','0','-2','1\nbad','\n'.join(map(str,range(1,12)))])
def test_uid_rejects_bad_values_without_echo(text):
    with pytest.raises(VaultError) as e:parse_uid_text(text)
    assert 'bad' not in str(e.value)

def test_uid_bom_comment_dedup():assert parse_uid_text('\ufeff1 # hello\n2\n1')==[1,2]

def test_round_robin_failure_fallback_and_relative_expiry(vault):
    v,t=vault;v.refresh(1);v.refresh(2)
    v.search('tag');v.search('tag')
    assert [call[1] for call in t.calls if call[0]=='search']==['synthetic-access-1','synthetic-access-2']
    before=v._revision
    with pytest.raises(VaultError,match='short-payout'):v.refresh(1)
    assert v._revision!=before and v.status()['slots'][0]['failures']==1
    t.error=RateLimited(4000)
    with pytest.raises(VaultError,match='rate-limited'):v.refresh(2)
    assert v.status()['slots'][1]['retry_after']==3000
    count=len(t.calls)
    with pytest.raises(VaultError,match='backoff'):v.refresh(2)
    assert len(t.calls)==count

def test_lock_fences_inflight_refresh(vault):
    v,t=vault;entered=threading.Event();release=threading.Event();errors=[];original=t.fetch_account
    def block(*args):entered.set();assert release.wait(3);return original(*args)
    t.fetch_account=block;revision=v._revision
    def run():
        try:v.refresh()
        except VaultError as error:errors.append(error.code)
    worker=threading.Thread(target=run);worker.start();assert entered.wait(3)
    v.lock();release.set();worker.join(3)
    assert errors==['stale-session'] and not worker.is_alive() and v._revision==revision and not v.status()['open']

def test_search_invalid_grant_one_refresh_budget(vault):
    v,t=vault;calls=[]
    def search(token,params):calls.append(token);raise VaultError('invalid-grant')
    t.search=search
    with pytest.raises(VaultError):v.search('tag')
    assert len([c for c in t.calls if c[0]=='refresh'])==1 and len(calls)==1

def test_keeper_and_unknown_remaining():
    data=normalize_data({'secret_key':'s','shaft_uids':[1,2],'slots':{'1':{'token_expires_at_ms':1601000},'2':{'token_expires_at_ms':3000000}}})
    assert keeper_plan(data,1000)==(1,False,1)
    data['slots']['1']['last_fetch_at']=990
    assert keeper_plan(data,1002)==(1,False,900)
    assert proactive_wait({},1000)==0

def test_preferences_restart(vault):
    v,_=vault;v.save_ranking_preferences(['tag','tag'],{'bookmark_min':500})
    assert v.ranking_preferences()['tags']==['tag'] and v.ranking_preferences()['filters']['bookmark_min']==500

def profile():return OAuthProfile('fixture-UA','ios','1','1','fixture-salt','fixture-client','fixture-profile-secret')

class Request:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def __call__(self,method,url,**kwargs):self.calls.append((method,url,kwargs));return self.responses.pop(0)
    def fenced_request(self,method,url,*,fence,epoch,validate,**kwargs):
        validate();return self(method,url,**kwargs)

def test_signature_exact_payload_and_no_cookie():
    import hmac
    request=Request([VaultResponse(200,{'account':{'access_token':'fixture','extra':'discard'},'expiresAt':4600000,'uid':555})])
    transport=ShaftTransport(request,oauth_profiles=[profile()]);result=transport.fetch_account(1,'fixture-key',{},1000)
    _,url,args=request.calls[0]
    assert url==ACCOUNT and args['body']==b'{"uid":1}'
    assert args['headers']['X-Shaft-Sign']==hmac.new(b'fixture-key',args['body'],hashlib.sha256).hexdigest()
    assert not any(k.lower()=='cookie' for k in args['headers']) and result['account_uid']==555 and 'extra' not in result

def test_expired_payout_refresh_online_sanitized_identity():
    request=Request([VaultResponse(200,{'account':{'access_token':'expired','refresh_token':'fixture-old'},'expired':True,'uid':555}),
        VaultResponse(200,{'access_token':'fresh','refresh_token':'fixture-new','expires_in':3600,'user':{'id':777,'is_premium':True,'email':'private'},'email':'private'}),
        VaultResponse(500,{})])
    t=ShaftTransport(request,oauth_profiles=[profile()]);r=t.fetch_account(1,'fixture',{},1000)
    assert r['access_token']=='fresh' and r['token_expires_at_ms']==4600000
    assert [c[1] for c in request.calls]==[ACCOUNT,OAUTH,ONLINE]
    oauth=request.calls[1][2];assert 'include_policy=true' in oauth['body'].decode() and not any(k.startswith('x-client') for k in oauth['headers'])
    body=json.loads(request.calls[2][2]['body']);assert body['uid']==body['account']['user']['id']==777 and 'private' not in str(body)

def test_invalid_report_only_oauth_invalid_grant_actual_borrowed_uid():
    payout=VaultResponse(200,{'account':{'refresh_token':'fixture-old'},'expired':True,'uid':555})
    request=Request([payout,VaultResponse(400,{'error':'invalid_grant'}),VaultResponse(200,{'ok':True}),payout,VaultResponse(400,{'error':'invalid_grant'})])
    t=ShaftTransport(request,oauth_profiles=[profile(),profile()]);slot={}
    with pytest.raises(VaultError,match='expired-payout'):t.fetch_account(1,'fixture',slot,1000)
    invalid=[c for c in request.calls if c[1]==INVALID];assert len(invalid)==1 and json.loads(invalid[0][2]['body'])['uid']==555
    assert len([c for c in request.calls if c[1]==OAUTH])==2

def test_payout_429_never_retry():
    request=Request([VaultResponse(429,{'quotas':[{'remaining':0,'resetsAt':5000000},{'remaining':0,'resetsAt':4000000}]})])
    with pytest.raises(RateLimited) as e:ShaftTransport(request,oauth_profiles=[profile()]).fetch_account(1,'s',{},1000)
    assert e.value.retry_at==4000 and len(request.calls)==1

def test_search_query_and_headers_independent_ai_r18():
    from datetime import datetime,timezone
    req=Request([VaultResponse(200,{'illusts':[]})]);t=ShaftTransport(req,oauth_profiles=[profile()],clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
    params=search_params('tag',500,r18=True,exclude_ai=True,offset=30);t.search('fixture-access',params)
    assert params['search_ai_type']==0 and params['offset']==30 and 'r18' not in params
    assert 'offset' not in search_params('tag')
    headers=req.calls[0][2]['headers'];assert headers['Authorization']=='Bearer fixture-access' and 'Cookie' not in headers
    assert headers['x-client-hash']==hashlib.md5((headers['x-client-time']+'fixture-salt').encode()).hexdigest()

def test_default_transport_constructs_without_request():assert default_transport().request.__class__.__name__=='UrlopenVaultRequest'

@pytest.mark.parametrize('status,headers,body,expected',[
    (503,{}, {},30),(408,{}, {},30),(400,{}, {},None),(200,{}, {'ok':False},None),
    (429,{'Retry-After':'20'}, {},20),(429,{'Retry-After':'0'}, {},None),
    (429,{'Retry-After':'61'}, {},None),(429,{'Retry-After':'20'}, {'quotas':[{'scope':'uid_weekly','remaining':0}]},None),
])
def test_online_retry_policy(status,headers,body,expected):
    req=Request([VaultResponse(status,body,headers)]);t=ShaftTransport(req,oauth_profiles=[profile()])
    t.queue_online('fixture',55,{'access_token':'a','refresh_token':'r','user':{'id':55}},1000)
    wait=t.online_tick(1000)
    assert wait==(900 if expected is None else expected)
    assert bool(t._online)==(expected is not None)
    t.clear_pending();assert not t._online

def test_online_retry_cap_and_no_wait_sleep():
    req=Request([VaultResponse(503,{}) for _ in range(5)]);t=ShaftTransport(req,oauth_profiles=[profile()])
    t.queue_online('fixture',55,{'access_token':'a','refresh_token':'r'},1000)
    for now in (1000,1030,1090,1210,1450):t.online_tick(now)
    assert len(req.calls)==5 and not t._online

def test_search_metadata_cache_is_validated_and_lock_clears(vault):
    v,t=vault;t.response={'illusts':[{'id':123,'page_count':2,'type':'manga','meta_pages':[
        {'image_urls':{'large':'https://i.pximg.net/img-master/img/2026/01/01/00/00/00/123_p0_master1200.jpg'}},
        {'image_urls':{'large':'https://i.pximg.net/img-master/img/2026/01/01/00/00/00/123_p1_master1200.jpg'}}]}]}
    v.search('tag');assert len(v.get_artwork_pages('123'))==2
    v.lock()
    with pytest.raises(VaultError,match='locked'):v.get_artwork_pages('123')

def test_keeper_starts_only_explicitly_stops_on_lock(vault):
    import time
    v,t=vault;assert v._keeper is None
    v.start_keeper();worker=v._keeper
    deadline=time.monotonic()+3
    while not t.calls and time.monotonic()<deadline:time.sleep(.01)
    assert t.calls
    v.lock();worker.join(3)
    assert not worker.is_alive() and not v.status()['open']


@pytest.mark.parametrize('field,value', [('access_token',['x']), ('refresh_token',False),
    ('token_type', 12), ('expires_in',float('nan')), ('token_expires_at_ms',-1),
    ('account_uid',True)])
def test_invalid_token_fields_do_not_publish_and_existing_generation_unlocks(vault, field, value):
    v,t=vault
    v.refresh(1)
    old_revision=v._revision
    _,before=v._snapshot()
    response={'access_token':'synthetic-new','token_expires_at_ms':999999999}
    response[field]=value
    t.fetch_account=lambda *args: response
    with pytest.raises(VaultError,match='invalid-response'):
        v.refresh(1)
    assert v._revision==old_revision and v._snapshot()[1]==before
    v.lock()
    v.unlock('fixture-password')
    assert v._snapshot()[1]==before


def synthetic_fenced(request):
    def fenced(method,url,*,fence,epoch,validate,**kwargs):
        validate();return request(method,url,**kwargs)
    request.fenced_request=fenced
    return request


def test_lock_does_not_wait_for_online_http_and_cannot_restore_its_retry(vault):
    v,_=vault;entered=threading.Event();release=threading.Event();locked=threading.Event();calls=[]
    def request(method,url,**kwargs):
        calls.append(url);entered.set();assert release.wait(5)
        return VaultResponse(503,{})
    transport=ShaftTransport(synthetic_fenced(request),oauth_profiles=[profile()]);v.transport=transport
    transport.queue_online('synthetic-secret',99,{'access_token':'a','refresh_token':'r'},1000)
    worker=threading.Thread(target=lambda:transport.online_tick(1000));worker.start()
    assert entered.wait(5)
    locker=threading.Thread(target=lambda:(v.lock(),locked.set()));locker.start()
    try:
        assert locked.wait(1), 'vault lock waited for the HTTP response'
        assert not v.status()['open']
    finally:
        release.set();worker.join(5);locker.join(5)
    assert not worker.is_alive() and not locker.is_alive()
    transport.online_tick(2000)
    assert len(calls)==1


def test_lock_during_account_response_prevents_followup_oauth_and_online(vault):
    v,_=vault;entered=threading.Event();release=threading.Event();calls=[];errors=[]
    def request(method,url,**kwargs):
        calls.append(url)
        if url==ACCOUNT:
            entered.set();assert release.wait(5)
            return VaultResponse(200,{'account':{'refresh_token':'synthetic'},'expired':True,'uid':99})
        raise AssertionError('post-lock followup request')
    v.transport=ShaftTransport(synthetic_fenced(request),oauth_profiles=[profile()])
    def refresh():
        try:v.refresh(1)
        except VaultError as error:errors.append(error.code)
    worker=threading.Thread(target=refresh);worker.start()
    assert entered.wait(5);v.lock();release.set();worker.join(5)
    assert not worker.is_alive() and calls==[ACCOUNT] and errors==['stale-session']


def test_online_queue_cannot_adopt_old_session_after_clear():
    entered=threading.Event();release=threading.Event();errors=[];calls=[]
    transport=ShaftTransport(lambda method,url,**kwargs:calls.append(url),oauth_profiles=[profile()])
    def gate():entered.set();assert release.wait(5)
    transport._check_session=gate
    def queue():
        try:transport.queue_online('synthetic',99,{'access_token':'a','refresh_token':'r'},1000)
        except VaultError as error:errors.append(error.code)
    worker=threading.Thread(target=queue);worker.start();assert entered.wait(5)
    transport.clear_pending();release.set();worker.join(5)
    assert not worker.is_alive() and errors==['stale-session']
    transport._check_session=lambda:None;transport.online_tick(2000)
    assert calls==[]


def test_selected_online_job_is_fenced_after_lock_before_request():
    entered=threading.Event();release=threading.Event();calls=[]
    def request(method,url,**kwargs):calls.append(url);return VaultResponse(200,{})
    transport=ShaftTransport(synthetic_fenced(request),oauth_profiles=[profile()])
    original=transport._signed
    def gated(*args):
        entered.set();assert release.wait(5)
        return original(*args)
    transport._signed=gated
    transport.queue_online('synthetic',99,{'access_token':'a','refresh_token':'r'},1000)
    worker=threading.Thread(target=lambda:transport.online_tick(1000));worker.start()
    assert entered.wait(5);transport.clear_pending();release.set();worker.join(5)
    assert not worker.is_alive() and calls==[]
    transport.online_tick(2000);assert calls==[]


def test_unverified_shutdown_blocks_same_process_unlock_auto_keeper_refresh_and_close(tmp_path):
    """A failed OS shutdown must never make an already-poisoned vault usable."""
    class AutounlockCrypto(FixtureCrypto):
        def wrap_key(self,key,*,binding=None):return b'synthetic-wrapped:'+key
        def unwrap_key(self,data,*,binding=None):
            assert data.startswith(b'synthetic-wrapped:')
            return data[len(b'synthetic-wrapped:'):]

    class RefusingSocket:
        def dup(self):return self
        def shutdown(self,how):raise OSError(errno.EIO,'synthetic shutdown failure')
        def close(self):pass

    request=Request([])
    transport=ShaftTransport(request,oauth_profiles=[profile()])
    vault=ExternalVaultService(tmp_path/'poisoned-vault',transport=transport,
                               crypto=AutounlockCrypto(),clock=lambda:1000)
    try:
        vault.initialize('fixture-password','1','fixture-secret')
        vault.configure_auto_unlock(True,binding=b'fixture-binding')
        fence=transport._socket_fence
        with fence.register(RefusingSocket(),fence.snapshot()):
            with pytest.raises(VaultError,match='socket-shutdown-unverified'):
                vault.lock()
        assert transport.revocation_unverified and fence.revocation_unverified
        status=vault.status()
        assert status['socket_shutdown_unverified'] is True
        assert status['transport_available'] is False and not status['open']
        assert not vault.try_auto_unlock(binding=b'fixture-binding')
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.unlock('fixture-password')
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.start_keeper()
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.refresh(1)
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.search('synthetic')
        assert vault._keeper is None and request.calls==[]
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.close()
        assert vault._owner is not None and not vault._closed
        with pytest.raises(Exception):
            # While owner is not verified closed, a fresh writer cannot take it.
            ExternalVaultService(vault.root,transport=transport,crypto=AutounlockCrypto())
    finally:
        # Release only this disposable synthetic fixture's owner after verifying
        # that the product correctly retained it on an unverified close.
        if vault._owner is not None:
            vault._owner.__exit__(None,None,None)
            vault._owner=None


def test_late_socket_poison_during_unlock_never_publishes_decrypted_data(tmp_path):
    """A prior health check cannot grant an unlock after another thread poisons."""
    entered=threading.Event();release=threading.Event();errors=[]
    crypto=FixtureCrypto()
    transport=ShaftTransport(Request([]),oauth_profiles=[profile()])
    vault=ExternalVaultService(tmp_path/'late-poison',crypto=crypto,transport=transport)
    try:
        vault.initialize('fixture-password','1','fixture-secret')
        vault.lock()
        original=crypto.decrypt
        def blocked(raw,key):
            entered.set()
            assert release.wait(5)
            return original(raw,key)
        crypto.decrypt=blocked
        def run():
            try:vault.unlock('fixture-password')
            except VaultError as error:errors.append(error.code)
        worker=threading.Thread(target=run)
        worker.start()
        try:
            assert entered.wait(5)
            transport._socket_fence.mark_close_unverified(
                OSError(errno.EIO,'synthetic close race'))
        finally:
            release.set();worker.join(5)
        assert not worker.is_alive()
        assert errors==['socket-shutdown-unverified']
        assert not vault.status()['open'] and vault._data is None
        assert vault._revision is not None and transport.revocation_unverified
        assert not transport.request.calls
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):vault.close()
        assert vault._owner is not None and not vault._closed
    finally:
        release.set()
        if vault._owner is not None:
            vault._owner.__exit__(None,None,None)
            vault._owner=None


def test_poisoned_transport_refuses_fenced_adapter_and_online_queue_without_callbacks():
    request=Request([])
    transport=ShaftTransport(request,oauth_profiles=[profile()])
    transport._socket_fence.mark_close_unverified(OSError(errno.EIO,'synthetic stop failure'))
    assert transport.revocation_unverified
    with pytest.raises(VaultError,match='socket-shutdown-unverified'):
        with transport.session_guard(lambda:True):
            transport._request('POST',ACCOUNT,{},b'{}')
    with pytest.raises(VaultError,match='socket-shutdown-unverified'):
        transport.queue_online('synthetic',1,{'access_token':'a','refresh_token':'r'},1000)
    assert transport.online_tick(1000)==900 and transport._online==[]
    assert request.calls==[]


def test_poison_during_fake_online_callback_cannot_enqueue_retry():
    class PoisoningRequest(Request):
        def fenced_request(self,method,url,*,fence,epoch,validate,**kwargs):
            validate()
            fence.mark_close_unverified(OSError(errno.EIO,'synthetic callback close failure'))
            self.calls.append((method,url,kwargs))
            return VaultResponse(503,{})

    request=PoisoningRequest([])
    transport=ShaftTransport(request,oauth_profiles=[profile()])
    transport.queue_online('synthetic',1,{'access_token':'a','refresh_token':'r'},1000)
    assert transport.online_tick(1000)==900
    assert len(request.calls)==1 and transport.revocation_unverified
    assert transport._online==[]
    assert transport.online_tick(1030)==900 and len(request.calls)==1
