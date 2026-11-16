import pytest
from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.external_vault_transport import search_params
from pixivvault_nicegui.premium_read import PremiumSearch


def test_period_reaches_search_request_and_is_validated():
    p = search_params('ウマ娘 脇', start_date='2026-10-01', end_date='2026-10-31')
    assert p['start_date'] == '2026-10-01' and p['end_date'] == '2026-10-31'
    assert p['word'] == 'ウマ娘 脇' and p['sort'] == 'popular_desc'
    for start, end in [('2026-11-01', '2026-10-01'), ('2026-02-30', None), ('20261001', None)]:
        with pytest.raises(VaultError):
            search_params('tag', start_date=start, end_date=end)


def test_premium_period_is_forwarded_without_silent_fallback():
    calls = []
    class Vault:
        def status(self): return dict(open=True, mode='nana7mi', transport_available=True)
        def search(self, *args, **kwargs):
            calls.append(kwargs)
            return {'illusts': [], 'next_url': None}
    PremiumSearch(Vault()).search('tag', start_date='2026-10-01', end_date='2026-10-31')
    assert calls[0]['start_date'] == '2026-10-01'
    class Reader:
        def require_identity(self): pytest.fail('date must not be silently ignored')
    with pytest.raises(VaultError, match='fallback-date-range-unavailable'):
        PremiumSearch(None, Reader()).search('tag', start_date='2026-10-01')


def test_personal_period_is_part_of_paging_identity():
    from pixivvault_nicegui.personal_ranking import PersonalRankingSource
    calls = []
    class Vault:
        def search(self, *args, **kwargs):
            calls.append(kwargs)
            return dict(illusts=[], next_url=None)
    page = PersonalRankingSource(Vault()).fetch(['a', 'b'], {}, start_date='2026-10-01', end_date='2026-10-31')
    assert calls[0]['end_date'] == '2026-10-31'
    assert page.key[-1] == ('2026-10-01', '2026-10-31')
