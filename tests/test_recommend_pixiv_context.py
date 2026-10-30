"""V06 keeps the original recommendation right-click Pixiv entry."""
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_recommendation_acquisition import make


def tagged(view, prefix):
    return [node for node in view.body.descendants()
            if str(node._props.get("data-testid", "")).startswith(prefix)]


@pytest.mark.asyncio
async def test_recommendation_right_click_targets_each_visible_work_without_get(
    tmp_path
):
    controller, _, fetch = make(tmp_path)
    opened = []
    client = Client(ui.page("/_v06_recommend_context"))
    try:
        with client:
            view = RecommendView(SimpleNamespace(recommendations=controller),
                                 on_open=opened.append)
            view.active = True
            await view.refresh()
            menus = tagged(view, "recommend-pixiv-context-")
            links = tagged(view, "recommend-pixiv-link-")
            assert len(menus) == len(links) == 2
            assert all(menu._props.get("context-menu") is True for menu in menus)
            assert {link._props["href"] for link in links} == {
                "https://www.pixiv.net/artworks/11",
                "https://www.pixiv.net/artworks/40",
            }
            assert all(link._props.get("target") == "_blank" for link in links)
            assert all(set(link._props["rel"].split()) >= {"noopener", "noreferrer"}
                       for link in links)
            assert len([n for n in view.body.descendants()
                        if isinstance(n, ui.button) and n.text == "保存済みを開く"]) == 2
            assert not fetch.calls and not opened
            view.close()
    finally:
        client.delete()
        controller.close()


@pytest.mark.asyncio
async def test_non_pixiv_identity_does_not_create_an_empty_or_unsafe_context_menu(
    tmp_path
):
    controller, _, fetch = make(tmp_path)
    client = Client(ui.page("/_v06_invalid_context"))
    try:
        with client:
            view = RecommendView(SimpleNamespace(recommendations=controller))
            view.active = True
            await view.refresh()
            invalid = SimpleNamespace(work_id="demo-0003", title="Synthetic",
                                      author_id="9", author_name="Synthetic")
            view.data["sections"] = {
                "superlike": (SimpleNamespace(key="fixture", title="",
                    works=(invalid,), exhausted=True),)
            }
            view.render()
            assert not tagged(view, "recommend-pixiv-context-")
            assert not tagged(view, "recommend-pixiv-link-")
            assert not fetch.calls
            view.close()
    finally:
        client.delete()
        controller.close()
