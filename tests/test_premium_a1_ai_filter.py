from copy import deepcopy
from pixivvault_nicegui.personal_ranking import PersonalRankingSource
from test_personal_ranking import raw


class API:
    def __init__(self, rows, next_page=True):
        self.packet={'illusts':rows,'next_url':'next' if next_page else None}
        self.calls=[]
    def search(self,*args,**kwargs):
        self.calls.append(kwargs)
        return self.packet


def mixed():
    return [raw(100+i,illust_ai_type=1 if i<4 else 2) for i in range(15)]


def test_ai_on_filters_locally_but_offset_stays_raw_and_off_keeps_all():
    api=API(mixed()); before=deepcopy(api.packet); source=PersonalRankingSource(api)
    on=source.fetch(['tag'],{},exclude_ai=True)
    off=source.fetch(['tag'],{},exclude_ai=False)
    assert len(on.rows)==4 and all(r['item'].ai_type!=2 for r in on.rows)
    assert len(off.rows)==15 and on.offset==off.offset==15
    assert api.calls[0]['exclude_ai'] is True and api.calls[1]['exclude_ai'] is False
    assert api.packet==before


def test_all_ai_append_keeps_paging_and_raw_offset_until_non_ai_arrives():
    api=API(mixed());source=PersonalRankingSource(api)
    first=source.fetch(['tag'],{},exclude_ai=True)
    api.packet={'illusts':[raw(200+i,illust_ai_type=2) for i in range(15)],'next_url':'next'}
    second=source.fetch(['tag'],{},exclude_ai=True,previous=first)
    assert len(second.rows)==4 and second.offset==30 and not second.exhausted
    assert api.calls[-1]['offset']==15
    api.packet={'illusts':[raw(300,illust_ai_type=1)],'next_url':None}
    third=source.fetch(['tag'],{},exclude_ai=True,previous=second)
    assert len(third.rows)==5 and third.offset==31 and third.exhausted
    assert api.calls[-1]['offset']==30 and len(first.rows)==4


def test_repeated_raw_ai_page_still_stops_instead_of_looping():
    api=API([raw(200+i,illust_ai_type=2) for i in range(15)])
    source=PersonalRankingSource(api)
    first=source.fetch(['tag'],{},exclude_ai=True)
    assert first.rows==() and first.offset==15 and not first.exhausted
    second=source.fetch(['tag'],{},exclude_ai=True,previous=first)
    assert second.rows==() and second.offset==30 and second.exhausted


def test_unknown_ai_type_is_retained_and_raw_end_still_ends():
    api=API([raw(100),raw(101,illust_ai_type=1),raw(102,illust_ai_type=2)],False)
    page=PersonalRankingSource(api).fetch(['tag'],{},exclude_ai=True)
    assert {r['illust_id'] for r in page.rows}=={'100','101'}
    assert page.offset==3 and page.exhausted
