"""Synthetic bookmark model contract; no API, database or UI."""

import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork


def arts(start, stop):
    return tuple(Artwork(f"synthetic-{i}", f"Title {i}", "Author") for i in range(start, stop))


def test_merge_by_id_remote_order_downloaded_badge_excludes_local_only():
    state = BookmarkState(local=arts(2, 4), cached_remote=arts(1, 3))
    assert [(x.work_id, x.downloaded) for x in state.items] == [
        ("synthetic-1", False), ("synthetic-2", True)]
    assert state.items[1].artwork == arts(2, 3)[0]


def test_more_fifty_without_replacing_prior_cards():
    state = BookmarkState(cached_remote=arts(0, 123))
    before = state.visible_items
    assert len(before) == 50 and state.has_more
    assert state.load_more() and len(state.visible_items) == 100
    assert state.visible_items[:50] == before
    assert state.load_more() and len(state.visible_items) == 123
    assert not state.load_more() and not state.has_more


def test_failure_preserves_cache_and_distinguishes_valid_empty():
    state = BookmarkState(cached_remote=arts(0, 3))
    token = state.begin_fetch()
    assert state.complete_fetch(token, (), ok=False)
    assert state.fetch_failed and state.status == "fetch_failed"
    assert len(state.remote_cache) == 3 and len(state.items) == 3
    token = state.begin_fetch()
    assert state.complete_fetch(token, (), ok=True)
    assert state.remote_cache == () and state.status == "empty"
    assert not state.fetch_failed
    unknown = BookmarkState()
    assert unknown.status == "unloaded"
    token = unknown.begin_fetch()
    assert unknown.complete_fetch(token, (), ok=False)
    assert unknown.status == "fetch_failed" and not unknown.has_remote_cache


def test_empty_remote_excludes_local_and_clears_selection_on_success():
    state = BookmarkState(local=arts(0, 3), cached_remote=arts(0, 3))
    assert state.select("synthetic-1")
    token = state.begin_fetch()
    assert state.complete_fetch(token, (), ok=True)
    assert state.has_remote_cache and state.remote_cache == ()
    assert state.items == () and state.visible_items == ()
    assert state.status == "empty" and state.selected_ids == set()
    assert not state.select("synthetic-1")


def test_retry_races_and_epoch_invalidation():
    state = BookmarkState(cached_remote=arts(1, 2))
    old = state.begin_fetch()
    newest = state.begin_fetch()
    assert old != newest
    assert not state.complete_fetch(old, arts(30, 32), ok=True)
    assert state.loading
    assert state.complete_fetch(newest, arts(10, 12), ok=True)
    assert [x.work_id for x in state.items] == ["synthetic-10", "synthetic-11"]
    assert not state.complete_fetch(newest, (), ok=True)
    pending = state.begin_fetch()
    state.revisit(False)
    assert not state.complete_fetch(pending, (), ok=True)
    state.revisit(True)
    assert state.items[0].work_id == "synthetic-10"
    assert not state.loading


def test_selection_revisit_and_client_isolation():
    a, b = BookmarkState(cached_remote=arts(0, 70)), BookmarkState(cached_remote=arts(0, 70))
    assert a.select("synthetic-0") and not a.select("synthetic-0")
    assert not a.select("missing")
    assert a.load_more()
    a.revisit(False)
    assert not a.select("synthetic-1") and not a.load_more()
    a.revisit(True)
    assert len(a.visible_items) == 70 and a.selected_ids == {"synthetic-0"}
    assert b.selected_ids == set() and len(b.visible_items) == 50
    assert a.deselect("synthetic-0") and not a.deselect("synthetic-0")
    assert a.select("synthetic-69")
    token = a.begin_fetch()
    assert a.complete_fetch(token, (), ok=True)
    assert not a.selected_ids
    b.close()
    assert b.begin_fetch() is None and not b.select("synthetic-1")


def test_input_validation_and_invalid_response_does_not_commit():
    with pytest.raises(ValueError):
        BookmarkState(page_size=0)
    with pytest.raises(TypeError):
        BookmarkState(cached_remote=("not-artwork",))
    state = BookmarkState(cached_remote=arts(1, 2))
    token = state.begin_fetch()
    with pytest.raises(TypeError):
        state.complete_fetch(token, ("bad",), ok=True)
    assert state.loading and state.remote_cache == arts(1, 2)
    assert state.complete_fetch(token, (), ok=False)
    with pytest.raises(TypeError):
        state.revisit(1)
