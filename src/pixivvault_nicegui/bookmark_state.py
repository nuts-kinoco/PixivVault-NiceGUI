"""Pure per-client state for the synthetic My Bookmarks view.

Callers own fetching and persistence. A failed fetch never replaces a successful
remote cache; an empty successful fetch deliberately replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import count
from typing import Iterable

from .models import Artwork
from .library_filters import LibraryFilters


_epochs = count(1)


@dataclass(frozen=True, slots=True)
class BookmarkCard:
    artwork: Artwork
    downloaded: bool

    @property
    def work_id(self) -> str:
        return self.artwork.work_id


class BookmarkState:
    """UI-independent cache, selection, pagination and fetch coordination."""

    def __init__(
        self,
        local: Iterable[Artwork] = (),
        cached_remote: Iterable[Artwork] | None = None,
        page_size: int = 50,
        *,
        known_artworks: Iterable[Artwork] = (),
    ) -> None:
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        self.page_size = page_size
        self._local = self._unique(local)
        self._remote = None if cached_remote is None else self._unique(cached_remote)
        self._known = self._unique(known_artworks)
        self.selected_ids: set[str] = set()
        self.shown_count = page_size
        self.query = ""
        self.filters = LibraryFilters()
        self.fetch_failed = False
        self.loaded_at: str | None = None
        self.loading = False
        self.silent_fetch = False
        self.active = True
        self.closed = False
        self.epoch = next(_epochs)
        self._pending: int | None = None

    @staticmethod
    def _unique(items: Iterable[Artwork]) -> tuple[Artwork, ...]:
        by_id: dict[str, Artwork] = {}
        for item in items:
            if not isinstance(item, Artwork):
                raise TypeError("bookmarks must contain Artwork")
            by_id.setdefault(item.work_id, item)
        return tuple(by_id.values())

    @property
    def has_remote_cache(self) -> bool:
        return self._remote is not None

    @property
    def remote_cache(self) -> tuple[Artwork, ...] | None:
        return self._remote

    @property
    def items(self) -> tuple[BookmarkCard, ...]:
        # Original _merge_bookmarks iterates remote only; local marks downloaded.
        local = {item.work_id: item for item in self._local}
        known = {item.work_id: item for item in self._known}
        remote = self._remote or ()
        cards = []
        for item in remote:
            artwork = local.get(item.work_id, item)
            # UI IDs already namespace novels as novel:<id>. This enriches
            # metadata only; remote membership and the local DL flag stay put.
            if artwork.bookmark_count is None:
                source = known.get(item.work_id)
                count = source.bookmark_count if source is not None else None
                if count is None:
                    count = item.bookmark_count
                if count is not None:
                    artwork = replace(artwork, bookmark_count=count)
            cards.append(BookmarkCard(artwork, item.work_id in local))
        return tuple(cards)

    @property
    def visible_items(self) -> tuple[BookmarkCard, ...]:
        return self.matched_items[:self.shown_count]

    @property
    def matched_items(self) -> tuple[BookmarkCard, ...]:
        needle = self.query.lower()
        return tuple(card for card in self.items
                     if self.filters.matches_artwork(card.artwork, downloaded=card.downloaded)
                     and (not needle or needle in " ".join((
                         card.work_id, card.artwork.title, card.artwork.author,
                         *card.artwork.tags)).lower()))

    def set_filters(self, filters: LibraryFilters) -> bool:
        if not isinstance(filters, LibraryFilters):
            raise TypeError("filters must be LibraryFilters")
        if self.closed or self.filters == filters:
            return False
        self.filters = filters
        self.shown_count = self.page_size
        return True

    def set_query(self, query: str) -> bool:
        if self.closed or not self.active or not isinstance(query, str) or query == self.query:
            return False
        self.query = query
        self.shown_count = self.page_size
        return True

    @property
    def has_more(self) -> bool:
        return len(self.visible_items) < len(self.matched_items)

    @property
    def status(self) -> str:
        if self.fetch_failed:
            return "fetch_failed"
        if self.loading and not self.silent_fetch:
            return "loading"
        if not self.items and self.has_remote_cache:
            return "empty"
        if not self.items and not self.has_remote_cache:
            return "unloaded"
        if self.items and not self.matched_items:
            return "no_match"
        return "ready"

    def load_more(self) -> bool:
        if self.closed or not self.active or not self.has_more:
            return False
        self.shown_count = min(self.shown_count + self.page_size, len(self.matched_items))
        return True

    def select(self, work_id: str) -> bool:
        if self.closed or not self.active or work_id not in {x.work_id for x in self.items}:
            return False
        if work_id in self.selected_ids:
            return False
        self.selected_ids.add(work_id)
        return True

    def deselect(self, work_id: str) -> bool:
        if self.closed or not self.active or work_id not in self.selected_ids:
            return False
        self.selected_ids.remove(work_id)
        return True

    def begin_fetch(self, *, silent: bool = False) -> int | None:
        """Start or retry a fetch; the returned epoch must accompany its result."""
        if self.closed or not self.active:
            return None
        self.epoch = next(_epochs)
        self._pending = self.epoch
        self.loading = True
        self.silent_fetch = silent
        self.fetch_failed = False
        return self.epoch

    def complete_fetch(self, epoch: int, remote: Iterable[Artwork], *, ok: bool) -> bool:
        """Return False for an obsolete/cancelled response without reading its payload."""
        if (self.closed or not self.active or not self.loading
                or type(epoch) is not int or epoch != self._pending):
            return False
        if type(ok) is not bool:
            raise TypeError("ok must be bool")
        new_remote = self._unique(remote) if ok else None
        self.loading = False
        self.silent_fetch = False
        self._pending = None
        self.fetch_failed = not ok
        if ok:
            self._remote = new_remote
            self.shown_count = self.page_size
            self.selected_ids.intersection_update(card.work_id for card in self.items)
        return True

    def cancel_fetch(self, epoch: int) -> bool:
        if self.closed or not self.active or self._pending != epoch:
            return False
        self.loading = False
        self.silent_fetch = False
        self._pending = None
        return True

    def replace_local(self, local: Iterable[Artwork]) -> bool:
        if self.closed:
            return False
        self._local = self._unique(local)
        self.selected_ids.intersection_update(card.work_id for card in self.items)
        return True

    def adopt_cache(self, remote: Iterable[Artwork], local: Iterable[Artwork], loaded_at: str | None) -> None:
        """Adopt a completed service snapshot, retaining selection where possible."""
        self._remote = self._unique(remote)
        self._local = self._unique(local)
        self.loaded_at = loaded_at
        self.fetch_failed = False
        self.selected_ids.intersection_update(card.work_id for card in self.items)

    def replace_known(self, known_artworks: Iterable[Artwork]) -> bool:
        if self.closed:
            return False
        self._known = self._unique(known_artworks)
        return True

    def revisit(self, active: bool) -> None:
        """A tab switch keeps visible data and selection, invalidating old fetches."""
        if type(active) is not bool:
            raise TypeError("active must be bool")
        if self.closed:
            return
        if self.active != active:
            self.active = active
            self.epoch = next(_epochs)
            self._pending = None
            self.loading = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.active = False
            self.loading = False
            self._pending = None
            self.epoch = next(_epochs)
