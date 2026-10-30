"""Owned Egara embedding ledger and legacy-compatible combined similarity source.

Only verified saved image bytes are accepted as new input. Historical source
SQLite stays immutable and is never opened as a writer. The 1024-float vectors
share the original EgaraNet style/composition namespace; all pages are indexed
with the original 1-based page identity, not the GUI's zero-based index.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import math
from pathlib import Path
import re
import sqlite3
import struct
from threading import Condition, Event, Lock, RLock
from time import monotonic
from typing import NamedTuple

from .egara_inference import EGARA_BB_SHA256, MODEL, STYLE, COMPOSITION
from .fs_paths import native_io_path, sqlite_readonly_uri
from .managed_downloads import _safe_path, _write, _read_bounded
from .similar_saved import (SavedSimilaritySource, SimilaritySourceError,
                            decode_embeddings_pages, search_similar,
                            STATUS_NO_QUERY_EMBEDDING, normalize_key)

MARKER = '.pixivvault-egara-index-v1'
IDENTITY = b'PixivVault NiceGUI EgaraNet managed embeddings v1\n'
DATABASE = 'embeddings.sqlite3'
APPLICATION_ID = 1347831108
_SHA = re.compile('[a-f0-9]{64}\\Z')
_ID = re.compile('[1-9][0-9]{0,19}\\Z')
# close() publishes cancellation, then waits at most this long. A longer
# noncancellable read (SHA/index/ZIP) is reported, never treated as closed.
CLOSE_DRAIN_SECONDS = 30.0
# A failed media identity is retried on every explicit search; rescanning the
# whole private legacy index to see recovered storage is rate-limited.
MEDIA_REFRESH_SECONDS = 30.0


class ManagedSimilarityError(ValueError):
    pass


class SimilarityPreparationBusy(SimilaritySourceError):
    """Retryable: another explicit search is preparing the owned proof cache."""
    def __init__(self):
        ValueError.__init__(self, 'similarity preparation in progress; retry')


class LegacyCpuPreparation(NamedTuple):
    """Keys only; never vectors, local paths or media identities."""
    prepared: frozenset  # complete pair and identity proved in this run
    ineligible: frozenset  # incomplete/invalid frozen pair: final for this DB SHA
    retry: frozenset  # media identity unavailable now; a later use retries it


def _validate_embedding(data):
    if type(data) is not bytes or len(data) != 4096:
        raise ManagedSimilarityError('解析結果の次元が不正です。')
    vec = struct.unpack('<1024f', data)
    if not all(math.isfinite(v) for v in vec):
        raise ManagedSimilarityError('解析結果が不正です。')
    norm = math.fsum(v * v for v in vec)
    if not 0.99 <= norm <= 1.01:
        raise ManagedSimilarityError('解析結果の正規化が不正です。')


class ManagedSimilarityIndex:
    """One app-owned SQLite database, two vectors committed per saved page."""

    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ManagedSimilarityError('解析保存先には絶対パスが必要です。')
        self.root = native_io_path(self.root)
        self._lock = RLock()
        self._closed = False
        _safe_path(self.root)
        marker = self.root / MARKER
        if self.root.exists():
            _safe_path(marker, file=True)
            if not marker.is_file() or _read_bounded(marker, 4096) != IDENTITY:
                raise ManagedSimilarityError('未所有の解析保存先です。')
        else:
            if not self.root.parent.is_dir():
                raise ManagedSimilarityError('解析保存先の親フォルダがありません。')
            self.root.mkdir(exist_ok=False)
            _write(marker, IDENTITY)
        self.path = self.root / DATABASE
        self._check_paths()
        self._conn = None
        try:
            self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            current = self._conn.execute('PRAGMA application_id').fetchone()[0]
            if current not in (0, APPLICATION_ID) or (self.path.stat().st_size > 0 and current == 0):
                raise ManagedSimilarityError('解析台帳の形式が違います。')
            self._conn.execute('PRAGMA busy_timeout=3000')
            self._conn.execute('PRAGMA journal_mode=DELETE')
            self._conn.execute('PRAGMA synchronous=FULL')
            if current == 0:
                self._conn.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    CREATE TABLE pages(
                        work_id TEXT NOT NULL, page INTEGER NOT NULL,
                        model TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                        receipt_sha256 TEXT NOT NULL, model_sha256 TEXT NOT NULL,
                        PRIMARY KEY(work_id,page,model)
                    );
                    CREATE TABLE work_embeddings(
                        work_id TEXT NOT NULL, kind TEXT NOT NULL, model TEXT NOT NULL,
                        page INTEGER NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
                        PRIMARY KEY(work_id,kind,model,page)
                    );
                    CREATE TABLE legacy_provenance(
                        work_id TEXT NOT NULL, page INTEGER NOT NULL,
                        model TEXT NOT NULL, db_sha256 TEXT NOT NULL,
                        PRIMARY KEY(work_id,page,model)
                    );
                    CREATE TABLE legacy_cpu_adoption(
                        work_id TEXT NOT NULL, page INTEGER NOT NULL,
                        model TEXT NOT NULL, db_sha256 TEXT NOT NULL,
                        media_token TEXT NOT NULL,
                        PRIMARY KEY(work_id,page,model)
                    );
                    COMMIT;
                """)
            required = {'pages', 'work_embeddings'}
            tables = {r[0] for r in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not required <= tables:
                raise ManagedSimilarityError('解析台帳が不完全です。')
            if 'legacy_provenance' not in tables:
                self._conn.execute('CREATE TABLE legacy_provenance('
                    'work_id TEXT NOT NULL,page INTEGER NOT NULL,model TEXT NOT NULL,'
                    'db_sha256 TEXT NOT NULL,PRIMARY KEY(work_id,page,model))')
            if 'legacy_cpu_adoption' not in tables:
                self._conn.execute('CREATE TABLE legacy_cpu_adoption('
                    'work_id TEXT NOT NULL,page INTEGER NOT NULL,model TEXT NOT NULL,'
                    'db_sha256 TEXT NOT NULL,media_token TEXT NOT NULL,'
                    'PRIMARY KEY(work_id,page,model))')
            stat = self.path.stat()
            self._db_identity = (stat.st_dev, stat.st_ino)
        except BaseException:
            if self._conn is not None:
                self._conn.close()
            raise

    def _check_paths(self):
        _safe_path(self.root)
        if _read_bounded(self.root / MARKER, 4096) != IDENTITY:
            raise ManagedSimilarityError('解析保存先の所有情報が変わりました。')
        for suffix in ('', '-wal', '-shm', '-journal'):
            _safe_path(Path(str(self.path) + suffix), file=True)

    def _check(self):
        if self._closed:
            raise ManagedSimilarityError('解析台帳は終了しています。')
        try:
            self._check_paths()
            stat = self.path.stat()
        except OSError:
            raise ManagedSimilarityError('解析台帳がありません。') from None
        if not self.path.is_file() or (stat.st_dev, stat.st_ino) != self._db_identity:
            raise ManagedSimilarityError('解析台帳がありません。')

    def _complete_vectors(self, work_id, page):
        rows = self._conn.execute(
            'SELECT kind,dim,vec FROM work_embeddings WHERE work_id=? AND page=? AND model=?',
            (work_id, page, MODEL)).fetchall()
        if len(rows) != 2 or {row[0] for row in rows} != {STYLE, COMPOSITION}:
            return False
        try:
            for _, dim, data in rows:
                if dim != 1024:
                    return False
                _validate_embedding(data)
        except ManagedSimilarityError:
            return False
        return True

    def source_status(self, work_id, page, image_sha256, receipt_sha256):
        with self._lock:
            self._check()
            self._conn.execute('BEGIN')
            try:
                row = self._conn.execute(
                    'SELECT source_sha256,receipt_sha256,model_sha256 FROM pages WHERE work_id=? AND page=? AND model=?',
                    (work_id, page, MODEL)).fetchone()
                return (row == (image_sha256, receipt_sha256, EGARA_BB_SHA256)
                        and self._complete_vectors(work_id, page))
            finally:
                self._conn.execute('ROLLBACK')

    def commit(self, work_id, page, image_sha256, receipt_sha256, vectors, *,
               model_sha256=EGARA_BB_SHA256, legacy_db_sha256=None):
        if (type(work_id) is not str or not _ID.fullmatch(work_id) or
                type(page) is not int or not 1 <= page <= 1000 or
                any(type(digest) is not str or not _SHA.fullmatch(digest)
                    for digest in (image_sha256, receipt_sha256, model_sha256)) or
                model_sha256 != EGARA_BB_SHA256 or type(vectors) is not dict or
                set(vectors) != {STYLE, COMPOSITION} or
                (legacy_db_sha256 is not None and
                 (type(legacy_db_sha256) is not str or not _SHA.fullmatch(legacy_db_sha256)))):
            raise ManagedSimilarityError('解析保存要求が不正です。')
        for value in vectors.values():
            _validate_embedding(value)
        with self._lock:
            self._check()
            self._conn.execute('BEGIN IMMEDIATE')
            try:
                existing = self._conn.execute(
                    'SELECT source_sha256,receipt_sha256,model_sha256 FROM pages WHERE work_id=? AND page=? AND model=?',
                    (work_id, page, MODEL)).fetchone()
                if (existing == (image_sha256, receipt_sha256, model_sha256)
                        and self._complete_vectors(work_id, page)):
                    self._conn.execute('ROLLBACK')
                    return False
                self._conn.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?)',
                                   (work_id, page, MODEL, image_sha256,
                                    receipt_sha256, model_sha256))
                for kind in (STYLE, COMPOSITION):
                    self._conn.execute('INSERT OR REPLACE INTO work_embeddings VALUES(?,?,?,?,?,?)',
                                       (work_id, kind, MODEL, page, 1024, vectors[kind]))
                if legacy_db_sha256 is not None:
                    self._conn.execute('INSERT OR REPLACE INTO legacy_provenance VALUES(?,?,?,?)',
                                       (work_id,page,MODEL,legacy_db_sha256))
                else:
                    self._conn.execute('DELETE FROM legacy_provenance WHERE work_id=? AND page=? AND model=?',
                                       (work_id,page,MODEL))
                self._conn.execute('DELETE FROM legacy_cpu_adoption WHERE work_id=? AND page=? AND model=?',
                                   (work_id,page,MODEL))
                self._conn.execute('COMMIT')
            except BaseException:
                self._conn.execute('ROLLBACK')
                raise
            return True

    def owned_keys(self):
        with self._lock:
            self._check()
            return frozenset(self._conn.execute('SELECT work_id,page FROM pages WHERE model=?', (MODEL,)))

    def has_owned_key(self, work_id, page):
        """Point lookup: never load N owned keys for each of N legacy pages."""
        with self._lock:
            self._check()
            return self._conn.execute(
                'SELECT 1 FROM pages WHERE work_id=? AND page=? AND model=? LIMIT 1',
                (str(work_id), page, MODEL)).fetchone() is not None

    def current_legacy_page(self, legacy, work_id, page):
        """A stale owned key must not supersede valid frozen CPU vectors."""
        with self._lock:
            self._check()
            row = self._conn.execute(
                'SELECT source_sha256,receipt_sha256,model_sha256 FROM pages '
                'WHERE work_id=? AND page=? AND model=?', (str(work_id), page, MODEL)).fetchone()
            if (row is None or row[2] != EGARA_BB_SHA256
                    or not self._complete_vectors(str(work_id), page)):
                return False
        # File/ZIP identity I/O runs only after the SQL lock is released.
        return legacy.matches(str(work_id), page, row[1])

    def legacy_db_replaced(self, legacy, work_id, page):
        """Only explicit DB provenance distinguishes replacement from media drift."""
        with self._lock:
            self._check()
            row = self._conn.execute(
                'SELECT db_sha256 FROM legacy_provenance WHERE work_id=? AND page=? AND model=?',
                (str(work_id),page,MODEL)).fetchone()
            return bool(row and row[0] != legacy.source._signature[-1].hex())

    def accept_legacy_cpu(self, legacy, work_id, page):
        """Read-only validation of an earlier pinned replacement CPU pair.

        Preparation is separate from this read-only predicate. A removed or
        changed image invalidates the pin without making unrelated queries
        fail. Only the app-owned proof cache is ever written by preparation.
        """
        work_id=str(work_id)
        db_sha=legacy.source._signature[-1].hex()
        with self._lock:
            self._check()
            proof=self._conn.execute('SELECT db_sha256,media_token FROM legacy_cpu_adoption '
                'WHERE work_id=? AND page=? AND model=?',(work_id,page,MODEL)).fetchone()
        return bool(proof and proof[0] == db_sha
                    and legacy.matches(work_id,page,proof[1]))

    def legacy_cpu_adoption_pending(self, legacy, work_id, page):
        """A new frozen generation has not yet pinned this old GPU key."""
        db_sha = legacy.source._signature[-1].hex()
        with self._lock:
            self._check()
            return self._conn.execute(
                'SELECT 1 FROM legacy_provenance l LEFT JOIN legacy_cpu_adoption a '
                'ON l.work_id=a.work_id AND l.page=a.page AND l.model=a.model '
                'WHERE l.work_id=? AND l.page=? AND l.model=? AND l.db_sha256<>? '
                'AND (a.db_sha256 IS NULL OR a.db_sha256<>?) LIMIT 1',
                (str(work_id), page, MODEL, db_sha, db_sha)).fetchone() is not None

    def reconcile_legacy_cpu(self, legacy, *, only=None, cancelled=None):
        return len(self.prepare_legacy_cpu_proofs(
            legacy, only=only, cancelled=cancelled).prepared)

    def prepare_legacy_cpu_proofs(self, legacy, *, only=None, skip=frozenset(),
                                  tokens=None, cancelled=None):
        """Prepare complete replacement CPU proofs on explicit use, not startup.

        Only pages that were GPU-indexed from an explicitly different frozen
        SQLite generation are candidates. Missing/ambiguous/corrupt media is
        reported for retry, never settled; an incomplete or corrupt pair is
        never adopted. If this SAME frozen DB was already pinned, its token is
        retained so media drift across a restart cannot silently reset that
        proof. No source or media is edited; only the owned ledger is written.

        The SQL lock covers only the candidate snapshot and the final atomic
        commit. Frozen-DB reads, SHA boundaries and file/ZIP identity I/O run
        without it, and cancelled() is polled between candidates.
        """
        db_sha = legacy.source._signature[-1].hex()
        from .local_media_adapter import MediaAmbiguous, MediaMissing, MediaUnavailable
        where = ''
        parameters = (MODEL, db_sha)
        if only is not None:
            work_id, page = only
            if (type(work_id) is not str or not _ID.fullmatch(work_id)
                    or type(page) is not int or not 1 <= page <= 1000):
                raise ManagedSimilarityError('旧CPU照合の対象が不正です。')
            where = ' AND p.work_id=? AND p.page=?'
            parameters += (work_id, page)
        with self._lock:
            self._check()
            candidates = self._conn.execute(
                'SELECT p.work_id,p.page,a.db_sha256,a.media_token '
                'FROM pages p JOIN legacy_provenance l ON '
                'p.work_id=l.work_id AND p.page=l.page AND p.model=l.model '
                'LEFT JOIN legacy_cpu_adoption a ON '
                'p.work_id=a.work_id AND p.page=a.page AND p.model=a.model '
                'WHERE p.model=? AND l.db_sha256<>?' + where + ' ORDER BY p.work_id,p.page',
                parameters).fetchall()
        # The query includes only potentially replaced old GPU generations.
        # skip holds pairs already found incomplete/invalid in this same
        # immutable generation; adopted keys are excluded by the query.
        candidates = [(wid,page) for wid,page,pinned_sha,_ in candidates
                      if pinned_sha != db_sha and (wid,page) not in skip]
        if not candidates:
            return LegacyCpuPreparation(frozenset(), frozenset(), frozenset())

        def stop():
            if cancelled is not None and cancelled():
                raise ManagedSimilarityError('旧CPU照合は取消されました。')

        if tokens is None:
            tokens = legacy.search_tokens()
        # Preparation remains enclosed by full SHA checks off the UI thread.
        # Each chunk is cancellable; do not weaken this to stat-only checks.
        legacy.check(cancelled=cancelled)
        prepared, ineligible, retry = [], set(), set()
        connection = None
        try:
            connection = sqlite3.connect(sqlite_readonly_uri(legacy.source.path,immutable=True),uri=True)
            connection.execute('PRAGMA query_only=ON')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(work_embeddings)')}
            if not {'work_id','page','kind','model','dim','vec'} <= columns:
                # A generation without its vector table proves nothing; fail
                # closed instead of reporting a completed preparation.
                raise ManagedSimilarityError('旧解析元の形式が不正です。')
            for work_id,page in candidates:
                stop()
                rows = connection.execute(
                    'SELECT kind,dim,vec FROM work_embeddings '
                    'WHERE work_id=? AND page=? AND model=?',
                    (work_id,page,MODEL)).fetchall()
                if len(rows) != 2 or {row[0] for row in rows} != {STYLE,COMPOSITION}:
                    ineligible.add((work_id,page))
                    continue
                try:
                    for _,dim,blob in rows:
                        if dim != 1024:
                            raise ManagedSimilarityError('旧CPUの次元が不正です。')
                        _validate_embedding(blob)
                except ManagedSimilarityError:
                    ineligible.add((work_id,page))
                    continue
                # Page-local: a missing, locked or corrupt original neither
                # aborts other candidates nor settles this one.
                try:
                    token = tokens.token(work_id,page)
                except (MediaMissing, MediaUnavailable, MediaAmbiguous, OSError, ValueError):
                    retry.add((work_id,page))
                    continue
                prepared.append((work_id,page,MODEL,db_sha,token))
        except sqlite3.Error:
            raise ManagedSimilarityError('旧解析元を読み取れません。') from None
        finally:
            if connection is not None:
                connection.close()
        legacy.check(cancelled=cancelled)
        # close() may have started during the reads; publish nothing then.
        stop()
        if prepared:
            with self._lock:
                self._check()
                self._conn.execute('BEGIN IMMEDIATE')
                try:
                    for item in prepared:
                        stop()
                        # Another client may already have completed the same
                        # adoption. Never replace a proof from this DB generation.
                        old = self._conn.execute(
                            'SELECT db_sha256 FROM legacy_cpu_adoption '
                            'WHERE work_id=? AND page=? AND model=?',item[:3]).fetchone()
                        if old is not None and old[0] == db_sha:
                            continue
                        # A GPU result committed meanwhile for this generation
                        # (or a managed one) needs no replacement proof.
                        replaced = self._conn.execute(
                            'SELECT 1 FROM legacy_provenance WHERE work_id=? AND page=? '
                            'AND model=? AND db_sha256<>?',item[:3] + (db_sha,)).fetchone()
                        if replaced is None:
                            continue
                        self._conn.execute(
                            'INSERT OR REPLACE INTO legacy_cpu_adoption VALUES(?,?,?,?,?)',item)
                    self._conn.execute('COMMIT')
                except BaseException:
                    self._conn.execute('ROLLBACK')
                    raise
        return LegacyCpuPreparation(frozenset(item[:2] for item in prepared),
                                    frozenset(ineligible), frozenset(retry))

    def accepted_legacy_keys(self, legacy, rows, *, tokens=None):
        """Use a single ledger snapshot to decide which frozen CPU rows survive."""
        accepted = {(str(wid), int(page)) for kind in (STYLE, COMPOSITION)
                    for wid, page, _, _ in rows[kind]}
        db_sha = legacy.source._signature[-1].hex()
        with self._lock:
            self._check()
            # A single read snapshot instead of N marker reads/path walks and
            # N separate queries for N stale keys. Only a current-generation
            # adoption proof warrants opening its legacy file/ZIP identity.
            proofs = self._conn.execute(
                'SELECT p.work_id,p.page,a.db_sha256,a.media_token FROM pages p '
                'LEFT JOIN legacy_cpu_adoption a ON p.work_id=a.work_id '
                'AND p.page=a.page AND p.model=a.model WHERE p.model=?',
                (MODEL,)).fetchall()
        # Identity I/O after the SQL lock; one search shares one token cache.
        if tokens is None:
            tokens = legacy.search_tokens()
        return {(str(wid), page) for wid, page, source_sha, token in proofs
                if (str(wid), page) not in accepted and
                not (source_sha == db_sha and tokens.matches(str(wid), page, token))}

    def snapshot_rows(self, current=None, legacy=None, *, tokens=None):
        live = None
        if current is not None:
            live = {}
            for _, receipt, checksum in current.receipts:
                wid = receipt['work_id']
                for page in current.source_pages[wid]:
                    live[(wid, page + 1)] = (receipt['files'][page]['sha256'], checksum)
        # Both kinds come from one read transaction; file/ZIP identity is
        # checked after the SQL lock is released.
        with self._lock:
            self._check()
            self._conn.execute('BEGIN')
            try:
                found = {kind: self._conn.execute(
                    'SELECT e.work_id,e.page,e.dim,e.vec,p.source_sha256,p.receipt_sha256 FROM work_embeddings e JOIN pages p '
                    'ON p.work_id=e.work_id AND p.page=e.page AND p.model=e.model '
                    'WHERE e.model=? AND e.kind=? AND p.model_sha256=? '
                    'ORDER BY e.work_id,e.page', (MODEL, kind, EGARA_BB_SHA256)).fetchall()
                    for kind in (STYLE, COMPOSITION)}
            finally:
                self._conn.execute('ROLLBACK')
        # Each complete page has two kinds. Its original ZIP/file identity is
        # opened once per (work_id,page) for the search, not once per kind.
        if legacy is not None and tokens is None:
            tokens = legacy.search_tokens()
        rows = {}
        for kind in (STYLE, COMPOSITION):
            accepted = []
            for wid, page, dim, data, image_sha, receipt_sha in found[kind]:
                if live is not None or legacy is not None:
                    managed_current = live is not None and live.get((wid,page)) == (image_sha,receipt_sha)
                    managed_work = current is not None and wid in current.source_pages
                    legacy_current = (not managed_work and legacy is not None
                                      and tokens.matches(wid, page, receipt_sha))
                    if not managed_current and not legacy_current:
                        continue
                if dim != 1024:
                    raise ManagedSimilarityError('解析台帳の次元が不正です。')
                _validate_embedding(data)
                accepted.append((wid, page, dim, data))
            rows[kind] = tuple(accepted)
        return rows

    def analyze_saved_page(self, writer, inference, work_id, zero_page, *, is_current=None):
        """One pinned receipt; no Pixiv GET and no image changes."""
        if type(zero_page) is not int or not 0 <= zero_page < 1000:
            raise ManagedSimilarityError('画像ページが不正です。')
        if getattr(inference, 'model_sha256', None) != EGARA_BB_SHA256:
            raise ManagedSimilarityError('互換モデルではありません。')
        pinned = writer.verified_saved_receipt(work_id, verify_pages=False)
        if pinned is None:
            raise ManagedSimilarityError('保存済み画像が見つかりません。')
        relative, receipt, receipt_digest, _, _ = pinned
        if zero_page >= receipt['page_count']:
            raise ManagedSimilarityError('画像ページが存在しません。')
        writer.assert_page_visible(work_id, zero_page)
        image_sha = receipt['files'][zero_page]['sha256']
        if self.source_status(work_id, zero_page+1, image_sha, receipt_digest):
            if is_current is not None and not is_current():
                raise ManagedSimilarityError('解析操作は取消されました。')
            current = writer.verified_saved_receipt(work_id, verify_pages=False)
            if current is None or current[2] != receipt_digest:
                raise ManagedSimilarityError('解析中に保存世代が変わりました。')
            writer.assert_page_visible(work_id, zero_page)
            return False
        data = writer.read_saved_page(relative, zero_page, receipt_digest)
        if sha256(data).hexdigest() != image_sha:
            raise ManagedSimilarityError('保存画像の検証に失敗しました。')
        if self.source_status(work_id, zero_page+1, image_sha, receipt_digest):
            return False
        if is_current is not None and not is_current():
            raise ManagedSimilarityError('解析操作は取消されました。')
        vectors = inference.infer(data)
        with writer.work_lock(work_id):
            current = writer.verified_saved_receipt(work_id, verify_pages=False)
            if current is None or current[2] != receipt_digest:
                raise ManagedSimilarityError('解析中に保存世代が変わりました。')
            writer.assert_page_visible(work_id, zero_page)
            if is_current is not None and not is_current():
                raise ManagedSimilarityError('解析操作は取消されました。')
            return self.commit(work_id, zero_page+1, image_sha, receipt_digest,
                               vectors, model_sha256=inference.model_sha256)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()


class CombinedSimilaritySource:
    """Read immutable legacy vectors and owned newly generated vectors together."""
    def __init__(self, frozen_db, managed_index, *, writer=None, legacy=None):
        self.old = (frozen_db if isinstance(frozen_db, SavedSimilaritySource)
                    else SavedSimilaritySource(frozen_db) if frozen_db is not None else None)
        self.new = managed_index
        self.writer = writer
        self.legacy = legacy
        self._closed = False
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._closing = Event()
        self._active = 0
        self._prepare_lock = Lock()
        self._legacy_cpu_prepared = False
        self._legacy_cpu_ineligible = frozenset()
        self._legacy_cpu_retry = frozenset()
        self._legacy_cpu_failure = False
        self._legacy_media_refresh_after = None

    @contextmanager
    def _operation(self):
        with self._condition:
            if self._closed or self._closing.is_set():
                raise SimilaritySourceError()
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def _prepare_legacy_cpu(self, tokens):
        if self.legacy is None:
            return
        if not self._prepare_lock.acquire(blocking=False):
            raise SimilarityPreparationBusy()
        try:
            if self._closing.is_set():
                raise SimilaritySourceError()
            if self._legacy_cpu_failure:
                raise ManagedSimilarityError('旧解析元の照合を完了できません。凍結コピーを確認してください。')
            if self._legacy_cpu_prepared and not self._legacy_cpu_retry:
                return
            if self._legacy_cpu_retry:
                # One explicit retry may rebuild the private index to see
                # recovered removable storage or a previously corrupt ZIP.
                # Never reset same-generation adoption tokens already stored.
                now = monotonic()
                if (self._legacy_media_refresh_after is None
                        or now >= self._legacy_media_refresh_after):
                    # Reserve the window BEFORE I/O so an unavailable device
                    # does not trigger a whole-library rescan on every click.
                    # Individual page proofs below still retry immediately.
                    self._legacy_media_refresh_after = now + MEDIA_REFRESH_SECONDS
                    self.legacy.refresh_media_index()
            try:
                result = self.new.prepare_legacy_cpu_proofs(
                    self.legacy, skip=self._legacy_cpu_ineligible,
                    tokens=tokens, cancelled=self._closing.is_set)
            except Exception:
                if not self._closing.is_set():
                    # An immutable DB/schema/ledger failure requires explicit
                    # source repair; don't rehash/index it on every timer/click.
                    self._legacy_cpu_failure = True
                raise
            self._legacy_cpu_ineligible |= result.ineligible
            self._legacy_cpu_retry = result.retry
            self._legacy_cpu_prepared = True
        finally:
            self._prepare_lock.release()

    def prepare_legacy_cpu(self):
        """Explicit off-UI preparation; no global lock is held during I/O."""
        with self._operation():
            tokens = self.legacy.search_tokens(cancelled=self._closing.is_set) if self.legacy else None
            self._prepare_legacy_cpu(tokens)

    def search(self, work_id, page=1):
        return self._search(normalize_key((work_id, page)))

    def search_author(self, work_ids, representative_id):
        work_ids = tuple(str(value) for value in work_ids)
        representative_id = str(representative_id)
        if not work_ids or representative_id not in work_ids:
            raise ValueError('invalid author similarity origin')
        return self._search((representative_id, 1), candidates=work_ids)

    def _search(self, origin, *, candidates=None):
        with self._operation():
            tokens = self.legacy.search_tokens(cancelled=self._closing.is_set) if self.legacy else None
            self._prepare_legacy_cpu(tokens)
            current = self.writer.catalog_snapshot(verify_pages=False) if self.writer is not None else None
            shared_source = (self.old is not None and self.legacy is not None
                             and self.old is self.legacy.source)
            if self.legacy is not None:
                self.legacy.check(cancelled=self._closing.is_set)
            new_rows = self.new.snapshot_rows(current, self.legacy, tokens=tokens)
            replaced = ({receipt['work_id'] for _, receipt, _ in current.receipts}
                        if current is not None else set())
            old_rows = {STYLE: (), COMPOSITION: ()}
            if self.old is not None:
                with self.old._lock:
                    # An identical old/legacy guard was already SHA-checked
                    # at search entry. Keep two SHA boundaries total, rather
                    # than verifying the same frozen DB four times.
                    if (self.old._closed or
                            (not shared_source and
                             self.old._fingerprint(cancelled=self._closing.is_set) != self.old._signature)):
                        raise SimilaritySourceError()
                    connection = None
                    try:
                        connection = sqlite3.connect(sqlite_readonly_uri(self.old.path, immutable=True), uri=True)
                        connection.execute('PRAGMA query_only=ON')
                        columns = {r[1] for r in connection.execute('PRAGMA table_info(work_embeddings)')}
                        if not {'work_id','page','kind','model','dim','vec'} <= columns:
                            raise SimilaritySourceError()
                        for kind in (STYLE, COMPOSITION):
                            old_rows[kind] = tuple(connection.execute(
                                'SELECT work_id,page,dim,vec FROM work_embeddings '
                                'WHERE model=? AND kind=? ORDER BY work_id,page',
                                (MODEL, kind)))
                    except (sqlite3.Error, OSError):
                        raise SimilaritySourceError() from None
                    finally:
                        if connection is not None:
                            connection.close()
                    if not shared_source and self.old._fingerprint(cancelled=self._closing.is_set) != self.old._signature:
                        raise SimilaritySourceError()
            # Suppression is independent of kind. Search must never execute
            # an adoption write, and evaluating it twice would recheck each
            # potentially missing legacy original unnecessarily.
            suppressed = (self.new.accepted_legacy_keys(self.legacy, new_rows, tokens=tokens)
                          if self.legacy is not None else self.new.owned_keys())
            def decoded(kind):
                current_keys = {(str(wid), int(page)) for wid, page, _, _ in new_rows[kind]}
                combined = {(str(wid), int(page)): (wid, page, dim, blob)
                            for wid, page, dim, blob in old_rows[kind]
                            if str(wid) not in replaced and
                            ((str(wid),page) not in current_keys) and
                            ((str(wid),page) not in suppressed)}
                for wid, page, dim, blob in new_rows[kind]:
                    combined[(str(wid), int(page))] = (wid, page, dim, blob)
                ordered = sorted(combined.values(), key=lambda r: (str(r[0]), r[1]))
                return decode_embeddings_pages(ordered)
            comp_keys, comp_matrix = decoded(COMPOSITION)
            style_keys, style_matrix = decoded(STYLE)
            if candidates is not None:
                all_keys = set(comp_keys) | set(style_keys)
                chosen = next((wid for wid in (origin[0], *candidates)
                               if (wid,1) in all_keys), origin[0])
                origin = (chosen,1)
            result = search_similar(comp_keys, comp_matrix, origin)
            kind = COMPOSITION
            if result['status'] == STATUS_NO_QUERY_EMBEDDING:
                result = search_similar(style_keys, style_matrix, origin)
                kind = STYLE
            result = dict(result, kind=kind)
            if self.writer is not None and self.writer.catalog_snapshot(verify_pages=False).revision != current.revision:
                raise SimilaritySourceError()
            if self.legacy is not None:
                self.legacy.check(cancelled=self._closing.is_set)
            if self._closing.is_set():
                raise SimilaritySourceError()
            return (origin[0], result) if candidates is not None else result

    def close(self):
        # Publish cancellation before acquiring any lock used by a search.
        # Never close old SQLite guards while active preparation still uses it.
        self._closing.set()
        deadline = monotonic() + CLOSE_DRAIN_SECONDS
        with self._condition:
            if self._closed:
                return
            while self._active:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError('similarity search shutdown not confirmed')
                self._condition.wait(min(.1, remaining))
            self._closed = True
            if self.old is not None:
                self.old.close()
