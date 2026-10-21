"""NiceGUIのサーバー側要素生成を確認する。ブラウザ入力や画素比較は行わない。"""

import pytest
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
        dialogs = [element for element in client.elements.values() if isinstance(element, ui.dialog)]
        buttons = [element for element in client.elements.values() if isinstance(element, ui.button)]
        assert len(cards) == 50
        assert len(dialogs) == 1
        tabs = [element for element in buttons if "pv-tab" in element.classes]
        assert [element.text.split(" · ")[0] for element in tabs] == [
            "ダウンロード", "キュー", "失敗リスト", "重複整理",
            "学習", "ランキング", "ライブラリ", "おすすめ",
        ]
        # ランキングと学習の検証経路だけを有効にする。他の6画面は未移植。
        assert sum(bool(element.props.get("disable")) for element in tabs) == 6
        # ナビゲーションは既存トークンのCSSで色を制御する。
        # Quasar既定text-primaryを残すと選択中の青背景と文字が同色になる。
        assert all(element.props.get("color") is None for element in tabs)
    finally:
        client.delete()


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
