"""Pure-event wiring tests; actual layout, decode and native controls are separate gates."""

import asyncio
from types import SimpleNamespace

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


@pytest_asyncio.fixture
async def library():
    services = ApplicationServices(120)
    client = Client(ui.page("/_tag_filter_unit"))
    with client:
        view = LibraryView(services)
    try:
        yield view, services, client
    finally:
        client.delete()
        services.close()


def add_tag(view, tag):
    panel = view.filter_panel
    if not panel.opened:
        panel.toggle()
    panel.tag_input.set_value(tag)
    panel.add()


@pytest.mark.asyncio
async def test_default_closed_filters_and_same_work_and_semantics(library):
    view, _, _ = library
    panel = view.filter_panel
    assert not panel.root.visible and not panel.now.visible
    assert view.filters == LibraryFilters()
    assert len(view.author_cards) == 4
    add_tag(view, "　縦長　")
    assert view.filters.include == frozenset({"縦長"})
    assert view.state.filters == view.bookmark_state.filters == view.filters
    assert panel.now.visible and len(view.author_cards) == 4
    add_tag(view, "横長")
    # Every author owns both kinds, but no single work has both tags.
    assert not view.author_cards
    assert view.empty.text == "条件に合う作者がいません"
    panel.toggle_mode()
    assert view.filters.mode == "or" and len(view.author_cards) == 4
    panel.flip("縦長", panel._session)
    assert view.filters.include == frozenset({"横長"})
    assert view.filters.exclude == frozenset({"縦長"})
    add_tag(view, "縦長")
    assert "縦長" in view.filters.include and "縦長" not in view.filters.exclude


@pytest.mark.asyncio
async def test_filter_keeps_representative_and_unfiltered_author_detail(library):
    view, _, _ = library
    add_tag(view, "縦長")
    assert view.author_cards["demo-author-01"].props["data-representative-id"] == "demo-0060"
    view.open_author("demo-author-01")
    assert view.state.selected_author.work_count == 60
    assert view.state.total_page_count == 98 and len(view.page_cards) == 50
    view.more()
    assert len(view.page_cards) == 98
    view.close_author()
    assert view.filters.include == frozenset({"縦長"})
    assert view.state.filters == view.filters


@pytest.mark.asyncio
async def test_shared_subview_filters_selection_and_reload_preservation(library):
    view, _, _ = library
    view.show_subview("bookmarks")
    view.bookmarks.select("demo-0002", True)
    view.bookmarks.more()
    assert view.bookmark_state.shown_count == 100
    add_tag(view, "縦長")
    assert view.bookmark_state.shown_count == 50
    assert view.bookmark_state.selected_ids == {"demo-0002"}
    assert all("縦長" in card.artwork.tags for card in view.bookmark_state.visible_items)
    view.show_subview("downloaded")
    assert view.state.filters == view.filters
    view.reload(preserve=True)
    assert view.state.filters == view.filters
    view.show_subview("bookmarks")
    assert view.bookmark_state.filters == view.filters
    assert view.bookmark_state.selected_ids == {"demo-0002"}
    view.filter_panel.clear()
    assert view.filters == LibraryFilters() and not view.filter_panel.now.visible
    assert len(view.bookmark_state.items) == 110


@pytest.mark.asyncio
async def test_old_chips_and_cards_do_not_cross_filter_or_tab_generation(library):
    view, _, _ = library
    add_tag(view, "縦長")
    panel = view.filter_panel
    old_session = panel._session
    opener = view.author_openers["demo-author-01"]
    old_open = next(listener.handler for listener in opener._event_listeners.values()
                    if listener.type == "click")
    panel.toggle_mode()
    panel.remove("縦長", old_session)
    assert "縦長" in view.filters.include
    old_open(None)
    assert view.state.selected_author_id is None
    old_session = panel._session
    view.set_active(False)
    panel.tag_input.set_value("横長")
    panel.add()
    assert "横長" not in view.filters.include
    view.set_active(True)
    panel.remove("縦長", old_session)
    assert "縦長" in view.filters.include


@pytest.mark.asyncio
async def test_static_control_browser_epochs_reject_queued_events_after_revisit(library):
    view, _, _ = library
    add_tag(view, "縦長")
    panel = view.filter_panel
    handlers = [next(listener.handler for listener in button._event_listeners.values()
                     if listener.type == "click")
                for button in (panel.mode_button, panel.add_button, panel.clear_button)]
    stale = SimpleNamespace(args={"epoch": panel._epoch, "value": "横長"})
    view.set_active(False)
    view.set_active(True)
    for handler in handlers:
        handler(stale)
    assert view.filters == LibraryFilters(frozenset({"縦長"}))
    handlers[0](SimpleNamespace(args={"epoch": panel._epoch}))
    assert view.filters.mode == "or"


@pytest.mark.asyncio
async def test_two_clients_never_share_filters_or_open_panel_state():
    services = ApplicationServices(120)
    clients = [Client(ui.page("/_tag_client_a")), Client(ui.page("/_tag_client_b"))]
    views = []
    try:
        for client in clients:
            with client:
                views.append(LibraryView(services))
        a, b = views
        add_tag(a, "縦長")
        assert a.filters.include and not b.filters.include
        assert a.filter_panel.opened and not b.filter_panel.opened
        a.set_active(False)
        a.set_active(True)
        assert a.filters.include == frozenset({"縦長"})
        assert a.filter_panel.opened
    finally:
        for client in clients:
            client.delete()
        services.close()


@pytest.mark.asyncio
async def test_filters_changed_during_bookmark_fetch_survive_success_and_failure(library, monkeypatch):
    view, services, _ = library
    view.show_subview("bookmarks")
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_fetch():
        started.set()
        await release.wait()
        return services.artworks[:110], services.artworks[:40]

    monkeypatch.setattr(services, "refresh_bookmark_cache", delayed_fetch)
    epoch = view.bookmark_state.begin_fetch()
    task = asyncio.create_task(view._refresh_bookmarks_async(epoch))
    await asyncio.wait_for(started.wait(), timeout=3)
    try:
        add_tag(view, "縦長")
        view.bookmarks.search("0002")
        view.bookmarks.select("demo-0002", True)
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=3)
    assert view.bookmark_state.filters == view.filters == LibraryFilters(frozenset({"縦長"}))
    assert [card.work_id for card in view.bookmark_state.visible_items] == ["demo-0002"]
    assert view.bookmark_state.selected_ids == {"demo-0002"}

    async def failed_fetch():
        raise RuntimeError("synthetic fetch failure")

    monkeypatch.setattr(services, "refresh_bookmark_cache", failed_fetch)
    await view._refresh_bookmarks_async(view.bookmark_state.begin_fetch())
    assert view.bookmark_state.fetch_failed
    assert view.bookmark_state.filters == view.filters
    assert [card.work_id for card in view.bookmark_state.visible_items] == ["demo-0002"]
