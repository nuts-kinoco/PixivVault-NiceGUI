"""Standalone synthetic bookmark controls; no real account or API."""

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.bookmark_view import BookmarkView


def arts(count):
    return tuple(Artwork(f"synthetic-{i}", f"Title {i}", "Author") for i in range(count))


@pytest_asyncio.fixture
async def bookmark():
    client = Client(ui.page("/_bookmark_unit"))
    source = arts(120)
    state = BookmarkState(local=source[:40], cached_remote=source)
    opened = []
    with client:
        view = BookmarkView(state, on_refresh=lambda: None,
                            on_open=opened.append)
        view.render()
    try:
        yield view, state, opened, client
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_selection_more_and_stable_existing_cards(bookmark):
    view, state, opened, _ = bookmark
    assert len(view.cards) == 50
    assert view.counter.text == "50 / 120 件表示"
    first_node = view.cards["synthetic-0"].id
    view.select("synthetic-0", True)
    assert state.selected_ids == {"synthetic-0"}
    assert view.selected_count.text == "選択中: 1件"
    view.more()
    assert len(view.cards) == 100
    assert view.cards["synthetic-0"].id == first_node
    view.open("synthetic-0")
    assert opened == ["synthetic-0"]
    view.more()
    assert len(view.cards) == 120
    assert not view.more_cell.visible
    assert not view.download_button.enabled


@pytest.mark.asyncio
async def test_refresh_updates_downloaded_badges_and_blocks_stale_sessions(bookmark):
    view, state, opened, _ = bookmark
    assert state.items[0].downloaded
    assert view.cards["synthetic-0"].id
    previous_session = view._session
    previous_first_node = view.cards["synthetic-0"].id
    state.replace_local(())
    view.refresh()
    assert not state.items[0].downloaded
    assert view.cards["synthetic-0"].id != previous_first_node
    before = state.shown_count
    view.more(previous_session)
    assert state.shown_count == before
    view.select("synthetic-1", True, previous_session)
    view.open("synthetic-1", previous_session)
    assert not state.selected_ids and not opened
    view.select("synthetic-1", True, view._session)
    assert state.selected_ids == {"synthetic-1"}
    live_session = view._session
    view.set_active(False)
    view.set_active(True)
    assert view._session is not live_session
    shown = state.shown_count
    view.more(live_session)
    view.open("synthetic-2", live_session)
    view.select("synthetic-2", True, live_session)
    assert state.shown_count == shown
    assert state.selected_ids == {"synthetic-1"} and not opened


@pytest.mark.asyncio
async def test_fetch_failure_and_successful_empty_are_distinct(bookmark):
    view, state, _, _ = bookmark
    token = state.begin_fetch()
    assert state.complete_fetch(token, (), ok=False)
    view.refresh()
    assert len(view.cards) == 50
    assert "取得に失敗" in view.message.text
    token = state.begin_fetch()
    assert state.complete_fetch(token, (), ok=True)
    view.refresh()
    assert not view.cards
    assert view.message.text == "ブックマークはありません"
    view.set_active(False)
    assert not state.select("synthetic-1")
    view.close()
    assert state.closed
