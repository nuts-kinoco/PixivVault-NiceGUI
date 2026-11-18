"""P3-1: ビューアの右クリック 4 項目（元 artbook_viewer.py:849-870、FL-M04）。通信なし。"""
import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.ui.artwork_viewer import ArtworkViewer
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_p3_viewer_author import Reader, services as viewer_services
from test_x_ctx_ranking_20261118 import click, label_of, make
from test_x_ctx_recommend_20261118 import make as make_recommend, open_view, item as rec_item

FOUR = ['pixivで作品ページを開く', '作者ページを開く', '出さない', '保存フォルダを開く']


def entries(viewer):
    return [label_of(i) for i in viewer.context_menu.descendants() if isinstance(i, ui.menu_item)]


def ctx(viewer, key):
    return viewer.context_items[key]


@pytest.mark.asyncio
async def test_viewer_context_menu_four_items_targets_and_existing_menu_kept(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    svc = viewer_services(Reader())
    client = Client(ui.page('/_x_ctx_view_items'))
    try:
        with client:
            view = GalleryView(svc)
            view.open('101')
            assert entries(view.viewer) == FOUR
            assert ctx(view.viewer, 'pixiv').props['href'] == 'https://www.pixiv.net/artworks/101'
            assert ctx(view.viewer, 'author').props['href'] == 'https://www.pixiv.net/users/91'
            assert ctx(view.viewer, 'pixiv').enabled and ctx(view.viewer, 'author').enabled
            # 前提が無い項目は無効（出さない先・保存先が無い）。
            assert not ctx(view.viewer, 'hide').enabled and not ctx(view.viewer, 'folder').enabled
            # 既存の ⋮ メニューのリンクとテスト ID は残る。
            assert view.viewer.pixiv_link.props['data-testid'] == 'viewer-pixiv-link'
            assert view.viewer.pixiv_link.props['href'] == 'https://www.pixiv.net/artworks/101'
            view.open('102')
            assert ctx(view.viewer, 'pixiv').props['href'] == 'https://www.pixiv.net/artworks/102'
            assert ctx(view.viewer, 'author').props['href'] == 'https://www.pixiv.net/users/92'
            listener = next(e for e in view.viewer.root._event_listeners.values()
                            if e.type == 'keydown' and e.js_handler and 'F10' in e.js_handler)
            opened = []
            monkeypatch.setattr(view.viewer.context_menu, 'open', lambda: opened.append(1))
            listener.handler(NS(args=None))
            assert opened == [1]
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_library_style_viewer_has_no_hide_item(monkeypatch):
    client = Client(ui.page('/_x_ctx_view_library'))
    try:
        with client:
            viewer = ArtworkViewer(lambda: None, lambda: None, lambda: None, lambda: None, lambda: None,
                                   context_hide=False)
            assert entries(viewer) == [FOUR[0], FOUR[1], FOUR[3]]
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_open_folder_item_opens_the_single_saved_location(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    svc = viewer_services(Reader())
    opened = []

    async def open_saved_folder(choice, *, is_current=None):
        opened.append((choice, is_current()))
    svc.saved_folder_choices = lambda: (NS(key='artwork', label='イラスト・漫画'),)
    svc.open_saved_folder = open_saved_folder
    client = Client(ui.page('/_x_ctx_view_folder'))
    try:
        with client:
            view = GalleryView(svc)
            view.open('101')
            assert ctx(view.viewer, 'folder').enabled
            await click(ctx(view.viewer, 'folder'))
            assert [o[0].key for o in opened] == ['artwork'] and opened[0][1] is True
            # 複数あるときは勝手に選ばず、ヘッダーの選択へ誘導する。
            svc.saved_folder_choices = lambda: (NS(key='a', label='A'), NS(key='b', label='B'))
            await click(ctx(view.viewer, 'folder'))
            assert len(opened) == 1 and 'ヘッダー' in view.viewer.action_status.text
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_ranking_viewer_hide_writes_shared_data_closes_viewer_and_drops_card(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, reader = make(tmp_path)
    from test_ranking_source import row
    reader.rows = [row(123), row(124)]
    client = Client(ui.page('/_x_ctx_view_rank_hide'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            view.gallery.open('123')
            assert view.gallery.state.viewer_open
            hide = ctx(view.gallery.viewer, 'hide')
            assert hide.enabled
            await click(hide)
            assert '123' in services.recommendations.snapshot()['hidden_works']
            assert not view.gallery.state.viewer_open
            assert set(view.gallery.cards) == {'124'}
            assert view.manage_badge.text == '1' and view.undo_button.visible
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_recommendation_viewer_hide_removes_work_from_shelf(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make_recommend(tmp_path)
    client = Client(ui.page('/_x_ctx_view_rec_hide'))
    try:
        with client:
            view = await open_view(services)
            viewer = view._recommend_viewer
            from pixivvault_nicegui.models import Artwork
            from pixivvault_nicegui.state import GalleryState
            art = Artwork('12', '確実作品', '別作者', author_id='92')
            viewer._viewer_state = GalleryState((art,))
            viewer.set_viewer_active(True)
            viewer._viewer_state.open_artwork('12', 0)
            await viewer._viewer_hide()
            assert '12' in services.recommendations.snapshot()['hidden_works']
            for _ in range(100):  # 読み直しは背景処理。最大約2秒待つ。
                await asyncio.sleep(0.02)
                shown = {w.work_id for gs in view.data['sections'].values() for g in gs for w in g.works}
                if '12' not in shown:
                    break
            assert '12' not in shown and '11' in shown
            view.close()
    finally:
        client.delete()
        services.close()
