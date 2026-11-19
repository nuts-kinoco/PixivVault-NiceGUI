"""Owned execution ledger for one author batch ("run"), plus the last_downloaded rule.

One run = the original's "実行 1 回": every selected author, one after another.
The ledger lives in its own marked folder (batch-runs.sqlite3) with the same
hardness as following_sync: marker file, one process owner, link refusal,
BEGIN IMMEDIATE and synchronous=FULL. It never opens a legacy DB or a network.

`last_downloaded` rule (original core.py:2254): an author is stamped only when
its whole step returned normally. A stop, a per-author exception or a closed
network gate never stamps; the bookmarks run never stamps. The stamp is written
into following_sync (the owned home of the column) by `complete_author` only.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
from uuid import uuid4

from .account_scope import AccountMismatch, require_same_account
from .fs_paths import native_io_path, sqlite_readonly_uri
from .managed_downloads import _safe_path, _read_bounded, _write, _fsync_dir
from .ugoira_storage import _owner_lock, _OWNER

MARKER = '.pixivvault-batch-runs.json'
IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"batch-runs","version":1}\n'
DATABASE = 'batch-runs.sqlite3'
APPLICATION_ID = 0x50564230
MAX_DB_BYTES = 256 * 1024 * 1024
MAX_RUNS = 5000

RUN_KINDS = ('authors', 'bookmarks')
RUN_ORIGINS = ('manual', 'auto')
RUN_ACTIVE = ('running', 'paused', 'stopping')
RUN_STATES = RUN_ACTIVE + ('stopped', 'done', 'failed', 'interrupted')
AUTHOR_STATES = ('pending', 'listing', 'downloading', 'done', 'failed', 'stopped', 'skipped')
AUTHOR_TERMINAL = ('done', 'failed', 'stopped', 'skipped')
ITEM_STATES = ('queued', 'done', 'error', 'stopped', 'lost')
COUNTERS = ('n_new', 'n_updated', 'n_restored', 'n_missing', 'n_skipped', 'n_unknown',
            'n_failed', 'n_absent_pending', 'n_absent_confirmed')
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_RUN_ID = re.compile(r'[0-9a-f]{32}\Z')
_CODE = re.compile(r'[A-Za-z0-9_.:-]{0,128}\Z')


class BatchRunError(RuntimeError):
    code = 'batch_run_failed'

    def __init__(self, code=None):
        super().__init__(code or self.code)


class BatchRunConflict(BatchRunError):
    code = 'batch_run_conflict'


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    kind: str
    origin: str
    target_type: str
    state: str
    started_utc: str
    finished_utc: str | None
    authors_total: int
    revision: int
    error_code: str
    account_id: str | None


@dataclass(frozen=True, slots=True)
class RunAuthor:
    run_id: str
    seq: int
    author_id: str
    state: str
    n_new: int = 0
    n_updated: int = 0
    n_restored: int = 0
    n_missing: int = 0
    n_skipped: int = 0
    n_unknown: int = 0
    n_failed: int = 0
    n_absent_pending: int = 0
    n_absent_confirmed: int = 0
    scan_id: str | None = None
    deletion_check: str = ''
    started_utc: str | None = None
    finished_utc: str | None = None
    error_code: str = ''
    last_downloaded_utc: str | None = None
    stamp_written: bool = False


@dataclass(frozen=True, slots=True)
class RunItem:
    run_id: str
    author_id: str
    work_key: str
    media: str
    operation_id: str
    job_id: str | None
    state: str


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise BatchRunError()
    return value


def _code(value):
    if type(value) is not str or _CODE.fullmatch(value) is None:
        raise BatchRunError()
    return value


def _run_id(value):
    if type(value) is not str or _RUN_ID.fullmatch(value) is None:
        raise BatchRunError()
    return value


def _count(value):
    if type(value) is not int or not 0 <= value <= 10_000_000:
        raise BatchRunError()
    return value


_RUN_COLUMNS = ('run_id', 'kind', 'origin', 'target_type', 'state', 'started_utc', 'finished_utc',
                'authors_total', 'revision', 'error_code', 'account_id')
_AUTHOR_COLUMNS = ('run_id', 'seq', 'author_id', 'state') + COUNTERS + (
    'scan_id', 'deletion_check', 'started_utc', 'finished_utc', 'error_code',
    'last_downloaded_utc', 'stamp_written')
_ITEM_COLUMNS = ('run_id', 'author_id', 'work_key', 'media', 'operation_id', 'job_id', 'state')

_SCHEMA = f'''
    BEGIN IMMEDIATE;
    PRAGMA application_id={APPLICATION_ID};
    PRAGMA user_version=3;
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ({','.join(repr(k) for k in RUN_KINDS)})),
        origin TEXT NOT NULL CHECK(origin IN ({','.join(repr(k) for k in RUN_ORIGINS)})),
        target_type TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ({','.join(repr(k) for k in RUN_STATES)})),
        started_utc TEXT NOT NULL,
        finished_utc TEXT,
        authors_total INTEGER NOT NULL CHECK(authors_total>=0),
        revision INTEGER NOT NULL CHECK(revision>=1),
        error_code TEXT NOT NULL DEFAULT '',
        account_id TEXT);
    CREATE TABLE run_authors (
        run_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        author_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ({','.join(repr(k) for k in AUTHOR_STATES)})),
        {','.join(c + ' INTEGER NOT NULL DEFAULT 0 CHECK(' + c + '>=0)' for c in COUNTERS)},
        scan_id TEXT,
        deletion_check TEXT NOT NULL DEFAULT '',
        started_utc TEXT,
        finished_utc TEXT,
        error_code TEXT NOT NULL DEFAULT '',
        last_downloaded_utc TEXT,
        stamp_written INTEGER NOT NULL DEFAULT 0 CHECK(stamp_written IN (0,1)),
        PRIMARY KEY(run_id,seq),
        UNIQUE(run_id,author_id),
        FOREIGN KEY(run_id) REFERENCES runs(run_id));
    CREATE TABLE run_items (
        run_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        work_key TEXT NOT NULL,
        media TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        job_id TEXT,
        state TEXT NOT NULL CHECK(state IN ({','.join(repr(k) for k in ITEM_STATES)})),
        PRIMARY KEY(run_id,author_id,work_key),
        FOREIGN KEY(run_id) REFERENCES runs(run_id));
    CREATE TABLE run_flags (
        run_id TEXT PRIMARY KEY,
        discarded INTEGER NOT NULL DEFAULT 0 CHECK(discarded IN (0,1)),
        verified_bookmark_account INTEGER NOT NULL DEFAULT 0 CHECK(verified_bookmark_account IN (0,1)),
        FOREIGN KEY(run_id) REFERENCES runs(run_id));
    COMMIT;
'''

_MIGRATE_1_TO_2 = '''
    BEGIN IMMEDIATE;
    CREATE TABLE run_flags (
        run_id TEXT PRIMARY KEY,
        discarded INTEGER NOT NULL DEFAULT 0 CHECK(discarded IN (0,1)),
        FOREIGN KEY(run_id) REFERENCES runs(run_id));
    PRAGMA user_version=2;
    COMMIT;
'''


_MIGRATE_2_TO_3 = '''
    BEGIN IMMEDIATE;
    ALTER TABLE run_flags ADD COLUMN verified_bookmark_account INTEGER NOT NULL DEFAULT 0
        CHECK(verified_bookmark_account IN (0,1));
    PRAGMA user_version=3;
    COMMIT;
'''


class BatchRunLedger:
    """One process owner per folder; engine and views share this instance."""

    def __init__(self, root, *, clock=None, synchronous='FULL'):
        if synchronous not in ('FULL', 'NORMAL'):  # NORMAL exists for bulk-scale tests only
            raise BatchRunError()
        self._lock = RLock()
        self._closed = False
        self._connection = self._owner = None
        self._validated_at = float('-inf')
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            raw = Path(root)
            if not raw.is_absolute() or '..' in raw.parts or raw == raw.parent:
                raise BatchRunError()
            self.root = native_io_path(raw)
            self.path = self.root / DATABASE
            _safe_path(self.root)
            if not self.root.exists():
                if not self.root.parent.is_dir():
                    raise BatchRunError()
                self.root.mkdir(mode=0o700)
                _write(self.root / MARKER, IDENTITY)
                _fsync_dir(self.root)
            self._check_root()
            owner = _owner_lock(self.root)
            owner.__enter__()
            self._owner = owner
            _safe_path(self.path, file=True)
            existed = self.path.exists()
            if existed:
                if self.path.stat().st_size > MAX_DB_BYTES:
                    raise BatchRunError()
                with closing(sqlite3.connect(sqlite_readonly_uri(self.path), uri=True)) as check:
                    self._verify_schema(check, versions=(1, 2, 3))
            else:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                                     | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                os.close(descriptor)
            self._connection = sqlite3.connect(str(self.path), timeout=0.1,
                                               isolation_level=None, check_same_thread=False)
            self._connection.execute('PRAGMA foreign_keys=ON')
            self._connection.execute('PRAGMA synchronous=' + synchronous)
            if not existed:
                self._connection.executescript(_SCHEMA)
            elif self._connection.execute('PRAGMA user_version').fetchone() == (1,):
                self._connection.executescript(_MIGRATE_1_TO_2)  # adds the discard flag table only
            if self._connection.execute('PRAGMA user_version').fetchone() == (2,):
                self._connection.executescript(_MIGRATE_2_TO_3)
            self._verify_schema(self._connection)
            metadata = self.path.lstat()
            self._identity = metadata.st_dev, metadata.st_ino
            metadata = self.root.lstat()
            self._root_identity = metadata.st_dev, metadata.st_ino
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise BatchRunError() from None

    # -- integrity ---------------------------------------------------------
    def _check_root(self):
        _safe_path(self.root)
        if _read_bounded(self.root / MARKER, len(IDENTITY)) != IDENTITY:
            raise BatchRunError()
        allowed = {MARKER, _OWNER, DATABASE, DATABASE + '-journal'}
        for child in self.root.iterdir():
            if child.name not in allowed:
                raise BatchRunError()
            _safe_path(child, file=True)

    @staticmethod
    def _verify_schema(connection, versions=(3,)):
        version = connection.execute('PRAGMA user_version').fetchone()
        if (connection.execute('PRAGMA application_id').fetchone() != (APPLICATION_ID,)
                or version not in [(v,) for v in versions]):
            raise BatchRunError()
        tables = {r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != ({'runs', 'run_authors', 'run_items'} if version == (1,)
                      else {'runs', 'run_authors', 'run_items', 'run_flags'}):
            raise BatchRunError()
        if version != (1,):
            flags = ('run_id', 'discarded', 'verified_bookmark_account') if version == (3,) else ('run_id', 'discarded')
            if tuple(r[1] for r in connection.execute('PRAGMA table_info(run_flags)')) != flags:
                raise BatchRunError()
        for table, columns in (('runs', _RUN_COLUMNS), ('run_authors', _AUTHOR_COLUMNS),
                               ('run_items', _ITEM_COLUMNS)):
            if tuple(r[1] for r in connection.execute(f'PRAGMA table_info({table})')) != columns:
                raise BatchRunError()

    def _ensure_open(self):
        if self._closed or self._connection is None:
            raise BatchRunError()
        # The link/identity re-check walks every ancestor of the folder; a bulk run writes
        # thousands of rows, so it is repeated at most twice a second, not once per row.
        now = time.monotonic()
        if now - self._validated_at < 0.5:
            return
        self._validated_at = now
        self._check_root()
        metadata = self.root.lstat()
        if (metadata.st_dev, metadata.st_ino) != self._root_identity:
            raise BatchRunError()
        _safe_path(self.path, file=True)
        metadata = self.path.lstat()
        if ((metadata.st_dev, metadata.st_ino) != self._identity
                or metadata.st_size > MAX_DB_BYTES):
            raise BatchRunError()

    def _now(self):
        return self._clock().astimezone(timezone.utc).isoformat()

    def _transaction(self, work):
        """Run `work(connection)` in one BEGIN IMMEDIATE; return its value."""
        with self._lock:
            try:
                self._ensure_open()
                self._connection.execute('BEGIN IMMEDIATE')
                result = work(self._connection)
                self._connection.execute('COMMIT')
                return result
            except BaseException as error:
                try:
                    if self._connection is not None and self._connection.in_transaction:
                        self._connection.execute('ROLLBACK')
                except Exception:
                    try:
                        self.close()
                    except Exception:
                        pass
                    raise BatchRunError() from None
                if isinstance(error, (BatchRunError, AccountMismatch)):
                    raise error from None
                if isinstance(error, Exception):
                    raise BatchRunError() from None
                raise

    def _read(self, work):
        with self._lock:
            try:
                self._ensure_open()
                self._connection.execute('BEGIN')
                try:
                    return work(self._connection)
                finally:
                    self._connection.execute('COMMIT')
            except BatchRunError:
                raise
            except Exception:
                raise BatchRunError() from None

    # -- runs --------------------------------------------------------------
    @staticmethod
    def _run_row(row):
        return RunRecord(*row)

    def begin_run(self, kind, origin, target_type, author_ids, *, account_id=None, verified_bookmark_account=False):
        """Create a `running` run with every author `pending`. One active run at a time."""
        if kind not in RUN_KINDS or origin not in RUN_ORIGINS or target_type not in ('both', 'illust', 'novel', ''):
            raise BatchRunError()
        authors = tuple(dict.fromkeys(_identifier(a) for a in author_ids))
        if account_id is not None:
            account_id = _identifier(account_id)
        if type(verified_bookmark_account) is not bool or (verified_bookmark_account and
                (kind != 'bookmarks' or account_id is None)):
            raise BatchRunError()
        run_id = uuid4().hex
        now = self._now()

        def create(connection):
            if connection.execute(
                    'SELECT 1 FROM runs WHERE state IN (?,?,?) LIMIT 1', RUN_ACTIVE).fetchone():
                raise BatchRunConflict()
            if connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0] >= MAX_RUNS:
                # Oldest finished runs make room; an active run never gets here.
                connection.execute('''DELETE FROM run_items WHERE run_id IN (
                    SELECT run_id FROM runs ORDER BY started_utc LIMIT 100)''')
                connection.execute('''DELETE FROM run_authors WHERE run_id IN (
                    SELECT run_id FROM runs ORDER BY started_utc LIMIT 100)''')
                connection.execute('''DELETE FROM run_flags WHERE run_id IN (
                    SELECT run_id FROM runs ORDER BY started_utc LIMIT 100)''')
                connection.execute('DELETE FROM runs WHERE run_id IN ('
                                   'SELECT run_id FROM runs ORDER BY started_utc LIMIT 100)')
            connection.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,NULL,?,1,?,?)',
                               (run_id, kind, origin, target_type, 'running', now, len(authors), '', account_id))
            if verified_bookmark_account:
                connection.execute('INSERT INTO run_flags(run_id, verified_bookmark_account) VALUES(?,1)', (run_id,))
            connection.executemany(
                'INSERT INTO run_authors(run_id,seq,author_id,state) VALUES(?,?,?,?)',
                [(run_id, seq, author, 'pending') for seq, author in enumerate(authors)])
            return run_id
        return self._transaction(create)

    def run(self, run_id):
        _run_id(run_id)
        row = self._read(lambda c: c.execute(
            'SELECT ' + ','.join(_RUN_COLUMNS) + ' FROM runs WHERE run_id=?', (run_id,)).fetchone())
        return None if row is None else self._run_row(row)

    def runs(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= MAX_RUNS:
            raise BatchRunError()
        rows = self._read(lambda c: c.execute(
            'SELECT ' + ','.join(_RUN_COLUMNS) + ' FROM runs ORDER BY started_utc DESC,rowid DESC LIMIT ?',
            (limit,)).fetchall())
        return tuple(self._run_row(r) for r in rows)

    def active_run(self):
        row = self._read(lambda c: c.execute(
            'SELECT ' + ','.join(_RUN_COLUMNS) + ' FROM runs WHERE state IN (?,?,?) LIMIT 1',
            RUN_ACTIVE).fetchone())
        return None if row is None else self._run_row(row)

    def set_run_state(self, run_id, state, error_code=''):
        """Finish terminal runs; a mismatched account leaves the completion date unset."""
        _run_id(run_id)
        _code(error_code)
        if state not in RUN_STATES:
            raise BatchRunError()
        now = self._now()

        def move(connection):
            row = connection.execute('SELECT state FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise BatchRunError()
            if row[0] not in RUN_ACTIVE:
                if row[0] == state:
                    return
                raise BatchRunConflict()
            finished = now if state not in RUN_ACTIVE and error_code != AccountMismatch.code else None
            connection.execute(
                'UPDATE runs SET state=?,error_code=?,finished_utc=?,revision=revision+1 WHERE run_id=?',
                (state, error_code, finished, run_id))
        self._transaction(move)

    def recover_interrupted(self):
        """After a restart no run can still be active: mark each one `interrupted`.

        Authors that were mid-step become `stopped`; they were never stamped.
        """
        now = self._now()

        def recover(connection):
            ids = [r[0] for r in connection.execute(
                'SELECT run_id FROM runs WHERE state IN (?,?,?)', RUN_ACTIVE)]
            for run_id in ids:
                connection.execute(
                    "UPDATE run_authors SET state='stopped',finished_utc=? WHERE run_id=? "
                    "AND state IN ('listing','downloading')", (now, run_id))
                connection.execute(
                    "UPDATE runs SET state='interrupted',finished_utc=?,revision=revision+1 WHERE run_id=?",
                    (now, run_id))
            return tuple(ids)
        return self._transaction(recover)

    RESUMABLE_STATES = ('stopped', 'interrupted', 'failed')

    def resumable_run(self):
        """The newest manual run, when it ended early with authors left and was not discarded.

        Terminal states are final, so "resume" is a new run seeded from `unfinished_authors`.
        A newer manual run (even a finished one) ends the offer. An automatic ☆ check does not: it
        runs unattended on a timer and must not make the user's interrupted run disappear.
        """
        def find(connection):
            row = connection.execute(
                'SELECT ' + ','.join(_RUN_COLUMNS) + " FROM runs WHERE origin='manual' "
                'ORDER BY started_utc DESC,rowid DESC LIMIT 1').fetchone()
            if row is None or row[4] not in self.RESUMABLE_STATES:
                return None
            if connection.execute('SELECT 1 FROM run_flags WHERE run_id=? AND discarded=1',
                                  (row[0],)).fetchone():
                return None
            if not connection.execute("SELECT 1 FROM run_authors WHERE run_id=? AND state!='done' LIMIT 1",
                                      (row[0],)).fetchone():
                return None
            return row
        row = self._read(find)
        return None if row is None else self._run_row(row)

    def has_verified_bookmark_account(self, run_id):
        _run_id(run_id)
        return bool(self._read(lambda c: c.execute(
            'SELECT 1 FROM run_flags WHERE run_id=? AND verified_bookmark_account=1',
            (run_id,)).fetchone()))

    def unfinished_authors(self, run_id):
        """Author IDs of a run that are not `done`, in the run's order."""
        _run_id(run_id)
        rows = self._read(lambda c: c.execute(
            "SELECT author_id FROM run_authors WHERE run_id=? AND state!='done' ORDER BY seq",
            (run_id,)).fetchall())
        return tuple(r[0] for r in rows)

    def discard_run(self, run_id):
        """The user chose not to resume: stop offering this run."""
        _run_id(run_id)

        def flag(connection):
            if connection.execute('SELECT 1 FROM runs WHERE run_id=?', (run_id,)).fetchone() is None:
                raise BatchRunError()
            connection.execute('INSERT INTO run_flags(run_id,discarded) VALUES(?,1) ON CONFLICT(run_id) DO UPDATE SET discarded=1',
                               (run_id,))
        self._transaction(flag)

    # -- authors -----------------------------------------------------------
    @staticmethod
    def _author_row(row):
        values = list(row)
        values[-1] = bool(values[-1])
        return RunAuthor(*values)

    def authors(self, run_id):
        _run_id(run_id)
        rows = self._read(lambda c: c.execute(
            'SELECT ' + ','.join(_AUTHOR_COLUMNS) + ' FROM run_authors WHERE run_id=? ORDER BY seq',
            (run_id,)).fetchall())
        return tuple(self._author_row(r) for r in rows)

    def update_author(self, run_id, author_id, *, state=None, counters=None, scan_id=None,
                      deletion_check=None, error_code=None):
        """Record progress for one author. `done` is reserved for `complete_author`."""
        _run_id(run_id)
        _identifier(author_id)
        if state is not None and (state not in AUTHOR_STATES or state == 'done'):
            raise BatchRunError()
        counters = {} if counters is None else dict(counters)
        if any(k not in COUNTERS for k in counters):
            raise BatchRunError()
        for value in counters.values():
            _count(value)
        if scan_id is not None and (type(scan_id) is not str or re.fullmatch(r'[0-9a-f]{32}', scan_id) is None):
            raise BatchRunError()
        if deletion_check is not None:
            _code(deletion_check)
        if error_code is not None:
            _code(error_code)
        now = self._now()

        def update(connection):
            self._update_author(connection, run_id, author_id, state, counters, scan_id,
                                deletion_check, error_code, now)
        self._transaction(update)

    @staticmethod
    def _update_author(connection, run_id, author_id, state, counters, scan_id, deletion_check,
                       error_code, now):
        row = connection.execute(
            'SELECT state FROM run_authors WHERE run_id=? AND author_id=?', (run_id, author_id)).fetchone()
        if row is None:
            raise BatchRunError()
        if row[0] in AUTHOR_TERMINAL:
            raise BatchRunConflict()
        sets, values = [], []
        for name, value in counters.items():
            sets.append(name + '=?')
            values.append(value)
        if scan_id is not None:
            sets.append('scan_id=?')
            values.append(scan_id)
        if deletion_check is not None:
            sets.append('deletion_check=?')
            values.append(deletion_check)
        if error_code is not None:
            sets.append('error_code=?')
            values.append(error_code)
        if state is not None:
            sets.append('state=?')
            values.append(state)
            if row[0] == 'pending' and state != 'pending':
                sets.append('started_utc=?')
                values.append(now)
            if state in AUTHOR_TERMINAL:
                sets.append('finished_utc=?')
                values.append(now)
        if sets:
            connection.execute('UPDATE run_authors SET ' + ','.join(sets) + ' WHERE run_id=? AND author_id=?',
                               (*values, run_id, author_id))

    def complete_author(self, run_id, author_id, counters=None, *, following=None, account_id=None):
        """The author's step returned normally: mark it `done` and stamp last_downloaded.

        The stamp goes to following_sync only for an `authors` run, only when
        the run is still running or paused (not stopping), and only when the
        account already has a row for the author. Returns the stamp or None.
        """
        _run_id(run_id)
        _identifier(author_id)
        counters = {} if counters is None else dict(counters)
        if any(k not in COUNTERS for k in counters):
            raise BatchRunError()
        for value in counters.values():
            _count(value)
        stamp = self._now()

        def complete(connection):
            run = connection.execute('SELECT kind,state,account_id FROM runs WHERE run_id=?',
                                     (run_id,)).fetchone()
            if run is None:
                raise BatchRunError()
            kind, run_state, run_account = run
            if account_id is not None:
                require_same_account(run_account, account_id)
            row = connection.execute('SELECT state FROM run_authors WHERE run_id=? AND author_id=?',
                                     (run_id, author_id)).fetchone()
            if row is None or row[0] in AUTHOR_TERMINAL:
                raise BatchRunConflict()
            stamping = kind == 'authors' and run_state in ('running', 'paused')
            sets = ['state=?', 'finished_utc=?'] + [name + '=?' for name in counters]
            values = ['done', stamp, *counters.values()]
            if stamping:
                sets.append('last_downloaded_utc=?')
                values.append(stamp)
            connection.execute('UPDATE run_authors SET ' + ','.join(sets) + ' WHERE run_id=? AND author_id=?',
                               (*values, run_id, author_id))
            connection.execute("UPDATE run_authors SET started_utc=COALESCE(started_utc,?) "
                               "WHERE run_id=? AND author_id=?", (stamp, run_id, author_id))
            return stamping, run_account
        stamping, run_account = self._transaction(complete)
        if not stamping or following is None:
            return None
        if run_account is None:
            return None
        try:
            following.record_downloaded(run_account, author_id, stamp)
        except Exception:
            return None  # the author is recorded done; stamp_written stays 0 and shows it
        self._transaction(lambda c: c.execute(
            'UPDATE run_authors SET stamp_written=1 WHERE run_id=? AND author_id=?', (run_id, author_id)))
        return stamp

    def finish_author(self, run_id, author_id, state, *, counters=None, error_code=''):
        """Terminal non-`done` outcome (failed / stopped / skipped): never stamps."""
        if state not in ('failed', 'stopped', 'skipped'):
            raise BatchRunError()
        self.update_author(run_id, author_id, state=state, counters=counters, error_code=error_code)

    # -- items -------------------------------------------------------------
    def record_item(self, run_id, author_id, work_key, media, operation_id, *, job_id=None, state='queued'):
        _run_id(run_id)
        _identifier(author_id)
        for value, limit in ((work_key, 64), (media, 32), (operation_id, 128)):
            if type(value) is not str or not 1 <= len(value) <= limit:
                raise BatchRunError()
        if state not in ITEM_STATES or (job_id is not None and (type(job_id) is not str or len(job_id) > 64)):
            raise BatchRunError()

        def record(connection):
            connection.execute(
                'INSERT INTO run_items VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,author_id,work_key) '
                'DO UPDATE SET job_id=COALESCE(excluded.job_id,job_id),state=excluded.state,'
                'operation_id=excluded.operation_id',
                (run_id, author_id, work_key, media, operation_id, job_id, state))
        self._transaction(record)

    def record_items(self, run_id, author_id, rows):
        """Many `record_item` rows in one transaction: (work_key, media, operation_id, job_id, state)."""
        _run_id(run_id)
        _identifier(author_id)
        clean = []
        for work_key, media, operation_id, job_id, state in rows:
            for value, limit in ((work_key, 64), (media, 32), (operation_id, 128)):
                if type(value) is not str or not 1 <= len(value) <= limit:
                    raise BatchRunError()
            if state not in ITEM_STATES or (job_id is not None and (type(job_id) is not str or len(job_id) > 64)):
                raise BatchRunError()
            clean.append((run_id, author_id, work_key, media, operation_id, job_id, state))
        if not clean:
            return

        def record(connection):
            connection.executemany(
                'INSERT INTO run_items VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,author_id,work_key) '
                'DO UPDATE SET job_id=COALESCE(excluded.job_id,job_id),state=excluded.state,'
                'operation_id=excluded.operation_id', clean)
        self._transaction(record)

    def items(self, run_id, author_id=None):
        _run_id(run_id)
        sql = 'SELECT ' + ','.join(_ITEM_COLUMNS) + ' FROM run_items WHERE run_id=?'
        args = [run_id]
        if author_id is not None:
            sql += ' AND author_id=?'
            args.append(_identifier(author_id))
        rows = self._read(lambda c: c.execute(sql + ' ORDER BY rowid', args).fetchall())
        return tuple(RunItem(*r) for r in rows)

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._connection is not None:
                    self._connection.close()
            finally:
                self._connection = None
                owner, self._owner = self._owner, None
                if owner is not None:
                    owner.__exit__(None, None, None)


def saved_counts_by_author(receipts):
    """The original table's 保存済み: per-author count over managed saved receipts.

    `receipts` is `managed_downloads.saved_receipts_with_dates(...)` output
    (`(relative, receipt, digest, date)`) or any iterable of such tuples.
    """
    counts = Counter()
    for item in receipts:
        author = item[1].get('author_id')
        if type(author) is str and _ID.fullmatch(author):
            counts[author] += 1
    return dict(counts)
