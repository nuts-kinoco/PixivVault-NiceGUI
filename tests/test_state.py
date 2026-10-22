import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.models import Artwork, RequestToken
from pixivvault_nicegui.state import GalleryState


def test_paging_and_search_and_viewer_preserve_list_state():
    state = GalleryState(generate_demo_artworks(121))
    assert (state.total_count, state.shown_count, state.has_more) == (121, 50, True)
    state.load_more()
    assert state.shown_count == 100
    assert state.open_artwork("demo-0101")
    assert state.viewer_index == 100
    assert state.selected_artwork.work_id == "demo-0101"
    state.close_viewer()
    assert not state.viewer_open and state.shown_count == 100
    state.load_more()
    state.load_more()
    assert state.shown_count == 121 and not state.has_more
    state.set_query("架空作家 01")
    assert state.total_count == 11
    assert state.shown_count == 11
    assert state.selected_id is None
    assert not state.open_artwork("demo-0002")
    assert state.open_artwork("demo-0001")
    assert state.move_viewer(1)
    assert state.viewer_index == 1
    assert state.move_viewer(-1)
    assert not state.move_viewer(-1)
    assert not state.move_viewer(-100)
    assert not state.move_viewer(100)
    state.set_query("nOnExIsTeNt")
    assert (state.matching_items, state.visible_items, state.total_count) == ((), (), 0)
    assert not state.viewer_open


def test_casefold_fields_and_zero_items():
    state = GalleryState((Artwork("A", "Straße", "ALICE", "Blue Ocean"),))
    for query in ("STRASSE", "alice", "oCEAN"):
        state.set_query(query)
        assert state.total_count == 1
    empty = GalleryState(())
    assert empty.visible_items == () and not empty.has_more
    assert not empty.open_artwork("x")
    assert not empty.move_viewer(1)


def test_request_tokens_generation_revisit_and_one_shot():
    state = GalleryState(generate_demo_artworks(3))
    assert state.open_artwork("demo-0001")
    first = state.begin_request("demo-0001")
    assert not state.accept_result(RequestToken(first.generation, "demo-0002"))
    assert state.accept_result(first)
    assert not state.accept_result(first)
    assert state.move_viewer(1)
    assert not state.accept_result(first)
    assert state.open_artwork("demo-0001")
    assert not state.accept_result(first)
    second = state.begin_request("demo-0001")
    state.set_query("架空")
    assert not state.accept_result(second)
    assert state.open_artwork("demo-0001")
    last = state.begin_request("demo-0001")
    state.close()
    assert state.closed and not state.accept_result(last)
    assert not state.open_artwork("demo-0001")


def test_same_artwork_new_request_supersedes_older_out_of_order():
    state = GalleryState(generate_demo_artworks(2))
    assert state.open_artwork("demo-0001")
    old = state.begin_request("demo-0001")
    new = state.begin_request("demo-0001")
    assert new.generation > old.generation
    assert old != new
    assert not state.accept_result(old)  # A arrives before B
    assert state.accept_result(new)
    assert not state.accept_result(new)


def test_unissued_tokens_are_rejected_even_with_current_generation():
    state = GalleryState(generate_demo_artworks(2))
    assert state.open_artwork("demo-0001")
    assert not state.accept_result(RequestToken(state.generation, "demo-0001"))
    current = state.begin_request("demo-0001")
    assert not state.accept_result(RequestToken(current.generation, current.work_id))
    assert not state.accept_result(RequestToken(current.generation + 1, "demo-0001"))
    assert state.accept_result(current)
    assert not state.accept_result(current)


def test_begin_request_requires_open_viewer_and_selected_work():
    state = GalleryState(generate_demo_artworks(2))
    with pytest.raises(RuntimeError, match="viewer is closed"):
        state.begin_request("demo-0001")
    assert state.open_artwork("demo-0001")
    with pytest.raises(ValueError, match="selected artwork"):
        state.begin_request("demo-0002")
    token = state.begin_request("demo-0001")
    state.close_viewer()
    assert not state.accept_result(token)
    with pytest.raises(RuntimeError, match="viewer is closed"):
        state.begin_request("demo-0001")
    assert state.open_artwork("demo-0001")
    state.close()
    with pytest.raises(RuntimeError, match="viewer is closed"):
        state.begin_request("demo-0001")


def test_page_card_opens_requested_page_and_invalid_page_keeps_current_request():
    state = GalleryState(generate_demo_artworks(3))
    assert state.open_artwork("demo-0003", 2)
    assert state.selected_page == 2
    current = state.begin_request("demo-0003")
    for invalid in (-1, 3, True, 1.5):
        with pytest.raises(ValueError, match="page_no"):
            state.open_artwork("demo-0003", invalid)
    assert state.selected_page == 2 and state.finish_image(current)
    assert not state.move_page(1)
    assert state.move_page(-1)
    assert state.selected_page == 1


def test_independent_instances_and_invalid_inputs():
    items = generate_demo_artworks(4)
    left, right = GalleryState(items, page_size=2), GalleryState(items, page_size=2)
    left.set_query("0001")
    left.set_card_size("large")
    left.open_artwork("demo-0001")
    assert (right.query, right.card_size, right.selected_id) == ("", "large", None)
    assert right.shown_count == 2
    with pytest.raises(ValueError):
        right.set_card_size("tiny")
    with pytest.raises(ValueError):
        GalleryState(items, page_size=0)
    with pytest.raises(ValueError):
        GalleryState((items[0], items[0]))
