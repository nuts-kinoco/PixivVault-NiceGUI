"""SQLite storage owned exclusively by one profile/account's visual learner."""
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from errno import EACCES, EAGAIN
from hashlib import sha256
import json
import logging
from math import isfinite
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
from time import monotonic, sleep
from uuid import uuid4

if os.name == 'nt':
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl

from .fs_paths import logical_path, native_io_path, sqlite_readonly_uri
from .managed_generations import _open_shared, _replace_retrying
from .visual_preference import EmbeddingKey, Generation, ModelSnapshot


OWNER_MARKER = '.pixivvault-visual-preference-v1'
APPLICATION_ID = 0x50565650  # PVVP
_LOG = logging.getLogger(__name__)
SCHEMA_VERSION = 1
_DATABASE = 'visual_preference.sqlite3'
_HEX64 = re.compile(r'[0-9a-f]{64}')
_HEX32 = re.compile(r'[0-9a-f]{32}')
_MARKER_LIMIT = 512
_IO_OFFSET = _MARKER_LIMIT + 1
_CURRENT_READ_OFFSET = _IO_OFFSET + 1
_IO_TIMEOUT = 15.0
_CONTROL_TIMEOUT = 2.0
_RETRY_INTERVAL = 0.02
_JOURNAL_MAGIC = bytes.fromhex('d9d505f920a163d7')
_EMBEDDING_COLUMNS = 'work_id,page,variant,image_sha,model_sha,preprocessing_version'
_EMBEDDING_WHERE = 'work_id=? AND page=? AND variant=? AND image_sha=? AND model_sha=? AND preprocessing_version=?'
_UNFENCED_EVENT = object()
_INVALIDATIONS = frozenset({'judgment', 'reason_change', 'undo', 'restore', 'reset',
                            'image_change', 'model_change', 'off', 'account_change', 'gpu_failure'})
_SCHEMA = {
    'owner': 'CREATE TABLE owner(scope TEXT NOT NULL, store_id TEXT NOT NULL)',
    'generation_state': '''CREATE TABLE generation_state(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), number INTEGER NOT NULL,
        model_sha TEXT, preprocessing_version TEXT, judgment_fingerprint TEXT,
        purging INTEGER NOT NULL DEFAULT 0 CHECK(purging IN (0,1)),
        CHECK((model_sha IS NULL AND preprocessing_version IS NULL AND judgment_fingerprint IS NULL)
           OR (model_sha IS NOT NULL AND preprocessing_version IS NOT NULL AND judgment_fingerprint IS NOT NULL))
    )''',
    'candidate_embeddings': '''CREATE TABLE candidate_embeddings(
        work_id TEXT NOT NULL, page INTEGER NOT NULL CHECK(page>=0),
        variant TEXT NOT NULL CHECK(variant IN ('saved_original','view','thumb','thumb_fallback')),
        image_sha TEXT NOT NULL, model_sha TEXT NOT NULL, preprocessing_version TEXT NOT NULL,
        vector BLOB NOT NULL CHECK(typeof(vector)='blob' AND length(vector)>0 AND length(vector)%4=0),
        PRIMARY KEY(work_id,page,variant,image_sha,model_sha,preprocessing_version)
    ) WITHOUT ROWID''',
    'ranking_events': '''CREATE TABLE ranking_events(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
        captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, payload TEXT NOT NULL
    )''',
    'ranking_events_no_update': '''CREATE TRIGGER ranking_events_no_update BEFORE UPDATE ON ranking_events
        BEGIN SELECT RAISE(ABORT, 'ranking events are append-only'); END''',
    'ranking_events_no_delete': '''CREATE TRIGGER ranking_events_no_delete BEFORE DELETE ON ranking_events
        WHEN (SELECT purging FROM generation_state WHERE singleton=1) IS NOT 1
        BEGIN SELECT RAISE(ABORT, 'ranking events are append-only'); END''',
    'preference_models': '''CREATE TABLE preference_models(
        generation INTEGER PRIMARY KEY, payload TEXT NOT NULL
    )''',
}
_SCHEMA_ROWS = {
    ('trigger', name, 'ranking_events', sql) if sql.startswith('CREATE TRIGGER ')
    else ('table', name, name, sql)
    for name, sql in _SCHEMA.items()
} | {('table', 'sqlite_sequence', 'sqlite_sequence', 'CREATE TABLE sqlite_sequence(name,seq)')}


if os.name == 'nt':
    class _Overlapped(ctypes.Structure):
        _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                    ('offset', wintypes.DWORD), ('offset_high', wintypes.DWORD),
                    ('event', wintypes.HANDLE)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _lock_file = _kernel.LockFileEx
    _lock_file.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                           wintypes.DWORD, ctypes.POINTER(_Overlapped))
    _lock_file.restype = wintypes.BOOL
    _unlock_file = _kernel.UnlockFileEx
    _unlock_file.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(_Overlapped))
    _unlock_file.restype = wintypes.BOOL


@contextmanager
def _marker_lock(marker, *, exclusive, deadline, offset):
    if os.name != 'nt':
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        while True:
            try:
                fcntl.flock(marker.fileno(), flags | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (EACCES, EAGAIN) or not _pause(deadline):
                    raise
        yield  # Closing the marker releases this open-file lock.
        return

    overlapped = _Overlapped(offset=offset)
    handle = msvcrt.get_osfhandle(marker.fileno())
    flags = (2 if exclusive else 0) | 1  # LOCKFILE_FAIL_IMMEDIATELY
    while not _lock_file(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
        error = ctypes.get_last_error()
        if error != 33 or not _pause(deadline):  # ERROR_LOCK_VIOLATION
            raise ctypes.WinError(error)
    try:
        yield
    finally:
        if not _unlock_file(handle, 0, 1, 0, ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())


def _pause(deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        return False
    sleep(min(_RETRY_INTERVAL, remaining))
    return True


def _busy_timeout(db, deadline):
    db.execute(f'PRAGMA busy_timeout={max(0, int((deadline - monotonic()) * 1000))}')


def _publish_root(staging, root):
    if os.name == 'nt':
        staging.rename(root)  # Windows fails if the destination exists.
        return
    # Linux renameat2 atomically guards an existing, even empty, destination.
    import ctypes as posix_ctypes
    libc = posix_ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('non-replacing directory rename is unavailable')
    rename.argtypes = (posix_ctypes.c_int, posix_ctypes.c_char_p, posix_ctypes.c_int,
                       posix_ctypes.c_char_p, posix_ctypes.c_uint)
    if rename(-100, os.fsencode(staging), -100, os.fsencode(root), 1):  # AT_FDCWD, RENAME_NOREPLACE
        number = posix_ctypes.get_errno()
        raise OSError(number, os.strerror(number))


class VisualPreferenceStoreError(RuntimeError):
    """Storage was unavailable or ownership could not be established."""


class VisualPreferenceFenceChanged(VisualPreferenceStoreError):
    """A concurrent revocation superseded the inputs being captured."""


class _RevocationIOError(VisualPreferenceStoreError):
    """Both valid control records could not be written."""


@dataclass(frozen=True, slots=True)
class _JobFence:
    control: str
    number: int


@dataclass(frozen=True, slots=True)
class RankingEvent:
    sequence: int
    kind: str
    captured_at: str
    payload: dict


@dataclass(frozen=True, slots=True)
class _Journal:
    identity: tuple[int, int]
    nonce: int
    original_pages: int
    hot: bool


def _reject():
    raise VisualPreferenceStoreError('visual preference store is unavailable or unowned')


def _json(value):
    if not isinstance(value, dict):
        raise ValueError('expected an object')
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(',', ':'))


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        _reject()
    return path


def _stat(path, *, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (getattr(info, 'st_file_attributes', 0) & 0x400
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        _reject()
    return info.st_dev, info.st_ino


def _directories(path):
    return tuple(_stat(part, directory=True) for part in (*reversed(path.parents), path))


def _digest(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _validate_lineage(model_sha, preprocessing_version, judgment_fingerprint):
    if (not _digest(model_sha) or not _digest(judgment_fingerprint)
            or type(preprocessing_version) is not str or not preprocessing_version):
        raise ValueError('invalid visual preference lineage')


def _integer_list(value, size):
    return type(value) is list and len(value) == size and all(type(item) is int for item in value)


class VisualPreferenceStore:
    """A dedicated root. Pass the existing DB paths and image roots as protected paths.

    Use a distinct root per profile/account. The caller synchronously invalidates
    on input changes and reads the current generation again once immediately
    before applying the entire loaded snapshot. Each operation closes its connection.
    """

    def __init__(self, root, *, profile: str, account: str, protected_paths):
        try:
            self._attach(root, profile, account, protected_paths, create=True)
            deadline = monotonic() + _IO_TIMEOUT
            with self._locked(exclusive=True, deadline=deadline):
                self._open_database(deadline)
                with self._validated_read(deadline) as db:
                    if db.execute('PRAGMA quick_check(1)').fetchall() != [('ok',)]:
                        _reject()
                if self._read_database_owner() is None:
                    self._record_database_owner()
                if self._epoch() is None:
                    self._declare_revocation()  # Legacy fits are reissued under the new fence.
        except (OSError, sqlite3.Error, ValueError):
            _reject()

    def _attach(self, root, profile, account, protected_paths, *, create):
        if any(type(value) is not str or not value for value in (profile, account)):
            raise ValueError('visual preference scope requires a profile and an account')
        root = logical_path(_absolute(root))
        if os.name == 'nt' and root.drive.upper() != 'S:':
            _reject()
        self.root = native_io_path(root)
        self.path = self.root / _DATABASE
        self._sqlite_filename = sqlite_readonly_uri(self.path, immutable=False).rsplit('?', 1)[0]
        self._marker = self.root / OWNER_MARKER
        self._intent = self.root / (OWNER_MARKER + '.sqlite-intent')
        self._database_owner = self.root / (OWNER_MARKER + '.database-owner')
        self._revocation = self.root / (OWNER_MARKER + '.revocation')
        self._revocation_recovery = self.root / (OWNER_MARKER + '.revocation-recovery')
        self._event_purge = self.root / (OWNER_MARKER + '.event-purge')
        self._binding = self.root / (OWNER_MARKER + '.generation')
        self._fence_failed = False
        self._scope = sha256(json.dumps([profile, account], ensure_ascii=True).encode('ascii')).hexdigest()
        self._check_overlap(root, protected_paths)
        directories = _directories(self.root)
        if any(info is None for info in directories[:-1]):
            _reject()
        if directories[-1] is None and create:
            self._claim_root(directories[:-1])
        self._directory_ids = _directories(self.root)
        if self._directory_ids[:-1] != directories[:-1] or self._directory_ids[-1] is None:
            _reject()
        self._marker_id = _stat(self._marker)
        self._store_id = self._read_marker()

    def _claim_root(self, parents):
        staging = self.root.with_name(f'{self.root.name}.claim-{uuid4().hex}')
        staging.mkdir()
        marker = staging / OWNER_MARKER
        try:
            self._write_new(marker, {'scope': self._scope, 'store_id': uuid4().hex})
            if _directories(self.root.parent) != parents:
                _reject()
            try:
                _publish_root(staging, self.root)
            except OSError:
                if _stat(self.root, directory=True) is None:
                    raise
        finally:
            if _stat(staging, directory=True) is not None:
                marker.unlink(missing_ok=True)
                staging.rmdir()

    @staticmethod
    def _check_overlap(root, protected_paths):
        canonical_root = logical_path(root.resolve())
        for value in protected_paths:
            protected = logical_path(_absolute(value))
            for path in (protected, logical_path(protected.resolve())):
                if canonical_root.is_relative_to(path) or path.is_relative_to(canonical_root):
                    _reject()

    def _read_marker(self):
        if self._marker_id is None or _stat(self._marker) != self._marker_id:
            _reject()
        with self._marker.open('rb', buffering=0) as marker:
            info = os.fstat(marker.fileno())
            if (info.st_dev, info.st_ino) != self._marker_id or info.st_nlink != 1:
                _reject()
            raw = marker.read(_MARKER_LIMIT + 1)
        identity = json.loads(raw)
        if (len(raw) > _MARKER_LIMIT or not isinstance(identity, dict) or set(identity) != {'scope', 'store_id'}
                or identity['scope'] != self._scope or type(identity['store_id']) is not str
                or not _HEX32.fullmatch(identity['store_id'])):
            _reject()
        return identity['store_id']

    @contextmanager
    def _locked(self, *, exclusive=False, deadline, offset=_IO_OFFSET):
        """A bounded shared/exclusive I/O lock. Revocation does not acquire it."""
        self._check_owner()
        with self._marker.open('r+b', buffering=0) as marker:
            info = os.fstat(marker.fileno())
            if (info.st_dev, info.st_ino) != self._marker_id or info.st_nlink != 1:
                _reject()
            with _marker_lock(marker, exclusive=exclusive, deadline=deadline, offset=offset):
                self._check_owner()
                yield

    def _check_owner(self):
        if _directories(self.root) != self._directory_ids or self._read_marker() != self._store_id:
            _reject()

    def _check_paths(self):
        self._check_owner()
        if self._db_id is None or _stat(self.path) != self._db_id:
            _reject()

    def _check_sidecars(self, *, inspect_journal=True):
        for suffix in ('-wal', '-shm'):
            if _stat(self.path.with_name(_DATABASE + suffix)) is not None:
                _reject()
        journal = self.path.with_name(_DATABASE + '-journal')
        identity = _stat(journal)
        if identity is None or not inspect_journal:
            return None
        try:
            with journal.open('rb', buffering=0) as stream:
                info = os.fstat(stream.fileno())
                if (info.st_dev, info.st_ino) != identity or info.st_nlink != 1:
                    _reject()
                header = stream.read(28)
        except FileNotFoundError:
            return None  # A concurrent SQLite writer finalized the journal.
        if not any(header):
            return _Journal(identity, 0, 0, False)
        if len(header) != 28 or header[:8] not in (_JOURNAL_MAGIC, bytes(8)):
            _reject()
        sector, page = struct.unpack('>II', header[20:28])
        if any(not 512 <= size <= 65536 or size & (size - 1) for size in (sector, page)):
            _reject()
        nonce, original_pages = struct.unpack('>II', header[12:20])
        return _Journal(identity, nonce, original_pages, header[:8] == _JOURNAL_MAGIC)

    def _check_database_handle(self, stream):
        info = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino) != self._db_id or info.st_nlink != 1:
            _reject()

    def _check_header(self, stream, *, allow_empty=False):
        self._check_database_handle(stream)
        stream.seek(0)
        header = stream.read(100)
        if not header and allow_empty:
            return False
        if (len(header) != 100 or header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01'
                or int.from_bytes(header[60:64], 'big') != SCHEMA_VERSION
                or int.from_bytes(header[68:72], 'big') != APPLICATION_ID):
            _reject()
        return True

    def _read_record(self, path, fields, *, optional=(), deadline=None):
        deadline = monotonic() + _CONTROL_TIMEOUT if deadline is None else deadline
        while True:
            identity = _stat(path)
            if identity is None:
                return None
            try:
                with _open_shared(path) as stream:
                    info = os.fstat(stream.fileno())
                    if (info.st_dev, info.st_ino) == identity and info.st_nlink == 1:
                        raw = stream.read(_MARKER_LIMIT + 1)
                        break
            except FileNotFoundError:
                pass  # The next probe observes a concurrent replacement.
            if not _pause(deadline):
                _reject()
        value = json.loads(raw)
        required = {'store_id', *fields}
        if (len(raw) > _MARKER_LIMIT or not isinstance(value, dict)
                or not required <= value.keys() <= (required | set(optional))
                or value['store_id'] != self._store_id):
            _reject()
        return value

    @staticmethod
    def _write_new(path, value):
        with path.open('xb') as stream:
            stream.write(_json(value).encode('ascii'))
            stream.flush()
            os.fsync(stream.fileno())

    def _write_record(self, path, value):
        temporary = path.with_name(f'{path.name}.{uuid4().hex}.tmp')
        self._write_new(temporary, {'store_id': self._store_id, **value})
        try:
            self._check_owner()
            _stat(path)
            _replace_retrying(temporary, path, patience=_CONTROL_TIMEOUT)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_database_owner(self):
        value = self._read_record(self._database_owner, {'database'})
        if value is None:
            return None
        if not _integer_list(value['database'], 2):
            _reject()
        return tuple(value['database'])

    def _record_database_owner(self):
        self._write_record(self._database_owner, {'database': list(self._db_id)})

    def _read_intent(self):
        value = self._read_record(self._intent, {'database', 'initializing', 'journal'},
                                  optional={'pending_embedding'})
        if value is None:
            return None
        if (type(value['initializing']) is not bool
                or not _integer_list(value['database'], 2)
                or (not _integer_list(value['journal'], 3)
                    and not (value['initializing'] and value['journal'] is None))):
            _reject()
        pending = value.get('pending_embedding')
        if pending is not None and (value['initializing'] or not isinstance(pending, dict)
                                    or not {'key', 'epoch'} <= set(pending) <= {'key', 'epoch', 'rejected'}
                                    or ('rejected' in pending and pending['rejected'] is not True)
                                    or not _digest(pending['key'])
                                    or type(pending['epoch']) is not str or not _HEX32.fullmatch(pending['epoch'])):
            _reject()
        return value

    def _write_intent(self, journal=None, *, initializing=False, pending=None):
        self._read_intent()  # Keep unknown files or links at the intent path untouched.
        self._write_record(self._intent, {'database': list(self._db_id), 'initializing': initializing,
                                         'journal': [*journal.identity, journal.nonce] if journal else None,
                                         'pending_embedding': pending})

    def _clear_intent(self, deadline):
        journal = self._check_sidecars()
        if journal is not None and journal.hot:
            _reject()  # Only the verified rollback path can consume this.
        intent = self._read_intent()
        if intent is None:
            return
        if intent['database'] != list(self._db_id):
            _reject()
        pending = intent.get('pending_embedding')
        if pending is not None and (pending.get('rejected') or pending['epoch'] != self._epoch()):
            # An interrupted cleanup can leave a cold journal. Settle the
            # original insertion before any later transaction replaces its seal.
            self._remove_unaccepted_embedding(pending, deadline)
        if self._check_sidecars() is None:
            self._intent.unlink()

    def _remove_unaccepted_embedding(self, pending, deadline):
        """Keep the seal until a revoked insertion is removed, even after a crash."""
        self._check_paths()
        with self.path.open('rb') as pinned:
            self._check_header(pinned)
            with closing(self._connect(write=True, deadline=deadline)) as db:
                db.execute('BEGIN IMMEDIATE')
                self._validate_schema(db)
                keys = db.execute(f'SELECT {_EMBEDDING_COLUMNS} FROM candidate_embeddings')
                matching = next((key for key in keys if _key_digest(key) == pending['key']), None)
                keys.close()
                if matching is not None:
                    self._seal_write(db, pending=pending)
                    db.execute(f'DELETE FROM candidate_embeddings WHERE {_EMBEDDING_WHERE}', matching)
                _busy_timeout(db, deadline)
                db.commit()

    def _matches_journal(self, intent, journal, *, initializing=False):
        return (intent is not None and intent['initializing'] == initializing
                and intent['database'] == list(self._db_id)
                and intent['journal'] == [*journal.identity, journal.nonce])

    def _verify_hot_journal(self, journal):
        if not self._matches_journal(self._read_intent(), journal):
            _reject()

    def _connect(self, *, write, deadline, immutable=False):
        mode = 'rw' if write else 'ro'
        uri = f'{self._sqlite_filename}?mode={mode}' + ('&immutable=1' if immutable else '')
        db = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=max(0, deadline - monotonic()))
        try:
            db.execute('PRAGMA trusted_schema=OFF')
        except BaseException:
            db.close()
            raise
        return db

    def _validate_without_replay(self, deadline):
        with closing(self._connect(write=False, deadline=deadline, immutable=True)) as db:
            self._validate_schema(db)

    def _open_database(self, deadline):
        self._db_id = _stat(self.path)
        recorded_owner = self._read_database_owner()
        journal = self._check_sidecars()
        if self._db_id is None:
            if journal is not None:
                _reject()
            with self.path.open('xb') as reserved:
                info = os.fstat(reserved.fileno())
                self._db_id = info.st_dev, info.st_ino
                self._check_paths()
                if self._check_sidecars() is not None:
                    _reject()
                self._record_database_owner()
                with closing(self._connect(write=True, deadline=deadline)) as db:
                    self._initialize(db)
            return

        if recorded_owner is not None and recorded_owner != self._db_id:
            _reject()
        with self.path.open('rb') as pinned:
            self._check_database_handle(pinned)
            self._check_paths()
            intent = self._read_intent()
            interrupted_init = (journal is not None and journal.hot and journal.original_pages == 0
                                and self._matches_journal(intent, journal, initializing=True))
            hot = journal is not None and journal.hot
            if hot and not interrupted_init:
                self._verify_hot_journal(journal)
            # A sealed journal authorizes recovery of this physical DB. Its
            # header, owner and schema are checked in the recovered image.
            initialized = not interrupted_init and (hot or self._check_header(pinned, allow_empty=True))
            if initialized and journal is None:
                self._clear_intent(deadline)
                return
            if initialized and not hot:
                self._validate_without_replay(deadline)
            elif journal is not None and (journal.original_pages != 0 or journal.hot and not interrupted_init):
                if not initialized:
                    _reject()
            if not initialized and recorded_owner is None:
                self._record_database_owner()
            with closing(self._connect(write=True, deadline=deadline)) as db:
                if initialized:
                    db.execute('BEGIN IMMEDIATE')  # SQLite rolls back a hot journal here.
                    self._check_header(pinned)
                    self._validate_schema(db)
                    db.commit()
                else:
                    if interrupted_init:
                        # Consume the old journal before replacing its seal.
                        db.execute('BEGIN IMMEDIATE')
                        db.rollback()
                    self._initialize(db)
            self._clear_intent(deadline)

    def _initialize(self, db):
        self._write_intent(initializing=True)
        db.execute('BEGIN IMMEDIATE')
        self._check_paths()
        self._check_sidecars()
        if db.execute('PRAGMA page_count').fetchone() not in ((0,), (1,)) or db.execute(
                'SELECT 1 FROM sqlite_schema LIMIT 1').fetchone() is not None:
            _reject()
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        self._seal_journal(initializing=True)
        for statement in _SCHEMA.values():
            db.execute(statement)
        db.execute('INSERT INTO owner VALUES (?,?)', (self._scope, self._store_id))
        # A rebuilt empty DB needs a fresh generation range even if its owner
        # marker survived. Burned tokens cannot be replayed into the new DB.
        db.execute('INSERT INTO generation_state(singleton,number) VALUES (1,?)',
                   (uuid4().int >> 66,))
        self._check_paths()
        self._check_sidecars()
        db.commit()
        self._clear_intent(monotonic())

    def _seal_write(self, db, *, pending=None):
        # Materialize the native journal without writing the owner page. The
        # temporary flag is never exposed outside this transaction.
        self._raw_generation(db)
        db.execute('UPDATE generation_state SET purging=1 WHERE singleton=1')
        self._seal_journal(pending=pending)
        db.execute('UPDATE generation_state SET purging=0 WHERE singleton=1')

    def _seal_journal(self, *, initializing=False, pending=None):
        journal = self._check_sidecars()
        if journal is None or journal.hot:
            _reject()
        self._write_intent(journal, initializing=initializing, pending=pending)

    @contextmanager
    def _validated_read(self, deadline, *, inspect_journal=True):
        self._check_paths()
        self._check_sidecars(inspect_journal=inspect_journal)
        with self.path.open('rb') as pinned:
            self._check_header(pinned)
            self._check_paths()
            with closing(self._connect(write=False, deadline=deadline)) as db:
                db.execute('BEGIN')
                _busy_timeout(db, deadline)
                self._validate_schema(db)
                yield db
                self._check_paths()

    @contextmanager
    def _transaction(self, *, write=False, wait=True, expected=None, after_commit=None,
                     embedding_key=None, expected_epoch=None, is_current=None, timeout=None):
        deadline = monotonic() + ((_IO_TIMEOUT if timeout is None else timeout) if wait else 0)
        try:
            with self._locked(exclusive=write, deadline=deadline):
                if not write:
                    with self._validated_read(deadline) as db:
                        yield db
                    return
                self._check_paths()
                if self._check_sidecars() is not None:
                    self._open_database(deadline)
                self._clear_intent(deadline)
                with self.path.open('rb') as pinned:
                    self._check_header(pinned)
                    self._check_paths()
                    settling_rejection = False
                    try:
                        with closing(self._connect(write=True, deadline=deadline)) as db:
                            db.execute('PRAGMA synchronous=FULL')
                            db.execute('PRAGMA secure_delete=ON')
                            _busy_timeout(db, deadline)
                            db.execute('BEGIN IMMEDIATE')
                            self._validate_schema(db)
                            epoch = self._epoch() if embedding_key is not None or expected_epoch is not None else None
                            if expected is not None and self._generation(db) != expected:
                                yield None
                                return
                            if expected_epoch is not None and (self._fence_failed or
                                    _JobFence(epoch, self._generation_number(db)) != expected_epoch):
                                yield None
                                return
                            pending = None
                            if embedding_key is not None and db.execute(
                                    f'SELECT 1 FROM candidate_embeddings WHERE {_EMBEDDING_WHERE}',
                                    embedding_key).fetchone() is None:
                                pending = {'key': _key_digest(embedding_key), 'epoch': epoch}
                            self._seal_write(db, pending=pending)
                            yield db
                            self._check_paths()
                            self._check_sidecars()
                            if ((expected is not None and not self._fence_allows(expected))
                                    or (expected_epoch is not None
                                        and (self._fence_failed or self._epoch() != expected_epoch.control))
                                    or (is_current is not None and not is_current())):
                                db.rollback()
                            else:
                                _busy_timeout(db, deadline)
                                db.commit()
                                if (pending is not None and is_current is not None and not is_current()
                                        and self._epoch() == epoch):
                                    # Keep the pending seal until a stop crossing
                                    # this commit has retired the new insertion.
                                    try:
                                        self._declare_revocation()
                                    except _RevocationIOError:
                                        # Keep a durable negative receipt even if a second
                                        # interruption prevents physical row cleanup.
                                        settling_rejection = True
                                        intent = self._read_intent()
                                        intent.pop('store_id')
                                        intent['pending_embedding'] = dict(pending, rejected=True)
                                        self._write_record(self._intent, intent)
                                        settling_rejection = False
                                if after_commit is not None:
                                    after_commit()
                    except BaseException:
                        try:
                            if not settling_rejection:
                                self._clear_intent(deadline)
                        except (OSError, sqlite3.Error, ValueError, VisualPreferenceStoreError):
                            pass  # Keep the seal for recovery and preserve the original error.
                        raise
                    self._clear_intent(deadline)
        except (OSError, sqlite3.Error, ValueError):
            _reject()

    def _validate_schema(self, db):
        rows = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema').fetchall()
        if (db.execute('PRAGMA application_id').fetchone() != (APPLICATION_ID,)
                or db.execute('PRAGMA user_version').fetchone() != (SCHEMA_VERSION,)
                or len(rows) != len(_SCHEMA_ROWS) or set(rows) != _SCHEMA_ROWS
                or db.execute('SELECT scope,store_id FROM owner LIMIT 2').fetchall()
                != [(self._scope, self._store_id)]):
            _reject()

    def _raw_generation(self, db):
        row = db.execute('SELECT number,model_sha,preprocessing_version,judgment_fingerprint,purging '
                         'FROM generation_state WHERE singleton=1').fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0 or row[4] != 0:
            _reject()
        if row[1] is None:
            return None
        _validate_lineage(*row[1:4])
        return Generation(self._store_id, *row[:4])

    @staticmethod
    def _generation_number(db):
        row = db.execute('SELECT number,purging FROM generation_state WHERE singleton=1').fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0 or row[1] != 0:
            _reject()
        return row[0]

    def _epoch(self, *, deadline=None):
        primary = self._read_epoch(self._revocation, deadline=deadline)
        recovery = self._read_epoch(self._revocation_recovery, deadline=deadline)
        if recovery is None:
            return primary
        # Either record invalidates prior inputs, including when another
        # handle renews the primary while recovery is being published.
        return sha256(f'{primary or ""}:{recovery}'.encode('ascii')).hexdigest()[:32]

    def _read_epoch(self, path, *, deadline):
        value = self._read_record(path, {'epoch'}, deadline=deadline)
        if value is None:
            return None
        if type(value['epoch']) is not str or not _HEX32.fullmatch(value['epoch']):
            _reject()
        return value['epoch']

    def _read_binding(self, *, deadline=None):
        value = self._read_record(self._binding, {'database', 'epoch', 'number'}, deadline=deadline)
        if value is None:
            return None
        epoch = value['epoch']
        if (not _integer_list(value['database'], 2)
                or type(value['number']) is not int or value['number'] < 0
                or (epoch is not None and (type(epoch) is not str or not _HEX32.fullmatch(epoch)))):
            _reject()
        return value

    def _fence_allows(self, expected=None, *, deadline=None):
        self._check_paths()
        if self._fence_failed or (expected is not None and expected.store_id != self._store_id):
            return False
        binding = self._read_binding(deadline=deadline)
        epoch = self._epoch(deadline=deadline)
        if binding is None:
            return False
        return (epoch is not None and binding['database'] == list(self._db_id) and binding['epoch'] == epoch
                and (expected is None or binding['number'] == expected.number))

    def _declare_revocation(self):
        self._check_owner()
        try:
            self._epoch()  # Preserve an unrecognized record.
            renewal = {'epoch': uuid4().hex}
            try:
                self._write_record(self._revocation, renewal)
            except OSError:
                # Windows can prevent replacing a single control file while
                # still allowing a different owned record to be published.
                self._check_owner()
                self._read_epoch(self._revocation_recovery, deadline=None)
                try:
                    self._write_record(self._revocation_recovery, renewal)
                except OSError:
                    self._fence_failed = True
                    raise _RevocationIOError('visual preference revocation records unavailable') from None
                _LOG.warning('visual preference generation fence used recovery record')
        except _RevocationIOError:
            raise
        except (OSError, ValueError, VisualPreferenceStoreError):
            self._fence_failed = True
            _reject()
        self._fence_failed = False

    def _generation(self, db):
        current = self._raw_generation(db)
        return current if current is not None and self._fence_allows(current) else None

    def current_generation(self) -> Generation | None:
        if not self._fence_allows():
            return None
        with self._transaction() as db:
            current = self._generation(db)
        return current if current is not None and self.is_current(current) else None

    def is_current(self, expected: Generation) -> bool:
        """Check one whole snapshot before use, without waiting on a generation change."""
        if not isinstance(expected, Generation):
            return False
        try:
            deadline = monotonic()
            if not self._fence_allows(expected, deadline=deadline):
                return False
            with self._current_reader(expected, deadline) as db:
                if db is None:
                    return False
                current = self._raw_generation(db)
            return current == expected and self._fence_allows(expected, deadline=deadline)
        except (VisualPreferenceStoreError, OSError, sqlite3.Error, ValueError):
            return False

    @contextmanager
    def _current_reader(self, expected, deadline):
        if os.name != 'nt':
            with self._transaction(wait=False) as db:
                yield db
            return
        # A RESERVED SQLite writer permits readers. Use a separate byte range
        # so recovery purge can still drain readers before unlinking any file.
        with self._locked(deadline=deadline, offset=_CURRENT_READ_OFFSET):
            if not self._fence_allows(expected, deadline=deadline):
                yield None
                return
            with self._validated_read(deadline, inspect_journal=False) as db:
                yield db

    def advance_generation(self, *, model_sha, preprocessing_version, judgment_fingerprint,
                           input_epoch: _JobFence | None = None) -> Generation:
        """Bind new inputs. Capture input_epoch before reading labels for a delayed fit."""
        _validate_lineage(model_sha, preprocessing_version, judgment_fingerprint)
        epoch = input_epoch if input_epoch is not None else self.capture_embedding_epoch()

        def bind():
            if not self._fence_failed and self._epoch() == epoch.control:
                self._read_binding()
                self._write_record(self._binding, {'database': list(self._db_id), 'epoch': epoch.control,
                                                  'number': current.number})

        with self._transaction(write=True, after_commit=bind, expected_epoch=epoch) as db:
            if db is None:
                _reject()
            db.execute('UPDATE generation_state SET number=number+1,model_sha=?,'
                       'preprocessing_version=?,judgment_fingerprint=? WHERE singleton=1',
                       (model_sha, preprocessing_version, judgment_fingerprint))
            db.execute('DELETE FROM preference_models')
            current = self._raw_generation(db)
        if not self.is_current(current):
            _reject()
        return current

    def _retire(self, db):
        self._raw_generation(db)
        db.execute('UPDATE generation_state SET number=number+1,model_sha=NULL,'
                   'preprocessing_version=NULL,judgment_fingerprint=NULL WHERE singleton=1')
        db.execute('DELETE FROM preference_models')

    def invalidate(self, reason: str):
        """Retire a generation immediately, including jobs that are still running."""
        if reason not in _INVALIDATIONS:
            raise ValueError('unknown visual preference invalidation')
        self._check_paths()
        try:
            self._declare_revocation()
        except _RevocationIOError:
            # If neither valid control record can be written, retire the
            # published generation in the owned database when it is writable.
            self._epoch()
            with self._transaction(write=True, timeout=_CONTROL_TIMEOUT) as db:
                self._retire(db)
            self._fence_failed = False
            _LOG.warning('visual preference generation fence used database retirement')

    def publish(self, expected: Generation, payload: dict) -> bool:
        """Publish a complete generation. A stale job or a different retry loses."""
        if not isinstance(expected, Generation) or not self._fence_allows(expected):
            return False
        model = ModelSnapshot.from_payload(expected, payload)
        if model is None:
            return False
        encoded = _json(model.to_payload())
        with self._transaction(write=True, expected=expected) as db:
            if db is None:
                return False
            db.execute('INSERT INTO preference_models VALUES (?,?) ON CONFLICT DO NOTHING',
                       (expected.number, encoded))
            published = db.execute('SELECT payload FROM preference_models WHERE generation=?',
                                   (expected.number,)).fetchone() == (encoded,)
        return published and self.is_current(expected)

    def load_model(self, expected: Generation) -> ModelSnapshot | None:
        if not isinstance(expected, Generation) or not self._fence_allows(expected):
            return None
        with self._transaction() as db:
            if self._generation(db) != expected:
                return None
            row = db.execute('SELECT payload FROM preference_models WHERE generation=?',
                             (expected.number,)).fetchone()
            if row is None or type(row[0]) is not str:
                return None
            model = ModelSnapshot.from_payload(expected, json.loads(row[0]))
        return model if self.is_current(expected) else None

    def put_embedding(self, expected: Generation, key: EmbeddingKey, vector) -> bool:
        if not key.matches(expected) or not self._fence_allows(expected):
            return False
        return self._put_embedding(key, vector, expected=expected) and self.is_current(expected)

    def capture_embedding_epoch(self) -> _JobFence:
        """Capture the revocation fence for a shadow job, without issuing a fit."""
        self._check_paths()
        if self._fence_failed:
            _reject()
        epoch = self._epoch()
        deadline = monotonic() + _CONTROL_TIMEOUT
        reader_lock = (self._locked(deadline=deadline, offset=_CURRENT_READ_OFFSET)
                       if os.name == 'nt' else nullcontext())
        try:
            with reader_lock, self._validated_read(deadline, inspect_journal=False) as db:
                number = self._generation_number(db)
            if self._fence_failed or epoch is None:
                _reject()
            if self._epoch() != epoch:
                raise VisualPreferenceFenceChanged('visual preference inputs were superseded')
        except (OSError, sqlite3.Error, ValueError):
            _reject()
        return _JobFence(epoch, number)

    def has_candidate_embedding(self, key: EmbeddingKey) -> bool:
        with self._transaction() as db:
            return db.execute(f'SELECT 1 FROM candidate_embeddings WHERE {_EMBEDDING_WHERE}',
                              _key_values(key)).fetchone() is not None

    def put_candidate_embedding(self, epoch: _JobFence, key: EmbeddingKey, vector, *, is_current=None) -> bool:
        """Record content-derived features, without creating a published model generation."""
        if self.capture_embedding_epoch() != epoch:
            return False
        written = self._put_embedding(key, vector, expected_epoch=epoch, is_current=is_current)
        return (written and self.capture_embedding_epoch() == epoch
                and (is_current is None or is_current()))

    def _put_embedding(self, key, vector, **fence):
        try:
            values = tuple(vector)
            if not values or not all(isfinite(value) for value in values):
                raise ValueError('invalid FP32 embedding')
            encoded = struct.pack(f'<{len(values)}f', *values)
        except (OverflowError, TypeError, struct.error):
            raise ValueError('invalid FP32 embedding') from None
        with self._transaction(write=True, embedding_key=_key_values(key), **fence) as db:
            if db is None:
                return False
            db.execute('INSERT INTO candidate_embeddings VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT DO NOTHING', (*_key_values(key), encoded))
        return True

    def get_embedding(self, expected: Generation, key: EmbeddingKey) -> tuple[float, ...] | None:
        if not key.matches(expected) or not self._fence_allows(expected):
            return None
        with self._transaction() as db:
            if self._generation(db) != expected:
                return None
            row = db.execute('SELECT vector FROM candidate_embeddings WHERE work_id=? AND page=? '
                             'AND variant=? AND image_sha=? AND model_sha=? AND preprocessing_version=?',
                             _key_values(key)).fetchone()
            if row is None or type(row[0]) is not bytes or len(row[0]) % 4:
                return None
            values = tuple(value for (value,) in struct.iter_unpack('<f', row[0]))
        return values if values and all(map(isfinite, values)) and self.is_current(expected) else None

    def capture_event_epoch(self):
        self._check_paths()
        return self._read_epoch(self._event_purge, deadline=None)

    @staticmethod
    def _normalize_event_payload(kind, payload):
        def normalize(row):
            if (isinstance(row, dict) and 'judgment' in row and row['judgment'] is None
                    and row.get('revision', 0) == 0
                    and (row.get('saved') is True or row.get('bookmarked') is True)):
                return dict(row, judgment='')
            return row

        if kind == 'ranking' and isinstance(payload, dict) and isinstance(payload.get('candidates'), list):
            return dict(payload, candidates=[normalize(row) for row in payload['candidates']])
        return normalize(payload)

    def progress(self):
        """Read durable counters for the settings status."""
        with self._transaction() as db:
            count = db.execute('SELECT count(*) FROM candidate_embeddings').fetchone()[0]
            return count, self._generation_number(db)

    def append_event(self, kind: str, payload: dict, epoch=_UNFENCED_EVENT) -> int | None:
        """Append ranking/judgment metadata. The caller supplies only local, unsigned fields."""
        if type(kind) is not str or not kind:
            raise ValueError('invalid visual preference event kind')
        encoded = _json(self._normalize_event_payload(kind, payload))
        with self._transaction(write=True) as db:
            if epoch is not _UNFENCED_EVENT and epoch != self.capture_event_epoch():
                return None
            return db.execute('INSERT INTO ranking_events(kind,payload) VALUES (?,?)',
                              (kind, encoded)).lastrowid

    def events(self) -> tuple[RankingEvent, ...]:
        with self._transaction() as db:
            rows = db.execute('SELECT sequence,kind,captured_at,payload FROM ranking_events ORDER BY sequence')
            return tuple(RankingEvent(sequence, kind, at, self._normalize_event_payload(kind, json.loads(payload)))
                         for sequence, kind, at, payload in rows)

    def purge(self):
        """Erase visual data in place, preserving ownership and the stale-job fence."""
        self._check_paths()
        self._declare_revocation()
        with self._transaction(write=True) as db:
            self._declare_revocation()
            self.capture_event_epoch()
            self._write_record(self._event_purge, {'epoch': uuid4().hex})
            self._retire(db)
            db.execute('DELETE FROM candidate_embeddings')
            db.execute('UPDATE generation_state SET purging=1 WHERE singleton=1')
            db.execute('DELETE FROM ranking_events')
            db.execute('UPDATE generation_state SET purging=0 WHERE singleton=1')

    @classmethod
    def purge_owned(cls, root, *, profile: str, account: str, protected_paths):
        """Reset a proven owned store even when normal opening refuses recovery."""
        store = cls.__new__(cls)
        try:
            store._attach(root, profile, account, protected_paths, create=False)
            deadline = monotonic() + _IO_TIMEOUT
            with store._locked(exclusive=True, deadline=deadline):
                store._db_id = _stat(store.path)
                recorded = store._read_database_owner()
                if store._db_id is not None:
                    if recorded is None:  # Legacy v1: prove ownership without replay.
                        intent = store._read_intent()
                        if intent is None or intent['database'] != list(store._db_id):
                            with store.path.open('rb') as pinned:
                                store._check_header(pinned)
                                store._validate_without_replay(deadline)
                        store._record_database_owner()
                    elif recorded != store._db_id:
                        _reject()
                sidecars = [store.path.with_name(_DATABASE + suffix) for suffix in ('-journal', '-wal', '-shm')]
                targets = [(path, _stat(path)) for path in (*sidecars, store._intent)]
                targets.append((store.path, store._db_id))
                if _stat(store.path) != store._db_id:
                    _reject()
                if store._db_id is None and recorded is None and any(identity for _, identity in targets):
                    _reject()
                store._declare_revocation()
                readers = (store._locked(exclusive=True, deadline=deadline, offset=_CURRENT_READ_OFFSET)
                           if os.name == 'nt' else nullcontext())
                with readers:
                    for path, identity in targets:
                        if identity is not None:
                            store._check_owner()
                            if store._db_id is not None:
                                store._check_paths()
                            if _stat(path) != identity:
                                _reject()
                            path.unlink()
                    store._database_owner.unlink(missing_ok=True)
        except (OSError, sqlite3.Error, ValueError):
            _reject()


def _key_values(key):
    return (key.work_id, key.page, key.variant, key.image_sha, key.model_sha,
            key.preprocessing_version)


def _key_digest(key):
    return sha256(json.dumps(key, ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
