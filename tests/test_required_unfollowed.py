import asyncio
from required_network_guard import no_network
from io import BytesIO
from threading import Event, Lock
from types import SimpleNamespace
import pytest
from PIL import Image
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def row(uid, wid='21', name='Generated', thumb=True):
    return dict(user_id=uid, id=wid, user_name=name,
                thumb_url='https://i.pximg.net/generated.jpg' if thumb else '')


class Reader:
    def __init__(self):
        self.calls=[];self.identity='7';self.routes={}
    def verify_identity(self):self.calls.append('verify');return self.identity
    def require_identity(self):return self.identity
    def get_following_users(self, uid, rest_type):
        self.calls.append(('follow',rest_type))
        return [dict(user_id='10')] if rest_type=='show' else [dict(user_id='11')]
    def get_bookmarked_works(self,uid,rest_type):
        self.calls.append(('illustration',rest_type));return self.routes.get(('illustration',rest_type),[])
    def get_bookmarked_novels(self,uid,rest_type):
        self.calls.append(('novel',rest_type));return self.routes.get(('novel',rest_type),[])
    def get_image_bytes(self,url):
        out=BytesIO();Image.new('RGB',(12,12),'blue').save(out,format='PNG');return out.getvalue()
    def close(self):pass


def fetch(reader,**kw):
    from pixivvault_nicegui.unfollowed_authors import fetch_unfollowed_authors
    return fetch_unfollowed_authors(reader,**kw)


def test_routes_dedupe_following_and_missing_preview():
    reader=Reader()
    reader.routes={('illustration','show'):[row('10'),row('11'),row('0'),row('12',name='-----'),row('13',thumb=False)],
                   ('novel','hide'):[row('013','22'),row('14','23')]}
    result=fetch(reader,kinds=('illustration','novel'),visibilities=('show','hide'))
    assert [a.author_id for a in result.authors]==['13','14']
    assert result.authors[0].thumbnail.endswith('generated.jpg')
    assert result.authors[0].kind=='novel'
    assert reader.calls==['verify',('follow','show'),('follow','hide'),('illustration','show'),
                          ('illustration','hide'),('novel','show'),('novel','hide')]


def test_selected_route_only_and_no_partial_success():
    reader=Reader();reader.routes[('novel','hide')]=[row('20')]
    assert len(fetch(reader,kinds=('novel',),visibilities=('hide',)).authors)==1
    assert ('illustration','show') not in reader.calls and ('novel','show') not in reader.calls
    def broken():
        yield row('21')
        raise OSError('private path cookie')
    reader.routes[('novel','hide')]=broken()
    with pytest.raises(ValueError,match='unfollowed-author-fetch-failed'):
        fetch(reader,kinds=('novel',),visibilities=('hide',))


@pytest.mark.parametrize('case',['identity','cancel','unsafe_url','limit'])
def test_incomplete_or_untrusted_result_is_rejected(case):
    reader=Reader();reader.routes[('illustration','show')]=[row('20'),row('21')]
    kw=dict(kinds=('illustration',),visibilities=('show',))
    if case=='identity':reader.require_identity=lambda:'8'
    if case=='cancel':kw['is_current']=lambda:False
    if case=='unsafe_url':reader.routes[('illustration','show')][0]['thumb_url']='file:///secret'
    if case=='limit':kw['max_items_per_route']=1
    with pytest.raises(ValueError):fetch(reader,**kw)


@pytest.mark.asyncio
async def test_real_ui_entry_explicit_fetch_zero_error_and_closed_result():
    s=ApplicationServices(1);reader=Reader();s.live_client=reader
    client=Client(ui.page('/_required_unfollowed'))
    try:
        with client:
            owner=ManagedDownloadView(s)
            assert hasattr(owner,'unfollowed_button')
            owner.open_unfollowed()
            view=owner.unfollowed_view
            assert not reader.calls
            assert await view.refresh()
            assert '0' in view.notice.text and not view.busy
            gate,entered=Event(),Event()
            def slow(*a,**kw):entered.set();gate.wait(3);return [row('20')]
            reader.get_bookmarked_works=slow
            task=asyncio.create_task(view.refresh())
            await asyncio.to_thread(entered.wait,2)
            view.hide();gate.set()
            assert not await task
            assert view.authors==()
            owner.open_unfollowed()
            reader.get_bookmarked_works=lambda *a,**kw:(_ for _ in ()).throw(OSError('private credential'))
            assert not await view.refresh()
            assert '取得できません' in view.notice.text and 'private' not in view.notice.text
            owner.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_thumbnails_are_local_bounded_and_dialog_owned():
    from pixivvault_nicegui.ui.unfollowed_authors_view import UnfollowedAuthorsView
    reader=Reader();reader.routes[('illustration','show')]=[row(str(100+i)) for i in range(12)]
    lock=Lock();active=peak=0;gate=Event()
    original=reader.get_image_bytes
    def image(url):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        gate.wait(2)
        try:return original(url)
        finally:
            with lock:active-=1
    reader.get_image_bytes=image
    s=SimpleNamespace(live_client=reader);client=Client(ui.page('/_required_unfollowed_thumbs'))
    try:
        with client:
            view=UnfollowedAuthorsView(s);view.show()
            task=asyncio.create_task(view.refresh())
            for _ in range(100):
                if peak==8:break
                await asyncio.sleep(.01)
            assert peak==8
            gate.set();assert await task
            images=[x for x in view.rows.descendants() if x.props.get('data-testid')=='unfollowed-thumb']
            assert len(images)==12 and all(x.source.startswith('data:image/jpeg;base64,') for x in images)
            view.hide();view.close()
    finally:gate.set();client.delete()
