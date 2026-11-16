"""Known structured non-premium reason; synthetic transport only."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.external_vault_transport import ShaftTransport, VaultResponse, ACCOUNT
from pixivvault_nicegui.personal_ranking import PersonalRankingSource, normalize_personal_filters
from pixivvault_nicegui.premium_read import PremiumSearch, unavailable_message
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from test_external_vault import profile

class Label:
    def __init__(self):self.text=''
    def set_text(self,value):self.text=value
    def disable(self):pass

def test_structured_false_is_only_known_nonpremium_no_retry():
    calls=[]
    def request(method,url,**kwargs):
        calls.append(url)
        return VaultResponse(200,{'account':{'access_token':'synthetic','user':{'id':9,'is_premium':False}},'expiresAt':9000000})
    t=ShaftTransport(request,oauth_profiles=[profile()],clock=lambda:datetime.fromtimestamp(1000,timezone.utc))
    with pytest.raises(VaultError,match='not-premium') as error:t.fetch_account(1,'synthetic',{},1000)
    assert error.value.code=='not-premium' and calls==[ACCOUNT]

def test_display_reason_is_bounded_and_not_inferred_from_unknown():
    assert '非プレミアム' in unavailable_message('not-premium')
    assert '非プレミアム' in unavailable_message('borrow-accounts-unavailable',reason='not-premium')
    assert '非プレミアム' not in unavailable_message('borrow-accounts-unavailable',reason='unknown')
    assert unavailable_message('pixiv-network-stopped',reason='not-premium')==unavailable_message('pixiv-network-stopped')
    assert unavailable_message('unknown',reason='not-premium') is None

def run_view(error):
    calls=[]
    class Vault:
        def status(self):return {'open':True,'mode':'nana7mi','transport_available':True}
        def search(self,*args,**kwargs):calls.append('borrow');raise error
    class Reader:
        def require_identity(self):calls.append('identity')
        def search_illusts(self,*args,**kwargs):calls.append('own-get');return {'works':[]}
    vault=Vault();source=PersonalRankingSource(PremiumSearch(vault,Reader(),preserve_expiry=True))
    v=PersonalRankingView.__new__(PersonalRankingView);v.active=True;v.closed=False;v.busy=False;v.epoch=1
    v.source=source;v.selected_tags=lambda:['synthetic'];v.current_filters=lambda:normalize_personal_filters({})
    v._search_available=lambda:True;v._context_task=None
    async def context():return {}
    v._refresh_context=context;v._server_dates=lambda:(None,None)
    v.r18=SimpleNamespace(value=False);v.ai=SimpleNamespace(value=False)
    v.fetch_button=Label();v.gallery=SimpleNamespace(more_button=Label());v.notice=Label()
    v.pin=None;v.page=None;v.proxy=SimpleNamespace(images=None);v.vault=vault;v.availability=lambda **kw:None
    asyncio.run(v.load())
    return v.notice.text,calls

def test_actual_personal_view_displays_known_reason_without_new_get():
    error=VaultError('borrow-accounts-unavailable');error.reason='not-premium'
    text,calls=run_view(error)
    assert '非プレミアム' in text and calls==['borrow']

def test_stop_does_not_become_nonpremium_or_trigger_own_get():
    text,calls=run_view(VaultError('pixiv-network-stopped'))
    assert '非プレミアム' not in text and '停止' in text and calls==['borrow']


def test_personal_view_busy_and_generic_codes_are_visible_without_secrets():
    text,calls=run_view(VaultError('busy'))
    assert text=='\u501f\u7528\u30a2\u30ab\u30a6\u30f3\u30c8\u306e\u6e96\u5099\u4e2d\u3067\u3059\u3002\u3057\u3070\u3089\u304f\u5f85\u3063\u3066\u304b\u3089\u3082\u3046\u4e00\u5ea6\u300c\u53d6\u5f97\u30fb\u66f4\u65b0\u300d\u3092\u62bc\u3057\u3066\u304f\u3060\u3055\u3044\u3002'
    assert calls==['borrow']
    text,_=run_view(VaultError('search-failed'))
    assert '\uff08\u7406\u7531: search-failed\uff09' in text
    for unsafe in ('C:/secret/token', '123456789', 'token-secret-123'):
        text,_=run_view(VaultError(unsafe))
        assert unsafe not in text
