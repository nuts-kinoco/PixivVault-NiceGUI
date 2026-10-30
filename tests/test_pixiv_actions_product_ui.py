"""Viewer and recommendation controls use the same app-owned action results."""
from types import SimpleNamespace
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationSnapshot, RecommendationWork,
)
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_following_sync_ui import runtime
from test_pixiv_actions import stack
from test_pixiv_action_ui import token


@pytest.mark.asyncio
async def test_real_viewer_buttons_reject_old_page_and_do_one_explicit_bookmark(stack, monkeypatch):
    runtime(monkeypatch)
    class Images:
        @staticmethod
        def url(work_id, page=0, variant='view'):
            return f'/synthetic/{work_id}/{page}/{variant}.png'
    services = SimpleNamespace(
        artworks=(Artwork('123', 'Synthetic', 'Author', author_id='91', page_count=2),),
        images=Images(), live_client=stack.reader, managed_downloads=None,
        pixiv_actions=stack.actions, closing=False,
    )
    client = Client(ui.page('/_action_product_viewer'))
    try:
        with client:
            gallery = GalleryView(services)
        gallery.open('123')
        controls = gallery._pixiv_actions
        assert controls.bookmark is gallery.viewer.bookmark_button
        assert controls.follow is gallery.viewer.follow_button
        old = token(controls, 'bookmark')
        gallery.move_page(1)
        assert not await controls.click('bookmark', old)
        assert stack.http.reads == stack.http.posts == []
        assert await controls.click('bookmark', token(controls, 'bookmark'))
        assert gallery.viewer.bookmark_button.text == 'ブクマ済み'
        assert len(stack.http.posts) == 1
        gallery.close_viewer()
        assert not controls.bookmark.enabled and not controls.follow.enabled
        assert not stack.actions._closing
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_recommendation_rerender_retires_old_binding_and_keeps_shared_result(stack, monkeypatch):
    runtime(monkeypatch)
    snapshot = RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('superlike', 'Generated', (
            RecommendationWork('123', 'Synthetic', '91', 'Author'),)),)),),
        unavailable=(),
    )
    controller = RecommendationController(SimpleNamespace(load=lambda: snapshot))
    services = SimpleNamespace(recommendations=controller, pixiv_actions=stack.actions, closing=False)
    client = Client(ui.page('/_action_product_recommend'))
    try:
        with client:
            view = RecommendView(services)
        view.active = True
        await view.refresh()
        assert len(view._action_bindings) == 1 and stack.http.reads == []
        first = view._action_bindings[0]
        viewer_binding = view._recommend_viewer._pixiv_actions
        assert first.hub.bindings == {first, viewer_binding}
        assert not viewer_binding.bookmark.enabled and not viewer_binding.follow.enabled
        stale = token(first, 'follow')
        view.render()
        current = view._action_bindings[0]
        assert first._closed and first not in current.hub.bindings
        assert not await first.click('follow', stale)
        assert stack.http.reads == stack.http.posts == []
        assert await current.click('follow', token(current, 'follow'))
        assert current.follow.text == 'フォロー解除'
        view.render()
        assert view._action_bindings[0].follow.text == 'フォロー解除'
        assert current._closed and current not in current.hub.bindings
        assert view._action_bindings[0].hub.bindings == {
            view._action_bindings[0], viewer_binding}
        view.set_active(False)
        assert not await view._action_bindings[0].click('follow', token(view._action_bindings[0], 'follow'))
        assert len(stack.http.posts) == 1
        view.close()
        assert not current.hub.bindings
    finally:
        client.delete()
        controller.close()
