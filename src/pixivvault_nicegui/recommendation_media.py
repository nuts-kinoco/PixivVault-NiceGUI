"""Recommendation previews borrow the existing verified image providers.

Creating a shelf snapshot performs no HTTP request and does not open a source
database. Only its visible records are registered. Local saved identities,
including managed tombstones, always take precedence over remote previews.
"""
from __future__ import annotations

from dataclasses import dataclass

from .bookmark_remote_adapter import RemoteBookmark
from .models import Artwork
from .recommendation_state import RecommendationWork


class RecommendationMediaUnavailable(ValueError):
    def __init__(self):
        super().__init__('recommendation_image_unavailable_or_changed')


class RecommendationRemoteImages:
    """Guard the registered HTTP provider, including already issued URLs.

    Do not acquire the registry lock from RemoteBookmarkImages._record: that
    method runs under the delegate lock, while registry URL/HEAD lookups take
    those locks in the opposite order. This wrapper has no lock and observes
    the current local catalog only before/after the complete delegate call.
    Image bytes, GIFs and page metadata are discarded if ownership changed
    during their I/O. Neither this wrapper nor its delegate owns the reader.
    """
    def __init__(self, remote, registry):
        self._remote = remote
        self._registry = registry
        self._closed = False

    @property
    def works(self):
        return self._remote.works

    def _guard(self, work_id):
        registry = self._registry
        if self._closed or registry is None:
            raise RecommendationMediaUnavailable()
        try:
            catalog = registry.library
        except (KeyError, RuntimeError):
            raise RecommendationMediaUnavailable() from None
        if (work_id in getattr(catalog, 'source_pages', {})
                or work_id in getattr(catalog, 'works', {})):
            raise RecommendationMediaUnavailable()
        if self._closed or self._registry is not registry:
            raise RecommendationMediaUnavailable()

    def _checked(self, work_id, method, *args):
        self._guard(work_id)
        value = method(work_id, *args)
        try:
            self._guard(work_id)
        except BaseException:
            # Do not retain a rejected image/GIF in an exception traceback.
            value = None
            raise
        return value

    def url(self, work_id, page_no=0, variant='view'):
        return self._checked(work_id, self._remote.url, page_no, variant)

    def get(self, work_id, page_no=0, variant='view'):
        return self._checked(work_id, self._remote.get, page_no, variant)

    def peek(self, work_id, page_no=0, variant='view'):
        return self._checked(work_id, self._remote.peek, page_no, variant)

    def is_animation(self, work_id):
        return self._checked(work_id, self._remote.is_animation)

    def supports_original(self, work_id):
        return self._checked(work_id, self._remote.supports_original)

    def display_animation(self, work_id):
        return self._checked(work_id, self._remote.display_animation)

    def needs_metadata(self, work_id):
        return self._checked(work_id, self._remote.needs_metadata)

    def prepare_artwork(self, work_id):
        return self._checked(work_id, self._remote.prepare_artwork)

    def artwork_for(self, work_id):
        return self._checked(work_id, self._remote.artwork_for)

    def configure_cache(self, cache):
        if self._closed:
            raise RecommendationMediaUnavailable()
        self._remote.configure_cache(cache)

    def close(self):
        if not self._closed:
            self._remote.close()
            self._closed = True
            self._registry = None


@dataclass
class RecommendationSelection:
    artwork: Artwork
    images: object
    owns_pin: bool = False
    closed: bool = False

    def close(self):
        if not self.closed:
            self.closed = True
            if self.owns_pin:
                self.images.close()


def _record(work):
    return RemoteBookmark(
        work.work_id, 'illustration', 'show', work.title, work.author_id,
        work.author_name, work.tags, work.thumbnail_url, work.create_date,
        work.page_count or 1, work.illust_type,
        work.x_restrict if work.x_restrict is not None else 0,
        page_count_known=work.page_count is not None or work.illust_type == 2)


def _local_snapshot(services):
    """Observe a local catalog inside one retained, immutable version."""
    acquire = getattr(services, 'acquire_library_images', None)
    registry = getattr(services, 'images', None)
    if not callable(acquire) or registry is None:
        return None, None
    for _ in range(3):
        before = acquire()
        after = None
        try:
            catalog = registry.library
            after = acquire()
            if before.token == after.token:
                return before, catalog
        except BaseException:
            before.close()
            raise
        finally:
            if after is not None:
                after.close()
        before.close()
    raise RecommendationMediaUnavailable()


class RecommendationMediaSnapshot:
    """Two borrowed service catalogs at most; never own or close the reader."""

    def __init__(self, services, works):
        self.services = services
        self.closed = False
        self.works = {}
        for work in works:
            # Defensive rendering of an invalid/unsupported legacy card must
            # not manufacture an ID or request an unrelated remote image.
            if isinstance(work, RecommendationWork):
                self.works.setdefault(work.work_id, work)
        self.local_pin = None
        self.local_token = None
        self.remote_pin = None
        self.saved = {}
        self.reserved = frozenset()
        self.blocked = frozenset()
        try:
            self.local_pin, catalog = _local_snapshot(services)
            self.local_token = getattr(self.local_pin, 'token', None)
            local_works = getattr(catalog, 'works', {})
            source_pages = getattr(catalog, 'source_pages', {})
            # Look up only visible IDs; do not scan every saved receipt for
            # every image or duplicate the full library in each client.
            self.reserved = frozenset(wid for wid in self.works
                                      if wid in local_works or wid in source_pages)
            self.blocked = frozenset(wid for wid in self.reserved
                                     if wid in source_pages and not source_pages[wid])
            self.saved = {wid: local_works[wid] for wid in self.reserved
                          if wid in local_works and wid not in self.blocked}
            if not self.saved and self.local_pin is not None:
                self.local_pin.close()
                self.local_pin = None
            records = tuple(_record(work) for wid, work in self.works.items()
                            if wid not in self.reserved)
            acquire = getattr(services, 'acquire_recommendation_images', None)
            if records and callable(acquire):
                try:
                    self.remote_pin = acquire(records)
                except RuntimeError:
                    # A read-only/offline profile may have no remote image
                    # capability. It still displays saved metadata/local data.
                    self.remote_pin = None
        except BaseException:
            self.close()
            raise

    def matches(self, works):
        if self.closed:
            return False
        wanted = {}
        for work in works:
            if isinstance(work, RecommendationWork):
                wanted.setdefault(work.work_id, work)
        if wanted != self.works:
            return False
        acquire = getattr(self.services, 'acquire_library_images', None)
        if not callable(acquire):
            return self.local_token is None
        pin = acquire()
        try:
            return pin.token == self.local_token
        finally:
            pin.close()

    def _require(self, work_id):
        if self.closed or work_id not in self.works:
            raise RecommendationMediaUnavailable()
        return self.works[work_id]

    def ensure_source(self, work_id):
        self._require(work_id)
        if work_id in self.blocked:
            raise RecommendationMediaUnavailable()
        if work_id in self.reserved:
            if (work_id not in self.saved or self.local_pin is None
                    or not self.local_pin.can_open(work_id)):
                raise RecommendationMediaUnavailable()
            return
        # A remote-only card must be refreshed if a managed/local identity was
        # published meanwhile. It cannot bypass a newly published tombstone.
        registry = getattr(self.services, 'images', None)
        catalog = getattr(registry, 'library', None)
        if (work_id in getattr(catalog, 'source_pages', {})
                or work_id in getattr(catalog, 'works', {})):
            raise RecommendationMediaUnavailable()

    def thumbnail_url(self, work_id):
        work = self._require(work_id)
        self.ensure_source(work_id)
        if work_id in self.reserved:
            return self.local_pin.url(work_id, 0, 'thumb')
        if self.remote_pin is None or not work.thumbnail_url:
            return None
        return self.remote_pin.url(work_id, 0, 'thumb')

    def needs_detail(self, work_id):
        work = self._require(work_id)
        self.ensure_source(work_id)
        return work_id not in self.reserved and (
            work.illust_type is None or not work.author_id)

    def read_detail(self, work_id):
        """The caller invokes this only after an explicit, still-current click.

        Return data only from the worker thread. A cancelled/stale await can
        therefore never orphan a newly acquired registry pin.
        """
        work = self._require(work_id)
        self.ensure_source(work_id)
        reader = (getattr(self.services, 'live_client', None)
                  or getattr(getattr(self.services, 'ranking_source', None), 'reader', None))
        read = getattr(reader, 'get_discovery_work', None)
        if not callable(read):
            raise RecommendationMediaUnavailable()
        candidate = RecommendationWork.from_record(read(work_id))
        if (candidate.work_id != work_id or candidate.illust_type is None
                or not candidate.author_id
                or (work.author_id and candidate.author_id != work.author_id)):
            raise RecommendationMediaUnavailable()
        return candidate

    def select(self, work_id, resolved=None):
        """Acquire a viewer selection on the UI loop; this method performs no GET."""
        work = self._require(work_id)
        self.ensure_source(work_id)
        if work_id in self.reserved:
            if resolved is not None:
                raise RecommendationMediaUnavailable()
            return RecommendationSelection(self.saved[work_id].artwork, self.local_pin)
        chosen = resolved or work
        if (chosen.work_id != work_id or chosen.illust_type is None
                or not chosen.author_id
                or (work.author_id and chosen.author_id != work.author_id)):
            raise RecommendationMediaUnavailable()
        owned = resolved is not None or self.remote_pin is None
        if owned:
            acquire = getattr(self.services, 'acquire_recommendation_images', None)
            if not callable(acquire):
                raise RecommendationMediaUnavailable()
            pin = acquire((_record(chosen),))
        else:
            pin = self.remote_pin
        try:
            if not pin.can_open(work_id):
                raise RecommendationMediaUnavailable()
            art = pin.artwork_for(work_id)
            if art is None or art.work_id != work_id:
                raise RecommendationMediaUnavailable()
            # The transport record uses the existing remote-media convention;
            # the displayed metadata keeps unknown access restriction unknown.
            from dataclasses import replace
            art = replace(art, x_restrict=chosen.x_restrict)
            return RecommendationSelection(art, pin, owns_pin=owned)
        except BaseException:
            if owned:
                pin.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        for pin in (self.remote_pin, self.local_pin):
            if pin is not None:
                pin.close()
        self.remote_pin = self.local_pin = None
