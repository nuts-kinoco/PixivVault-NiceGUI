"""Synthetic wire/route acceptance; no credentials, cookies or external I/O."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
import hashlib
import hmac
import json

import pytest
from pixivvault_nicegui import external_vault_transport as wire
from pixivvault_nicegui.external_vault import ExternalVaultService, VaultError
from pixivvault_nicegui.ranking_source import RankingSource, RankingQuery
from test_external_vault import FixtureCrypto, FixtureTransport

NOW = 1000

def profile():
    return wire.OAuthProfile('fixture-app', 'ios', 'fixture', '1', 'salt', 'client', 'secret')

def transport(request):
    return wire.ShaftTransport(request, oauth_profiles=[profile()],
        clock=lambda: datetime(2026, 11, 8, tzinfo=timezone.utc))

def row(wid=12):
    return dict(id=wid, title='Synthetic work', type='illust', user=dict(id=7, name='Synthetic artist'),
        tags=[dict(name='sample')], page_count=1, x_restrict=0, illust_ai_type=1,
        image_urls=dict(medium='https://i.pximg.net/c/250x250_80_a2/img-master/img/2026/01/01/00/00/00/12_p0_square1200.jpg'),
        total_bookmarks=30, total_view=40, create_date='2026-11-01T00:00:00+09:00')

def test_signed_canonical_payout_request():
    seen=[]
    def request(method, url, **kwargs):
        seen.append((method,url,kwargs))
        return wire.VaultResponse(200, dict(uid=1, account=dict(access_token='fixture-access',
            refresh_token='fixture-refresh', user=dict(id=99,is_premium=True)), expiresAt=4600000))
    result=transport(request).fetch_account(1,'fixture-signing-key',{},NOW)
    method,url,kw=seen[0]
    assert (method,url,kw['body'])==('POST',wire.ACCOUNT,b'{"uid":1}')
    assert kw['headers']['X-Shaft-Sign']==hmac.new(b'fixture-signing-key',kw['body'],hashlib.sha256).hexdigest()
    assert result['account_uid']==99  # top-level uid is requester, not borrowed identity

def test_app_search_explicit_authorization():
    seen=[]
    def request(method,url,**kwargs):
        seen.append((method,url,kwargs));return wire.VaultResponse(200,{'illusts':[row()]})
    assert transport(request).search('fixture-access',wire.search_params('sample'))['illusts']
    headers=seen[0][2]['headers']
    assert 'X-Shaft-Explicit-Authorization' not in headers
    assert headers['Authorization']=='Bearer fixture-access'
    assert not any(k.lower()=='cookie' for k in headers)

def test_expired_stored_token_requests_payout_first():
    seen=[]
    def request(method,url,**kwargs):
        seen.append(url)
        assert url==wire.ACCOUNT
        return wire.VaultResponse(200,dict(account=dict(access_token='fresh-payout',
            refresh_token='fresh-refresh',user=dict(id=99,is_premium=True)),expiresAt=4600000))
    result=transport(request).fetch_account(1,'fixture-key',dict(refresh_token='stored',
        account_uid=99,token_expires_at_ms=0),NOW)
    assert result['access_token']=='fresh-payout'
    assert seen==[wire.ACCOUNT]


def test_expired_payout_refreshes_returned_credential_after_payout():
    seen=[]
    def request(method,url,**kwargs):
        seen.append(url)
        if url==wire.ACCOUNT:
            return wire.VaultResponse(200,dict(expired=True,account=dict(
                access_token='expired',refresh_token='payout-refresh',user=dict(id=99,is_premium=True))))
        if url==wire.OAUTH:
            form=parse_qs(kwargs['body'].decode())
            assert form['refresh_token']==['payout-refresh']
            return wire.VaultResponse(200,dict(access_token='renewed',refresh_token='rotated',expires_in=3600))
        assert url==wire.ONLINE
        return wire.VaultResponse(200,{})
    result=transport(request).fetch_account(1,'fixture-key',dict(refresh_token='stored',
        account_uid=99,token_expires_at_ms=0),NOW)
    assert result['access_token']=='renewed'
    assert seen[:2]==[wire.ACCOUNT,wire.OAUTH]


def test_quota_waits_for_all_exhausted_windows():
    assert wire.quota_retry_at({'quotas':[dict(remaining=0,resetsAt=2000000),
        dict(used=10,max=10,resetsAt=3000000)]},NOW)==3000

def test_payout_429_never_repeats():
    seen=[]
    def request(method,url,**kw):
        seen.append(url);return wire.VaultResponse(429,{'quotas':[dict(remaining=0,resetsAt=2000000)]})
    with pytest.raises(wire.RateLimited) as error:transport(request).fetch_account(1,'fixture',{},NOW)
    assert error.value.retry_at==2000 and seen==[wire.ACCOUNT]

def test_successful_payout_remembers_exhausted_quota_without_blocking_token():
    seen=[]
    def request(method,url,**kw):
        seen.append(url)
        return wire.VaultResponse(200,dict(uid=1,account=dict(access_token='valid',user=dict(id=99)),
            expiresAt=4600000,quotas=[dict(remaining=0,resetsAt=2000000)]))
    t=transport(request);slot={};result=t.fetch_account(1,'fixture',slot,NOW)
    assert result['access_token']=='valid' and result['payout_not_before']==2000
    with pytest.raises(wire.RateLimited):t.fetch_account(1,'fixture',slot,NOW)
    assert seen==[wire.ACCOUNT]

def test_requester_is_never_reported_as_borrowed_identity():
    seen=[]
    def request(method,url,**kw):
        seen.append(url)
        if url==wire.ACCOUNT:return wire.VaultResponse(200,dict(uid=1,expired=True,account=dict(refresh_token='dead')))
        return wire.VaultResponse(400,{'error':'invalid_grant'})
    with pytest.raises(VaultError):transport(request).fetch_account(1,'fixture',{},NOW)
    assert wire.INVALID not in seen

def test_dated_ranking_uses_shared_stop_gate_and_never_falls_back(tmp_path):
    from test_oauth_send_boundary import make_vault,OfflineWire,owned_scope
    from pixivvault_nicegui.external_vault_transport import UrlopenVaultRequest
    # Production-shaped wire emits an observed 429 at the new exact app route.
    v,request,scope=make_vault(tmp_path,[wire.VaultResponse(200,dict(uid=1,
        account=dict(access_token='valid',user=dict(id=99)),expiresAt=4600000)),
        wire.VaultResponse(429,{})])
    try:
        v.refresh(1)
        with pytest.raises(VaultError,match='pixiv-network-stopped'):v.ranking(date='20200102')
        assert scope.gate.snapshot().blocked
        count=len(request.calls)
        with pytest.raises(VaultError):v.search('sample')
        assert len(request.calls)==count
    finally:v.close()

def test_own_personal_fallback_labels_unavailable_conditions():
    from pixivvault_nicegui.premium_read import PremiumSearch
    own=OwnReader();packet=PremiumSearch(None,own).search('sample',100)
    assert packet['illusts']==[] and 'ブクマ数下限は未適用' in packet['order_label']
    assert len(own.calls)==2

@pytest.mark.asyncio
async def test_ui_setup_requires_user_key_and_clears_input(tmp_path,monkeypatch):
    from nicegui import Client,ui
    from types import SimpleNamespace
    from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
    t=FixtureTransport();v=ExternalVaultService(tmp_path/'ui-vault',transport=t,crypto=FixtureCrypto())
    monkeypatch.setattr(v,'start_keeper',lambda **kw:None)
    client=Client(ui.page('/_borrow_setup'))
    view=None
    try:
        with client:view=ExternalVaultView(SimpleNamespace(external_vault=v))
        assert view.secret.value in ('',None)
        view.password.set_value('generated-password');view.uid_text.set_value('1')
        await view.action('initialize')
        assert not v.status()['open'] and not t.calls
        assert '署名キーを入力' in view.notice.text
        view.password.set_value('generated-password');view.uid_text.set_value('1');view.secret.set_value('synthetic-user-key')
        await view.action('initialize')
        assert v.status()['open'] and v._snapshot()[1]['secret_key']=='synthetic-user-key'
        assert not t.calls and view.secret.value==''
    finally:
        if view:view.close()
        client.delete();v.close()

@pytest.mark.asyncio
async def test_header_toggle_unlocks_through_popup_with_earlier_vault_password(tmp_path,monkeypatch):
    import asyncio
    from nicegui import Client,ui,core
    from types import SimpleNamespace
    from pixivvault_nicegui.premium_gate import PremiumAuthorization
    from pixivvault_nicegui.ui.shell import DemoShell
    from test_external_vault_ui import setup
    from test_premium_authorization import Protection,CHECK,TEST_PASSWORD
    from pixivvault_nicegui import external_vault_autounlock
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    protection=Protection();monkeypatch.setattr(external_vault_autounlock,'_dpapi',protection)
    services,v,t,_=setup(tmp_path)
    v.lock();monkeypatch.setattr(v,'start_keeper',lambda **kw:None)
    gate=PremiumAuthorization(v,protect=protection,verifier=CHECK,clock=lambda:1000)
    v.premium_authorization=gate;services.premium_authorization=gate
    default=tmp_path/'default.txt';default.write_bytes(b'99999\n');services.default_uid_path=default
    client=Client(ui.page('/_borrow_popup_unlock'));shell=None
    try:
        with client:shell=DemoShell(services)
        dialog=shell.borrow_dialog
        assert dialog is not None and not gate.status()['enabled']
        task=asyncio.create_task(shell.borrow_changed(SimpleNamespace(value=True)))
        await asyncio.sleep(0)
        dialog.password.set_value(TEST_PASSWORD);dialog.password_dialog.submit(True)
        for _ in range(500):
            if dialog.legacy_box.visible or task.done():break
            await asyncio.sleep(.01)
        # activation password differs from the vault's: the popup asks for the earlier one
        assert dialog.legacy_box.visible and not v.status()['open']
        dialog.legacy.set_value('fixture-password');dialog.password_dialog.submit(True)
        for _ in range(500):
            if dialog.save_dialog.value or task.done():break
            await asyncio.sleep(.01)
        dialog.save_dialog.submit(False)
        await asyncio.wait_for(task,10)
        assert gate.status()['enabled'] and v.status()['open'] and t.calls==[]
        assert v._snapshot()[1]['shaft_uids']==[1]  # default file never overwrote the saved UIDs
        assert shell.borrow_switch.value
    finally:
        if shell:shell.close()
        client.delete();gate.close();services.close()

@pytest.mark.asyncio
async def test_header_toggle_and_library_search_entry_are_local_until_fetch(tmp_path,monkeypatch):
    import asyncio
    from nicegui import Client,ui,core
    from types import SimpleNamespace
    from pixivvault_nicegui.ui.shell import DemoShell,NAVIGATION,AVAILABLE_TABS
    from test_external_vault_ui import setup
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    services,v,t,_=setup(tmp_path)
    v.lock();monkeypatch.setattr(v,'start_keeper',lambda **kw:None)
    monkeypatch.setattr(v,'_write_auto',lambda enabled:None)
    client=Client(ui.page('/_borrow_navigation'));shell=None
    try:
        with client:shell=DemoShell(services)
        assert shell.borrow_switch is not None and not shell.borrow_switch.value
        await shell.borrow_changed(SimpleNamespace(value=True))
        # The removed Shaft tab must not exist as a navigation entry or tab at all.
        assert 'external' not in dict(NAVIGATION) and 'external' not in AVAILABLE_TABS
        assert not any('Shaft' in label or '外部連携' in label for _,label in NAVIGATION)
        assert not v.status()['open']
        v.unlock('fixture-password');shell.sync_borrow()
        assert shell.borrow_switch.value and t.calls==[]
        shell.open_premium_search('sample')
        assert shell.active_tab=='personal' and shell.personal_view.tags.value=='sample' and t.calls==[]
        await shell.borrow_changed(SimpleNamespace(value=False))
        assert not v.status()['open'] and not shell.borrow_switch.value
    finally:
        if shell:shell.close()
        client.delete();services.close()
        await asyncio.sleep(0)

def test_ranking_app_route_is_exact_and_authenticated():
    seen=[]
    def request(method,url,**kw):
        seen.append((method,url,kw));return wire.VaultResponse(200,{'illusts':[row()]})
    t=transport(request)
    assert t.ranking('fixture',mode='day',date='20200102',offset=30)['illusts']
    assert urlsplit(seen[0][1]).path=='/v1/illust/ranking'
    assert parse_qs(urlsplit(seen[0][1]).query)['date']==['2020-01-02']
    assert 'X-Shaft-Explicit-Authorization' not in seen[0][2]['headers']
    with pytest.raises(VaultError):t.ranking('fixture',mode='https://other.invalid')

def test_vault_ranking_reuses_encrypted_slot(tmp_path):
    class T(FixtureTransport):
        def ranking(self,token,**kwargs):self.calls.append(('ranking',kwargs));return {'illusts':[row()]}
    t=T();v=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=t,clock=lambda:NOW)
    try:
        v.initialize('password','1','fixture-key');v.refresh()
        assert v.ranking(mode='day',date='20200102')['illusts']
        assert len([c for c in t.calls if c[0]=='refresh'])==1
    finally:v.close()

class OwnReader:
    def __init__(self):self.calls=[]
    def require_identity(self):self.calls.append('identity')
    def get_ranking(self,**kw):self.calls.append(('ranking',kw));return []
    def search_illusts(self,*args,**kw):self.calls.append(('search',args,kw));return {'works':[]}

class Premium:
    def __init__(self,error=None):self.error=error;self.calls=[]
    def status(self):return dict(open=True,mode='nana7mi',transport_available=True)
    def search(self,*a,**kw):
        self.calls.append(('search',a,kw))
        if self.error:raise VaultError(self.error)
        return {'illusts':[row()], 'next_url':None}
    def ranking(self,**kw):
        self.calls.append(('ranking',kw))
        if self.error:raise VaultError(self.error)
        return {'illusts':[row()], 'next_url':None}

def test_date_ranking_uses_premium_and_keeps_own_session_separate():
    own=OwnReader();premium=Premium()
    page=RankingSource(own,premium).fetch(RankingQuery(date='20200102'))
    assert page.items[0].record.author_name=='Synthetic artist'
    assert premium.calls[0][0]=='ranking' and own.calls==[]

def test_tag_ranking_retains_next_page():
    own=OwnReader();premium=Premium()
    page=RankingSource(own,premium).fetch(RankingQuery(tag='sample'),page=2)
    assert len(page.items)==1 and premium.calls[0][2]['offset']==30

@pytest.mark.parametrize('error',['rate-limited','backoff','payout-failed'])
def test_borrow_unavailable_falls_back_to_own_with_honest_order(error):
    own=OwnReader();page=RankingSource(own,Premium(error)).fetch(RankingQuery(tag='sample'))
    assert own.calls and '新着' in page.order_label and '人気順' in page.order_label

@pytest.mark.parametrize('error',['pixiv-network-stopped','pixiv-outcome-uncertain','socket-shutdown-unverified'])
def test_stop_and_unknown_never_start_own_fallback(error):
    own=OwnReader()
    with pytest.raises(Exception):RankingSource(own,Premium(error)).fetch(RankingQuery(tag='sample'))
    assert own.calls==[]
