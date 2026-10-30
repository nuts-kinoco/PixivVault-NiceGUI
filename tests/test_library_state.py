"""ライブラリの作者集約・ページ既知性・独立stateの受入。"""

import pytest

from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.library_fixtures import build_library_fixture
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork, PageRef
from pixivvault_nicegui.library_state import LibraryState
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.models import Artwork


def make_state(count=120, page_size=50):
    return LibraryState(build_library_fixture(generate_demo_artworks(count)), page_size)


def test_full_grouping_stable_representative_and_all_works_after_filter():
    state = make_state()
    assert (state.total_author_count, state.matched_author_count,
            state.shown_author_count) == (4, 4, 4)
    first = next(author for author in state.snapshot.authors if author.author_id == "demo-author-01")
    assert first.work_count == 60
    assert first.representative.work_id == "demo-0060"
    assert {work.artwork.author for work in first.works} == {first.name}
    assert first.view_count == sum(work.view_count for work in first.works)
    assert first.capacity_bytes is None
    assert state.set_query("0003")
    assert [author.author_id for author in state.visible_authors] == ["demo-author-01"]
    assert state.visible_authors[0].representative == first.representative
    assert state.open_author("demo-author-01")
    assert state.selected_author.work_count == 60
    assert state.total_page_count == 98
    assert state.shown_page_count == 50
    assert state.unknown_page_work_count == 1
    assert state.visible_pages[0].key == ("demo-0060", 0)
    assert state.visible_pages[1].key == ("demo-0060", 1)
    assert state.visible_pages[2].key == ("demo-0060", 2)
    assert ("demo-0057", 0) in {page.key for page in state.visible_pages}
    assert ("demo-0057", 1) not in {page.key for page in state.visible_pages}
    assert state.has_more_pages
    generation = state.generation
    first_pages = state.visible_pages
    assert state.load_more_pages() and state.shown_page_count == 98
    assert state.visible_pages[:50] == first_pages
    assert state.generation == generation
    assert not state.has_more_pages and not state.load_more_pages()
    assert state.close_author()
    assert state.query == "0003" and state.selected_author is None
    assert state.open_author("demo-author-01")
    assert state.shown_page_count == 50 and state.generation != generation


def test_same_name_different_author_id_and_search_each_work_and_tags():
    state = make_state()
    duplicates = [author for author in state.snapshot.authors if author.name == "架空作家 同名"]
    assert {author.author_id for author in duplicates} == {"demo-author-02", "demo-author-03"}
    assert [author.work_count for author in duplicates] == [20, 20]
    assert state.set_query("架空作家 同名")
    assert state.matched_author_count == 2
    assert state.set_query("demo-0090")
    assert [author.author_id for author in state.visible_authors] == ["demo-author-03"]
    assert state.open_author("demo-author-03")
    assert state.selected_author.work_count == 20
    assert state.set_query("notfound")
    assert not state.visible_authors and state.selected_author is None
    assert not state.open_author("demo-author-03")
    assert state.set_query("診断用") and state.matched_author_count == 4
    assert state.set_query("  ") and state.matched_author_count == 4


def test_historical_author_rename_groups_by_id_and_keeps_all_pages():
    works = (
        LibraryWork(Artwork("old", "旧作", "旧名", page_count=2), "author-7", last_backup="2025-01-01"),
        LibraryWork(Artwork("new", "新作", "新名"), "author-7", last_backup="2026-01-01"),
        LibraryWork(Artwork("other", "別作品", "新名"), "author-8"),
    )
    state = LibraryState(LibrarySnapshot(works))
    assert state.total_author_count == 2
    assert state.snapshot.authors[0].work_count == 2
    assert state.snapshot.authors[0].representative.work_id == "new"
    assert state.set_query("旧名")
    assert [author.author_id for author in state.visible_authors] == ["author-7"]
    assert state.open_author("author-7")
    assert [page.key for page in state.visible_pages] == [("new", 0), ("old", 0), ("old", 1)]
    assert state.close_author()
    assert state.set_query("新名")
    assert {author.author_id for author in state.visible_authors} == {"author-7", "author-8"}


def test_legacy_missing_author_id_still_opens_detail():
    state = LibraryState(LibrarySnapshot((
        LibraryWork(Artwork("no-author", "作者ID不明", ""), ""),
    )))
    assert state.open_author("")
    assert state.selected_author is not None
    assert state.selected_author.author_id == ""
    assert [item.key for item in state.visible_pages] == [("no-author", 0)]
    assert state.close_author()


def test_sort_direction_preserved_per_column_and_null_backup_last():
    state = make_state()
    assert state.sort_key == "view_count" and state.descending
    assert state.visible_authors[0].author_id == "demo-author-02"
    assert state.select_sort("last_backup")
    assert state.descending and state.visible_authors[0].author_id == "demo-author-04"
    assert state.select_sort("last_backup")
    assert not state.descending and state.visible_authors[0].author_id == "demo-author-01"
    assert state.select_sort("view_count")
    assert state.descending
    assert state.select_sort("view_count") and not state.descending
    assert state.select_sort("last_backup") and not state.descending
    with pytest.raises(ValueError, match="sort key"):
        state.select_sort("unsupported-column")
    nullable = LibrarySnapshot((
        LibraryWork(Artwork("undated", "タイトル", "作者A"), "a", last_backup=None),
        LibraryWork(Artwork("dated", "タイトル", "作者B"), "b", last_backup="2026-10-20"),
    ))
    state = LibraryState(nullable)
    state.select_sort("last_backup")
    assert [a.author_id for a in state.visible_authors] == ["b", "a"]
    state.select_sort("last_backup")
    assert [a.author_id for a in state.visible_authors] == ["a", "b"]


def test_unknown_page_fallback_and_legacy_page_boundary():
    source = Artwork("synthetic-a", "タイトル", "架空作者", page_count=3)
    work = LibraryWork(source, "author-a", page_count_known=False, last_backup=None)
    assert source.page_count == 3 and work.artwork.page_count == 1
    assert work.page_count == 1 and not work.page_count_known
    ref = PageRef.from_legacy(work, 1)
    assert ref.key == ("synthetic-a", 0) and ref.legacy_page_no == 1
    with pytest.raises(ValueError, match="page_no"):
        PageRef.from_legacy(work, 2)
    with pytest.raises(ValueError, match="legacy_page_no"):
        PageRef.from_legacy(work, True)
    raw = generate_demo_artworks(60)
    snapshot = build_library_fixture(raw)
    unknown = next(work for work in snapshot.works if work.work_id == "demo-0057")
    assert not unknown.page_count_known and unknown.artwork.page_count == 1
    catalog = DemoImageCatalog(raw)
    try:
        assert catalog.url(unknown.work_id, 0, "thumb").endswith("/demo-0057/0/thumb.png")
        assert catalog.url("demo-0060", 2, "view").endswith("/demo-0060/2/view.png")
    finally:
        catalog.close()


def test_generation_reentry_close_empty_and_instance_isolation():
    snapshot = build_library_fixture(generate_demo_artworks(120))
    first, second = LibraryState(snapshot), LibraryState(snapshot)
    assert first.open_author("demo-author-01")
    opened = first.generation
    assert first.load_more_pages() and first.generation == opened
    assert first.set_query("0003") and first.generation != opened
    assert first.selected_author is None
    assert first.open_author("demo-author-01") and first.shown_page_count == 50
    first.close()
    assert first.closed and first.selected_author_id is None
    assert not first.open_author("demo-author-01")
    assert not first.load_more_pages() and not first.close_author()
    assert not first.set_query("anything") and not first.select_sort("view_count")
    assert (second.query, second.matched_author_count, second.selected_author_id) == ("", 4, None)
    assert LibraryState(LibrarySnapshot(())).visible_authors == ()
    assert not LibraryState(LibrarySnapshot(())).open_author("missing")


def test_reject_duplicate_work_ids_and_invalid_parameters():
    work = LibraryWork(Artwork("synthetic-a", "題", "作者"), "author-a")
    with pytest.raises(ValueError, match="duplicate"):
        LibrarySnapshot((work, work))
    with pytest.raises(ValueError, match="page_size"):
        LibraryState(LibrarySnapshot(()), page_size=0)
    with pytest.raises(ValueError, match="author_id"):
        LibraryWork(work.artwork, None)
    with pytest.raises(ValueError, match="view_count"):
        LibraryWork(work.artwork, "author-a", view_count=True)
    assert LibrarySnapshot((work,)).authors[0].work_count == 1
    known = LibraryWork(Artwork("known", "題", "作者"), "author-a", capacity_bytes=1024)
    assert LibrarySnapshot((known,)).authors[0].capacity_bytes == 1024
    assert LibrarySnapshot((work, known)).authors[0].capacity_bytes is None


def test_author_list_pages_beyond_fifty_and_rejects_hidden_author_switch():
    snapshot = LibrarySnapshot(tuple(LibraryWork(
        Artwork(f"test-{i:03d}", f"題{i}", f"作者{i}"), f"author-{i:03d}")
        for i in range(51)))
    state = LibraryState(snapshot)
    assert state.shown_author_count == 50 and state.has_more_authors
    assert not state.open_author("author-050")
    assert state.load_more_authors() and state.shown_author_count == 51
    assert not state.has_more_authors
    assert state.open_author("author-050")
    assert not state.open_author("author-000")
    assert state.selected_author_id == "author-050"
    assert not state.load_more_authors()
    assert state.close_author()
    assert state.set_query("題50") and state.shown_author_count == 1
