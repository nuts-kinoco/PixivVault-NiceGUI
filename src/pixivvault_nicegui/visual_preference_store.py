"""SQLite storage owned exclusively by one profile/account's visual learner."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
from uuid import uuid4

if os.name == 'nt':
    import msvcrt
else:
    import fcntl

from .fs_paths import logical_path, native_io_path, sqlite_readonly_uri
from .visual_preference import EmbeddingKey, Generation, ModelSnapshot


OWNER_MARKER = '.pixivvault-visual-preference-v1'
APPLICATION_ID = 0x50565650  # PVVP
SCHEMA_VERSION = 1
_DATABASE = 'visual_preference.sqlite3'
_HEX64 = re.compile(r'[0-9a-f]{64}')
_HEX32 = re.compile(r'[0-9a-f]{32}')
_MARKER_LIMIT = 512
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


class VisualPreferenceStoreError(RuntimeError):
    """Storage was unavailable or ownership could not be established."""


@dataclass(frozen=True, slots=True)
class RankingEvent:
    sequence: int
    kind: str
    captured_at: str
    payload: dict


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


class VisualPreferenceStore:
    """A dedicated root. Pass the existing DB paths and image roots as protected paths.

    Use a distinct root per profile/account. The caller synchronously invalidates
    on input changes and reads the current generation again immediately before
    applying a loaded snapshot. Each operation owns and closes its connection.
    """

    def __init__(self, root, *, profile: str, account: str, protected_paths):
        if any(type(value) is not str or not value for value in (profile, account)):
            raise ValueError('visual preference scope requires a profile and an account')
        root = logical_path(_absolute(root))
        if os.name == 'nt' and root.drive.upper() != 'S:':
            _reject()
        self.root = native_io_path(root)
        self.path = self.root / _DATABASE
        self._sqlite_filename = sqlite_readonly_uri(self.path, immutable=False).rsplit('?', 1)[0]
        self._marker = self.root / OWNER_MARKER
        self._scope = sha256(json.dumps([profile, account], ensure_ascii=True).encode('ascii')).hexdigest()
        try:
            self._check_overlap(root, protected_paths)
            directories = _directories(self.root)
            if any(info is None for info in directories[:-1]):
                _reject()
            fresh = directories[-1] is None
            if fresh:
                self.root.mkdir()
                identity = _json({'scope': self._scope, 'store_id': uuid4().hex}).encode('ascii')
                with self._marker.open('xb') as marker:
                    marker.write(identity)
                    marker.flush()
                    os.fsync(marker.fileno())
            self._directory_ids = _directories(self.root)
            if self._directory_ids[:-1] != directories[:-1]:
                _reject()
            self._marker_id = _stat(self._marker)
            self._store_id = self._read_marker()
            with self._locked():
                self._db_id = _stat(self.path)
                self._check_sidecars()
                if fresh:
                    if self._db_id is not None:
                        _reject()
                    # Reserve the filename exclusively before SQLite sees it.
                    with self.path.open('xb') as reserved:
                        info = os.fstat(reserved.fileno())
                        self._db_id = info.st_dev, info.st_ino
                        self._check_paths()
                        self._check_sidecars()
                        with closing(self._connect(write=True)) as db:
                            self._initialize(db)
            with self._transaction() as db:
                if db.execute('PRAGMA quick_check(1)').fetchall() != [('ok',)]:
                    _reject()
        except (OSError, sqlite3.Error, ValueError):
            _reject()

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
    def _locked(self, *, wait=True):
        """Serialize path checks and SQLite I/O across handles and processes."""
        if _directories(self.root) != self._directory_ids or self._read_marker() != self._store_id:
            _reject()
        with self._marker.open('r+b', buffering=0) as marker:
            info = os.fstat(marker.fileno())
            if (info.st_dev, info.st_ino) != self._marker_id or info.st_nlink != 1:
                _reject()
            # A lock beyond EOF lets other handles read the marker while waiting.
            if os.name == 'nt':
                marker.seek(_MARKER_LIMIT + 1)
                msvcrt.locking(marker.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            else:
                flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(marker.fileno(), flags)
            try:
                if _directories(self.root) != self._directory_ids or self._read_marker() != self._store_id:
                    _reject()
                yield
            finally:
                if os.name == 'nt':
                    marker.seek(_MARKER_LIMIT + 1)
                    msvcrt.locking(marker.fileno(), msvcrt.LK_UNLCK, 1)

    def _check_paths(self):
        if (_directories(self.root) != self._directory_ids or self._read_marker() != self._store_id
                or self._db_id is None or _stat(self.path) != self._db_id):
            _reject()

    def _check_sidecars(self, *, own_transaction=False):
        for suffix in ('-wal', '-shm', '-journal'):
            if _stat(self.path.with_name(_DATABASE + suffix)) is not None and (
                    suffix != '-journal' or not own_transaction):
                _reject()

    def _check_header(self, stream):
        # Check before opening SQLite. A foreign rollback journal or WAL must
        # never be replayed, even during a read of a foreign or corrupt file.
        info = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino) != self._db_id or info.st_nlink != 1:
            _reject()
        header = stream.read(100)
        if (len(header) != 100 or header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01'
                or int.from_bytes(header[60:64], 'big') != SCHEMA_VERSION
                or int.from_bytes(header[68:72], 'big') != APPLICATION_ID):
            _reject()

    def _connect(self, *, write):
        mode = 'rw' if write else 'ro'
        return sqlite3.connect(f'{self._sqlite_filename}?mode={mode}', uri=True, isolation_level=None)

    def _initialize(self, db):
        db.execute('BEGIN IMMEDIATE')
        self._check_paths()
        self._check_sidecars(own_transaction=True)
        if db.execute('PRAGMA page_count').fetchone() not in ((0,), (1,)) or db.execute(
                'SELECT 1 FROM sqlite_schema LIMIT 1').fetchone() is not None:
            _reject()
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
        db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        for statement in _SCHEMA.values():
            db.execute(statement)
        db.execute('INSERT INTO owner VALUES (?,?)', (self._scope, self._store_id))
        db.execute('INSERT INTO generation_state(singleton,number) VALUES (1,0)')
        self._check_paths()
        self._check_sidecars(own_transaction=True)
        db.commit()

    @contextmanager
    def _transaction(self, *, write=False, wait=True):
        try:
            with self._locked(wait=wait):
                self._check_paths()
                self._check_sidecars()
                # Keep the checked database handle open during SQLite I/O.
                with self.path.open('rb') as pinned:
                    self._check_header(pinned)
                    self._check_paths()
                    with closing(self._connect(write=write)) as db:
                        db.execute('PRAGMA trusted_schema=OFF')
                        if write:
                            db.execute('PRAGMA synchronous=FULL')
                            db.execute('PRAGMA secure_delete=ON')
                        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                        self._check_paths()
                        self._check_sidecars()
                        self._validate_schema(db)
                        yield db
                        self._check_paths()
                        self._check_sidecars(own_transaction=write)
                        db.commit()
        except (OSError, sqlite3.Error, ValueError):
            _reject()

    def _validate_schema(self, db):
        if (db.execute('PRAGMA application_id').fetchone() != (APPLICATION_ID,)
                or db.execute('PRAGMA user_version').fetchone() != (SCHEMA_VERSION,)
                or dict(db.execute('SELECT name,sql FROM sqlite_schema WHERE name != ?',
                                   ('sqlite_sequence',))) != _SCHEMA
                or db.execute('SELECT scope,store_id FROM owner LIMIT 2').fetchall()
                != [(self._scope, self._store_id)]):
            _reject()

    def _generation(self, db):
        row = db.execute('SELECT number,model_sha,preprocessing_version,judgment_fingerprint,purging '
                         'FROM generation_state WHERE singleton=1').fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0 or row[4] != 0:
            _reject()
        if row[1] is None:
            return None
        _validate_lineage(*row[1:4])
        return Generation(self._store_id, *row[:4])

    def current_generation(self) -> Generation | None:
        with self._transaction() as db:
            return self._generation(db)

    def is_current(self, expected: Generation) -> bool:
        """Recheck a loaded snapshot before UI application, without waiting for a writer."""
        if not isinstance(expected, Generation):
            return False
        try:
            with self._transaction(wait=False) as db:
                return self._generation(db) == expected
        except VisualPreferenceStoreError:
            return False

    def advance_generation(self, *, model_sha, preprocessing_version, judgment_fingerprint) -> Generation:
        """Set a new lineage and retire all previously published models atomically."""
        _validate_lineage(model_sha, preprocessing_version, judgment_fingerprint)
        with self._transaction(write=True) as db:
            self._generation(db)
            db.execute('UPDATE generation_state SET number=number+1,model_sha=?,'
                       'preprocessing_version=?,judgment_fingerprint=? WHERE singleton=1',
                       (model_sha, preprocessing_version, judgment_fingerprint))
            db.execute('DELETE FROM preference_models')
            return self._generation(db)

    def _retire(self, db):
        self._generation(db)
        db.execute('UPDATE generation_state SET number=number+1,model_sha=NULL,'
                   'preprocessing_version=NULL,judgment_fingerprint=NULL WHERE singleton=1')
        db.execute('DELETE FROM preference_models')

    def invalidate(self, reason: str):
        """Retire a generation immediately, including jobs that are still running."""
        if reason not in _INVALIDATIONS:
            raise ValueError('unknown visual preference invalidation')
        with self._transaction(write=True) as db:
            self._retire(db)

    def publish(self, expected: Generation, payload: dict) -> bool:
        """Publish a complete generation. A stale job or a different retry loses."""
        if not isinstance(expected, Generation):
            return False
        model = ModelSnapshot.from_payload(expected, payload)
        if model is None:
            return False
        encoded = _json(model.to_payload())
        with self._transaction(write=True) as db:
            if self._generation(db) != expected:
                return False
            db.execute('INSERT INTO preference_models VALUES (?,?) ON CONFLICT DO NOTHING',
                       (expected.number, encoded))
            return db.execute('SELECT payload FROM preference_models WHERE generation=?',
                              (expected.number,)).fetchone() == (encoded,)

    def load_model(self, expected: Generation) -> ModelSnapshot | None:
        with self._transaction() as db:
            if not isinstance(expected, Generation) or self._generation(db) != expected:
                return None
            row = db.execute('SELECT payload FROM preference_models WHERE generation=?',
                             (expected.number,)).fetchone()
            if row is None or type(row[0]) is not str:
                return None
            return ModelSnapshot.from_payload(expected, json.loads(row[0]))

    def put_embedding(self, expected: Generation, key: EmbeddingKey, vector) -> bool:
        if not key.matches(expected):
            return False
        try:
            values = tuple(vector)
            if not values or not all(isfinite(value) for value in values):
                raise ValueError('invalid FP32 embedding')
            encoded = struct.pack(f'<{len(values)}f', *values)
        except (OverflowError, TypeError, struct.error):
            raise ValueError('invalid FP32 embedding') from None
        with self._transaction(write=True) as db:
            if self._generation(db) != expected:
                return False
            db.execute('INSERT INTO candidate_embeddings VALUES (?,?,?,?,?,?,?) '
                       'ON CONFLICT DO NOTHING', (*_key_values(key), encoded))
            return True

    def get_embedding(self, expected: Generation, key: EmbeddingKey) -> tuple[float, ...] | None:
        if not key.matches(expected):
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
            return values if values and all(map(isfinite, values)) else None

    def append_event(self, kind: str, payload: dict) -> int:
        """Append ranking/judgment metadata. The caller supplies only local, unsigned fields."""
        if type(kind) is not str or not kind:
            raise ValueError('invalid visual preference event kind')
        encoded = _json(payload)
        with self._transaction(write=True) as db:
            return db.execute('INSERT INTO ranking_events(kind,payload) VALUES (?,?)',
                              (kind, encoded)).lastrowid

    def events(self) -> tuple[RankingEvent, ...]:
        with self._transaction() as db:
            rows = db.execute('SELECT sequence,kind,captured_at,payload FROM ranking_events ORDER BY sequence')
            return tuple(RankingEvent(sequence, kind, at, json.loads(payload))
                         for sequence, kind, at, payload in rows)

    def purge(self):
        """Erase visual data in place, preserving ownership and the stale-job fence."""
        with self._transaction(write=True) as db:
            self._retire(db)
            db.execute('DELETE FROM candidate_embeddings')
            db.execute('UPDATE generation_state SET purging=1 WHERE singleton=1')
            db.execute('DELETE FROM ranking_events')
            db.execute('UPDATE generation_state SET purging=0 WHERE singleton=1')


def _key_values(key):
    return (key.work_id, key.page, key.variant, key.image_sha, key.model_sha,
            key.preprocessing_version)
