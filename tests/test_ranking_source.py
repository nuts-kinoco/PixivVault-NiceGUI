"""Synthetic original-ranking contract tests; no external service."""
import pytest
from pixivvault_nicegui.ranking_source import (RankingQuery,RankingSource,RankingReadFailed,
    RankingUnavailable,merge_pages,sorted_items)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected

def row(wid=123,**values):
    return dict(illust_id=wid,user_id=91,user_name='作者',title='作品',tags=['猫'],
        url='https://i.pximg.net/fixture.png',rank=1,yes_rank=2,rating_count=5,view_count=10,**values)
class Reader:
    def __init__(self):self.calls=[];self.rows=[row()];self.ai=1;self.failure=None
    def require_identity(self):self.calls.append(('identity',));return '91'
    def get_ranking(self,**values):
        self.calls.append(('ranking',values))
        if self.failure:raise self.failure
        return self.rows
    def search_illusts(self,word,**values):
        self.calls.append(('search',word,values))
        return {'works':[dict(id='123',user_id='91',user_name='作者',title='作品',tags=['猫'],thumbnail_url='https://i.pximg.net/fixture.png',ai_type=2)]}
    def get_work_info(self,wid):self.calls.append(('detail',wid));return {'ai_type':self.ai}

def test_ranking_selection_short_page_is_not_exhaustion():
    r=Reader();source=RankingSource(r)
    assert r.calls==[]
    page=source.fetch(RankingQuery(mode='weekly',content='manga',date='20261001',r18=True))
    assert len(page.items)==1 and not page.exhausted
    assert r.calls[-1]==('ranking',dict(mode='weekly_r18',content='manga',date='20261001',page=1))
    assert not page.items[0].record.page_count_known
    r.rows=[]
    assert source.fetch(page.query,2).exhausted

@pytest.mark.parametrize('status',[403,429,500])
def test_other_failures_are_not_empty_success(status):
    r=Reader();r.failure=HTTPStatusRejected(status)
    with pytest.raises(RankingReadFailed):RankingSource(r).fetch(RankingQuery(),2)

def test_404_first_unavailable_and_later_exhausted():
    r=Reader();r.failure=HTTPStatusRejected(404);s=RankingSource(r)
    with pytest.raises(RankingUnavailable):s.fetch(RankingQuery())
    assert s.fetch(RankingQuery(),2).exhausted

def test_ai_exclusion_does_not_change_raw_end_condition():
    r=Reader();r.ai=2
    result=RankingSource(r).fetch(RankingQuery(exclude_ai=True))
    assert result.items==() and not result.exhausted and ('detail','123') in r.calls
    r.ai=None
    with pytest.raises(RankingReadFailed):RankingSource(r).fetch(RankingQuery(exclude_ai=True))

def test_explicit_nonpremium_search_order_and_no_fake_counts():
    r=Reader();page=RankingSource(r).fetch(RankingQuery(tag='猫',r18=True))
    assert page.exhausted and '新着順のみ' in page.order_label
    assert page.items[0].rating_count is None and page.items[0].view_count is None
    assert r.calls[-1]==('search','猫',dict(page=1,order='date_d',mode='r18'))
    with pytest.raises(ValueError):RankingSource(r).fetch(page.query,2)

def test_original_sort_axes_and_stale_page_rejection():
    r=Reader();a=row();b=row(124);b.update(rank=2,yes_rank=0,rating_count=3,view_count=50)
    r.rows=[a,b];s=RankingSource(r);one=s.fetch(RankingQuery())
    assert [i.record.remote_id for i in sorted_items(one.items,'rating_count')]==['123','124']
    assert [i.record.remote_id for i in sorted_items(one.items,'momentum')]==['124','123']
    assert [i.record.remote_id for i in sorted_items(one.items,'view_count')]==['124','123']
    merged=merge_pages(one,s.fetch(RankingQuery(),2))
    assert len(merged.items)==2 and merged.page==2
    with pytest.raises(ValueError):merge_pages(one,s.fetch(RankingQuery(content='manga'),2))

@pytest.mark.parametrize('kwargs',[dict(mode='monthly',r18=True),dict(mode='male'),dict(date='20260230'),dict(date='２０２６１００１'),dict(tag='x\n'),dict(content='unknown')])
def test_invalid_selection_fails_without_read(kwargs):
    with pytest.raises(ValueError):RankingQuery(**kwargs)

def test_invalid_cdn_never_produces_snapshot():
    r=Reader();r.rows[0]['url']='https://evil.example/secret'
    with pytest.raises(RankingReadFailed):RankingSource(r).fetch(RankingQuery())


def test_ai_classification_operation_deadline_is_bounded(monkeypatch):
    from pixivvault_nicegui import ranking_source as mod
    r=Reader();ticks=iter([0,0,mod.MAX_OPERATION_SECONDS+1])
    monkeypatch.setattr(mod.time,'monotonic',lambda:next(ticks))
    with pytest.raises(RankingReadFailed):RankingSource(r).fetch(RankingQuery(exclude_ai=True))
