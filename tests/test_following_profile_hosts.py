"""Generated profile URLs only; no external I/O or real identities."""
import json,time
from html import escape
from urllib.parse import urlsplit,parse_qs
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.following_sync import FollowingSyncStore,FollowingSyncError
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient,InvalidPayload,DestinationRejected,_validate_destination
from pixivvault_nicegui.bookmark_remote_adapter import thumbnail_url

@pytest.fixture
def reader(tmp_path):
    clients=[]
    def make(url):
        cookie=tmp_path/'generated-cookie.txt'
        cookie.write_text("# Netscape HTTP Cookie File\n"+f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time())+86400}\tPHPSESSID\t55_generatedonly\n",'utf-8')
        calls=[]
        def transport(request,*args):
            assert request.get_method()=='GET' and request.data is None
            u=urlsplit(request.full_url);calls.append(u.path)
            assert u.netloc=='www.pixiv.net'
            if u.path=='/':
                return ('<meta id="meta-global-data" content="'+escape(json.dumps({'userData':{'id':'55'}}))+'">').encode()
            if u.path=='/ajax/user/extra':body={}
            else:
                assert u.path=='/ajax/user/55/following'
                q=parse_qs(u.query)
                body={'users':[] if q['offset']!=['0'] else [dict(userId='41' if q['rest']==['show'] else '42',userName='Generated author',userAccount=None,profileImageUrl=url)]}
            return json.dumps({'error':False,'body':body}).encode()
        c=LivePixivReadClient(cookie,transport=transport);clients.append(c)
        return c,calls
    yield make
    for c in clients:c.close()

@pytest.mark.parametrize('host',['i.pximg.net','s.pximg.net'])
def test_following_default_profile_reaches_both_routes_and_reopens(tmp_path,reader,host):
    url=f'https://{host}/common/images/no_profile.png';client,calls=reader(url)
    root=tmp_path/'following'
    with_store=FollowingSyncStore(root)
    try:
        saved=with_store.sync(client)
        assert len(saved.following)==2 and all(a.profile_img==url for a in saved.following)
        assert calls.count('/ajax/user/55/following')==4
    finally:with_store.close()
    reopened=FollowingSyncStore(root)
    try:assert reopened.snapshot('55')==saved
    finally:reopened.close()

@pytest.mark.parametrize('host',['i.pximg.net','s.pximg.net'])
def test_store_profile_contract_independent_of_reader(tmp_path,host):
    url=f'https://{host}/common/images/no_profile.png'
    class Reader:
        def require_identity(self):return '55'
        def get_following_users_detailed(self,uid,rest):
            return () if rest=='hide' else (dict(user_id='41',name='Generated',account='',profile_img=url),)
    store=FollowingSyncStore(tmp_path/'following')
    try:assert store.sync(Reader()).following[0].profile_img==url
    finally:store.close()

@pytest.mark.parametrize('url',[
 'http://s.pximg.net/common/no_profile.png',
 'https://s.pximg.net.evil.invalid/common/no_profile.png',
 'https://evil.invalid/common/no_profile.png',
 'https://other.pximg.net/common/no_profile.png',
 'https://user@s.pximg.net/common/no_profile.png',
 'https://s.pximg.net:443/common/no_profile.png',
 'https://s.pximg.net/common/no_profile.png#fragment',
 ' https://s.pximg.net/common/no_profile.png',
 'https://s.pximg.net/common/no_\nprofile.png',
 'https://s.pximg.net',
])
def test_bad_profile_host_or_url_keeps_previous_sync(tmp_path,reader,url):
    store=FollowingSyncStore(tmp_path/'following')
    try:
        good,_=reader('https://i.pximg.net/common/no_profile.png');before=store.sync(good)
        bad,_=reader(url)
        with pytest.raises(FollowingSyncError):store.sync(bad)
        assert store.snapshot('55')==before
    finally:store.close()

def test_profile_acceptance_does_not_widen_original_or_bookmark_images():
    url='https://s.pximg.net/common/images/no_profile.png'
    with pytest.raises(ValueError):thumbnail_url(url)
    with pytest.raises(DestinationRejected):_validate_destination(url,'image')
