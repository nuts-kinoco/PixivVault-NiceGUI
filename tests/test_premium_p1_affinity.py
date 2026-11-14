import pytest
from pixivvault_nicegui.external_vault import ExternalVaultService, VaultError
from pixivvault_nicegui.premium_read import PremiumSearch
from pixivvault_nicegui.personal_ranking import PersonalRankingSource
from pixivvault_nicegui.ranking_source import RankingSource, RankingQuery
from test_external_vault import FixtureCrypto, FixtureTransport
from test_premium_borrow_routes import row


@pytest.fixture
def stack(tmp_path):
    class Transport(FixtureTransport):
        def __init__(self):
            super().__init__(); self.reads=[]; self.read_error=None
        def search(self, token, params):
            self.reads.append((token, dict(params)))
            if self.read_error: raise self.read_error
            offset=params.get('offset',0)
            account=int(token.rsplit('-',1)[1])
            return {'illusts':[row(account*1000+offset+i) for i in range(30)],'next_url':'next'}
        def ranking(self, token, **params): return self.search(token,params)
    transport=Transport()
    service=ExternalVaultService(tmp_path/'v',crypto=FixtureCrypto(),transport=transport,clock=lambda:1000)
    service.initialize('synthetic-password','1\n2','synthetic-signing')
    service.refresh(1);service.refresh(2)
    yield service,transport
    service.close()


def test_pages_keep_account_but_new_query_rotates(stack):
    v,t=stack; source=PersonalRankingSource(PremiumSearch(v))
    first=source.fetch(['tag'],{})
    second=source.fetch(['tag'],{},previous=first)
    assert [token for token,_ in t.reads]==['synthetic-access-1']*2
    assert len(second.rows)==60 and {int(r['illust_id'])//1000 for r in second.rows}=={1}
    source.fetch(['new-tag'],{})
    assert t.reads[-1][0]=='synthetic-access-2'
    assert 'synthetic-access' not in repr(first.page_session)


@pytest.mark.parametrize('change',['account','expired','locked'])
def test_unavailable_page_account_never_silently_switches(stack,change):
    v,t=stack;source=PersonalRankingSource(PremiumSearch(v));first=source.fetch(['tag'],{})
    if change=='account': v._data['slots']['1']['account_uid']=999
    if change=='expired': v.clock=lambda:5000
    if change=='locked': v.lock()
    with pytest.raises(VaultError,match='paging-account-unavailable|stale-page'):
        source.fetch(['tag'],{},previous=first)
    assert len(t.reads)==1 and len(first.rows)==30


def test_page_session_rejects_different_query_and_other_vault(stack,tmp_path):
    v,t=stack; packet=PremiumSearch(v).search('tag'); session=packet['_borrow_session']
    with pytest.raises(VaultError,match='stale-page'):
        v.search('different',offset=30,page_session=session)
    other=ExternalVaultService(tmp_path/'other',crypto=FixtureCrypto(),transport=t,clock=lambda:1000)
    try:
        other.initialize('synthetic-password','1','synthetic-signing');other.refresh(1)
        with pytest.raises(VaultError,match='stale-page'):
            other.search('tag',offset=30,page_session=session)
    finally:other.close()
    assert len(t.reads)==1


def test_invalid_token_during_append_does_not_retry_other_account(stack):
    v,t=stack;source=PersonalRankingSource(PremiumSearch(v));first=source.fetch(['tag'],{})
    t.read_error=VaultError('invalid-grant')
    with pytest.raises(VaultError,match='paging-account-unavailable'):
        source.fetch(['tag'],{},previous=first)
    assert [token for token,_ in t.reads]==['synthetic-access-1']*2
    assert len(first.rows)==30


@pytest.mark.parametrize('query',[RankingQuery(tag='tag'),RankingQuery(date='20261031')])
def test_other_borrowed_ranking_pages_keep_affinity(stack,query):
    v,t=stack
    class Reader:
        def require_identity(self): pytest.fail('no own-account fallback during append')
    source=RankingSource(Reader(),v)
    first=source.fetch(query)
    source.fetch(query,2,previous=first)
    assert [token for token,_ in t.reads]==['synthetic-access-1']*2


def test_direct_personal_source_also_requests_account_affinity(stack):
    v,t=stack;source=PersonalRankingSource(v)
    first=source.fetch(['tag'],{})
    second=source.fetch(['tag'],{},previous=first)
    assert first.page_session is not None and second.page_session is first.page_session
    assert [token for token,_ in t.reads]==['synthetic-access-1']*2
