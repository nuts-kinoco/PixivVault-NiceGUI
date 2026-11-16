"""Synthetic source composition; no original DB, network or real images."""
from dataclasses import replace
from types import MappingProxyType
import hashlib

import pytest

from pixivvault_nicegui.composite_library import (
    CompositeIdentityConflict, CompositeLocalCatalog, compose_library,
)
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.local_media_adapter import MediaMissing, MediaUnavailable
from pixivvault_nicegui.media import ImageBlob
from pixivvault_nicegui.models import Artwork


def work(work_id, *, pages=1, author="9", kind="illust", title="old",
         backup="2020-01-01", views=0, size=None, viewed=None):
    return LibraryWork(
        Artwork(work_id, title, "Author", page_count=pages, viewed=viewed),
        author, last_backup=backup, view_count=views, capacity_bytes=size,
        work_type=kind,
    )


def compose(old, new, *, pages=None, counts=None, excluded=(), old_local=None,
            new_local=None):
    if pages is None:
        pages = {item.work_id: tuple(range(item.page_count)) for item in new.works}
    if counts is None:
        counts = {item.work_id: item.page_count for item in new.works}
    return compose_library(
        old, new,
        tuple(item.artwork for item in old.works) if old_local is None else old_local,
        tuple(item.artwork for item in new.works) if new_local is None else new_local,
        managed_source_pages=pages, managed_original_page_counts=counts,
        managed_excluded_work_ids=excluded,
    )


class Catalog:
    """The managed leaf accepts visible indices and maps them exactly once."""
    def __init__(self, snapshot, raw, *, pages=None):
        self.works = {item.work_id: item for item in snapshot.works}
        self.source_pages = MappingProxyType(dict(pages if pages is not None else {
            item.work_id: tuple(range(item.page_count)) for item in snapshot.works
        }))
        self.raw = {key: tuple(value) for key, value in raw.items()}
        self.calls = []
        self.closed = False
        self.close_count = 0
        self.fail = False
        self.cache_bindings = []

    def url(self, work_id, page_no=0, variant="view"):
        if self.closed:
            raise MediaUnavailable("catalog closed")
        if (work_id not in self.works or type(page_no) is not int or
                not 0 <= page_no < self.works[work_id].page_count or
                variant not in {"thumb", "view", "original"}):
            raise MediaMissing("image not found")
        return f"/media/{work_id}/{page_no}/{variant}.png"

    def get(self, work_id, page_no=0, variant="view"):
        self.url(work_id, page_no, variant)
        self.calls.append((work_id, page_no, variant))
        if self.fail:
            raise MediaUnavailable("selected receipt revoked")
        data = self.raw[work_id][self.source_pages[work_id][page_no]]
        return ImageBlob(data, "image/png", '"' + hashlib.sha256(data).hexdigest() + '"')

    def peek(self, work_id, page_no=0, variant="view"):
        self.url(work_id, page_no, variant)
        return None

    def artwork_for(self, work_id):
        item = self.works.get(work_id)
        return item.artwork if item is not None and not self.closed else None

    def supports_original(self, work_id):
        return not self.closed and work_id in self.works

    def configure_cache(self, cache):
        self.cache_bindings.append(cache)

    def close(self):
        self.close_count += 1
        self.closed = True


def test_union_keeps_selected_metadata_and_original_history_without_mutation():
    prior = work("101", pages=4, views=7, viewed=True)
    untouched = work("102", size=20)
    saved = work("101", pages=2, title="new", backup="2026-10-29", size=40)
    added = work("103", size=10)
    old = LibrarySnapshot((prior, untouched), author_capacities={"9": 777})
    new = LibrarySnapshot((saved, added))
    pages = {"101": (0, 2), "103": (0,)}
    counts = {"101": 3, "103": 1}
    result = compose(old, new, pages=pages, counts=counts,
                     old_local=(prior.artwork,), new_local=(saved.artwork, added.artwork))
    assert [item.work_id for item in result.snapshot.works] == ["101", "102", "103"]
    selected = result.snapshot.works[0]
    assert (selected.artwork.title, selected.page_count, selected.view_count) == ("new", 2, 7)
    assert selected.artwork.viewed is True
    assert selected.last_backup == saved.last_backup
    provenance = result.selections["101"]
    assert provenance.source == "managed"
    assert provenance.source_pages == (0, 2)
    assert provenance.original_page_count == 3
    assert provenance.legacy_work is prior
    assert provenance.managed_work is saved
    assert provenance.legacy_work.last_backup == "2020-01-01"
    assert result.snapshot.authors[0].capacity_bytes == 70
    assert [item.work_id for item in result.local_bookmarks] == ["101", "103"]
    assert result.local_bookmarks[0] == selected.artwork
    pages["101"] = (1,)
    counts["101"] = 999
    assert result.managed_source_pages["101"] == (0, 2)
    assert result.managed_original_page_counts["101"] == 3
    assert old.works[0] is prior and old.author_capacities["9"] == 777
    assert new.works[0] is saved and saved.view_count == 0
    with pytest.raises(TypeError):
        result.selections["101"] = provenance


@pytest.mark.parametrize("change", [{"author_id": "10"}, {"work_type": "manga"}])
def test_same_id_conflicts_are_explicit_not_merged(change):
    prior = work("101")
    current = replace(work("101", title="new"), **change)
    with pytest.raises(CompositeIdentityConflict) as failure:
        compose(LibrarySnapshot((prior,)), LibrarySnapshot((current,)))
    assert failure.value.work_id == "101"


def test_unknown_legacy_author_and_illustration_alias_do_not_fake_a_conflict():
    prior = work("101", author="", kind="illustration", views=3)
    current = work("101", author="9", kind="illust")
    result = compose(LibrarySnapshot((prior,)), LibrarySnapshot((current,)))
    assert result.snapshot.works[0].author_id == "9"
    assert result.snapshot.works[0].view_count == 3


def test_partial_visibility_delegates_visible_index_once_and_keeps_raw_count():
    old = LibrarySnapshot((work("101", pages=4),))
    new = LibrarySnapshot((work("101", pages=2),))
    mapping = {"101": (1, 3)}
    result = compose(old, new, pages=mapping, counts={"101": 4})
    legacy = Catalog(old, {"101": (b"L0", b"L1", b"L2", b"L3")})
    managed = Catalog(new, {"101": (b"M0", b"M1", b"M2", b"M3")}, pages=mapping)
    catalog = CompositeLocalCatalog(result, legacy_catalog=legacy, managed_catalog=managed)
    try:
        assert catalog.artwork_for("101").page_count == 2
        assert catalog.get("101", 0, "original").data == b"M1"
        assert catalog.get("101", 1, "original").data == b"M3"
        assert managed.calls == [("101", 0, "original"), ("101", 1, "original")]
        assert not legacy.calls
        assert result.selections["101"].original_page_count == 4
        with pytest.raises(MediaMissing):
            catalog.get("101", 2, "original")
        managed.fail = True
        with pytest.raises(MediaUnavailable, match="revoked"):
            catalog.get("101", 0, "original")
        assert not legacy.calls
    finally:
        catalog.close()


def test_empty_mapping_and_explicit_tombstone_never_revive_legacy_work_or_badge():
    old = LibrarySnapshot((work("101", pages=3), work("102")))
    new = LibrarySnapshot((work("103"),))
    mapping = {"101": (), "103": (0,)}
    result = compose(old, new, pages=mapping, counts={"101": 3, "103": 1}, excluded={"102"})
    assert tuple(item.work_id for item in result.snapshot.works) == ("103",)
    assert tuple(item.work_id for item in result.local_bookmarks) == ("103",)
    assert result.excluded_work_ids == frozenset({"101", "102"})
    legacy = Catalog(old, {"101": (b"L0", b"L1", b"L2"), "102": (b"L",)})
    managed = Catalog(new, {"101": (b"M0", b"M1", b"M2"), "103": (b"M",)}, pages=mapping)
    catalog = CompositeLocalCatalog(result, legacy_catalog=legacy, managed_catalog=managed)
    try:
        for key in ("101", "102"):
            assert catalog.artwork_for(key) is None
            with pytest.raises(MediaMissing):
                catalog.get(key, 0, "original")
        assert not legacy.calls and not managed.calls
    finally:
        catalog.close()


@pytest.mark.parametrize("mapping,counts", [
    ({}, {"101": 3}),
    ({"101": (0, 2)}, {}),
    ({"101": (0,)}, {"101": 3}),
    ({"101": (0, 0)}, {"101": 3}),
    ({"101": (0, 3)}, {"101": 3}),
    ({"101": (False, 2)}, {"101": 3}),
])
def test_inconsistent_visible_snapshot_and_source_mapping_are_rejected(mapping, counts):
    with pytest.raises(ValueError):
        compose(LibrarySnapshot(()), LibrarySnapshot((work("101", pages=2),)),
                pages=mapping, counts=counts)


def test_registry_keeps_both_clients_on_their_own_source_mapping():
    old = LibrarySnapshot((work("101", pages=3),))
    first = LibrarySnapshot((work("101", pages=2),))
    second = LibrarySnapshot((work("101", pages=1),))
    first_pages, second_pages = {"101": (0, 2)}, {"101": (2,)}
    legacy_a = Catalog(old, {"101": (b"L0", b"L1", b"L2")})
    legacy_b = Catalog(old, {"101": (b"L0", b"L1", b"L2")})
    managed_a = Catalog(first, {"101": (b"A0", b"A1", b"A2")}, pages=first_pages)
    managed_b = Catalog(second, {"101": (b"B0", b"B1", b"B2")}, pages=second_pages)
    combined_a = CompositeLocalCatalog(
        compose(old, first, pages=first_pages, counts={"101": 3}),
        legacy_catalog=legacy_a, managed_catalog=managed_a)
    combined_b = CompositeLocalCatalog(
        compose(old, second, pages=second_pages, counts={"101": 3}),
        legacy_catalog=legacy_b, managed_catalog=managed_b)
    registry = LibraryImageRegistry(Catalog(LibrarySnapshot(()), {}))
    pin_a = pin_b = None
    try:
        registry.publish(combined_a, None)
        pin_a = registry.acquire()
        registry.publish(combined_b, None)
        pin_b = registry.acquire()
        assert pin_a.token != pin_b.token
        assert pin_a.get("101", 0, "original").data == b"A0"
        assert pin_a.get("101", 1, "original").data == b"A2"
        assert pin_b.get("101", 0, "original").data == b"B2"
        assert "catalog=" + pin_a.token in pin_a.url("101", 1, "original")
        assert not managed_a.closed
        pin_a.close()
        assert managed_a.closed and legacy_a.closed
        assert not managed_b.closed
    finally:
        if pin_a is not None:
            pin_a.close()
        if pin_b is not None:
            pin_b.close()
        registry.close()
    assert managed_a.close_count == managed_b.close_count == 1
    assert legacy_a.close_count == legacy_b.close_count == 1


def test_candidate_catalog_must_match_the_fixed_snapshot_mapping():
    new = LibrarySnapshot((work("101", pages=2),))
    result = compose(LibrarySnapshot(()), new, pages={"101": (0, 2)}, counts={"101": 3})
    wrong = Catalog(new, {"101": (b"0", b"1", b"2")}, pages={"101": (1, 2)})
    with pytest.raises(ValueError):
        CompositeLocalCatalog(result, managed_catalog=wrong)
    assert not wrong.closed
    wrong.close()


def test_pinned_page_identity_is_delegated_with_visible_index_only():
    old = LibrarySnapshot((work("101", pages=3), work("102")))
    new = LibrarySnapshot((work("101", pages=2),))
    pages = {"101": (0, 2)}
    result = compose(old, new, pages=pages, counts={"101": 3})
    legacy = Catalog(old, {"101": (b"L0", b"L1", b"L2"), "102": (b"L",)})
    managed = Catalog(new, {"101": (b"M0", b"M1", b"M2")}, pages=pages)
    identity = object()
    calls = []
    def selected(wid, visible):
        calls.append((wid, visible))
        return identity
    managed.source_page_identity = selected
    combined = CompositeLocalCatalog(result, legacy_catalog=legacy, managed_catalog=managed)
    try:
        assert combined.source_page_identity("101", 1) is identity
        assert calls == [("101", 1)]
        assert combined.source_page_identity("102", 0) is None
        with pytest.raises(MediaMissing):
            combined.source_page_identity("101", 2)
    finally:
        combined.close()
