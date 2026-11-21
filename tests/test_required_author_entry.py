import asyncio
from required_network_guard import no_network
from threading import Event
from types import SimpleNamespace
import pytest
from nicegui import Client, ui, core
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate, AuthorTargetSetting
from pixivvault_nicegui.recommendation_state import RecommendationController, RecommendationSnapshot, RecommendationGroup, RecommendationWork
from pixivvault_nicegui.ui.author_browse_view import AuthorBrowseView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_ranking_ui import Pin
from dataclasses import replace


def setup():
    calls, accepted = [], []
    class Reader:
        def require_identity(self):self._verified_identity = '7'; return self._verified_identity
        def get_user_works(self, aid, **kw):
            calls.append(('read', aid))
            return [dict(id='11', user_id=aid, title='Generated', user_name='Author')]
        def close(self):pass
    s = ApplicationServices(1)
    s.live_client = Reader()
    s.acquire_ranking_images = lambda records: Pin(records)
    s.author_media = SimpleNamespace(author_target=lambda aid:AuthorTargetSetting(aid,None,0), close=lambda:None)
    def preview(aid, **kw):
        calls.append(('preview',aid))
        return tuple(AuthorMediaCandidate('illustration', wid, aid, 'generated-date', 1, 'Generated', state)
                     for wid, state in [('11',Difference.NEW),('12',Difference.SKIPPED)])
    def enqueue(c, op, **kw):
        accepted.append((c.author_id,c.work_id,op,kw['origin']))
        return object(), True, 'a'*64
    s.author_media_preview, s.enqueue_author_media = preview, enqueue
    work=RecommendationWork('11','Generated','91','Author')
    s.recommendations = RecommendationController(SimpleNamespace(load=lambda:RecommendationSnapshot(
        sections=(('superlike',(RecommendationGroup('g','Generated',(work,)),)),))))
    return s, calls, accepted


@pytest.mark.asyncio
async def test_recommend_context_author_entry_opens_without_enqueue_and_guards_old_render(monkeypatch):
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    s,calls,accepted=setup();client=Client(ui.page('/_required_author_context'))
    try:
        with client:
            view=RecommendView(s);view.active=True
            await view.refresh()
            buttons=[n for n in view.body.descendants() if n.props.get('data-testid')=='recommend-author-superlike-11']
            assert len(buttons)==1
            generation=view._render_generation
            await view.open_author_page('11',generation=generation)
            assert view._author_browser.visible and view._author_browser.author_id=='91'
            assert not accepted
            view._author_browser.hide();view.render()
            before=list(calls)
            await view.open_author_page('11',generation=generation)
            assert calls==before and not view._author_browser.visible
            view.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_author_overlay_previews_then_explicitly_enqueues_only_eligible_for_that_author():
    s,calls,accepted=setup();client=Client(ui.page('/_required_author_download'))
    try:
        with client:
            view=AuthorBrowseView(s,s.live_client)
            await view.show('91','Author')
            assert not accepted
            assert await view.author_download.preview()
            assert not accepted
            assert await view.author_download.submit()
            assert [(aid,wid,origin) for aid,wid,_,origin in accepted]==[('91','11','id')]
            assert not await view.author_download.submit()
            view.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_gallery_context_opens_author_and_rejects_hidden_card():
    from pixivvault_nicegui.ui.gallery import GalleryView
    s,calls,_=setup();art=replace(s.artworks[0],author_id='91');s.artworks=(art,)
    client=Client(ui.page('/_required_gallery_author'))
    try:
        with client:
            view=GalleryView(s)
            buttons=view.cards[art.work_id].data['menu']
            assert [button['testid'] for button in buttons] == ['gallery-author-open']
            event = dict(work_id=art.work_id, action='author', epoch=view.grid.render_epoch)
            await view.dispatch_card_event(event)
            assert view._author_browser.author_id=='91'
            view._author_browser.hide();view.search('no matching generated title')
            before=list(calls);await view.dispatch_card_event(event)
            assert calls==before and not view._author_browser.visible
            view.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_bookmark_author_context_uses_only_current_visible_card():
    from pixivvault_nicegui.ui.bookmark_view import BookmarkView
    from pixivvault_nicegui.bookmark_state import BookmarkState
    s,calls,_=setup();art=replace(s.artworks[0],author_id='91');opened=[]
    client=Client(ui.page('/_required_bookmark_author'))
    try:
        with client:
            state=BookmarkState(local=(),cached_remote=(art,))
            view=BookmarkView(state,on_author=lambda item:opened.append(item))
            view.render()
            assert any(x.props.get('data-testid')=='bookmark-author-open' for x in view.cards[art.work_id].descendants())
            token=view._session
            assert await view.open_author(art.work_id,token)
            assert opened==[art]
            view.search('unmatched');assert not await view.open_author(art.work_id,token)
            view.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_author_overlay_switch_and_hide_discard_previous_selection():
    s,calls,accepted=setup();client=Client(ui.page('/_required_author_switch'))
    try:
        with client:
            view=AuthorBrowseView(s,s.live_client);await view.show('91','First')
            assert await view.author_download.preview()
            await view.show('92','Second')
            assert not await view.author_download.submit() and not accepted
            assert await view.author_download.preview()
            view.hide()
            assert not await view.author_download.submit() and not accepted
            view.close()
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_author_overlay_close_during_preview_does_not_enqueue():
    s,calls,accepted=setup();client=Client(ui.page('/_required_author_late'))
    entered,release=Event(),Event();original=s.author_media_preview
    def slow(*a,**kw):
        entered.set();assert release.wait(5)
        return original(*a,**kw)
    s.author_media_preview=slow
    try:
        with client:
            view=AuthorBrowseView(s,s.live_client);await view.show('91','Author')
            task=asyncio.create_task(view.author_download.preview())
            assert await asyncio.to_thread(entered.wait,5)
            view.close();release.set()
            assert not await task and not accepted
    finally:release.set();client.delete();s.close()
