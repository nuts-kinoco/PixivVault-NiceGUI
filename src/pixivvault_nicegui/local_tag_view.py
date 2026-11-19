"""Ephemeral projection of locally inferred tags; never owns a database."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from threading import RLock
from types import MappingProxyType

from .legacy.database import normalize_tag


@dataclass(frozen=True)
class LocalTagSnapshot:
    epoch: int
    fingerprint: str | None
    by_work: object
    seed_counts: object


class LocalTagView:
    def __init__(self, tagger, local_image, saved_works, saved_ids):
        self.tagger, self.local_image = tagger, local_image
        self.saved_works, self.saved_ids = saved_works, saved_ids
        self._lock = RLock()
        self._entries = {}
        self._projection = None
        self._epoch = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pv-local-tag-view')
        self._validation_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='pv-local-tag-check')
        self._future = None
        self._rescan = False

    def epoch(self):
        with self._lock:
            return self._epoch

    def invalidate(self):
        with self._lock:
            self._epoch += 1
            self._entries = {}
            self._projection = None
            if self._future is not None and not self._future.done():
                self._rescan = True
            return self._epoch

    def revision(self):
        try:
            snapshot = self.checked_snapshot()
            return snapshot.epoch, snapshot.fingerprint
        except RuntimeError:
            return self.epoch(), None

    def snapshot_async(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('local seed unavailable')
            return self._validation_executor.submit(self.snapshot)

    def checked_snapshot(self):
        return self.snapshot_async().result()

    def _eligible(self):
        saved = {str(value) for value in self.saved_ids()}
        return {str(a.work_id) for w in self.saved_works()
                for a in (getattr(w, 'artwork', w),) if not a.tags and str(a.work_id) in saved}

    def refresh_async(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('local seed unavailable')
            if self._future is None or self._future.done():
                self._future = self._executor.submit(self._refresh_loop)
            else:
                self._rescan = True
            return self._future

    def _refresh_loop(self):
        while True:
            with self._lock:
                if self._closed:
                    return
                self._rescan = False
                start_epoch = self._epoch
                old = dict(self._entries)
            fingerprint = self.tagger.current_fingerprint()
            updated = {}
            if fingerprint is not None:
                for wid in self._eligible():
                    with self._lock:
                        if self._closed:
                            return
                        if self._epoch != start_epoch:
                            break
                    try:
                        data = self.local_image(wid)
                        if not data:
                            continue
                        image_hash = sha256(data).hexdigest()
                        entry = old.get(wid)
                        if entry is None or entry[:2] != (fingerprint, image_hash):
                            predicted = self.tagger.predict_with_fingerprint(data)
                            if predicted is None or predicted[0] != fingerprint:
                                continue
                            current = self.local_image(wid)
                            if not current or sha256(current).hexdigest() != image_hash:
                                continue
                            entry = (fingerprint, image_hash, tuple(name for name, _ in predicted[1]))
                        updated[wid] = entry
                    except (OSError, ValueError, RuntimeError):
                        continue
            with self._lock:
                if self._closed:
                    return
                if self._epoch != start_epoch:
                    if self._rescan:
                        continue
                    return
                if updated != self._entries:
                    self._entries = updated
                    self._epoch += 1
                if not self._rescan:
                    return

    def snapshot(self):
        with self._lock:
            epoch, entries = self._epoch, dict(self._entries)
            closed = self._closed
        fingerprint = None if closed else self.tagger.try_current_fingerprint()
        if fingerprint is None and not closed and getattr(self.tagger, 'status', None) == '検証中':
            try:
                self.refresh_async()
            except RuntimeError:
                pass
        works, counts = {}, {}
        if fingerprint is not None:
            try:
                for wid in self._eligible():
                    entry = entries.get(wid)
                    if entry is None or entry[0] != fingerprint:
                        continue
                    try:
                        data = self.local_image(wid)
                    except (OSError, ValueError, RuntimeError):
                        continue
                    if not data or sha256(data).hexdigest() != entry[1]:
                        continue
                    works[wid] = entry[2]
                    unique = {}
                    for name in entry[2]:
                        normalized = normalize_tag(name)
                        if normalized:
                            unique.setdefault(normalized, name)
                    for tag, name in unique.items():
                        display, count = counts.get(tag, (name, 0))
                        counts[tag] = display, count + 1
            except (OSError, ValueError, RuntimeError):
                works, counts = {}, {}
        if self.epoch() != epoch or self.tagger.try_current_fingerprint() != fingerprint:
            fingerprint, works, counts = None, {}, {}
        else:
            projection = (fingerprint, tuple(sorted(works.items())))
            with self._lock:
                if self._epoch != epoch:
                    fingerprint, works, counts = None, {}, {}
                elif projection != self._projection:
                    self._projection = projection
                    self._epoch += 1
                    epoch = self._epoch
        return LocalTagSnapshot(epoch, fingerprint, MappingProxyType(works), MappingProxyType(counts))

    def current_tags(self, work_id):
        return self.snapshot().by_work.get(str(work_id), ())

    def merge_preferences(self, base, snapshot):
        result = deepcopy(base)
        if snapshot is None or snapshot.epoch != self.epoch() or snapshot.fingerprint is None:
            return result
        if self.tagger.try_current_fingerprint() != snapshot.fingerprint:
            return result
        for tag, (display, amount) in snapshot.seed_counts.items():
            counts = result.setdefault(tag, {'display_tag': display})
            counts['seed_saved'] = counts.get('seed_saved', 0) + amount
        return result

    def close(self):
        with self._lock:
            self._closed = True
            self._epoch += 1
            self._entries = {}
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._validation_executor.shutdown(wait=True, cancel_futures=False)
