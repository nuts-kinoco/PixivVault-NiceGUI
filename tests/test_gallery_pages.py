import math

import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.state import GalleryState


def test_multipage_navigation_and_old_page_rejection():
    state = GalleryState(generate_demo_artworks(6))
    assert state.open_artwork("demo-0003")
    first = state.begin_request(state.selected_id)
    assert state.move_page(1)
    second = state.begin_request(state.selected_id)
    assert not state.finish_image(first)
    assert state.finish_image(second)
    assert state.image_status == "ready"
    assert state.move_page(1)
    assert not state.move_page(1)
    assert state.selected_page == 2
    assert state.move_viewer(1)
    assert state.selected_page == 0
    assert not state.move_page(1)
    state.close_viewer()
    assert not state.move_page(1)


def test_load_failure_retry_and_query_invalidation():
    state = GalleryState(generate_demo_artworks(3))
    state.open_artwork("demo-0001")
    failed = state.begin_request(state.selected_id)
    assert state.finish_image(failed, success=False)
    assert state.image_status == "error"
    retry = state.begin_request(state.selected_id)
    assert not state.finish_image(failed)
    state.set_query("縦長")
    assert not state.finish_image(retry)
    assert state.image_status == "empty"
    assert state.total_count == 1
    assert state.matching_items is state.matching_items


@pytest.mark.parametrize("aspect", [0, -1, math.inf, math.nan])
def test_invalid_aspect_is_rejected(aspect):
    with pytest.raises(ValueError):
        Artwork("x", "a", "b", aspect_ratio=aspect)


@pytest.mark.parametrize("pages", [0, -1, 1.5, True])
def test_invalid_page_count_is_rejected(pages):
    with pytest.raises(ValueError):
        Artwork("x", "a", "b", page_count=pages)
