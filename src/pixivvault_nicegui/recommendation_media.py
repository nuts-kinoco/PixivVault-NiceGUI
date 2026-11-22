"""Recommendation previews borrow the existing verified image providers.

Creating a shelf snapshot performs no HTTP request and does not open a source
database. Only its visible records are registered. Local saved identities,
including managed tombstones, always take precedence over remote previews.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from hashlib import sha256
import re
from threading import Lock
from time import monotonic

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

    def capture_display(self, groups, *, lineage=True, view_lookup=None):
        """Retain existing PNG bytes for this draw without touching LRU recency."""
        units = []
        for group in groups:
            slots = []
            for work in group.works:
                thumb, variant, proof = self._observed_images(work, lineage, view_lookup)
                sha, analysis_variant, page = proof if proof is not None else (None, 'view', 0)
                slots.append(RecommendationDisplaySlot(work, work, thumb, thumb is not None,
                                                       variant, sha, analysis_variant, page))
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

    def check_saved_display_access(self, slate, captured):
        """Discard a whole permutation if any of its saved media lost access."""
        denied = set()
        checked = set()
        for unit in slate:
            for slot in unit.slots:
                work_id = slot.work.work_id
                if (work_id not in self.saved or work_id in checked
                        or (slot.image is None and slot.regular_sha is None)):
                    continue
                checked.add(work_id)
                if not self.saved_display_current(work_id):
                    denied.add(work_id)
        if not denied:
            return slate

        def clear(unit):
            return replace(unit, slots=tuple(
                replace(slot, image=None, image_available=False, image_variant=None, regular_sha=None,
                        signal=None, signal_generation=None) if slot.work.work_id in denied else slot
                for slot in unit.slots))

        bases = tuple(clear(unit) for unit in captured)
        return tuple(base if any(slot.work.work_id in denied for slot in unit.slots) else unit
                     for unit, base in zip(slate, bases))

    def saved_display_current(self, work_id):
        if work_id not in self.saved:
            return True
        try:
            self.ensure_source(work_id)
            return True
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
                if pin.closed:
                    return
                provider = pin.registry._version(pin.token).remote
                if isinstance(provider, RecommendationRemoteImages):
                    provider.view_observer = observer

    def _observed_images(self, work, lineage, view_lookup):
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
                    thumb, proof = _cached_local_images(provider, work.work_id, lineage)
                    return (thumb, 'saved_thumb', proof) if _display_png(thumb) else (None, None, proof)
                if isinstance(provider, RecommendationRemoteImages):
                    found = provider.observed_image(work.work_id, 'thumb')
                    regular = provider.observed_image(work.work_id, 'view') if lineage else None
                    digest = (_etag_digest(regular.blob) if regular is not None
                              and regular.variant == 'regular_view' else None)
                    if digest is None and lineage and view_lookup is not None:
                        digest = view_lookup(self, work)
                    proof = (digest, 'view', 0) if digest is not None else None
                    return (found.blob, 'thumb', proof) if found is not None and _display_png(found.blob) else (None, None, proof)
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


_SHA256 = re.compile(r'[0-9a-f]{64}')


def _etag_digest(blob):
    # Verified remote and saved-original providers use a quoted content hash.
    tag = blob.etag
    if isinstance(tag, str) and len(tag) == 66 and tag[0] == tag[-1] == '"' and _SHA256.fullmatch(tag[1:-1]):
        return tag[1:-1]
    return None


def _cached_local_images(provider, work_id, lineage):
    from .composite_library import CompositeLocalCatalog
    from .managed_download_catalog import ManagedDownloadCatalog

    if isinstance(provider, CompositeLocalCatalog):
        leaf = provider._select(work_id, 0, 'thumb')
        result = _cached_local_images(leaf, work_id, lineage)
        provider._still_open()
        return result
    lock = getattr(provider, '_lock', None)
    if lock is not None:
        with lock:
            if not getattr(provider, '_closed', True):
                proof = None
                if isinstance(provider, ManagedDownloadCatalog):
                    try:
                        _, receipt, _ = provider._check(work_id, 0, 'thumb')
                    except (ValueError, RuntimeError):
                        return None, None
                    if lineage:
                        page = provider.source_pages[work_id][0]
                        proof = (receipt['files'][page]['sha256'], 'saved_original', page)
                lookup = getattr(getattr(provider, '_shared_cache', None), 'peek_memory', None)
                if not callable(lookup):
                    lookup = getattr(provider, '_cache', {}).get
                if lineage and proof is None:
                    original = lookup((work_id, 0, 'original'))
                    if original is not None and (digest := _etag_digest(original)) is not None:
                        proof = (digest, 'saved_original', 0)
                return lookup((work_id, 0, 'thumb')), proof
    return None, None


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
    analysis_variant: str = 'view'
    analysis_page: int = 0
    signal: str | None = None
    signal_generation: int | None = None


@dataclass(frozen=True)
class RecommendationDisplayUnit:
    kind: str
    key: str
    slots: tuple[RecommendationDisplaySlot, ...]


def recommendation_visual_score(base, model, embedding, author_id):
    """Boundary for the rank adapter. A usable work embedding takes priority over D."""
    from .visual_preference import DEFAULT_ALPHA, blend

    signal = model.image_signal(embedding)
    if signal is not None:
        return blend(base, signal, alpha=DEFAULT_ALPHA), 'A'
    signal = model.author_signal(author_id)
    return blend(base, signal, alpha=DEFAULT_ALPHA), 'D' if signal is not None else None


class RecommendationVisual:
    """Optional services binding. Settings and observer lifetime belong to the owner."""

    def __init__(self, observer, *, enabled=lambda: False):
        self.observer = observer
        self._enabled = enabled
        self._view_lock = Lock()
        self._view_hashes = OrderedDict()

    def observe_view(self, media, work_id, page, image):
        if page == 0 and image.variant == 'regular_view' and media.remote_reader is not None:
            work = media.works.get(work_id)
            if work is not None:
                key = (id(media.remote_reader), media.local_token, work)
                digest = _etag_digest(image.blob) or sha256(image.data).hexdigest()
                with self._view_lock:
                    self._view_hashes[key] = (media.remote_reader, digest)
                    self._view_hashes.move_to_end(key)
                    if len(self._view_hashes) > 1024:
                        self._view_hashes.popitem(last=False)
        return self.observer.submit_image(work_id, page, image)

    def observed_view_sha(self, media, work):
        reader = media.remote_reader
        if reader is None:
            return None
        with self._view_lock:
            observed = self._view_hashes.get((id(reader), media.local_token, work))
        return observed[1] if observed is not None and observed[0] is reader else None

    def enabled(self):
        return self._enabled() is True

    def _running(self):
        return self.enabled() and self.observer._check_running(raise_on_release=False)

    def is_current(self, generation):
        """Publication fence. Full model and SQLite validation happens in prepare()."""
        from .visual_preference_store import VisualPreferenceStoreError

        condition = self.observer._condition
        # submit_image may be inspecting storage while holding this condition.
        if not condition.acquire(blocking=False):
            return False
        try:
            if not self._running() or not self._matches_model(generation):
                return False
        finally:
            condition.release()
        try:
            return self.observer.store._fence_allows(generation, deadline=monotonic())
        except (OSError, ValueError, VisualPreferenceStoreError):
            return False

    def _matches_model(self, generation):
        from .visual_preference_worker import _PREPROCESSING_VERSION

        return (generation.model_sha == self.observer._model_sha
                and generation.preprocessing_version == _PREPROCESSING_VERSION)

    def prepare(self, captured):
        """Read precomputed data off the UI loop. No GPU or image acquisition here."""
        from .visual_preference import DEFAULT_ALPHA, Embedding, EmbeddingKey

        eligible = [unit for unit in captured if unit.kind == 'certain' and len(unit.slots) > 1
                    and all(slot.image is not None for slot in unit.slots)]
        if not eligible or not self._running():
            return None
        store = self.observer.store
        generation = store.current_generation()
        if generation is None or not self._matches_model(generation):
            return None
        model = store.load_model(generation)
        if model is None or model.generation != generation:
            return None
        scores = {}
        signals = {}
        for unit in eligible:
            for index, slot in enumerate(unit.slots):
                work = slot.work
                embedding = None
                if slot.regular_sha is not None and _SHA256.fullmatch(slot.regular_sha):
                    key = EmbeddingKey(work.work_id, slot.analysis_page, slot.analysis_variant, slot.regular_sha,
                                       generation.model_sha, generation.preprocessing_version)
                    vector = store.get_embedding(generation, key)
                    if vector is not None:
                        embedding = Embedding(key, vector)
                # Keep the initial six-slot spacing when the shelf expands.
                base = max(0.0, 0.5 + DEFAULT_ALPHA * (5 - index) / 20)
                key = (unit.key, work.work_id)
                scores[key], signals[key] = recommendation_visual_score(base, model, embedding, work.author_id)
        return (generation, scores, signals) if store.is_current(generation) and self.is_current(generation) else None

    def project(self, captured, prepared, *, previous=(), protected=()):
        if prepared is not None and self.is_current(prepared[0]):
            generation, scores, signals = prepared
        else:
            generation, scores, signals = None, {}, {}
        prior = {(unit.kind, unit.key): unit.slots for unit in previous} if protected else {}
        result = []
        for unit in captured:
            if unit.kind != 'certain':
                result.append(unit)
                continue
            slots = unit.slots
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
                    by_id.pop(work_id)
                    if (generation is not None and old.signal_generation == generation.number
                            and old.base == slots[index].base):
                        current = replace(current, signal=old.signal, signal_generation=old.signal_generation)
                    anchors[index] = current
            movable = tuple(by_id.values())
            if (all(slot.image is not None for slot in slots)
                    and all((unit.key, slot.work.work_id) in scores for slot in movable)):
                movable = sorted(movable, key=lambda slot: -scores[unit.key, slot.work.work_id])
                movable = [replace(slot, signal=signal,
                                   signal_generation=generation.number if signal is not None else None)
                           for slot in movable
                           for signal in (signals[unit.key, slot.work.work_id],)]
            movable = iter(movable)
            projected = tuple(replace(anchors[i] if i in anchors else next(movable), base=base.base)
                              for i, base in enumerate(slots))
            # A protected slot can only displace cards whose images are held.
            result.append(replace(unit, slots=projected
                          if all(slot.base == slot.work or slot.image is not None for slot in projected) else slots))
        return tuple(result)

    def exposure(self, slate):
        rows = []
        for unit in slate:
            ranks = {slot.base.work_id: rank for rank, slot in enumerate(unit.slots, 1)}
            rows.extend(dict(work_id=slot.work.work_id, section=unit.kind, group=unit.key,
                             base_rank=ranks[slot.work.work_id], rank=rank,
                             image_available=slot.image_available, image_variant=slot.image_variant,
                             view_available=slot.regular_sha is not None,
                             analysis_variant=slot.analysis_variant if slot.regular_sha is not None else None,
                             signal=slot.signal, generation=slot.signal_generation)
                        for rank, slot in enumerate(unit.slots, 1))
        return {'candidates': rows}

    def exposure_epoch(self):
        # Capture the purge fence with no retry on the UI loop. The durable
        # writer rechecks it under the store transaction.
        store = self.observer.store
        store._check_paths()
        return store._read_epoch(store._event_purge, deadline=monotonic())

    def write_exposure(self, payload, epoch):
        store = self.observer.store
        if store.capture_event_epoch() == epoch:
            store.append_event('recommendation_exposure', payload, epoch)

    def record(self, slate):
        self.write_exposure(self.exposure(slate), self.exposure_epoch())
