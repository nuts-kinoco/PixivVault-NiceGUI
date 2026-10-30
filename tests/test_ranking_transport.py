"""Injected transport only, alongside unchanged reader regressions."""
import json
from urllib.parse import urlsplit,parse_qs
import pytest
from test_pixiv_read_client import MockTransport,make_client,packet
from pixivvault_nicegui import pixiv_read_client as mod
from pixivvault_nicegui.ranking_source import RankingSource,RankingQuery

def test_ranking_and_tag_search_use_bounded_get_transport(tmp_path):
    mock=MockTransport();mock.when('/ajax/user/extra',packet({}));client=make_client(tmp_path,mock)
    # make_client seeds matching synthetic homepage/identity.
    mock.when('/ranking.php',json.dumps({'contents':[dict(illust_id=123,user_id=91,title='作品',user_name='作者',url='https://i.pximg.net/fixture.png',tags=['猫'],rank=1)]}).encode())
    mock.when('/ajax/search/artworks/%E7%8C%AB',packet({'illustManga':{'data':[dict(id='123',userId='91',title='作品',userName='作者',tags=['猫'],url='https://i.pximg.net/fixture.png',pageCount=2,illustType=1,aiType=1,xRestrict=0)]}}))
    try:
        page=RankingSource(client).fetch(RankingQuery(mode='weekly',r18=True,date='20261001'))
        assert page.items[0].record.remote_id=='123'
        result=RankingSource(client).fetch(RankingQuery(tag='猫'))
        assert result.items[0].record.page_count==2
        requests=[r for r in mock.calls if urlsplit(r.full_url).path=='/ranking.php']
        assert len(requests)==1 and requests[0].get_method()=='GET'
        assert parse_qs(urlsplit(requests[0].full_url).query)['mode']==['weekly_r18']
        assert all(r.get_method()=='GET' for r in mock.calls)
    finally:client.close()

@pytest.mark.parametrize('url,kind',[
 ('https://evil.example/ranking.php?mode=daily&content=illust&format=json&p=1','ranking'),
 ('https://www.pixiv.net/ranking.php?mode=monthly_r18&content=illust&format=json&p=1','ranking'),
 ('https://www.pixiv.net/ranking.php?mode=daily&content=illust&format=json&p=1&p=2','ranking'),
 ('https://www.pixiv.net/ranking.php?mode=daily&content=illust&format=json&p=1&date=20260230','ranking'),
 ('https://www.pixiv.net/ajax/search/artworks/x?word=x&order=popular_d&mode=safe&p=1&s_mode=s_tag&type=all&lang=ja','tag_search'),
 ('https://www.pixiv.net/ajax/search/artworks/y?word=x&order=date_d&mode=safe&p=1&s_mode=s_tag&type=all&lang=ja','tag_search'),
 ('https://www.pixiv.net/ajax/search/artworks/x?word=x&order=date_d&mode=safe&p=2&s_mode=s_tag&type=all&lang=ja','tag_search'),
])
def test_ranking_destinations_are_exact(url,kind):
    with pytest.raises(mod.DestinationRejected):mod._validate_destination(url,kind)

def test_ranking_raw_json_duplicate_keys_rejected(tmp_path):
    mock=MockTransport();mock.when('/ajax/user/extra',packet({}));client=make_client(tmp_path,mock)
    mock.when('/ranking.php',b'{"contents":[],"contents":[]}')
    try:
        with pytest.raises(mod.InvalidPayload):client.get_ranking()
    finally:client.close()

def test_ai_detail_pins_work_id(tmp_path):
    mock=MockTransport();mock.when('/ajax/user/extra',packet({}));client=make_client(tmp_path,mock)
    mock.when('/ajax/illust/123',packet({'id':'124','aiType':1}))
    try:
        with pytest.raises(mod.InvalidPayload):client.get_work_info('123')
    finally:client.close()
