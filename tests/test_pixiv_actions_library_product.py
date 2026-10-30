"""Saved receipt author identity reaches the actual product library viewer."""
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationSnapshot, RecommendationWork,
)
from pixivvault_nicegui.ui.shell import DemoShell
from test_following_sync_ui import eventually, runtime
from test_managed_download_catalog import FakeWriter, _image
from test_pixiv_action_ui import token
from test_pixiv_actions_services import environment


def test_verified_author_is_preserved_in_every_managed_artwork_projection():
    writer = FakeWriter()
    snapshot, catalog, bookmarks = build_managed_library(writer)
    try:
        for work in snapshot.works:
            assert work.artwork.author_id == work.author_id == '9'
            assert catalog.artwork_for(work.work_id).author_id == work.author_id
        assert {art.author_id for art in bookmarks} == {'9'}
    finally:
        catalog.close()
    assert not writer.closed


@pytest.mark.asyncio
async def test_product_recommendation_saved_open_uses_receipt_author_and_shared_actions(environment, monkeypatch):
    runtime(monkeypatch)
    env = environment
    managed = env.path / 'managed'
    urls = ('https://i.pximg.net/generated/123_p0.png',
            'https://i.pximg.net/generated/123_p1.png')
    images = dict(zip(urls, (_image('red'), _image('blue'))))
    record = RemoteBookmark('123', 'illustration', 'show', 'Generated saved work',
        '91', 'Generated author', (), '', '2026-10-29T00:00:00Z', 2, 0, 0)
    media = SimpleNamespace(
        verify_identity=lambda: '55',
        get_artwork_original_pages=lambda wid: urls,
        get_image_bytes=images.__getitem__,
    )
    seed = ManagedPixivDownloads(managed, media, resolve_record=lambda wid: record)
    try:
        seed.run(SimpleNamespace(kind='pixiv-illust', target_id='123'),
                 SimpleNamespace(checkpoint=lambda: None, progress=lambda *_: None))
        receipt_before = seed.saved_receipts()
    finally:
        seed.close()

    # This creates the real ApplicationServices capability owner; the fixture
    # changes only GET/POST transports. No Artwork or viewer owner is injected.
    service = env.build(pixiv_download_root=managed, preferences_root=env.path / 'preferences')
    assert len(env.created_actions) == 1 and service.pixiv_actions is env.created_actions[0]
    snapshot = RecommendationSnapshot(sections=(
        ('superlike', (RecommendationGroup('superlike', 'Generated', (
            RecommendationWork('123', 'Generated saved work', '91', 'Generated author'),)),)),
    ), unavailable=())
    previous = service.recommendations
    service.recommendations = RecommendationController(SimpleNamespace(load=lambda: snapshot))
    if previous is not None:
        previous.close()
    env.http.bookmark = {'id': '777', 'private': True}
    client = Client(ui.page('/_actual_saved_action_product'))
    try:
        with client:
            shell = DemoShell(service)
            shell.show('recommend')
        await eventually(lambda: bool(shell.recommend_view._action_bindings)
                         and not shell.recommend_view.busy)
        assert env.http.reads == env.http.posts == []
        open_buttons = [element for element in shell.recommend_view.body.descendants()
                        if isinstance(element, ui.button) and element.text == '保存済みを開く']
        assert len(open_buttons) == 1
        handlers = [listener.handler for listener in open_buttons[0]._event_listeners.values()
                    if listener.type == 'click' and listener.handler is not None]
        assert len(handlers) == 1
        # Use the actual recommendation button listener -> DemoShell
        # open_recommended -> LibraryView.open_saved_work path.
        with client:
            handlers[0](SimpleNamespace(sender=open_buttons[0], client=client, args=None))
        assert shell.active_tab == 'library'
        library = shell.library
        assert library._viewer_state.selected_artwork.work_id == '123'
        controls = library._pixiv_actions
        assert controls.actions is service.pixiv_actions
        assert controls.bookmark is library.viewer.bookmark_button
        assert controls.follow is library.viewer.follow_button
        assert controls.bookmark.enabled and controls.follow.enabled
        assert library._viewer_state.selected_artwork.author_id == '91'
        assert env.http.reads == env.http.posts == []

        old = token(controls, 'bookmark')
        library.move_page(1)
        assert not await controls.click('bookmark', old)
        assert env.http.reads == env.http.posts == []
        assert await controls.click('bookmark', token(controls, 'bookmark'))
        assert controls.bookmark.text == 'ブクマ済み' and not controls.bookmark.enabled
        assert env.http.bookmark == {'id': '777', 'private': True}
        assert env.http.reads and env.http.posts == []
        assert service.managed_downloads.saved_receipts() == receipt_before
        assert not env.reader._closed
    finally:
        client.delete()

