"""Recommendation previews borrow the existing verified image providers.

Creating a shelf snapshot performs no HTTP request and does not open a source
database. Only its visible records are registered. Local saved identities,
including managed tombstones, always take precedence over remote previews.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .bookmark_remote_adapter import RemoteBookmark
from .learning_service import notify_shadow
from .library_media_registry import PinnedLibraryImages
from .media import ImageBlob
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
        self.view_observer = None

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
        observer = self.view_observer
        if observer is not None and page_no == 0 and variant == 'view':
            image = self._checked(work_id, self._remote.get_with_variant, page_no, variant)
            if image.variant == 'regular_view':
                notify_shadow(observer, work_id, page_no, image, route='recommendation_view_image')
            return image.blob
        return self._checked(work_id, self._remote.get, page_no, variant)

    def peek(self, work_id, page_no=0, variant='view'):
        return self._checked(work_id, self._remote.peek, page_no, variant)

    def observed_image(self, work_id, variant):
        return self._checked(work_id, self._remote.peek_with_variant, 0, variant)

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
            self.view_observer = None


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

    def __init__(self, services, works, *, view_observer=None):
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
        self._view_observer = view_observer
        self.remote_reader = None
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
                    self.remote_pin = self._acquire_remote(acquire, records, shelf=True)
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
        return self._local_current()

    def _local_current(self):
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

    def capture_display(self, groups, *, lineage=False):
        """Retain existing PNG bytes for this draw without touching LRU recency."""
        units = []
        for group in groups:
            slots = []
            for work in group.works:
                thumb, variant, regular = self._observed_images(work, lineage and group.kind == 'certain')
                slots.append(RecommendationDisplaySlot(work, work, thumb, thumb is not None, variant, regular))
            units.append(RecommendationDisplayUnit(group.kind, group.key, tuple(slots)))
        return tuple(units)

    def projection_source_current(self):
        if self.closed or getattr(self.services, 'closing', False):
            return False
        if self.remote_reader is not None and hasattr(self.services, 'live_client'):
            reader = self.services.live_client or getattr(self.services, '_vault_images', None)
            if self.remote_reader is not reader:
                return False
        try:
            return self._local_current()
        except (ValueError, RuntimeError, KeyError):
            return False

    @staticmethod
    def empty_display(groups):
        return tuple(RecommendationDisplayUnit(group.kind, group.key, tuple(
            RecommendationDisplaySlot(work, work) for work in group.works)) for group in groups)

    def _acquire_remote(self, acquire, records, *, shelf=False):
        pin = acquire(records)
        try:
            if isinstance(pin, PinnedLibraryImages):
                with pin.registry._lock:
                    provider = pin.registry._version(pin.token).remote
                    if isinstance(provider, RecommendationRemoteImages):
                        reader = getattr(provider._remote, 'client', None)
                        if shelf:
                            self.remote_reader = reader
                        # A viewer may acquire a pin after shelf acquisition
                        # failed. Only pass observations with a shelf lineage.
                        if reader is not None and reader is self.remote_reader:
                            provider.view_observer = self._view_observer
            return pin
        except BaseException:
            pin.close()
            raise

    def bind_view_observer(self, observer):
        self._view_observer = observer
        pin = self.remote_pin
        if isinstance(pin, PinnedLibraryImages):
            with pin.registry._lock:
                provider = pin.registry._version(pin.token).remote
                if isinstance(provider, RecommendationRemoteImages):
                    provider.view_observer = observer

    def _observed_images(self, work, lineage):
        if work.deleted or work.unavailable:
            return None, None, None
        try:
            self.ensure_source(work.work_id)
            local = work.work_id in self.saved
            pin = self.local_pin if local else self.remote_pin
            if isinstance(pin, PinnedLibraryImages):
                registry = pin.registry
                with registry._lock:
                    if pin.closed:
                        return None, None, None
                    version = registry._version(pin.token)
                    provider = registry._provider(version, work.work_id, 'thumb')
                # The media snapshot owns the pin. Leave the registry lock
                # before reading the provider, which may consult the registry.
                if local and provider is version.local:
                    thumb = _cached_local_image(provider, work.work_id)
                    return (thumb, 'saved_thumb', None) if _display_png(thumb) else (None, None, None)
                if isinstance(provider, RecommendationRemoteImages):
                    found = provider.observed_image(work.work_id, 'thumb')
                    if found is None or not _display_png(found.blob):
                        return None, None, None
                    regular = provider.observed_image(work.work_id, 'view') if lineage else None
                    if regular is None or regular.variant != 'regular_view':
                        regular = None
                    # The remote provider's PNG ETag is the SHA-256 of the
                    # delivered bytes, the same key used by the observer.
                    return found.blob, 'thumb', regular.blob.etag[1:-1] if regular is not None else None
        except (ValueError, RuntimeError, KeyError):
            pass
        return None, None, None

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
            pin = self._acquire_remote(acquire, (_record(chosen),))
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


def _cached_local_image(provider, work_id):
    from .composite_library import CompositeLocalCatalog

    if isinstance(provider, CompositeLocalCatalog):
        leaf = provider._select(work_id, 0, 'thumb')
        blob = _cached_local_image(leaf, work_id)
        provider._still_open()
        return blob
    lock = getattr(provider, '_lock', None)
    if lock is not None:
        with lock:
            if not getattr(provider, '_closed', True):
                key = (work_id, 0, 'thumb')
                lookup = getattr(getattr(provider, '_shared_cache', None), 'peek_memory', None)
                if callable(lookup):
                    return lookup(key)
                return getattr(provider, '_cache', {}).get(key)
    return None


def _display_png(blob):
    return (isinstance(blob, ImageBlob) and blob.media_type == 'image/png'
            and blob.data.startswith(b'\x89PNG\r\n\x1a\n'))


@dataclass(frozen=True)
class RecommendationDisplayGroup:
    kind: str
    key: str
    works: tuple[RecommendationWork, ...]


@dataclass(frozen=True)
class RecommendationDisplaySlot:
    base: RecommendationWork
    work: RecommendationWork
    image: ImageBlob | None = None
    image_available: bool = False
    image_variant: str | None = None
    regular_sha: str | None = None


@dataclass(frozen=True)
class RecommendationDisplayUnit:
    kind: str
    key: str
    slots: tuple[RecommendationDisplaySlot, ...]


def recommendation_visual_score(base, model, embedding, author_id):
    """Boundary for the rank adapter. A usable work embedding takes priority over D."""
    from .visual_preference import DEFAULT_ALPHA, blend

    signal = model.image_signal(embedding)
    if signal is None:
        signal = model.author_signal(author_id)
    return blend(base, signal, alpha=DEFAULT_ALPHA)


class RecommendationVisual:
    """Optional services binding. Settings and observer lifetime belong to the owner."""

    def __init__(self, observer, *, enabled=lambda: False):
        self.observer = observer
        self._enabled = enabled

    def enabled(self):
        return self._enabled() is True

    def _running(self):
        return self.enabled() and self.observer._check_running(raise_on_release=False)

    def is_current(self, generation):
        from .visual_preference_worker import _PREPROCESSING_VERSION

        return (self._running() and generation.model_sha == self.observer._model_sha
                and generation.preprocessing_version == _PREPROCESSING_VERSION
                and self.observer.store.is_current(generation))

    def prepare(self, captured):
        """Read precomputed data off the UI loop. No GPU or image acquisition here."""
        from .visual_preference import DEFAULT_ALPHA, Embedding, EmbeddingKey
        from .visual_preference_worker import _PREPROCESSING_VERSION

        eligible = [unit for unit in captured if unit.kind == 'certain' and len(unit.slots) > 1
                    and all(slot.image is not None for slot in unit.slots)]
        if not eligible or not self._running():
            return None
        store = self.observer.store
        generation = store.current_generation()
        if (generation is None or generation.model_sha != self.observer._model_sha
                or generation.preprocessing_version != _PREPROCESSING_VERSION):
            return None
        model = store.load_model(generation)
        if model is None or model.generation != generation:
            return None
        scores = {}
        for unit in eligible:
            # The existing shelf has only ordinal positions. Keep a stable
            # rank prior within a quarter of the permitted visual weight.
            span = len(unit.slots) - 1
            for index, slot in enumerate(unit.slots):
                work = slot.work
                embedding = None
                if slot.regular_sha is not None:
                    key = EmbeddingKey(work.work_id, 0, 'view', slot.regular_sha,
                                       generation.model_sha, generation.preprocessing_version)
                    vector = store.get_embedding(generation, key)
                    if vector is not None:
                        embedding = Embedding(key, vector)
                base = 0.5 + DEFAULT_ALPHA * (span - index) / (4 * span)
                scores[unit.key, work.work_id] = recommendation_visual_score(
                    base, model, embedding, work.author_id)
        return (generation, scores) if self.is_current(generation) else None

    def project(self, captured, prepared, *, previous=(), protected=()):
        scores = prepared[1] if prepared is not None and self.is_current(prepared[0]) else {}
        prior = {(unit.kind, unit.key): unit.slots for unit in previous} if protected else {}
        result = []
        for unit in captured:
            if unit.kind != 'certain':
                result.append(unit)
                continue
            slots = tuple(replace(slot, regular_sha=None) for slot in unit.slots)
            by_id = {slot.work.work_id: slot for slot in slots}
            anchors = {}
            for index, old in enumerate(prior.get((unit.kind, unit.key), ())[:len(slots)]):
                work_id = old.work.work_id
                current = by_id.get(work_id)
                if current is None or current.work != old.work:
                    continue
                if current.image is None and old.image is not None:
                    current = replace(current, image=old.image, image_available=True, image_variant=old.image_variant)
                    by_id[work_id] = current
                if work_id in protected:
                    anchors[index] = by_id.pop(work_id)
            movable = tuple(by_id.values())
            if (all(slot.image is not None for slot in slots)
                    and all((unit.key, slot.work.work_id) in scores for slot in movable)):
                movable = sorted(movable, key=lambda slot: -scores[unit.key, slot.work.work_id])
            movable = iter(movable)
            projected = tuple(replace(anchors[i] if i in anchors else next(movable), base=base.base)
                              for i, base in enumerate(slots))
            # A protected slot can only displace cards whose images are held.
            result.append(replace(unit, slots=projected
                          if all(slot.base == slot.work or slot.image is not None for slot in projected) else slots))
        return tuple(result)

    def record(self, slate):
        rows = []
        for unit in slate:
            ranks = {slot.base.work_id: rank for rank, slot in enumerate(unit.slots, 1)}
            rows.extend(dict(work_id=slot.work.work_id, section=unit.kind, group=unit.key,
                             base_rank=ranks[slot.work.work_id], rank=rank,
                             image_available=slot.image_available, image_variant=slot.image_variant)
                        for rank, slot in enumerate(unit.slots, 1))
        # Reuse the existing observer queue and its purge fence. A profile
        # without this optional service continues along the baseline view.
        self.observer._record_event('recommendation_exposure', lambda _: {'candidates': rows})
