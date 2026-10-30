"""Read-only legacy folder/ZIP inputs for the separate Egara ledger."""
from hashlib import sha256
import json
import sqlite3
from contextlib import closing, contextmanager
from threading import RLock, local
from zipfile import BadZipFile

from .egara_inference import EGARA_BB_SHA256
from .local_media_adapter import ReadonlyLocalMediaCatalog, MediaMissing, MediaUnavailable, MediaAmbiguous
from .managed_similarity import ManagedSimilarityError
from .managed_similarity import _validate_embedding
from .egara_inference import MODEL, STYLE, COMPOSITION
from .fs_paths import sqlite_readonly_uri
from .frozen_source_pin import FrozenSourcePin, FrozenSourceError


class LegacySearchTokens:
    """One explicit query's media identity cache, never reused by another."""
    def __init__(self, legacy, *, cancelled=None):
        self.legacy = legacy
        self.cancelled = cancelled
        self._tokens = {}

    def checkpoint(self):
        if self.legacy._closed or (self.cancelled is not None and self.cancelled()):
            raise ManagedSimilarityError('旧解析の検索は取消されました。')

    def token(self, work_id, page):
        self.checkpoint()
        key = str(work_id), page
        if key not in self._tokens:
            try:
                with self.legacy.catalog.analysis_checkpoint(self.checkpoint):
                    self._tokens[key] = self.legacy._token(*key)
            except (BadZipFile, KeyError, MediaMissing, MediaUnavailable, MediaAmbiguous, OSError):
                self._tokens[key] = None
            self.checkpoint()
        value = self._tokens[key]
        if value is None:
            raise MediaUnavailable('legacy search media unavailable')
        return value

    def matches(self, work_id, page, receipt_sha):
        self.checkpoint()
        try:
            return self.token(work_id, page) == receipt_sha
        except (MediaMissing, MediaUnavailable, MediaAmbiguous, OSError):
            return False


class LegacySimilarityInputs:
    def __init__(self, image_root, snapshot, source_guard):
        self.catalog = ReadonlyLocalMediaCatalog(image_root, snapshot)
        # An inference source must be re-read, not returned from an old UI cache.
        self.catalog._cache_limit = 0
        self.source = source_guard
        self._closed = False
        self._lock = RLock()
        self._completed_keys = None
        self._scan_state = local()
        self._snapshot = snapshot

    def check(self, *, full=None, cancelled=None):
        """Use a mandatory Windows deny-write pin, otherwise retain full SHA."""
        with self.source._lock:
            if self._closed or self.source._closed:
                raise ManagedSimilarityError('旧解析元が変更されています。')
            pin = getattr(self._scan_state, 'pin', None)
            if pin is not None:
                try:
                    pin.check()
                except FrozenSourceError:
                    raise ManagedSimilarityError('旧解析元が変更または取消されています。') from None
            else:
                fingerprint = (self.source._fingerprint() if cancelled is None else
                               self.source._fingerprint(cancelled=cancelled))
                if fingerprint != self.source._signature:
                    raise ManagedSimilarityError('旧解析元が変更されています。')

    def search_tokens(self, *, cancelled=None):
        return LegacySearchTokens(self, cancelled=cancelled)

    def refresh_media_index(self):
        """Retry an unavailable private index without altering the input files."""
        if self._closed:
            raise ManagedSimilarityError('旧画像解析は終了しています。')
        self.catalog.replace_snapshot(self._snapshot)

    @contextmanager
    def verified_scan(self, *, cancelled=None):
        """Pin a verified frozen copy for the finite scan; never stat-only.

        Windows denies normal data writes/rename while the read handle lives.
        One cancellable SHA verifies admission, and closing does not rehash.
        On platforms without a mandatory pin, check() keeps full-SHA behavior.
        """
        previous = getattr(self._scan_state, 'pin', None)
        if previous is not None:
            self.check()
            yield
            return
        try:
            with FrozenSourcePin(self.source.path, self.source._signature,
                                 cancelled=cancelled) as pin:
                self._scan_state.pin = pin
                self._scan_state.cancelled = cancelled
                try:
                    self.check()
                    def checkpoint():
                        if self._closed or (cancelled is not None and cancelled()):
                            raise ManagedSimilarityError('旧解析の走査は取消されました。')
                    with self.catalog.analysis_checkpoint(checkpoint):
                        yield
                finally:
                    self._scan_state.pin = None
                    self._scan_state.cancelled = None
        except FrozenSourceError:
            raise ManagedSimilarityError('旧解析元が変更または取消されています。') from None

    def pages(self):
        self.check()
        return tuple((work.work_id, page + 1)
                     for work in self.catalog.works.values()
                     if work.work_type not in ('novel', 'ugoira') and work.page_count_known
                     for page in range(work.page_count))

    def _legacy_completed(self):
        if self._completed_keys is None:
            valid = {}
            with closing(sqlite3.connect(sqlite_readonly_uri(self.source.path, immutable=True), uri=True)) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(work_embeddings)')}
                if {'work_id','page','kind','model','dim','vec'} <= columns:
                    for wid, page, kind, dim, data in db.execute(
                            'SELECT work_id,page,kind,dim,vec FROM work_embeddings WHERE model=?', (MODEL,)):
                        cancelled = getattr(self._scan_state, 'cancelled', None)
                        if cancelled is not None and cancelled():
                            raise ManagedSimilarityError('旧解析の走査は取消されました。')
                        if kind not in (STYLE,COMPOSITION) or dim != 1024 or type(page) is not int or page < 1:
                            continue
                        try:
                            _validate_embedding(data)
                        except ManagedSimilarityError:
                            continue
                        valid.setdefault((str(wid),page), set()).add(kind)
            self.check()
            self._completed_keys = {key for key,kinds in valid.items() if kinds == {STYLE,COMPOSITION}}
        return self._completed_keys

    def _token(self, work_id, page):
        value = (self.source._signature[-1].hex(), str(work_id), page,
                 self.catalog.analysis_source_identity(str(work_id), page - 1))
        return sha256(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()

    def matches(self, work_id, page, receipt_sha):
        if self._closed:
            raise ManagedSimilarityError('旧画像解析は終了しています。')
        try:
            return self._token(work_id, page) == receipt_sha
        except (MediaMissing, MediaUnavailable, MediaAmbiguous, OSError, ValueError, BadZipFile, KeyError):
            return False

    def analyze(self, index, inference, work_id, page, *, is_current=None):
        if type(page) is not int or not 1 <= page <= 1000:
            raise ManagedSimilarityError('旧画像のページが不正です。')
        if inference.model_sha256 != EGARA_BB_SHA256:
            raise ManagedSimilarityError('互換モデルではありません。')
        self.check()
        completed=(str(work_id),page) in self._legacy_completed()
        owned=index.has_owned_key(str(work_id),page)
        if (completed and owned and
                index.legacy_cpu_adoption_pending(self, str(work_id), page)):
            index.reconcile_legacy_cpu(self, only=(str(work_id), page))
            if index.legacy_cpu_adoption_pending(self, str(work_id), page):
                # A transiently unavailable original must not permanently
                # supersede its complete frozen CPU pair with needless GPU
                # inference. Retry this page after the source is available.
                raise MediaUnavailable('legacy CPU source preparation unavailable')
        if (completed and (not owned or index.accept_legacy_cpu(self, work_id, page))):
            # Preserve validated original CPU vectors; no unnecessary inference
            # or duplicate namespace just because CUDA is now available.
            return False
        token = self._token(work_id, page)
        if index.current_legacy_page(self, str(work_id), page):
            return False
        data = self.catalog.get(str(work_id), page - 1, 'original').data
        image_sha = sha256(data).hexdigest()
        if self._token(work_id, page) != token:
            raise ManagedSimilarityError('解析元が変更されています。')
        if index.source_status(str(work_id), page, image_sha, token):
            return False
        if is_current is not None and not is_current():
            raise ManagedSimilarityError('解析操作は取消されました。')
        vectors = inference.infer(data)
        self.check()
        if self._token(work_id, page) != token or (is_current is not None and not is_current()):
            raise ManagedSimilarityError('解析元または操作が変わりました。')
        return index.commit(str(work_id), page, image_sha, token, vectors,
                            legacy_db_sha256=self.source._signature[-1].hex())

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self.catalog.close()
