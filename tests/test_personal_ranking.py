import pytest
from datetime import datetime
from pixivvault_nicegui.personal_ranking import PersonalRankingSource,apply_personal_filters,normalize_personal_filters,normalize_item
from pixivvault_nicegui.external_vault import VaultError
from pixivvault_nicegui.ranking_source import RankingSource,RankingQuery,RankingReadFailed

def raw(wid=123,aid=10,**kwargs):
    return dict(id=wid,title='fixture',user={'id':aid,'name':'author'},image_urls={'square_medium':f'https://i.pximg.net/img-master/img/2026/01/01/00/00/00/{wid}_p0_square1200.jpg'},tags=[{'name':'tag'}],page_count=1,type='illust',create_date='2020-01-01T00:00:00+09:00',**kwargs)
class Vault:
    def __init__(self):self.packet={'illusts':[raw()],'next_url':'server-pagination-marker'};self.calls=[]
    def search(self,*args,**kwargs):self.calls.append((args,kwargs));return self.packet
    def status(self):return {'open':True,'mode':'nana7mi'}

def test_personal_raw_offset_duplicate_end_and_stable_page_order():
    vault=Vault();source=PersonalRankingSource(vault);filters=normalize_personal_filters({})
    first=source.fetch(['tag'],filters);assert first.offset==1 and not first.exhausted
    vault.packet={'illusts':[raw(124,11),raw(125,10)],'next_url':'next'}
    second=source.fetch(['tag'],filters,previous=first,author_counts={'11':10})
    assert [r['illust_id'] for r in second.rows]==['123','124','125'] and second.offset==3
    assert vault.calls[-1][1]['offset']==1
    third=source.fetch(['tag'],filters,previous=second);assert third.exhausted and third.offset==5
    with pytest.raises(VaultError):source.fetch(['other'],filters,previous=second)

def test_personal_original_filters_exclusion_normalized_ids_dates_unknown_kept():
    items=[normalize_item(raw()),normalize_item(raw(124)),normalize_item(raw(125))]
    items[1]['create_date']='unparseable';items[2]['create_date']='2026-10-25T00:00:00+09:00'
    selected,dropped=apply_personal_filters(items,normalize_personal_filters({'date_preset':'1week','exclude_downloaded':True}),downloaded_ids={'125'},now=datetime(2026,10,26))
    assert [i['illust_id'] for i in selected]==['124'] and dropped==2
    assert normalize_personal_filters({})['exclude_hidden_works'] is True

def test_personal_all_filter_combinations_and_original_unknown_author_limit():
    items=[normalize_item(raw(i,10 if i<125 else 20)) for i in range(123,127)]
    filters=normalize_personal_filters({'tags_include':['TAG'],'tags_exclude':['other'],'per_author_max':1,'exclude_hidden_works':True})
    selected,dropped=apply_personal_filters(items,filters,hidden_work_ids={'125'})
    assert [i['illust_id'] for i in selected]==['123','126'] and dropped==2

def test_premium_does_not_require_main_cookie_or_silent_fallback():
    class Reader:
        def require_identity(self):raise AssertionError('main Cookie must not be used')
    vault=Vault();source=RankingSource(Reader(),vault)
    result=source.fetch(RankingQuery(tag='tag',r18=True,exclude_ai=True))
    assert len(result.items)==1 and result.exhausted and result.order_label.startswith('人気順（プレミアム）') and 'R-18 絞り込み未適用' in result.order_label
    assert vault.calls[0][1]['exclude_ai'] is True
    vault.packet={'illusts':'bad'}
    with pytest.raises(RankingReadFailed):source.fetch(RankingQuery(tag='tag'))


@pytest.mark.asyncio
async def test_personal_context_wait_is_off_loop_and_late_result_is_discarded(monkeypatch):
    import asyncio
    from threading import Event
    from nicegui import Client,ui,background_tasks
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
    entered,release=Event(),Event();tasks=[];create=asyncio.create_task
    services=ApplicationServices(1)
    def slow_context():
        entered.set();assert release.wait(5)
        return {'downloaded_ids':{'123'},'trend_rows':[]}
    services.personal_ranking_context=slow_context
    monkeypatch.setattr(background_tasks,'create',lambda coro:tasks.append(create(coro)) or tasks[-1])
    client=Client(ui.page('/_personal_context_off_loop'))
    try:
        with client:
            view=PersonalRankingView(services);view.set_active(True)
            assert await asyncio.to_thread(entered.wait,5)
            # The event loop can process another operation while disk/GET waits.
            await asyncio.wait_for(asyncio.sleep(0),1)
            view.set_active(False);release.set();await asyncio.gather(*tasks)
            assert view.context()=={}
    finally:
        release.set();client.delete();services.close()
