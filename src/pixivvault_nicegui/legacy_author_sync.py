"""Compare a frozen legacy catalog with one proved-complete author listing.

The original SQLite copy and the original media tree are strictly read-only.
Only the separate managed directory owns durable deletion observations. A
scan_id identifies ONE completed remote crawl; replays must use the same ID.
Calls without scan_id are display-only and never advance missing streaks.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import stat
from threading import Lock, RLock
import time
from types import MappingProxyType
from typing import Mapping

from .failed_records_repository import (
    immutable_copy_signature, read_failed_records,
    MAX_IMMUTABLE_COPY_BYTES,
)
from .library_models import LibrarySnapshot, LibraryWork
from .local_media_adapter import ReadonlyLocalMediaCatalog
from .models import Artwork


SIDECAR = '.pixivvault-legacy-author-sync.sqlite3'
MAX_LEGACY_WORKS = 50_000
MAX_REMOTE_WORKS = 20_000
MAX_SCAN_RECEIPTS = 100_000  # Fail closed; pruning would permit old scan IDs to replay.
MAX_SIDECAR_BYTES = 64 * 1024 * 1024
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_SCAN_ID = re.compile(r'[0-9a-f]{32}\Z')
_SUPPORTED = frozenset(('illust', 'manga', 'ugoira'))
_COLUMNS = frozenset(('work_id', 'user_id', 'title', 'update_date',
                      'is_deleted', 'missing_streak', 'page_count', 'content_type'))
_STORE_LOCK = Lock()  # Serializes creation through commit across bridge instances.


@dataclass(frozen=True, slots=True)
class LegacySaved:
    work_id: str
    author_id: str
    update_date: str | None
    deleted: bool
    files_present: bool | None
    failed: bool


@dataclass(frozen=True, slots=True)
class LegacyRemoteAbsent:
    """Remote absence only; unrelated to local ZIP/folder media presence."""

    work_id: str
    title: str
    status: str  # pending | confirmed
    streak: int


@dataclass(frozen=True, slots=True)
class LegacyAuthorScan:
    saved: Mapping[str, LegacySaved]
    remote_missing_pending: int
    remote_missing_confirmed: int
    failed_history: int
    remote_absent: tuple[LegacyRemoteAbsent, ...] = ()


@dataclass(frozen=True, slots=True)
class _SourceWork:
    work_id: str
    author_id: str
    title: str
    update_date: str | None
    deleted: bool
    missing_streak: int
    page_count: int | None
    content_type: str


def _safe_path(path: Path, *, directory: bool) -> Path:
    """Reject links/reparse points in every existing component, including parents."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError('absolute source and managed paths required')
    for node in (path, *path.parents):
        try:
            info = node.lstat()
        except FileNotFoundError as exc:
            raise ValueError('path must already exist') from exc
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('linked paths rejected')
    info = path.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('directory required')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('standalone regular source file required')
    return path.resolve(strict=True)


def _nonoverlap(a: Path, b: Path) -> None:
    if a == b or a in b.parents or b in a.parents:
        raise ValueError('legacy and managed roots overlap')


def _id(value: object, label: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(f'{label} is invalid')
    return value


def _date(value: object) -> str | None:
    if value is None:
        return None
    if (type(value) is not str or len(value) > 512 or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ValueError('unsupported update date')
    return value or None


def _row(row: sqlite3.Row) -> _SourceWork:
    wid = _id(row['work_id'], 'legacy work ID')
    author = _id(row['user_id'], 'legacy author ID')
    title = row['title']
    if (type(title) is not str or len(title) > 512 or
            any(ord(ch) < 32 or ord(ch) == 127 for ch in title)):
        raise ValueError('unsupported legacy title')
    kind = row['content_type']
    if kind is None or kind == '':
        kind = 'illust'  # The original core treats absent legacy types as illust.
    if type(kind) is not str or kind not in (*_SUPPORTED, 'novel'):
        raise ValueError('unsupported legacy content type')
    deleted, streak, pages = (row['is_deleted'], row['missing_streak'], row['page_count'])
    if deleted is None:
        deleted = 0
    if streak is None:
        streak = 0
    if (type(deleted) is not int or deleted not in (0, 1) or
            type(streak) is not int or not 0 <= streak <= 1_000_000 or
            (pages is not None and (type(pages) is not int or not 1 <= pages <= 1000))):
        raise ValueError('invalid legacy deletion or page metadata')
    return _SourceWork(wid, author, title, _date(row['update_date']),
                       bool(deleted), streak, pages, kind)


def _validate_remote(author: str, remote: tuple[dict, ...]) -> tuple[frozenset[str], bytes]:
    """Caller guarantees completeness via get_author_artworks_complete.

    This boundary verifies a whole, author-scoped result, not network provenance.
    A partial list must NEVER be passed to this method.
    """
    if type(remote) is not tuple or len(remote) > MAX_REMOTE_WORKS:
        raise ValueError('complete remote tuple required')
    seen: set[str] = set()
    normalized = []
    for item in remote:
        if type(item) is not dict:
            raise ValueError('invalid remote artwork')
        wid = _id(item.get('id'), 'remote work ID')
        if _id(item.get('user_id'), 'remote author ID') != author or wid in seen:
            raise ValueError('remote author mismatch or duplicate work')
        kind, count = item.get('type'), item.get('page_count')
        if (type(kind) is not int or kind not in (0, 1, 2) or
                type(count) is not int or not 1 <= count <= 1000 or
                'update_date' not in item):
            raise ValueError('incomplete remote artwork')
        updated = _date(item['update_date'])
        seen.add(wid)
        normalized.append((wid, kind, count, updated))
    normalized.sort(key=lambda item: int(item[0]))
    payload = json.dumps(normalized, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
    return frozenset(seen), sha256(payload).digest()


class LegacyAuthorBridge:
    """Read-only immutable legacy source with isolated atomic scan receipts."""

    def __init__(self, readonly_db: Path, readonly_media_root: Path, managed_root: Path,
                 *, source_signature=None):
        self.readonly_db = _safe_path(readonly_db, directory=False)
        self.readonly_media_root = _safe_path(readonly_media_root, directory=True)
        self.managed_root = _safe_path(managed_root, directory=True)
        _nonoverlap(self.readonly_db, self.managed_root)
        _nonoverlap(self.readonly_media_root, self.managed_root)
        # The source and original media may have a common ancestor; they are
        # never opened for writes. Managed storage must be entirely separate.
        current = immutable_copy_signature(self.readonly_db)
        if (source_signature is not None and
                (type(source_signature) is not tuple or len(source_signature) != 4 or
                 source_signature != current)):
            raise ValueError('legacy source signature mismatch')
        self.source_signature = current
        self.source_digest = current[3].hex()
        self.sidecar = self.managed_root / SIDECAR
        self._lifecycle_lock = RLock()
        self._closed = False

    def _check_source(self) -> None:
        if immutable_copy_signature(self.readonly_db) != self.source_signature:
            raise ValueError('immutable legacy source changed')

    def assert_source_unchanged(self) -> None:
        """Public revalidation gate for a later managed download/receipt."""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError('legacy author bridge closed')
            self._check_source()

    def close(self) -> None:
        """Idempotently end this bridge's lifetime; open scans finish first."""
        with self._lifecycle_lock:
            self._closed = True

    def _source_works(self, author: str) -> tuple[_SourceWork, ...]:
        """A bounded single immutable SQLite read transaction."""
        uri = self.readonly_db.as_uri() + '?mode=ro&immutable=1'
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.row_factory = sqlite3.Row
            if hasattr(conn, 'setlimit'):
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            columns = {field[1] for field in conn.execute('PRAGMA table_info(works)')}
            if not _COLUMNS <= columns:
                raise ValueError('unsupported legacy works schema')
            # Limit the complete table, not only this author's slice, so hidden
            # records cannot disguise an oversized or unexpectedly broad copy.
            if conn.execute('SELECT 1 FROM works LIMIT 1 OFFSET ?', (MAX_LEGACY_WORKS,)).fetchone():
                raise ValueError('legacy works limit exceeded')
            # Legacy schemas added user_id later, leaving historical unrelated
            # records with NULL author and possibly partial metadata. These
            # cannot be attributed to this author, so never let them break a
            # valid author's read-only preview. The full DB size, schema and
            # source SHA checks above still cover every row and source byte.
            records = []
            seen = set()
            for row in conn.execute('SELECT work_id,user_id,title,update_date,is_deleted,'
                                    'missing_streak,page_count,content_type FROM works '
                                    'WHERE CAST(user_id AS TEXT)=? ORDER BY rowid', (author,)):
                item = _row(row)
                if item.work_id in seen:
                    raise ValueError('duplicate legacy work IDs')
                seen.add(item.work_id)
                records.append(item)
            conn.execute('COMMIT')
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return tuple(records)

    def _presence(self, works: tuple[_SourceWork, ...]) -> dict[str, bool | None]:
        """Reuse the media catalog's index, never opening or decoding images."""
        candidates = []
        for item in works:
            if item.content_type not in ('illust', 'manga'):
                continue
            art = Artwork(item.work_id, item.title, item.author_id,
                          page_count=item.page_count or 1)
            candidates.append(LibraryWork(art, item.author_id,
                                          page_count_known=item.page_count is not None,
                                          work_type=item.content_type))
        result: dict[str, bool | None] = {item.work_id: None for item in works}
        if not candidates:
            return result
        catalog = ReadonlyLocalMediaCatalog(self.readonly_media_root,
                                             LibrarySnapshot(tuple(candidates)))
        try:
            inspect = getattr(catalog, 'inspect_presence', None)
            if inspect is None:
                # The dedicated media inspector is supplied by another worker.
                # A missing implementation is an unknown, never false/missing.
                return result
            for item in works:
                if item.content_type not in ('illust', 'manga') or item.page_count is None:
                    continue
                try:
                    presence = inspect(item.work_id)
                except (OSError, ValueError, RuntimeError):
                    presence = None
                if presence is not None and type(presence) is not bool:
                    raise ValueError('media inspector returned an invalid state')
                result[item.work_id] = presence
            return result
        finally:
            catalog.close()

    def _sidecar_safe(self) -> None:
        if self.sidecar.exists() or self.sidecar.is_symlink():
            info = self.sidecar.lstat()
            # A DIFFERENT PROCESS can have opened the new SQLite file but not
            # committed its first schema transaction yet. Its inode can briefly
            # be zero bytes. Never silently adopt a persistent empty/corrupt DB.
            for _ in range(10):
                if info.st_size != 0:
                    break
                time.sleep(0.05)
                info = self.sidecar.lstat()
            if (self.sidecar.is_symlink() or
                    getattr(info, 'st_file_attributes', 0) & 0x400 or
                    not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    not 0 < info.st_size <= MAX_SIDECAR_BYTES):
                raise ValueError('managed observation store rejected')
        for suffix in ('-wal', '-shm'):
            if Path(str(self.sidecar) + suffix).exists():
                raise ValueError('unexpected managed WAL state')

    def _open_store(self, *, write: bool) -> sqlite3.Connection | None:
        self._sidecar_safe()
        if not write and not self.sidecar.exists():
            return None
        uri = self.sidecar.as_uri() + ('?mode=rwc' if write else '?mode=ro')
        conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
        try:
            conn.execute('PRAGMA busy_timeout=5000')
            if write:
                conn.execute('PRAGMA synchronous=FULL')
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('CREATE TABLE IF NOT EXISTS metadata('
                             'key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                conn.execute('CREATE TABLE IF NOT EXISTS observations('
                             'source_digest TEXT NOT NULL, author_id TEXT NOT NULL, '
                             'scan_id TEXT NOT NULL, remote_digest BLOB NOT NULL, '
                             'PRIMARY KEY(source_digest,author_id,scan_id))')
                conn.execute('CREATE TABLE IF NOT EXISTS states('
                             'author_id TEXT NOT NULL, work_id TEXT NOT NULL, '
                             'missing_streak INTEGER NOT NULL CHECK(missing_streak>=0), '
                             'deleted INTEGER NOT NULL CHECK(deleted IN (0,1)), '
                             'PRIMARY KEY(author_id,work_id))')
                prior = conn.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()
                if prior is None:
                    conn.execute('INSERT INTO metadata(key,value) VALUES (?,?)',
                                 ('source_sha256', self.source_digest))
                elif prior[0] != self.source_digest:
                    raise ValueError('managed state belongs to a different legacy copy')
            else:
                conn.execute('BEGIN')
                prior = conn.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()
                if prior is None or prior[0] != self.source_digest:
                    raise ValueError('managed state source mismatch')
            return conn
        except BaseException:
            conn.rollback()
            conn.close()
            raise

    def _read_states(self, conn: sqlite3.Connection | None,
                     author: str) -> dict[str, tuple[int, bool]]:
        if conn is None:
            return {}
        result = {}
        for wid, streak, deleted in conn.execute(
                'SELECT work_id,missing_streak,deleted FROM states WHERE author_id=?', (author,)):
            if (type(wid) is not str or not _ID.fullmatch(wid) or
                    type(streak) is not int or not 0 <= streak <= 1_000_000 or
                    type(deleted) is not int or deleted not in (0, 1)):
                raise ValueError('managed observation state damaged')
            result[wid] = (streak, bool(deleted))
            if len(result) > MAX_LEGACY_WORKS:
                raise ValueError('managed observation state limit exceeded')
        return result

    def observe_author(self, user_id: str, remote: tuple[dict, ...], *,
                       scan_id: str | None = None) -> LegacyAuthorScan:
        """Display a complete author result, optionally committing one crawl.

        `scan_id` must be a fresh UUID4 hex for a *new* completed crawl; retries
        reuse it. A repeated ID with a changed result is rejected. Without an
        ID, the method only reads state and cannot advance deletion decisions.
        """
        with self._lifecycle_lock, _STORE_LOCK:
            if self._closed:
                raise RuntimeError('legacy author bridge closed')
            return self._observe(author=user_id, remote=remote, scan_id=scan_id)

    def _observe(self, *, author: str, remote: tuple[dict, ...],
                 scan_id: str | None) -> LegacyAuthorScan:
        author = _id(author, 'author ID')
        if scan_id is not None and (type(scan_id) is not str or
                                    not _SCAN_ID.fullmatch(scan_id)):
            raise ValueError('invalid scan ID')
        present, remote_digest = _validate_remote(author, remote)
        self._check_source()
        works = self._source_works(author)
        failures = read_failed_records(self.readonly_db, immutable=True)
        matching_failures = frozenset(r.work_id for r in failures if r.user_id == author)
        media = self._presence(works)
        self._check_source()  # Both source reads and media indexing must precede writes.
        conn = self._open_store(write=scan_id is not None)
        try:
            states = self._read_states(conn, author)
            if conn is not None and scan_id is not None:
                recorded = conn.execute(
                    'SELECT remote_digest FROM observations WHERE source_digest=? '
                    'AND author_id=? AND scan_id=?',
                    (self.source_digest, author, scan_id)).fetchone()
                if recorded is not None and recorded[0] != remote_digest:
                    raise ValueError('scan ID replay differs from recorded complete listing')
                fresh = recorded is None
                if fresh:
                    if conn.execute('SELECT 1 FROM observations LIMIT 1 OFFSET ?',
                                    (MAX_SCAN_RECEIPTS,)).fetchone():
                        raise ValueError('scan receipt capacity reached')
                    missing = [item for item in works if item.content_type in _SUPPORTED
                               and item.work_id not in present]
                    population = sum(item.content_type in _SUPPORTED for item in works)
                    threshold = (5 if population >= 5 and population and
                                 len(missing) / population >= .40 else 3)
                    for item in works:
                        if item.content_type not in _SUPPORTED:
                            continue
                        prior_streak, prior_deleted = states.get(
                            item.work_id, (item.missing_streak, item.deleted))
                        if item.work_id in present:
                            # Reappearance alone is not a successful restore.
                            # A validated managed download receipt is required
                            # before services may present a deleted work as saved.
                            next_streak, next_deleted = 0, prior_deleted or item.deleted
                        elif prior_deleted:
                            next_streak, next_deleted = prior_streak, True
                        else:
                            next_streak = min(1_000_000, prior_streak + 1)
                            next_deleted = next_streak >= threshold
                        states[item.work_id] = (next_streak, next_deleted)
                        conn.execute('INSERT INTO states(author_id,work_id,missing_streak,deleted)'
                                     ' VALUES (?,?,?,?) ON CONFLICT(author_id,work_id) DO UPDATE '
                                     'SET missing_streak=excluded.missing_streak,deleted=excluded.deleted',
                                     (author, item.work_id, next_streak, int(next_deleted)))
                    conn.execute('INSERT INTO observations('
                                 'source_digest,author_id,scan_id,remote_digest) VALUES (?,?,?,?)',
                                 (self.source_digest, author, scan_id, remote_digest))
                self._check_source()  # Reject a changed copy before commit.
                conn.commit()
            elif conn is not None:
                conn.commit()  # Ends read-only transaction, no mutation.
            saved: dict[str, LegacySaved] = {}
            pending = confirmed = 0
            absent: list[LegacyRemoteAbsent] = []
            for item in works:
                streak, deleted = states.get(item.work_id,
                                             (item.missing_streak, item.deleted))
                if item.content_type in _SUPPORTED and item.work_id not in present:
                    if deleted:
                        confirmed += 1
                    else:
                        pending += 1
                    absent.append(LegacyRemoteAbsent(
                        item.work_id, item.title,
                        'confirmed' if deleted else 'pending', streak))
                saved[item.work_id] = LegacySaved(item.work_id, item.author_id,
                                                  item.update_date, deleted,
                                                  media[item.work_id],
                                                  item.work_id in matching_failures)
            return LegacyAuthorScan(MappingProxyType(saved), pending, confirmed,
                                    sum(r.user_id == author for r in failures), tuple(absent))
        except BaseException:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()
