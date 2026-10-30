"""Combine frozen legacy reads and verified managed receipts without fallback.

Callers supply already validated, same-generation managed inputs. No SQLite,
filesystem, network or writer operations occur here. Managed source pages are
the X07 pinned visible-to-original mapping; leaf catalog APIs take VISIBLE
indices and perform the mapping themselves. Empty mappings are tombstones.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType

from .library_models import LibrarySnapshot, LibraryWork
from .local_media_adapter import MediaMissing, MediaUnavailable
from .models import Artwork


class CompositeIdentityConflict(ValueError):
    """Candidate publication must fail rather than merge two identities."""
    def __init__(self, work_id: str, reason: str):
        super().__init__("library source identity conflict")
        self.work_id = work_id
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LibrarySelection:
    source: str
    source_pages: tuple[int, ...]
    original_page_count: int | None
    legacy_work: LibraryWork | None
    managed_work: LibraryWork | None


@dataclass(frozen=True, slots=True)
class CompositeLibrary:
    snapshot: LibrarySnapshot
    local_bookmarks: tuple[Artwork, ...]
    selections: Mapping[str, LibrarySelection]
    managed_source_pages: Mapping[str, tuple[int, ...]]
    managed_original_page_counts: Mapping[str, int]
    excluded_work_ids: frozenset[str]
    legacy_snapshot: LibrarySnapshot
    managed_snapshot: LibrarySnapshot

    def __post_init__(self):
        object.__setattr__(self, "local_bookmarks", tuple(self.local_bookmarks))
        object.__setattr__(self, "selections", MappingProxyType(dict(self.selections)))
        object.__setattr__(self, "managed_source_pages", MappingProxyType({
            key: tuple(value) for key, value in self.managed_source_pages.items()
        }))
        object.__setattr__(self, "managed_original_page_counts",
                           MappingProxyType(dict(self.managed_original_page_counts)))
        object.__setattr__(self, "excluded_work_ids", frozenset(self.excluded_work_ids))


def _managed_maps(snapshot, source_pages, original_counts):
    if not isinstance(source_pages, Mapping) or not isinstance(original_counts, Mapping):
        raise ValueError("managed source mappings required")
    counts = dict(original_counts)
    if set(counts) != set(source_pages):
        raise ValueError("managed original counts and mapping identities differ")
    pages = {}
    for work_id, values in source_pages.items():
        count = counts[work_id]
        if (type(work_id) is not str or not work_id or type(count) is not int or
                not 1 <= count <= 1000 or isinstance(values, (str, bytes))):
            raise ValueError("invalid managed source mapping")
        try:
            indices = tuple(values)
        except TypeError:
            raise ValueError("invalid managed source mapping") from None
        if (any(type(index) is not int or not 0 <= index < count for index in indices)
                or indices != tuple(sorted(set(indices)))):
            raise ValueError("invalid managed source page indices")
        pages[work_id] = indices
    works = {item.work_id: item for item in snapshot.works}
    if set(works) != {key for key, value in pages.items() if value}:
        raise ValueError("managed visible snapshot and mappings differ")
    for work_id, item in works.items():
        if not item.page_count_known or item.page_count != len(pages[work_id]):
            raise ValueError("managed visible page count differs")
    return pages, counts


def _kind(value):
    return "illust" if value == "illustration" else value


def _merge_work(legacy, managed):
    if legacy is None:
        return managed
    if legacy.author_id and managed.author_id and legacy.author_id != managed.author_id:
        raise CompositeIdentityConflict(managed.work_id, "author")
    if _kind(legacy.work_type) != _kind(managed.work_type):
        raise CompositeIdentityConflict(managed.work_id, "type")
    artwork = managed.artwork
    if artwork.viewed is None and legacy.artwork.viewed is not None:
        artwork = replace(artwork, viewed=legacy.artwork.viewed)
    return replace(
        managed, artwork=artwork,
        view_count=max(legacy.view_count, managed.view_count),
        last_backup=(managed.last_backup if managed.last_backup is not None
                     else legacy.last_backup),
    )


def _local_ids(items, source):
    result = []
    for item in items:
        if not isinstance(item, Artwork) or item.work_id not in source:
            raise ValueError("local bookmark does not belong to its source snapshot")
        result.append(item.work_id)
    return result


def _author_capacities(works, selections, legacy, managed):
    """Reuse an aggregate only when that author's selected source is intact.

    Mixed sources and filtered authors use per-work capacities instead. The
    LibrarySnapshot then preserves unknown sizes rather than inventing zero.
    """
    groups = {}
    for item in works:
        groups.setdefault(item.author_id, set()).add(item.work_id)
    capacities = {}
    for author_id, selected_ids in groups.items():
        origins = {selections[work_id].source for work_id in selected_ids}
        if len(origins) != 1:
            continue
        source = legacy if origins == {"legacy"} else managed
        original_ids = {item.work_id for item in source.works if item.author_id == author_id}
        if selected_ids == original_ids and author_id in source.author_capacities:
            capacities[author_id] = source.author_capacities[author_id]
    return capacities


def compose_library(
    legacy_snapshot: LibrarySnapshot,
    managed_snapshot: LibrarySnapshot,
    legacy_local_bookmarks=(),
    managed_local_bookmarks=(),
    *,
    managed_source_pages: Mapping[str, tuple[int, ...]],
    managed_original_page_counts: Mapping[str, int],
    managed_excluded_work_ids=(),
) -> CompositeLibrary:
    """Build one immutable candidate, retaining both originals as provenance.

    Original page counts come from the same catalog's unchanged raw receipts,
    never from visible counts or max(source_pages). Explicit exclusions may
    further restrict an older fixed map; they cannot revive it. Missing/corrupt
    reads must be handled before this function, never as a false empty snapshot.
    """
    if not isinstance(legacy_snapshot, LibrarySnapshot) or not isinstance(managed_snapshot, LibrarySnapshot):
        raise TypeError("library snapshots required")
    pages, counts = _managed_maps(managed_snapshot, managed_source_pages,
                                  managed_original_page_counts)
    if isinstance(managed_excluded_work_ids, (str, bytes)):
        raise ValueError("excluded work IDs must be a collection")
    excluded = frozenset(managed_excluded_work_ids)
    if any(type(key) is not str or not key for key in excluded):
        raise ValueError("invalid excluded work identity")
    excluded = excluded | frozenset(key for key, value in pages.items() if not value)
    legacy = {item.work_id: item for item in legacy_snapshot.works}
    managed = {item.work_id: item for item in managed_snapshot.works}
    local_ids = _local_ids(legacy_local_bookmarks, legacy)
    local_ids += _local_ids(managed_local_bookmarks, managed)
    ordered_ids = tuple(legacy) + tuple(key for key in managed if key not in legacy)
    works = []
    selections = {}
    for work_id in ordered_ids:
        if work_id in excluded:
            continue
        prior = legacy.get(work_id)
        current = managed.get(work_id)
        if work_id in pages:
            if current is None:
                raise ValueError("managed source selection unavailable")
            item = _merge_work(prior, current)
            selection = LibrarySelection("managed", pages[work_id], counts[work_id],
                                         prior, current)
        else:
            item = prior
            if item is None:
                raise ValueError("legacy source selection unavailable")
            selection = LibrarySelection(
                "legacy", tuple(range(item.page_count)),
                item.page_count if item.page_count_known else None, item, None)
        works.append(item)
        selections[work_id] = selection
    by_id = {item.work_id: item for item in works}
    seen = set()
    local = []
    for work_id in local_ids:
        if work_id in by_id and work_id not in seen:
            local.append(by_id[work_id].artwork)
            seen.add(work_id)
    names = dict(legacy_snapshot.author_names)
    names.update(managed_snapshot.author_names)
    for item in works:
        if selections[item.work_id].source == "managed":
            names[item.author_id] = item.display_author_name
        else:
            names.setdefault(item.author_id, item.display_author_name)
    snapshot = LibrarySnapshot(
        tuple(works),
        author_capacities=_author_capacities(works, selections, legacy_snapshot, managed_snapshot),
        author_names=names,
    )
    return CompositeLibrary(snapshot, tuple(local), selections, pages, counts,
                            excluded, legacy_snapshot, managed_snapshot)


class CompositeLocalCatalog:
    """One registry version owns dedicated legacy and managed leaf catalogs.

    Construction transfers ownership only on success. Leaves must not be
    shared with another registry version. Registry references retain this
    wrapper and its leaves until the last client/in-flight read releases them.
    A failed managed read propagates; no legacy or remote fallback is attempted.
    """
    def __init__(self, composition: CompositeLibrary, *, legacy_catalog=None,
                 managed_catalog=None):
        if not isinstance(composition, CompositeLibrary):
            raise TypeError("composite library required")
        providers = {"legacy": legacy_catalog, "managed": managed_catalog}
        for work_id, selection in composition.selections.items():
            provider = providers[selection.source]
            expected = (selection.legacy_work if selection.source == "legacy"
                        else selection.managed_work)
            if provider is None or getattr(provider, "works", {}).get(work_id) != expected:
                raise ValueError("catalog does not match selected source snapshot")
        if managed_catalog is not None:
            mapping = getattr(managed_catalog, "source_pages", None)
            if not isinstance(mapping, Mapping) or dict(mapping) != dict(composition.managed_source_pages):
                raise ValueError("catalog does not match pinned managed source mapping")
        self.composition = composition
        self.works = MappingProxyType({item.work_id: item for item in composition.snapshot.works})
        self.source_pages = MappingProxyType({
            **{key: value.source_pages for key, value in composition.selections.items()},
            **{key: () for key in composition.excluded_work_ids},
        })
        self._providers = providers
        self._lock = RLock()
        self._closed = False
        self._shared_cache = None

    def _select(self, work_id, page_no, variant):
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            item = self.works.get(work_id) if isinstance(work_id, str) else None
            if (item is None or type(page_no) is not int or
                    not 0 <= page_no < item.page_count or
                    variant not in {"thumb", "view", "original"}):
                raise MediaMissing("image not found")
            selection = self.composition.selections[work_id]
            return self._providers[selection.source]

    def _still_open(self):
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")

    def url(self, work_id, page_no=0, variant="view"):
        provider = self._select(work_id, page_no, variant)
        return provider.url(work_id, page_no, variant)

    def artwork_for(self, work_id):
        with self._lock:
            item = self.works.get(work_id) if isinstance(work_id, str) else None
            return item.artwork if item is not None and not self._closed else None

    def supports_original(self, work_id):
        with self._lock:
            if self._closed or work_id not in self.works:
                return False
            provider = self._providers[self.composition.selections[work_id].source]
            method = getattr(provider, "supports_original", None)
            return bool(callable(method) and method(work_id))

    def get(self, work_id, page_no=0, variant="view"):
        provider = self._select(work_id, page_no, variant)
        value = provider.get(work_id, page_no, variant)
        self._still_open()
        return value

    def peek(self, work_id, page_no=0, variant="view"):
        provider = self._select(work_id, page_no, variant)
        method = getattr(provider, "peek", None)
        value = method(work_id, page_no, variant) if callable(method) else None
        self._still_open()
        return value

    def external_target(self, work_id, page_no=0):
        provider = self._select(work_id, page_no, "view")
        method = getattr(provider, "external_target", None)
        if not callable(method):
            raise MediaUnavailable("saved target unavailable")
        value = method(work_id, page_no)
        self._still_open()
        return value

    def source_page_identity(self, work_id, visible_page_no=0):
        provider = self._select(work_id, visible_page_no, "original")
        if self.composition.selections[work_id].source != "managed":
            return None
        method = getattr(provider, "source_page_identity", None)
        if not callable(method):
            raise MediaUnavailable("pinned managed page identity unavailable")
        identity = method(work_id, visible_page_no)
        self._still_open()
        return identity

    def configure_cache(self, shared_cache):
        with self._lock:
            if self._closed:
                raise MediaUnavailable("catalog closed")
            if self._shared_cache is shared_cache:
                return
            unique = {id(item): item for item in self._providers.values() if item is not None}
            if any(not callable(getattr(item, "configure_cache", None)) for item in unique.values()):
                raise ValueError("leaf catalog does not support shared image cache")
            for item in unique.values():
                item.configure_cache(shared_cache)
            self._shared_cache = shared_cache

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unique = {id(item): item for item in self._providers.values() if item is not None}
        first_error = None
        for item in unique.values():
            try:
                item.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
