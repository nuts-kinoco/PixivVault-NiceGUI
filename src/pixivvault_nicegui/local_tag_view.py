"""Ephemeral projection of locally inferred tags; never owns a database.

Settings and detected model failures invalidate the published epoch synchronously.
External model changes are detected at verification events and on the periodic scan.
Image availability, saved state and API tags reflect the last background validation
(default interval: 10 seconds). Readers use that immutable snapshot without file I/O.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from threading import Event, RLock, Thread
from types import MappingProxyType

from .legacy.database import normalize_tag


@dataclass(frozen=True)
class LocalTagSnapshot:
    epoch: int
    fingerprint: str | None
    by_work: object
    seed_counts: object


REFRESH_INTERVAL_SECONDS = 10
_EMPTY = MappingProxyType({})


class LocalTagView:
    def __init__(self, tagger, local_image, saved_works, saved_ids, *, image_token=None):
        self.tagger, self.local_image = tagger, local_image
        self.saved_works, self.saved_ids = saved_works, saved_ids
        self.image_token = image_token
        self._lock = RLock()
        self._entries = {}
        self._retired = []
        self._epoch = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pv-local-tag-view')
        self._future = None
        self._running = False
        self._rescan = False
        self._snapshot = LocalTagSnapshot(0, None, _EMPTY, _EMPTY)
        subscribe = getattr(tagger, 'subscribe_invalidation', None)
        self._unsubscribe = subscribe(self.invalidate) if subscribe is not None else None
        self._stop = Event()
        self._timer = Thread(target=self._periodic, name='pv-local-tag-timer', daemon=True)
        self._timer.start()

    def _periodic(self):
        while not self._stop.wait(REFRESH_INTERVAL_SECONDS):
            try:
                with self._lock:
                    running = self._running
                if running:
                    self._revalidate_published()
                else:
                    self.refresh_async()
            except (OSError, ValueError, RuntimeError):
                if self._stop.is_set():
                    return

    def _revalidate_published(self):
        # A slow image read or inference cannot postpone expiry of published
        # entries. This timer pass checks metadata and never runs inference.
        check_model = getattr(self.tagger, 'try_current_fingerprint', None)
        if check_model is not None:
            check_model()
        if self.image_token is None:
            return
        with self._lock:
            previous, old = self._snapshot, self._entries
        if not old:
            return
        eligible = self._eligible()
        retained = {}
        for wid, entry in old.items():
            if wid not in eligible:
                continue
            try:
                if self.image_token(wid) == entry[1]:
                    retained[wid] = entry
            except (OSError, ValueError, RuntimeError):
                continue
        if retained == old:
            return
        works, counts = self._project(retained)
        with self._lock:
            if self._closed or old is not self._entries:
                return
            self._publish(retained, previous.fingerprint, works, counts, True)
            self._rescan = self._running

    def refresh_now(self):
        """Wait for the same background validation used by production scans."""
        self.refresh_async().result()

    def epoch(self):
        with self._lock:
            return self._epoch

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            if self._entries:
                # Drop references to the old projection on the worker, not here.
                self._retired.append((self._entries, self._snapshot))
            self._entries = {}
            self._snapshot = LocalTagSnapshot(self._epoch, None, _EMPTY, _EMPTY)
            self._rescan = self._running
            return self._epoch

    def revision(self):
        snapshot = self.snapshot()
        return snapshot.epoch, snapshot.fingerprint

    def _eligible(self):
        saved = {str(value) for value in self.saved_ids()}
        return {str(a.work_id) for w in self.saved_works()
                for a in (getattr(w, 'artwork', w),) if not a.tags and str(a.work_id) in saved}

    def refresh_async(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('local seed unavailable')
            if self._running and not self._future.cancelled():
                self._rescan = True
            else:
                self._running = True
                self._future = self._executor.submit(self._refresh_loop)
            return self._future

    def _refresh_loop(self):
        try:
            while True:
                with self._lock:
                    if self._closed:
                        self._running = False
                        return
                    self._rescan = False
                    previous, old = self._snapshot, self._entries
                    retired, self._retired = self._retired, []
                    epoch = previous.epoch
                retired.clear()
                fingerprint = self.tagger.current_fingerprint()
                updated = {}
                if fingerprint is not None:
                    for wid in sorted(self._eligible()):
                        if self.epoch() != epoch:
                            break
                        try:
                            entry = self._refresh_entry(wid, fingerprint, old.get(wid))
                        except (OSError, ValueError, RuntimeError):
                            continue
                        if entry is not None:
                            updated[wid] = entry
                changed = fingerprint != previous.fingerprint or updated != old
                if changed:
                    works, counts = self._project(updated)
                    visible_changed = fingerprint != previous.fingerprint or works != previous.by_work
                with self._lock:
                    if changed and not self._closed and old is self._entries:
                        self._publish(updated, fingerprint, works, counts, visible_changed)
                    if not self._rescan or self._closed:
                        self._running = False
                        return
        except BaseException:
            with self._lock:
                self._running = False
            raise

    def _publish(self, entries, fingerprint, works, counts, visible_changed):
        self._entries = entries
        if visible_changed:
            self._epoch += 1
            self._snapshot = LocalTagSnapshot(self._epoch, fingerprint,
                MappingProxyType(works), MappingProxyType(counts))

    def _refresh_entry(self, wid, fingerprint, old):
        if self.image_token is None:
            # Byte-only providers (such as isolated callers) have no metadata
            # API. The application's library registry always supplies a token.
            data = self.local_image(wid)
            token = sha256(data).digest() if data else None
        else:
            token = self.image_token(wid)
            data = None
        if token is None:
            return None
        if old is not None and old[:2] == (fingerprint, token):
            return old
        if self.image_token is not None:
            data = self.local_image(wid)
        if not data:
            return None
        predicted = self.tagger.predict_with_fingerprint(data)
        if predicted is None or predicted[0] != fingerprint:
            return None
        if self.image_token is None:
            current = self.local_image(wid)
            current_token = sha256(current).digest() if current else None
        else:
            current_token = self.image_token(wid)
        if current_token == token:
            return fingerprint, token, tuple(name for name, _ in predicted[1])
        return None

    @staticmethod
    def _project(entries):
        works, counts = {}, {}
        for wid, (_, _, names) in entries.items():
            works[wid] = names
            unique = {}
            for name in names:
                normalized = normalize_tag(name)
                if normalized:
                    unique.setdefault(normalized, name)
            for tag, name in unique.items():
                display, count = counts.get(tag, (name, 0))
                counts[tag] = display, count + 1
        return works, counts

    def snapshot(self):
        with self._lock:
            return self._snapshot

    def seed_counts(self):
        return self.snapshot().seed_counts

    def current_tags(self, work_id):
        return self.snapshot().by_work.get(str(work_id), ())

    def merge_preferences(self, base, snapshot):
        result = deepcopy(base)
        if snapshot is None or snapshot.epoch != self.epoch() or snapshot.fingerprint is None:
            return result
        for tag, (display, amount) in snapshot.seed_counts.items():
            counts = result.setdefault(tag, {'display_tag': display})
            counts['seed_saved'] = counts.get('seed_saved', 0) + amount
        return result if snapshot.epoch == self.epoch() else deepcopy(base)

    def close(self):
        self._stop.set()
        self._timer.join()
        if self._unsubscribe is not None:
            self._unsubscribe()
        with self._lock:
            if not self._closed:
                self._closed = True
                self.invalidate()
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._retired.clear()
