"""合成ライブラリのイベント結線。実画面の受入は別のbrowser/native検証で行う。"""

from types import SimpleNamespace

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.shell import DemoShell


@pytest_asyncio.fixture
async def library():
    services = ApplicationServices(120)
    client = Client(ui.page("/_library_unit"))
    with client:
        view = LibraryView(services)
    try:
        # Do not suspend a NiceGUI slot across the async fixture yield: cleanup
        # can clear its stack before Slot.__exit__ runs on teardown.
        yield view, services, client
    finally:
        client.delete()
        services.close()

def event_for(view):
    return SimpleNamespace(args=view._image_url)


@pytest.mark.asyncio
async def test_author_search_keeps_representative_and_detail_contains_all_pages(library):
    view, _, _ = library
    assert len(view.author_cards) == 4
    representative = view.author_cards["demo-author-01"].props["data-representative-id"]
    view.search("0003")
    assert list(view.author_cards) == ["demo-author-01"]
    assert view.author_cards["demo-author-01"].props["data-representative-id"] == representative == "demo-0060"
    author_node = view.author_cards["demo-author-01"].id
    view.open_author("demo-author-01")
    assert view.state.selected_author.work_count == 60
    assert view.state.total_page_count == 98
    assert len(view.page_cards) == 50
    assert ("demo-0060", 2) in view.page_cards
    assert ("demo-0057", 0) in view.page_cards
    assert ("demo-0057", 1) not in view.page_cards
    assert view.unknown_note.visible
    assert "1件" in view.unknown_note.text
    first_nodes = {key: card.id for key, card in view.page_cards.items()}
    view.more()
    assert len(view.page_cards) == 98
    assert all(view.page_cards[key].id == value for key, value in first_nodes.items())
    assert not view.more_cell.visible
    view.close_author()
    assert view.state.query == "0003"
    assert view.author_cards["demo-author-01"].id == author_node
    assert not view.page_cards
    view.open_author("demo-author-01")
    assert len(view.page_cards) == 50


@pytest.mark.asyncio
async def test_selected_page_and_request_generation_survive_only_their_owner(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    view.open_page("demo-0060", 2)
    assert view._viewer_state.selected_page == 2
    assert "/demo-0060/2/view.png" in view._image_url
    assert not view.next_page.enabled
    current = event_for(view)
    view._image_loaded(current)
    assert view.viewer.is_open
    view.close_viewer()
    assert view.state.selected_author_id == "demo-author-01"
    assert len(view.page_cards) == 50
    old_generation = view.state.generation
    view.open_page("demo-0060", 1)
    late = event_for(view)
    view.close_author()
    view._image_loaded(late)
    view._image_failed(late)
    assert not view.viewer.is_open
    assert not view._viewer_state.viewer_open
    view.open_author("demo-author-01")
    view.open_page("demo-0060", 2, old_generation)
    assert not view._viewer_state.viewer_open
    view.open_page("demo-0060", 2, view.state.generation)
    assert view._viewer_state.viewer_open


@pytest.mark.asyncio
async def test_image_failure_retry_and_non_active_calls_do_not_reopen_viewer(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    view.open_page("demo-0060", 0)
    failed = event_for(view)
    view._image_failed(failed)
    assert view.viewer.is_open and view.retry_button.visible
    view.request_image()
    retried = event_for(view)
    assert retried.args != failed.args
    view._image_loaded(failed)
    assert view._viewer_state.image_status == "loading"
    view._image_loaded(retried)
    assert view._viewer_state.image_status == "ready" and not view.retry_button.visible
    view.set_active(False)
    view.open_page("demo-0060", 2)
    view.open("demo-0060", 2)
    view.request_image()
    view._on_key(SimpleNamespace(args="arrowright"))
    view._image_loaded(retried)
    assert not view.viewer.is_open and not view._viewer_state.viewer_open
    assert view.viewer_image.props["src"] == ""
    assert view.state.selected_author_id == "demo-author-01"
    view.set_active(True)
    view.open_page("demo-0060", 2)
    assert view._viewer_state.selected_page == 2


@pytest.mark.asyncio
async def test_unknown_page_count_keeps_single_page_fallback_in_viewer(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    view.open_page("demo-0057", 0)
    assert view._viewer_state.selected_artwork.page_count == 1
    assert view.viewer_counter.text == "1/1"
    assert not view.previous_page.enabled and not view.next_page.enabled
    view.move_page(1)
    assert view._viewer_state.selected_page == 0


@pytest.mark.asyncio
async def test_reload_never_reuses_image_request_source(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    view.open_page("demo-0060", 2)
    stale = event_for(view)
    view.reload()
    view.open_author("demo-author-01")
    view.open_page("demo-0060", 2)
    current = event_for(view)
    assert current.args != stale.args
    view._image_loaded(stale)
    view._image_failed(stale)
    assert view._viewer_state.image_status == "loading"
    assert not view.viewer.is_open
    view._image_loaded(current)
    assert view._viewer_state.image_status == "ready" and view.viewer.is_open


@pytest.mark.asyncio
async def test_page_callback_keeps_append_valid_but_cannot_cross_reload(library):
    view, _, _ = library
    view.open_author("demo-author-01")
    card = view.page_cards[("demo-0060", 2)]
    click = next(listener.handler for listener in card._event_listeners.values()
                 if listener.type == "click")
    view.more()
    click(None)
    assert view._viewer_state.viewer_open and view._viewer_state.selected_page == 2
    view.reload()
    view.open_author("demo-author-01")
    click(None)
    assert not view._viewer_state.viewer_open
    assert not view.viewer.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("search", ("0003",)), ("sort", ("last_backup",)),
    ("close_author", ()), ("reload", ()), ("change_size", ("small",)), ("more", ()),
])
async def test_inactive_library_rejects_queued_user_actions(library, method, args):
    view, _, _ = library
    view.open_author("demo-author-01")
    state = view.state
    before = (state.query, state.sort_key, state.descending, state.selected_author_id,
              state.shown_page_count, view.card_size, tuple(view.page_cards))
    view.set_active(False)
    getattr(view, method)(*args)
    assert view.state is state
    assert (state.query, state.sort_key, state.descending, state.selected_author_id,
            state.shown_page_count, view.card_size, tuple(view.page_cards)) == before


@pytest.mark.asyncio
async def test_read_failure_is_not_empty_and_retry_recovers(library, monkeypatch):
    view, services, _ = library
    snapshot = services.library_snapshot()
    def fail():
        raise RuntimeError("synthetic read failure")
    monkeypatch.setattr(services, "library_snapshot", fail)
    view.reload()
    assert view.load_error
    assert view.error_row.visible
    assert not view.authors_panel.visible and not view.pages_panel.visible
    view.search("anything")
    monkeypatch.setattr(services, "library_snapshot", lambda: snapshot)
    view.reload()
    assert not view.load_error and not view.error_row.visible
    assert view.authors_panel.visible and len(view.author_cards) == 4
    monkeypatch.setattr(services, "library_snapshot", lambda: LibrarySnapshot(()))
    view.reload()
    assert not view.load_error and view.empty.visible
    assert view.empty.text == "保存済み作品がありません"


@pytest.mark.asyncio
async def test_two_clients_keep_private_library_state_and_share_sizes_only_inside_client():
    services = ApplicationServices(120)
    clients = [Client(ui.page("/_library_client_a")), Client(ui.page("/_library_client_b"))]
    shells = []
    try:
        for client in clients:
            with client:
                shells.append(DemoShell(services))
        first, second = shells
        with clients[0]:
            first.gallery.change_size("small")
            first.show("library")
            left = first.library
            assert left.card_size == "small"
            first.gallery.search("0003")
            first.gallery.more()
            first.gallery.change_size("medium")
            assert first.gallery.state.query == "" and first.gallery.state.shown_count == 50
            assert first.card_size == "small" and left.card_size == "small"
            left.open_author("demo-author-01")
            left.more()
            left.open_page("demo-0060", 2)
            late = event_for(left)
            first.show("ranking")
            assert not left.active and left.state.shown_page_count == 98
            left._image_loaded(late)
            assert not left.viewer.is_open
            first.gallery.change_size("medium")
            first.show("library")
            assert left.card_size == "medium"
            assert left.size_buttons["medium"].props["aria-pressed"] == "true"
            left.change_size("large")
            assert first.gallery.state.card_size == "large"
            assert first.gallery.size_buttons["large"].props["aria-pressed"] == "true"
            assert left.state.selected_author_id == "demo-author-01"
            assert left.state.shown_page_count == 98
            left.search("0003")
            assert left.state.selected_author_id is None
        with clients[1]:
            second.show("library")
            assert second.library.card_size == "large"
            assert second.library.state.query == ""
            assert second.library.state.selected_author_id is None
        clients[0].delete()
        assert left.closed and left.state.closed and left._viewer_state.closed
        assert not second.library.closed and not services.closed
    finally:
        for client in clients:
            if client.id in Client.instances:
                client.delete()
        services.close()
