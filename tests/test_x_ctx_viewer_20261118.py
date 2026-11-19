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
async def test_open_folder_item_never_falls_back_to_the_header_location(monkeypatch):
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
            assert not ctx(view.viewer, 'folder').enabled
            await click(ctx(view.viewer, 'folder'))
            assert not opened
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_ranking_viewer_hide_writes_shared_data_keeps_viewer_and_drops_card(tmp_path, monkeypatch):
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
            assert view.gallery._viewer_state.viewer_open
            assert view.gallery._viewer_state.selected_artwork.work_id == '123'
            assert set(view.gallery.cards) == {'124'}
            assert view.manage_badge.text == '1' and view.undo_button.visible
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_recommendation_viewer_hide_removes_work_from_shelf(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make_recommend_with_saved_work(tmp_path)
    client = Client(ui.page('/_x_ctx_view_rec_hide'))
    try:
        with client:
            view = await open_view(services)
            viewer = view._recommend_viewer
            await view.open_work('12', generation=view._render_generation)
            assert viewer._viewer_state.viewer_open
            image_pin = viewer.selection.images
            assert not image_pin.closed
            await viewer._viewer_hide()
            assert '12' in services.recommendations.snapshot()['hidden_works']
            for _ in range(100):  # 読み直しは背景処理。最大約2秒待つ。
                await asyncio.sleep(0.02)
                shown = {w.work_id for gs in view.data['sections'].values() for g in gs for w in g.works}
                if '12' not in shown:
                    break
            assert '12' not in shown and '11' in shown
            assert viewer._viewer_state.viewer_open
            assert viewer._viewer_state.selected_artwork.work_id == '12'
            assert not image_pin.closed
            assert image_pin.get('12').data
            viewer.close_viewer()
            assert image_pin.closed
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_viewer_folder_uses_saved_work_and_disables_unsaved(tmp_path, monkeypatch):
    from pixivvault_nicegui.desktop_integration import DesktopIntegration
    from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
    from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
    from pixivvault_nicegui.services import ApplicationServices
    from test_ac_library_cover_20261119 import png

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    root = tmp_path / 'images'
    author = root / 'Author(91)'
    folder = author / 'First(101)'
    folder.mkdir(parents=True)
    (folder / '101_p0.png').write_bytes(png('red'))
    (author / 'Second(102).png').write_bytes(png('blue'))
    opened = []
    svc = ApplicationServices(1, desktop=DesktopIntegration(
        allowed_roots=(root,), launcher=NS(open_default=opened.append)))
    view_services = viewer_services(Reader())
    from pixivvault_nicegui.models import Artwork
    first = view_services.artworks[0]
    second = Artwork('102', 'Second', 'Author', author_id='91')
    unsaved = Artwork('103', 'Unsaved', 'Author', author_id='91')
    svc.artworks = (first, second, unsaved)
    svc.library_image_root = root
    snapshot = LibrarySnapshot(tuple(LibraryWork(art, '91') for art in svc.artworks))
    svc.images.publish(ReadonlyLocalMediaCatalog(root, snapshot), None)
    client = Client(ui.page('/_ctx_work_folder'))
    try:
        with client:
            view = GalleryView(svc)
            for work_id, expected in [('101', folder), ('102', author), ('103', None)]:
                view.open(work_id)
                button = ctx(view.viewer, 'folder')
                if expected is not None:
                    for _ in range(100):
                        if button.enabled:
                            break
                        await asyncio.sleep(0.01)
                    assert button.enabled
                    await click(button)
                    assert opened[-1] == expected
                else:
                    await asyncio.sleep(0.1)
                    assert not button.enabled
                    await click(button)
            assert opened == [folder, author]
            view.close()
    finally:
        client.delete()
        svc.close()


def make_recommend_with_saved_work(tmp_path):
    from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
    from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
    from pixivvault_nicegui.models import Artwork
    from test_ac_library_cover_20261119 import png

    services = make_recommend(tmp_path)
    folder = tmp_path / 'Author(92)'
    folder.mkdir()
    (folder / 'Title(12).png').write_bytes(png('red'))
    services.library = LibrarySnapshot((LibraryWork(Artwork('12', 'Title', 'Author', author_id='92'), '92'),))
    services.images.publish(ReadonlyLocalMediaCatalog(tmp_path, services.library), None)
    return services


@pytest.mark.asyncio
async def test_library_viewer_hides_the_work_and_stays_open(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui.library_view import LibraryView

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make_recommend_with_saved_work(tmp_path)
    client = Client(ui.page('/_ctx_library_viewer_hide'))
    try:
        with client:
            view = LibraryView(services)
            work = view.state.snapshot.works[0]
            assert view.open_saved_work(work.work_id)
            assert view._viewer_state.viewer_open
            assert entries(view.viewer) == FOUR
            assert ctx(view.viewer, 'hide').enabled
            await click(ctx(view.viewer, 'hide'))
            assert view._viewer_state.viewer_open
            assert view._viewer_state.selected_artwork.work_id == work.work_id
            assert work.work_id in services.recommendations.snapshot()['hidden_works']
    finally:
        client.delete()
        services.close()
