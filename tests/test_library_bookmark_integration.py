"""Synthetic LibraryView subview wiring; never uses remote API or user cookies."""

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


@pytest_asyncio.fixture
async def library():
    services = ApplicationServices(120)
    client = Client(ui.page("/_library_bookmarks_unit"))
    with client:
        view = LibraryView(services)
    try:
        yield view, services, client
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_switching_subviews_preserves_author_and_bookmark_ownership(library):
    view, _, _ = library
    assert view.subview == "downloaded"
    assert view.authors_panel.visible
    assert len(view.bookmark_state.items) == 110
    assert len(view.bookmark_state.visible_items) == 50
    assert sum(card.downloaded for card in view.bookmark_state.items) == 40
    view.open_author("demo-author-01")
    assert view.state.selected_author_id == "demo-author-01"
    view.show_subview("bookmarks")
    assert view.subview == "bookmarks"
    assert view.bookmarks.root.visible
    assert not view.pages_panel.visible
    assert view.bookmark_state.active
    assert view.bookmark_state.select("demo-0003")
    assert view.bookmark_state.load_more()
    view.show_subview("downloaded")
    assert view.pages_panel.visible
    assert not view.bookmark_state.active
    view.show_subview("bookmarks")
    assert view.bookmark_state.selected_ids == {"demo-0003"}
    assert len(view.bookmark_state.visible_items) == 100
    view.show_subview("downloaded")
    assert view.state.selected_author_id == "demo-author-01"
    assert view.state.shown_page_count == 50


@pytest.mark.asyncio
async def test_refresh_success_zero_failure_and_stale_events(library, monkeypatch):
    view, services, _ = library
    view.show_subview("bookmarks")
    assert view.bookmark_state.select("demo-0001")
    monkeypatch.setattr(services, "bookmark_remote_snapshot", lambda: ())
    view.refresh_bookmarks()
    assert view.bookmark_state.has_remote_cache
    assert not view.bookmark_state.items
    assert view.bookmark_state.status == "empty"
    assert not view.bookmark_state.selected_ids

    def broken():
        raise ConnectionError("synthetic only")

    monkeypatch.setattr(services, "bookmark_remote_snapshot", broken)
    view.refresh_bookmarks()
    assert view.bookmark_state.fetch_failed
    assert view.bookmark_state.remote_cache == ()
    assert view.bookmark_state.status == "fetch_failed"
    monkeypatch.setattr(services, "bookmark_remote_snapshot", lambda: services.artworks[:77])
    view.refresh_bookmarks()
    assert len(view.bookmark_state.items) == 77
    assert not view.bookmark_state.fetch_failed
    old_token = view.bookmark_state.begin_fetch()
    view.show_subview("downloaded")
    assert not view.bookmark_state.complete_fetch(old_token, (), ok=True)
    view.show_subview("bookmarks")
    assert len(view.bookmark_state.items) == 77


@pytest.mark.asyncio
async def test_inactive_and_closed_bookmark_actions_cannot_modify_state(library):
    view, _, _ = library
    view.show_subview("bookmarks")
    view.set_active(False)
    before = tuple(item.work_id for item in view.bookmark_state.items)
    view.refresh_bookmarks()
    assert tuple(item.work_id for item in view.bookmark_state.items) == before
    assert not view.bookmark_state.select("demo-0001")
    view.set_active(True)
    assert view.bookmark_state.select("demo-0001")
    view.close()
    assert view.bookmark_state.closed
    view.refresh_bookmarks()
    assert not view.bookmark_state.begin_fetch()


@pytest.mark.asyncio
async def test_old_downloaded_detail_events_cannot_affect_bookmark_subview(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    generation = view.state.generation
    page_session, more_session = view._page_session, view._more_session
    assert view.state.shown_page_count == 50
    view.show_subview("bookmarks")
    view.open_page("demo-0060", 2, generation, page_session)
    view.more(more_session)
    view.close_author()
    assert not view._viewer_state.viewer_open
    assert view.state.selected_author_id == "demo-author-01"
    assert view.state.shown_page_count == 50
    view.show_subview("downloaded")
    view.open_page("demo-0060", 2, generation, page_session)
    assert view._viewer_state.viewer_open
