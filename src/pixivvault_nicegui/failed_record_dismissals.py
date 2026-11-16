"""App-owned, persistent dismissals for read-only legacy failure records.

Exact-record, source-scoped local decisions; original sources stay read-only.

The original failed_queue database and its media are never opened for
writing. Deleting one failure or clearing all failures in the NiceGUI view
only hides exact record contents for one source namespace. The ledger stores
the 64-hex namespace, SHA-256 digests of length-prefixed complete records,
revisions and fixed ownership metadata; never work IDs, titles, reasons or
paths. A reimported row with any changed field (including retry_count) has a
different digest and stays visible.

Several handles or processes may share one ledger: snapshot() returns a
per-namespace revision and dismiss() is a compare-and-swap against it, so a
confirmation captured before another client's change is refused instead of
hiding rows the user did not see.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock

from .failed_records_repository import MAX_TOTAL_UTF8_BYTES
from .failed_records_state import CSV_COLUMNS, FailureRecord, MAX_RECORDS
from .fs_paths import logical_path, native_io_path
from .managed_downloads import _read_bounded, _safe_path, _write

MARKER = '.pixivvault-failure-dismissals-v1'
IDENTITY = b'PixivVault NiceGUI failure record dismissals v1\n'
DATABASE = 'dismissals.sqlite3'
APPLICATION_ID = 1347831364  # b'PVFD'
SCHEMA_VERSION = 1
# Dismissed digests across every namespace. Reaching the bound refuses the
# whole operation; existing decisions are never evicted to make room.
MAX_DECISIONS = 100_000
# Same per-field bound as the read-only failed_queue reader.
MAX_FIELD_CHARS = 65_536
MAX_PATH_CHARS = 32_767
_MAX_INT = 2**63 - 1
_TEXT_FIELDS = CSV_COLUMNS[:-1]
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_RECORD_DOMAIN = b'pixivvault-failure-record-v1\0'
_SOURCE_DOMAIN = b'pixivvault-failure-source-v1\0'
_RECORD_OVERHEAD = len(_RECORD_DOMAIN) + 8 * len(CSV_COLUMNS)
_OWNER = (1, 'pixivvault-nicegui', 'failure-record-dismissals', SCHEMA_VERSION)
_DDL = (
    'CREATE TABLE owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1),'
    'application TEXT NOT NULL,purpose TEXT NOT NULL,version INTEGER NOT NULL)',
    'CREATE TABLE namespaces(namespace TEXT PRIMARY KEY CHECK(length(namespace)=64),'
    'revision INTEGER NOT NULL CHECK(revision>=1))',
    'CREATE TABLE dismissals(namespace TEXT NOT NULL CHECK(length(namespace)=64),'
    'digest TEXT NOT NULL CHECK(length(digest)=64),'
    'revision INTEGER NOT NULL CHECK(revision>=1),PRIMARY KEY(namespace,digest))',
)
# Exact sqlite_master content: any extra table, index, view or trigger is foreign.
_SCHEMA = frozenset({
    ('table', 'owner', 'owner', _DDL[0]),
    ('table', 'namespaces', 'namespaces', _DDL[1]),
    ('index', 'sqlite_autoindex_namespaces_1', 'namespaces', None),
    ('table', 'dismissals', 'dismissals', _DDL[2]),
    ('index', 'sqlite_autoindex_dismissals_1', 'dismissals', None),
})

ROOT_MESSAGE = '非表示台帳の保存先が不正です。'
PARENT_MESSAGE = '非表示台帳の親フォルダがありません。'
UNOWNED_MESSAGE = '未所有の非表示台帳保存先です。'
FORMAT_MESSAGE = '非表示台帳の形式が違います。'
UNAVAILABLE_MESSAGE = '非表示台帳を利用できません。元の記録は変更していません。'
CLOSED_MESSAGE = '非表示台帳は終了しています。'
INPUT_MESSAGE = '失敗記録の指定が不正です。'
SIZE_MESSAGE = '失敗記録の件数または内容が上限を超えています。'
CONFLICT_MESSAGE = '失敗記録の表示が他の操作で変わりました。再読み込みしてからやり直してください。'
LIMIT_MESSAGE = ('非表示にした記録が上限の100,000件を超えるため中止しました。'
                 '何も変更していません。')


class FailureDismissalError(ValueError):
    """Fixed public text; never includes paths, record fields or SQLite detail."""


class FailureDismissalConflict(FailureDismissalError):
    """The confirmed selection is stale; nothing was hidden."""


def _fail(message):
    raise FailureDismissalError(message)


def _safe(path, *, file=False):
    try:
        _safe_path(path, file=file)
    except (OSError, ValueError):
        raise FailureDismissalError(UNOWNED_MESSAGE) from None


def _path_text(value, message):
    try:
        text = os.fspath(value)
    except TypeError:
        raise FailureDismissalError(message) from None
    if (type(text) is not str or not text or len(text) > MAX_PATH_CHARS
            or '\0' in text or not Path(text).is_absolute()):
        _fail(message)
    return text


def source_namespace(source_path) -> str:
    """SHA-256 of the normalized absolute logical spelling; touches no file.

    Native long-path and logical spellings of one source give one namespace.
    The path itself is never stored.
    """
    text = _path_text(source_path, INPUT_MESSAGE)
    normalized = os.path.normcase(os.path.normpath(str(logical_path(Path(text)))))
    if len(normalized) > MAX_PATH_CHARS:
        _fail(INPUT_MESSAGE)
    return sha256(_SOURCE_DOMAIN + normalized.encode('utf-8', 'surrogatepass')).hexdigest()


def _record_payload(record) -> bytes:
    # Length-prefixed fields: ('ab','c') and ('a','bc') never collide.
    parts = [_RECORD_DOMAIN]
    for name in _TEXT_FIELDS:
        value = getattr(record, name)
        if type(value) is not str:
            _fail(INPUT_MESSAGE)
        if len(value) > MAX_FIELD_CHARS:
            _fail(SIZE_MESSAGE)
        data = value.encode('utf-8', 'surrogatepass')
        parts += (len(data).to_bytes(8, 'big'), data)
    retry = record.retry_count
    if type(retry) is not int or not 0 <= retry <= _MAX_INT:
        _fail(INPUT_MESSAGE)
    parts.append(retry.to_bytes(8, 'big'))
    return b''.join(parts)


def record_digest(record) -> str:
    """Digest of all seven FailureRecord fields, retry_count included."""
    if type(record) is not FailureRecord:
        _fail(INPUT_MESSAGE)
    return sha256(_record_payload(record)).hexdigest()


def _digests(records, *, allow_empty) -> tuple[str, ...]:
    # A tuple is required so an unbounded iterator is never consumed.
    if type(records) is not tuple or (not records and not allow_empty):
        _fail(INPUT_MESSAGE)
    if len(records) > MAX_RECORDS:
        _fail(SIZE_MESSAGE)
    seen = set()
    total = 0
    result = []
    for record in records:
        if (type(record) is not FailureRecord or type(record.work_id) is not str
                or record.work_id in seen):
            _fail(INPUT_MESSAGE)
        seen.add(record.work_id)
        payload = _record_payload(record)
        total += len(payload) - _RECORD_OVERHEAD
        if total > MAX_TOTAL_UTF8_BYTES:
            _fail(SIZE_MESSAGE)
        result.append(sha256(payload).hexdigest())
    return tuple(result)


def _namespace(value) -> str:
    if type(value) is not str or not _HEX64.fullmatch(value):
        _fail(INPUT_MESSAGE)
    return value


class FailureDismissals:
    """One handle on an app-owned dismissal ledger under a managed root.

    The root must be absent (created here, never its parents) or carry the
    exact owner marker. Unmarked existing folders, links/reparse points and
    foreign SQLite files are refused without writing to them.
    """

    def __init__(self, root):
        self._lock = RLock()
        self._closed = True
        self._conn = None
        self._created_root = False
        self.root = native_io_path(_path_text(root, ROOT_MESSAGE))
        self.path = self.root / DATABASE
        try:
            self._claim_root()
            self._connect()
        except BaseException as error:
            self._discard()
            if (isinstance(error, FailureDismissalError)
                    or not isinstance(error, (OSError, sqlite3.Error, ValueError))):
                raise
            raise FailureDismissalError(UNAVAILABLE_MESSAGE) from None
        self._closed = False

    def _claim_root(self):
        _safe(self.root)
        marker = self.root / MARKER
        if self.root.exists():
            try:
                identity = _read_bounded(marker, 4096)
            except (OSError, ValueError):
                raise FailureDismissalError(UNOWNED_MESSAGE) from None
            if identity != IDENTITY:
                _fail(UNOWNED_MESSAGE)
            if not self.path.is_file():
                # Lost decisions are not an empty history. Fail closed rather
                # than silently recreating a missing previously-owned ledger.
                _fail(UNAVAILABLE_MESSAGE)
            return
        if not self.root.parent.is_dir():
            _fail(PARENT_MESSAGE)
        self.root.mkdir(exist_ok=False)
        self._created_root = True
        try:
            _write(marker, IDENTITY)
        except BaseException:
            try:
                self.root.rmdir()  # Only the still-empty folder created above.
            except OSError:
                pass
            raise

    def _check_paths(self):
        _safe(self.root)
        try:
            identity = _read_bounded(self.root / MARKER, 4096)
        except (OSError, ValueError):
            raise FailureDismissalError(UNOWNED_MESSAGE) from None
        if identity != IDENTITY:
            _fail(UNOWNED_MESSAGE)
        for suffix in ('', '-journal', '-wal', '-shm'):
            _safe(Path(str(self.path) + suffix), file=True)

    def _sidecar(self, suffix):
        return Path(str(self.path) + suffix)

    def _connect(self):
        self._check_paths()
        # This ledger never uses WAL. A missing database with a leftover
        # journal could replay foreign pages into a fresh file.
        if any(self._sidecar(s).exists() for s in ('-wal', '-shm')) or (
                not self.path.exists() and self._sidecar('-journal').exists()):
            _fail(FORMAT_MESSAGE)
        conn = self._conn = sqlite3.connect(
            str(self.path), timeout=3.0, isolation_level=None, check_same_thread=False)
        conn.execute('PRAGMA busy_timeout=3000')
        conn.execute('PRAGMA trusted_schema=OFF')
        # Identify before any PRAGMA that could write to a foreign database.
        current = conn.execute('PRAGMA application_id').fetchone()[0]
        if current == 0 and not self._created_root:
            _fail(FORMAT_MESSAGE)
        if current not in (0, APPLICATION_ID) or (
                current == 0 and conn.execute('PRAGMA page_count').fetchone()[0] != 0):
            _fail(FORMAT_MESSAGE)
        if str(conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]).lower() != 'delete':
            _fail(FORMAT_MESSAGE)
        conn.execute('PRAGMA synchronous=FULL')
        if current == 0:
            self._initialize()
        self._validate_schema()
        info = self.path.stat()
        self._identity = (info.st_dev, info.st_ino)
        self._check_paths()

    def _initialize(self):
        conn = self._conn
        conn.execute('BEGIN IMMEDIATE')
        try:
            # Another process may have initialized it while we waited.
            current = conn.execute('PRAGMA application_id').fetchone()[0]
            if current == 0:
                # BEGIN IMMEDIATE may materialize the empty database header
                # (page_count=1) before any schema exists. Ownership was
                # verified before the transaction; reject foreign objects,
                # not that legitimate first header page.
                if (not self._created_root or
                        conn.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone() is not None):
                    _fail(FORMAT_MESSAGE)
                conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
                conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                for statement in _DDL:
                    conn.execute(statement)
                conn.execute('INSERT INTO owner VALUES(?,?,?,?)', _OWNER)
            elif current != APPLICATION_ID:
                _fail(FORMAT_MESSAGE)
            conn.execute('COMMIT')
        except BaseException:
            self._rollback()
            raise

    def _validate_schema(self):
        conn = self._conn
        conn.execute('BEGIN')
        try:
            if (conn.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
                _fail(FORMAT_MESSAGE)
            schema = conn.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 16').fetchall()
            if len(schema) != len(_SCHEMA) or frozenset(schema) != _SCHEMA:
                _fail(FORMAT_MESSAGE)
            owner = conn.execute(
                'SELECT singleton,application,purpose,version FROM owner LIMIT 2').fetchall()
            if owner != [_OWNER]:
                _fail(FORMAT_MESSAGE)
        finally:
            self._rollback()

    def _rollback(self):
        if self._conn is not None and self._conn.in_transaction:
            self._conn.execute('ROLLBACK')

    def _discard(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def _check(self):
        if self._closed:
            _fail(CLOSED_MESSAGE)
        self._check_paths()
        if any(self._sidecar(s).exists() for s in ('-wal', '-shm')):
            _fail(FORMAT_MESSAGE)
        info = self.path.stat()
        if not self.path.is_file() or (info.st_dev, info.st_ino) != self._identity:
            _fail(UNAVAILABLE_MESSAGE)

    def _guard(self, operation):
        with self._lock:
            try:
                self._check()
                return operation()
            except FailureDismissalError:
                raise
            except (OSError, sqlite3.Error, ValueError):
                raise FailureDismissalError(UNAVAILABLE_MESSAGE) from None

    def _revision(self, namespace) -> int:
        row = self._conn.execute(
            'SELECT revision FROM namespaces WHERE namespace=?', (namespace,)).fetchone()
        if row is None:
            return 0
        if type(row[0]) is not int or not 1 <= row[0] < _MAX_INT:
            _fail(FORMAT_MESSAGE)
        return row[0]

    def _count(self) -> int:
        return self._conn.execute('SELECT COUNT(*) FROM dismissals').fetchone()[0]

    def snapshot(self, namespace: str, records: tuple[FailureRecord, ...]
                 ) -> tuple[int, tuple[FailureRecord, ...]]:
        """Return (revision, records not dismissed) from one read transaction.

        Order and objects of `records` are preserved. Clear-all must pass the
        whole validated snapshot, never filtered or page-visible rows.
        """
        namespace = _namespace(namespace)
        digests = _digests(records, allow_empty=True)

        def read():
            conn = self._conn
            conn.execute('BEGIN')
            try:
                revision = self._revision(namespace)
                dismissed = set()
                for (digest,) in conn.execute(
                        'SELECT digest FROM dismissals WHERE namespace=? LIMIT ?',
                        (namespace, MAX_DECISIONS + 1)):
                    if type(digest) is not str or not _HEX64.fullmatch(digest):
                        _fail(FORMAT_MESSAGE)
                    dismissed.add(digest)
                if len(dismissed) > MAX_DECISIONS:
                    _fail(FORMAT_MESSAGE)
            finally:
                self._rollback()
            return revision, tuple(record for record, digest in zip(records, digests)
                                   if digest not in dismissed)

        return self._guard(read)

    def dismiss(self, namespace: str, selected: tuple[FailureRecord, ...], *,
                expected_revision: int) -> int:
        """Atomically hide exactly `selected`; return the new revision.

        Refuses with FailureDismissalConflict when another client changed the
        namespace since `expected_revision`, or when a selected record is
        already hidden (it cannot have been visible in that snapshot).
        """
        namespace = _namespace(namespace)
        if type(expected_revision) is not int or not 0 <= expected_revision < _MAX_INT - 1:
            _fail(INPUT_MESSAGE)
        digests = _digests(selected, allow_empty=False)

        def write():
            conn = self._conn
            conn.execute('BEGIN IMMEDIATE')
            try:
                if self._revision(namespace) != expected_revision:
                    raise FailureDismissalConflict(CONFLICT_MESSAGE)
                revision = expected_revision + 1
                before = self._count()
                if before > MAX_DECISIONS:
                    _fail(FORMAT_MESSAGE)
                conn.executemany(
                    'INSERT OR IGNORE INTO dismissals(namespace,digest,revision) VALUES(?,?,?)',
                    ((namespace, digest, revision) for digest in digests))
                after = self._count()
                if after - before != len(digests):
                    raise FailureDismissalConflict(CONFLICT_MESSAGE)
                if after > MAX_DECISIONS:
                    _fail(LIMIT_MESSAGE)
                conn.execute(
                    'INSERT INTO namespaces(namespace,revision) VALUES(?,?) '
                    'ON CONFLICT(namespace) DO UPDATE SET revision=excluded.revision',
                    (namespace, revision))
                conn.execute('COMMIT')
            except BaseException:
                self._rollback()
                raise
            return revision

        return self._guard(write)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._discard()
