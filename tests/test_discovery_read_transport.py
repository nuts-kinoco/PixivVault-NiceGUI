"""Injected transport only: no live account or network."""
from urllib.parse import urlsplit, parse_qs
import pytest
from test_pixiv_read_client import MockTransport, make_client, packet
from pixivvault_nicegui import pixiv_read_client as mod


def work(wid='123'):
    return dict(id=wid,userId='91',title='fixture',userName='author',tags=['tag'],
                url='https://i.pximg.net/fixture.png',pageCount=1,illustType=0,xRestrict=0,aiType=1)


def test_recommendation_page_is_explicit_and_ranking_remains_single_page(tmp_path):
    transport=MockTransport();transport.when('/ajax/user/extra',packet({}))
    transport.when('/ajax/search/artworks/tag',packet({'illustManga':{'data':[work()],'total':121},'relatedTags':['other']}))
    client=make_client(tmp_path,transport)
    try:
        result=client.search_recommendation_illusts('tag',page=2)
        assert result['works'][0]['id']=='123' and result['total']==121
        calls=[r for r in transport.calls if '/ajax/search/' in r.full_url]
        assert len(calls)==1 and calls[0].get_method()=='GET'
        assert parse_qs(urlsplit(calls[0].full_url).query)['p']==['2']
        assert 'bookmark_count' not in result['works'][0]
        with pytest.raises(mod.DestinationRejected):client.search_illusts('tag',page=2)
        assert len(transport.calls)==3
    finally:client.close()


def test_related_authors_identity_and_malformed_results(tmp_path):
    transport=MockTransport();transport.when('/ajax/user/extra',packet({}))
    transport.when('/ajax/user/91/recommends',packet({'users':[dict(userId='91',name='author',isFollowed=False)],'thumbnails':{'illust':[work()]}}))
    client=make_client(tmp_path,transport)
    try:
        result=client.get_recommended_users('91')
        assert result['users'][0]['user_id']=='91'
        assert result['works'][0]['thumbnail_url']=='https://i.pximg.net/fixture.png'
        assert all(r.get_method()=='GET' for r in transport.calls)
        with pytest.raises(mod.DestinationRejected):client.get_recommended_users('91',work_num=999)
    finally:client.close()


def test_discovery_early_exit_never_fetches_following_batch(tmp_path):
    transport=MockTransport();transport.when('/ajax/user/extra',packet({}))
    transport.when('/ajax/user/91/profile/all',packet({'illusts':{str(i):None for i in range(100,160)},'manga':{}}))
    def details(request):
        ids=parse_qs(urlsplit(request.full_url).query)['ids[]']
        return packet({'works':{wid:work(wid) for wid in ids}})
    transport.when('/ajax/user/91/profile/illusts',details)
    client=make_client(tmp_path,transport)
    try:
        result=client.get_user_works('91',early_exit_checker=lambda item:item['id']=='158')
        assert [r['id'] for r in result]==['159']
        assert len([r for r in transport.calls if 'profile/illusts' in r.full_url])==1
    finally:client.close()


@pytest.mark.parametrize('row',[dict(work(),url='https://evil.example/p.png'),dict(work(),userId='0'),dict(work(),isMasked=True),dict(work(),aiType=True)])
def test_discovery_metadata_fails_closed(row):
    with pytest.raises((mod.InvalidPayload,mod.DestinationRejected)):mod._discovery_record(row)
