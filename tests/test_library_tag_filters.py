from dataclasses import FrozenInstanceError

import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.library_filters import LibraryFilters, normalize_tag
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.library_state import LibraryState
from pixivvault_nicegui.models import Artwork


def art(identifier, title, tags, author="author"):
    return Artwork(identifier, title, author, tags=tuple(tags))


def test_normalize_and_immutable_conflict_contract():
    assert normalize_tag("　Ｆａｔｅ　") == "fate"
    assert normalize_tag("　ウマ娘　") == "ウマ娘"
    assert normalize_tag("  ") is None
    filters = LibraryFilters(include=frozenset(["ＦＡＴＥ", " fate ", " "]),
                             exclude=frozenset(["猫", "猫"]))
    assert filters.include == frozenset(["fate"])
    assert filters.exclude == frozenset(["猫"])
    with pytest.raises(FrozenInstanceError):
        filters.mode = "or"
    with pytest.raises(ValueError):
        LibraryFilters(include=frozenset(["ＦＡＴＥ"]), exclude=frozenset(["fate"]))
    with pytest.raises(ValueError):
        LibraryFilters(mode="all")


def test_compound_tag_match_and_or_exclude():
    tags = ("ＦＡＴＥ", " 青 ", "ウマ娘")
    assert LibraryFilters(include=frozenset(["fate", "青"])).matches(tags)
    assert not LibraryFilters(include=frozenset(["fate", "赤"])).matches(tags)
    assert LibraryFilters(include=frozenset(["fate", "赤"]), mode="or").matches(tags)
    assert not LibraryFilters(include=frozenset(["fate"]), exclude=frozenset(["青"])).matches(tags)
    assert LibraryFilters().matches(())


def test_library_work_level_and_filter_keeps_unfiltered_author_details():
    works = (
        LibraryWork(art("1", "needle", ["red"]), "a", last_backup="2026-01-01"),
        LibraryWork(art("2", "other", ["blue"]), "a", last_backup="2026-02-01"),
        LibraryWork(art("3", "needle", ["blue"]), "b", last_backup="2026-03-01"),
    )
    state = LibraryState(LibrarySnapshot(works), page_size=1)
    state.set_query("needle")
    assert state.set_filters(LibraryFilters(include=frozenset(["blue"])))
    assert [a.author_id for a in state.visible_authors] == ["b"]
    assert state.set_filters(LibraryFilters(include=frozenset(["red"])))
    assert [a.author_id for a in state.visible_authors] == ["a"]
    assert state.open_author("a")
    assert [page.work.work_id for page in state.visible_pages] == ["2"]
    previous = state.generation
    assert state.set_filters(LibraryFilters())
    assert state.selected_author is None
    assert state.shown_author_count == 1
    assert state.generation != previous
    assert not state.set_filters(LibraryFilters())
    state.close()
    assert not state.set_filters(LibraryFilters(include=frozenset(["red"])))


def test_bookmark_filter_preserves_source_order_selection_and_inactive_update():
    records = (art("1", "needle", ["blue"]), art("2", "needle", ["red"]),
               art("3", "other", ["blue"]))
    state = BookmarkState(cached_remote=records, page_size=1)
    state.select("2")
    state.load_more()
    assert state.shown_count == 2
    state.revisit(False)
    assert state.set_filters(LibraryFilters(include=frozenset(["blue"])))
    assert state.shown_count == 1
    assert [card.work_id for card in state.matched_items] == ["1", "3"]
    assert state.selected_ids == {"2"}
    state.revisit(True)
    assert state.set_query("needle")
    assert [card.work_id for card in state.matched_items] == ["1"]
    assert not state.set_filters(state.filters)
    state.close()
    assert not state.set_filters(LibraryFilters())
