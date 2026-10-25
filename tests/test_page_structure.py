"""NiceGUIのサーバー側要素生成を確認する。ブラウザ入力や画素比較は行わない。"""

import pytest
from types import SimpleNamespace
from nicegui import Client, ui

from pixivvault_nicegui.state import GalleryState
from pixivvault_nicegui.ui import gallery, shell


@pytest.mark.asyncio
async def test_page_builds_fifty_cards_one_dialog_and_existing_navigation():
    client = Client(ui.page("/_structure_test"))
    try:
        with client:
            shell.build_demo_page()
        cards = [element for element in client.elements.values() if "pv-card" in element.classes]
        dialogs = [element for element in client.elements.values() if element.props.get("role") == "dialog"]
        buttons = [element for element in client.elements.values() if isinstance(element, ui.button)]
        assert len(cards) == 50
        assert len(dialogs) == 1
        assert dialogs[0].props["aria-hidden"] == "true"
        tabs = [element for element in buttons if "pv-tab" in element.classes]
        assert [element.text.split(" · ")[0] for element in tabs] == [
            "ダウンロード", "キュー", "失敗リスト", "重複整理",
            "学習", "ランキング", "ライブラリ", "おすすめ", "定期チェック", "メタデータ補完",
        ]
        # Job screens explain the opt-in destination; actual mutation controls
        # remain disabled when no dedicated synthetic sandbox was specified.
        enabled = {element.props["data-testid"] for element in tabs if not element.props.get("disable")}
        assert enabled == {"tab-ranking", "tab-learning", "tab-library", "tab-failed", "tab-download", "tab-queue", "tab-scheduler"}
        assert {element.props["data-testid"] for element in tabs if element.props.get("disable")} == {
            "tab-dedup", "tab-recommend", "tab-metadata"}
        # ナビゲーションは既存トークンのCSSで色を制御する。
        # Quasar既定text-primaryを残すと選択中の青背景と文字が同色になる。
        assert all(element.props.get("color") is None for element in tabs)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_metadata_tab_is_unavailable_in_synthetic_mode_and_cannot_provision():
    from pixivvault_nicegui.services import ApplicationServices

    services = ApplicationServices()
    client = Client(ui.page('/_metadata_synthetic_gate'))
    try:
        with client:
            demo = shell.DemoShell(services)
        assert demo.buttons['metadata'].props.get('disable') is True
        assert demo.metadata_view is None
        prior_tab = demo.active_tab
        demo.show('metadata')
        assert demo.active_tab == prior_tab
        assert demo.metadata_view is None
        assert services.managed_metadata is None
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_metadata_tab_managed_navigation_does_not_open_sidecar_or_fetch(tmp_path):
    from pixivvault_nicegui.services import ApplicationServices

    class OfflineSource:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            if name.startswith('get_') or name in ('verify_identity', 'require_identity'):
                def forbidden(*args, **kwargs):
                    self.calls.append(name)
                    raise AssertionError('no Pixiv GET allowed by metadata tab')
                return forbidden
            raise AttributeError(name)

        def close(self):
            pass

    source = OfflineSource()
    root = tmp_path / 'managed'
    services = ApplicationServices(live_client=source, pixiv_download_root=root)
    client = Client(ui.page('/_metadata_managed_gate'))
    try:
        sidecar = root / '.pixivvault-managed-metadata.sqlite3'
        assert not sidecar.exists()
        with client:
            demo = shell.DemoShell(services)
            assert demo.buttons['metadata'].props.get('disable') is not True
            assert demo.metadata_view is None
            demo.show('metadata')
        assert demo.active_tab == 'metadata'
        assert demo.metadata_view is not None
        assert demo.metadata_view.root.visible
        assert services.managed_metadata is None
        assert not sidecar.exists()
        assert source.calls == []
        demo.show('ranking')
        assert not demo.metadata_view.root.visible
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_each_client_owns_its_state_and_deletion_closes_only_that_state(monkeypatch):
    states = []

    def create_state(*args, **kwargs):
        state = GalleryState(*args, **kwargs)
        states.append(state)
        return state

    monkeypatch.setattr(gallery, "GalleryState", create_state)
    clients = [Client(ui.page("/_client_a")), Client(ui.page("/_client_b"))]
    try:
        for client in clients:
            with client:
                shell.build_demo_page()
        assert len(states) == 2 and states[0] is not states[1]
        clients[0].delete()
        assert states[0].closed
        assert not states[1].closed
    finally:
        for client in clients:
            if client.id in Client.instances:
                client.delete()


@pytest.mark.asyncio
async def test_more_is_last_grid_cell_and_keeps_existing_card_ids():
    from pixivvault_nicegui.services import ApplicationServices

    services = ApplicationServices(120)
    client = Client(ui.page("/_more_contract"))
    try:
        with client:
            view = gallery.GalleryView(services)
            original_ids = {key: card.id for key, card in view.cards.items()}
            assert view.grid.default_slot.children[-1] is view.more_cell
            assert view.more_cell.parent_slot.parent is view.grid
            assert all(button.props.get('color') is None
                       for button in view.size_buttons.values())
            view.more()
            assert len(view.cards) == 100
            assert all(view.cards[key].id == value for key, value in original_ids.items())
            assert view.grid.default_slot.children[-1] is view.more_cell
            view.change_size("small")
            assert view.size_buttons["small"].props["aria-pressed"] == "true"
            assert view.size_buttons["large"].props["aria-pressed"] == "false"
            assert all(view.cards[key].id == value for key, value in original_ids.items())
            view.search("架空作品 0003")
            assert len(view.cards) == 1 and not view.more_cell.visible
            view.search("")
            assert len(view.cards) == 50 and view.more_cell.visible
            assert view.grid.default_slot.children[-1] is view.more_cell
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_tab_departure_cancels_pending_viewer_and_preserves_gallery_state():
    from pixivvault_nicegui.services import ApplicationServices

    services = ApplicationServices(120)
    client = Client(ui.page("/_tab_departure"))
    try:
        with client:
            demo = shell.DemoShell(services)
            view = demo.gallery
            assert view.viewer.root.parent_slot.parent is demo.content
            view.search("架空")
            view.more()
            view.change_size("small")
            card_ids = {key: card.id for key, card in view.cards.items()}
            view.open("demo-0003")
            old_event = SimpleNamespace(args=view._image_url)
            assert view.state.viewer_open and not view.viewer.is_open
            demo.show("learning")
            assert demo.learning.active
            assert not view.state.viewer_open and not view.root.visible
            assert view.viewer_image.props["src"] == ""
            view._image_loaded(old_event)
            view._image_failed(old_event)
            assert not view.viewer.is_open
            demo.show("ranking")
            assert not demo.learning.active and view.root.visible
            assert view.state.query == "架空" and view.state.shown_count == 100
            assert view.state.card_size == "small"
            assert card_ids == {key: card.id for key, card in view.cards.items()}
            view.open("demo-0004")
            view._image_loaded(SimpleNamespace(args=view._image_url))
            assert view.viewer.is_open
            demo.show("learning")
            assert not view.viewer.is_open
            assert view.viewer.root.props["aria-hidden"] == "true"
    finally:
        client.delete()
        services.close()
