"""NiceGUIのサーバー側要素生成を確認する。ブラウザ入力や画素比較は行わない。"""

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.state import GalleryState
from pixivvault_nicegui.ui import shell


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
        assert sum(bool(element.props.get("disable")) for element in tabs) == 7
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_each_client_owns_its_state_and_deletion_closes_only_that_state(monkeypatch):
    states = []

    def create_state(*args, **kwargs):
        state = GalleryState(*args, **kwargs)
        states.append(state)
        return state

    monkeypatch.setattr(shell, "GalleryState", create_state)
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
