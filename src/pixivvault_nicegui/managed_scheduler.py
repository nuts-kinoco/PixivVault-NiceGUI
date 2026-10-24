"""Isolated opt-in scheduling policy for managed-only author checks.

The source PixivVault database, credentials and media are never opened here.
Dispatch is injected; only an explicit caller may turn a due schedule into a
managed JobService enqueue. This module does not perform network requests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock
from threading import Event, Thread, current_thread
from typing import Callable
from uuid import uuid4


VALID_INTERVALS = frozenset({0, 6, 12, 24, 48})


class ScheduleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduleState:
    interval_hours: int
    startup_check: bool
    revision: int
    last_due_utc: str | None
    next_due_utc: str | None
    last_status: str


def _utc(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('timezone-aware datetime required')
    return now.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec='microseconds')


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _safe_path(path: Path) -> None:
    if not path.is_absolute():
        raise ScheduleError('schedule database path must be absolute')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ScheduleError('schedule database symlinks are forbidden')
        try:
            st = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ScheduleError('schedule database reparse points are forbidden')
        if item == path and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
            raise ScheduleError('schedule database must be a private regular file')


class ManagedScheduler:
    """Durable UTC due-time ledger; no background thread until opted in.

    Claiming a due check atomically advances its next eligible time, so a
    restart or concurrent browser cannot dispatch that same slot again.
    An interrupted dispatch remains explicitly recorded as `interrupted`.
    """

    def __init__(self, managed_root: Path):
        root = Path(managed_root)
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise ScheduleError('scheduler requires existing isolated managed root')
        self.path = root / '.pixivvault-managed-scheduler.sqlite3'
        _safe_path(self.path)
        self._lock = RLock()
        self._closed = False
        self._file_owner = None
        try:
            # This ledger is a single-writer process-owned resource, exactly
            # like the existing JobService SQLite. Never create a second
            # scheduler around the same managed root in another process.
            lock_path = root / '.pixivvault-managed-scheduler.lock'
            _safe_path(lock_path)
            self._file_owner = lock_path.open('a+b')
            self._file_owner.seek(0)
            if os.name == 'nt':
                import msvcrt
                if not self._file_owner.read(1):
                    self._file_owner.write(b'0')
                    self._file_owner.flush()
                self._file_owner.seek(0)
                msvcrt.locking(self._file_owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file_owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._db = sqlite3.connect(self.path, timeout=2, check_same_thread=False, isolation_level=None)
            self._db.execute('PRAGMA busy_timeout=2000')
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.execute('BEGIN IMMEDIATE')
            self._db.execute('CREATE TABLE IF NOT EXISTS schedule (id INTEGER PRIMARY KEY CHECK(id=1), interval_hours INTEGER NOT NULL, startup_check INTEGER NOT NULL, revision INTEGER NOT NULL, last_due_utc TEXT, next_due_utc TEXT, last_status TEXT NOT NULL)')
            self._db.execute('INSERT OR IGNORE INTO schedule VALUES (1,24,0,1,NULL,NULL,\'never\')')
            self._db.execute('CREATE TABLE IF NOT EXISTS dispatch_history (slot TEXT PRIMARY KEY, status TEXT NOT NULL, started_utc TEXT NOT NULL, finished_utc TEXT, authors INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0, deferred INTEGER NOT NULL DEFAULT 0, error_code TEXT)')
            self._db.execute('CREATE TABLE IF NOT EXISTS dispatch_items (slot TEXT NOT NULL, work_id TEXT NOT NULL, author_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, job_id TEXT, accepted_fingerprint TEXT, state TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(slot,work_id))')
            item_columns = {row[1] for row in self._db.execute('PRAGMA table_info(dispatch_items)')}
            if 'accepted_fingerprint' not in item_columns:
                self._db.execute('ALTER TABLE dispatch_items ADD COLUMN accepted_fingerprint TEXT')
            self._db.execute('CREATE TABLE IF NOT EXISTS deferred_work (author_id TEXT NOT NULL, work_id TEXT NOT NULL, update_date TEXT NOT NULL, page_count INTEGER NOT NULL, work_type INTEGER NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(author_id,work_id))')
            self._db.execute('CREATE TABLE IF NOT EXISTS favourite_authors (author_id TEXT PRIMARY KEY)')
            # Older ledgers could contain deferred rows for authors removed
            # during a live dispatch. They must not wake a runner forever.
            self._db.execute('DELETE FROM deferred_work WHERE author_id NOT IN (SELECT author_id FROM favourite_authors)')
            row = self._db.execute('SELECT interval_hours,startup_check,revision,last_due_utc,next_due_utc,last_status FROM schedule WHERE id=1').fetchone()
            if row is None or row[0] not in VALID_INTERVALS or row[1] not in (0,1) or row[2] < 1:
                raise ScheduleError('invalid managed schedule ledger')
            self._db.execute('COMMIT')
        except BaseException:
            if hasattr(self, '_db'):
                try:
                    self._db.execute('ROLLBACK')
                except sqlite3.Error:
                    pass
                self._db.close()
            if self._file_owner is not None:
                self._file_owner.close()
            raise

    def snapshot(self) -> ScheduleState:
        with self._lock:
            self._ensure_open()
            row = self._db.execute('SELECT interval_hours,startup_check,revision,last_due_utc,next_due_utc,last_status FROM schedule WHERE id=1').fetchone()
            return ScheduleState(row[0], bool(row[1]), row[2], row[3], row[4], row[5])

    def _ensure_open(self):
        if self._closed:
            raise ScheduleError('scheduler is closed')

    def configure(self, *, interval_hours: int, startup_check: bool, expected_revision: int, now: datetime) -> ScheduleState:
        if type(interval_hours) is not int or interval_hours not in VALID_INTERVALS or type(startup_check) is not bool:
            raise ValueError('unsupported interval or startup setting')
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('invalid schedule revision')
        current_time = _utc(now)
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                prior = self.snapshot()
                if expected_revision != prior.revision:
                    raise ScheduleError('schedule changed in another client')
                due = (None if interval_hours == 0 else _timestamp(current_time + timedelta(hours=interval_hours)))
                self._db.execute('UPDATE schedule SET interval_hours=?,startup_check=?,revision=?,next_due_utc=? WHERE id=1',
                                 (interval_hours, int(startup_check), prior.revision + 1, due))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            return self.snapshot()

    def claim_due(self, now: datetime, *, startup: bool = False) -> str | None:
        current_time = _utc(now)
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                state = self.snapshot()
                if state.last_status == 'interrupted':
                    # Do not dispatch another slot while an accepted earlier
                    # dispatch has no durable completion acknowledgement.
                    self._db.execute('COMMIT')
                    return None
                if startup:
                    eligible = state.startup_check
                else:
                    due = _parse(state.next_due_utc)
                    eligible = state.interval_hours > 0 and due is not None and current_time >= due
                if not eligible:
                    self._db.execute('COMMIT')
                    return None
                slot = _timestamp(current_time)
                next_due = (None if state.interval_hours == 0 else
                            _timestamp(current_time + timedelta(hours=state.interval_hours)))
                self._db.execute('UPDATE schedule SET revision=?,last_due_utc=?,next_due_utc=?,last_status=? WHERE id=1',
                                 (state.revision + 1, slot, next_due, 'interrupted'))
                self._db.execute('COMMIT')
                return slot
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def complete(self, slot: str, *, succeeded: bool) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                state = self.snapshot()
                if state.last_due_utc != slot or state.last_status != 'interrupted':
                    raise ScheduleError('stale schedule completion')
                self._db.execute('UPDATE schedule SET last_status=?,revision=? WHERE id=1',
                                 ('queued' if succeeded else 'error', state.revision + 1))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def acknowledge_interrupted(self, *, expected_revision: int) -> ScheduleState:
        """Explicitly acknowledge ambiguous crash recovery without replaying it."""
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                state = self.snapshot()
                if state.revision != expected_revision or state.last_status != 'interrupted':
                    raise ScheduleError('schedule acknowledgement changed')
                self._db.execute("UPDATE schedule SET last_status='unknown',revision=? WHERE id=1",
                                 (state.revision + 1,))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            return self.snapshot()

    def begin_dispatch(self, slot: str, now: datetime, authors: int) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute('INSERT OR IGNORE INTO dispatch_history(slot,status,started_utc,authors) VALUES(?,?,?,?)',
                             (slot, 'running', _timestamp(now), authors))

    def finish_dispatch(self, slot: str, *, status: str, accepted: int, deferred: int,
                        error_code: str | None = None, now: datetime | None = None) -> None:
        if status not in {'queued', 'failed', 'interrupted', 'empty'}:
            raise ValueError('invalid dispatch state')
        with self._lock:
            self._ensure_open()
            self._db.execute('UPDATE dispatch_history SET status=?,finished_utc=?,accepted=?,deferred=?,error_code=? WHERE slot=?',
                             (status, _timestamp(now or datetime.now(timezone.utc)), accepted, deferred,
                              error_code, slot))

    def prepare_dispatch_item(self, *, slot: str, work_id: str, author_id: str,
                              operation_id: str) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute('INSERT OR IGNORE INTO dispatch_items(slot,work_id,author_id,operation_id,job_id,state) VALUES(?,?,?,?,NULL,?)',
                             (slot, work_id, author_id, operation_id, 'submitting'))

    def bind_dispatch_job(self, *, slot: str, work_id: str, operation_id: str,
                          job_id: str, accepted_fingerprint: str) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute("UPDATE dispatch_items SET job_id=?,accepted_fingerprint=?,state='queued' WHERE slot=? AND work_id=? AND operation_id=?",
                             (job_id, accepted_fingerprint, slot, work_id, operation_id))

    def fail_dispatch_item(self, *, slot: str, work_id: str, operation_id: str) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute("UPDATE dispatch_items SET state='failed' WHERE slot=? AND work_id=? AND operation_id=? AND job_id IS NULL",
                             (slot, work_id, operation_id))

    def defer_work(self, *, author_id: str, work: dict, now: datetime) -> None:
        with self._lock:
            self._ensure_open()
            # The favourite list may have changed while an author's complete
            # inventory was being fetched. Do not recreate a row pruned by a
            # concurrent settings transaction.
            if self._db.execute('SELECT 1 FROM favourite_authors WHERE author_id=?', (author_id,)).fetchone() is None:
                return
            self._db.execute('INSERT INTO deferred_work(author_id,work_id,update_date,page_count,work_type,created_utc) VALUES(?,?,?,?,?,?) ON CONFLICT(author_id,work_id) DO UPDATE SET update_date=excluded.update_date,page_count=excluded.page_count,work_type=excluded.work_type',
                             (author_id, work['id'], work['update_date'], work['page_count'], work['type'], _timestamp(now)))

    def deferred_ids(self, author_id: str) -> frozenset[str]:
        with self._lock:
            self._ensure_open()
            return frozenset(row[0] for row in self._db.execute('SELECT work_id FROM deferred_work WHERE author_id=?', (author_id,)))

    def has_deferred(self) -> bool:
        with self._lock:
            self._ensure_open()
            return self._db.execute('SELECT 1 FROM deferred_work LIMIT 1').fetchone() is not None

    def deferred_count(self) -> int:
        with self._lock:
            self._ensure_open()
            return self._db.execute('SELECT COUNT(*) FROM deferred_work').fetchone()[0]

    def resolve_deferred(self, author_id: str, work_id: str) -> None:
        with self._lock:
            self._ensure_open()
            self._db.execute('DELETE FROM deferred_work WHERE author_id=? AND work_id=?', (author_id, work_id))

    def prune_deferred_inventory(self, author_id: str, present_ids: set[str]) -> None:
        """A complete verified inventory may retire stale deferred candidates."""
        if type(present_ids) is not set:
            raise ValueError('complete inventory IDs must be a set')
        with self._lock:
            self._ensure_open()
            rows = self._db.execute('SELECT work_id FROM deferred_work WHERE author_id=?', (author_id,)).fetchall()
            stale = [(author_id, row[0]) for row in rows if row[0] not in present_ids]
            self._db.executemany('DELETE FROM deferred_work WHERE author_id=? AND work_id=?', stale)

    def history(self) -> tuple[dict, ...]:
        with self._lock:
            self._ensure_open()
            self._db.row_factory = sqlite3.Row
            try:
                return tuple(dict(row) for row in self._db.execute('SELECT * FROM dispatch_history ORDER BY started_utc DESC LIMIT 200'))
            finally:
                self._db.row_factory = None

    def reconcile_jobs(self, jobs, receipt_fingerprints: dict[str, str],
                       operation_to_job: dict[str, str] | None = None,
                       operation_to_fingerprint: dict[str, str] | None = None) -> tuple[str, ...]:
        """Reconcile queued item states with durable JobService and CAS receipts."""
        by_id = {job.job_id: job for job in jobs}
        newly_saved = []
        with self._lock:
            self._ensure_open()
            rows = self._db.execute('SELECT slot,work_id,operation_id,job_id,accepted_fingerprint,state FROM dispatch_items').fetchall()
            operation_to_job = operation_to_job or {}
            operation_to_fingerprint = operation_to_fingerprint or {}
            for slot, wid, operation_id, jid, accepted_fingerprint, prior in rows:
                # A saved state means this exact accepted fingerprint was
                # already verified against the active CAS generation. Preserve
                # that durable historical success even if a later generation
                # becomes active before its pending toast is claimed; otherwise
                # a valid success could be downgraded to unknown and lose its
                # one-time notification merely because a later update finished.
                if prior == 'saved':
                    continue
                if jid is None and operation_id in operation_to_job:
                    jid = operation_to_job[operation_id]
                    self._db.execute('UPDATE dispatch_items SET job_id=? WHERE slot=? AND work_id=?', (jid, slot, wid))
                if accepted_fingerprint is None and operation_id in operation_to_fingerprint:
                    accepted_fingerprint = operation_to_fingerprint[operation_id]
                    self._db.execute('UPDATE dispatch_items SET accepted_fingerprint=? WHERE slot=? AND work_id=?',
                                     (accepted_fingerprint, slot, wid))
                job = by_id.get(jid)
                # A valid prior generation is not evidence that this accepted
                # operation saved its new generation. Match the job-specific
                # fingerprint recorded in the active CAS index.
                if (accepted_fingerprint is not None
                        and receipt_fingerprints.get(wid) == accepted_fingerprint):
                    state = 'saved'
                elif job is None:
                    state = 'failed' if prior == 'failed' else 'unknown'
                elif job.state in {'failed', 'error', 'stopped'}:
                    state = 'failed'
                elif job.state == 'running':
                    state = 'running'
                elif job.state in {'queued', 'paused', 'stopping'}:
                    state = job.state
                elif job.state == 'done':
                    state = 'unknown'  # terminal job without receipt is not success
                else:
                    state = 'unknown'
                if state != prior:
                    self._db.execute('UPDATE dispatch_items SET state=? WHERE slot=? AND work_id=?', (state, slot, wid))
            slots = self._db.execute("SELECT DISTINCT slot FROM dispatch_items").fetchall()
            for (slot,) in slots:
                states = [row[0] for row in self._db.execute(
                    'SELECT state FROM dispatch_items WHERE slot=?', (slot,))]
                if states and all(value == 'saved' for value in states):
                    status = 'saved'
                elif states and all(value in {'saved', 'failed'} for value in states) and 'failed' in states:
                    status = 'partial' if 'saved' in states else 'failed'
                elif states and all(value in {'saved', 'failed', 'unknown'} for value in states):
                    status = 'unknown'
                else:
                    continue
                self._db.execute('UPDATE dispatch_history SET status=?,finished_utc=COALESCE(finished_utc,?) WHERE slot=? AND status IN (\'queued\',\'running\',\'unknown\',\'partial\')',
                                 (status, _timestamp(datetime.now(timezone.utc)), slot))
            # Slots with no accepted jobs have no dispatch_items to aggregate.
            # Finalize unsupported-only/deferred-only runs instead of leaving
            # their history permanently queued.
            self._db.execute("UPDATE dispatch_history SET status='deferred', finished_utc=COALESCE(finished_utc, ?) WHERE accepted=0 AND deferred>0 AND status IN ('queued','running') AND slot NOT IN (SELECT DISTINCT slot FROM dispatch_items)",
                             (_timestamp(datetime.now(timezone.utc)),))
            return tuple(newly_saved)

    def pending_notifications(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_open()
            return tuple(row[0] for row in self._db.execute("SELECT DISTINCT work_id FROM dispatch_items WHERE state='saved' AND notified=0 ORDER BY work_id"))

    def claim_pending_notifications(self) -> tuple[str, ...]:
        """Atomically claim durable completion notices across concurrent clients.

        UI notifications are transient, so delivery is deliberately at-most-once:
        claiming before emitting prevents duplicate toasts on overlapping refreshes
        or another client. The durable history remains available after a reload.
        """
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                work_ids = tuple(row[0] for row in self._db.execute(
                    "SELECT DISTINCT work_id FROM dispatch_items WHERE state='saved' AND notified=0 ORDER BY work_id"))
                if work_ids:
                    self._db.execute("UPDATE dispatch_items SET notified=1 WHERE state='saved' AND notified=0")
                self._db.execute('COMMIT')
                return work_ids
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def reconcile_candidates(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_open()
            return tuple(row[0] for row in self._db.execute(
                # Saved items have already passed full receipt/page verification.
                # A pending transient toast is not a reason for an unattended
                # runner to re-hash and decode every saved page every five seconds.
                # Notification claims read their own durable queue instead.
                # A failed enqueue can have been accepted by JobService just
                # before the exception: its operation may later be verified.
                "SELECT DISTINCT work_id FROM dispatch_items WHERE state <> 'saved' ORDER BY work_id"))

    def reconcile_expected_fingerprints(self, recovered: dict[str, str]) -> dict[str, frozenset[str]]:
        """Expected accepted operations for unresolved works, never older CAS generations.

        A cheap receipt-header/index check can reject a different active
        generation without hashing or decoding all of its image pages. Include
        crash-recovered operation fingerprints before the binding is persisted.
        """
        with self._lock:
            self._ensure_open()
            expected: dict[str, set[str]] = {}
            for work_id, operation_id, persisted in self._db.execute(
                    "SELECT work_id,operation_id,accepted_fingerprint FROM dispatch_items "
                    "WHERE state <> 'saved' ORDER BY work_id"):
                values = expected.setdefault(work_id, set())
                fingerprint = persisted or recovered.get(operation_id)
                if fingerprint is not None:
                    values.add(fingerprint)
            return {work_id: frozenset(values) for work_id, values in expected.items()}

    def mark_notified(self, work_ids) -> None:
        with self._lock:
            self._ensure_open()
            self._db.executemany("UPDATE dispatch_items SET notified=1 WHERE work_id=? AND state='saved'", ((wid,) for wid in work_ids))

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()
                if self._file_owner is not None:
                    self._file_owner.close()

    def set_favourites(self, author_ids: tuple[str, ...], *, expected_revision: int) -> tuple[str, ...]:
        """Persist the explicitly selected star authors, never legacy DB favourites."""
        from .pixiv_read_client import _identifier
        if type(author_ids) is not tuple or len(author_ids) > 128:
            raise ValueError('invalid favourite author selection')
        ids = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                state = self.snapshot()
                if state.revision != expected_revision:
                    raise ScheduleError('schedule changed in another client')
                self._db.execute('CREATE TABLE IF NOT EXISTS favourite_authors(author_id TEXT PRIMARY KEY)')
                self._db.execute('DELETE FROM favourite_authors')
                self._db.executemany('INSERT INTO favourite_authors(author_id) VALUES (?)', ((uid,) for uid in ids))
                self._db.execute('DELETE FROM deferred_work WHERE author_id NOT IN (SELECT author_id FROM favourite_authors)')
                self._db.execute('UPDATE schedule SET revision=? WHERE id=1', (state.revision + 1,))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
        return ids

    def configure_all(self, *, author_ids: tuple[str, ...], interval_hours: int,
                      startup_check: bool, expected_revision: int,
                      now: datetime) -> ScheduleState:
        """One all-or-nothing settings transaction, using the client's CAS."""
        from .pixiv_read_client import _identifier
        if (type(author_ids) is not tuple or len(author_ids) > 128
                or type(interval_hours) is not int or interval_hours not in VALID_INTERVALS
                or type(startup_check) is not bool or type(expected_revision) is not int
                or expected_revision < 1):
            raise ValueError('invalid managed schedule settings')
        ids = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
        current_time = _utc(now)
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                previous = self.snapshot()
                if previous.revision != expected_revision:
                    raise ScheduleError('schedule changed in another client')
                self._db.execute('CREATE TABLE IF NOT EXISTS favourite_authors(author_id TEXT PRIMARY KEY)')
                self._db.execute('DELETE FROM favourite_authors')
                self._db.executemany('INSERT INTO favourite_authors(author_id) VALUES (?)',
                                     ((uid,) for uid in ids))
                self._db.execute('DELETE FROM deferred_work WHERE author_id NOT IN (SELECT author_id FROM favourite_authors)')
                next_due = (None if interval_hours == 0 else
                            _timestamp(current_time + timedelta(hours=interval_hours)))
                self._db.execute('UPDATE schedule SET interval_hours=?,startup_check=?,revision=?,next_due_utc=? WHERE id=1',
                                 (interval_hours, int(startup_check), expected_revision + 1, next_due))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise
            return self.snapshot()

    def favourites(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_open()
            exists = self._db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='favourite_authors'").fetchone()[0]
            return (tuple(row[0] for row in self._db.execute('SELECT author_id FROM favourite_authors ORDER BY author_id'))
                    if exists else ())

    @staticmethod
    def configured(root: Path) -> bool:
        """Only open a scheduler automatically when the user already opted in.

        Avoid opening a new SQLite database just because managed mode started.
        """
        path = Path(root) / '.pixivvault-managed-scheduler.sqlite3'
        _safe_path(path)
        return path.is_file()


def submit_author_batch(services, author_ids, *, cancellation: Event,
                        max_items: int = 50, ledger: ManagedScheduler | None = None,
                        slot: str | None = None) -> dict[str, int]:
    """Bounded synchronous scheduler handoff into existing durable managed jobs.

    Only remote *complete* author lists may authorize an unattended enqueue;
    no remote absence scan is recorded. User-supplied favourite identifiers
    must be preconfigured in isolated managed settings, not inferred from a
    legacy database. A fresh owner capability is scoped to this one dispatch.
    """
    from .author_download import Difference, decide_difference
    from .managed_download_requests import ManagedAuthorIntent
    from .pixiv_read_client import _identifier

    if (services.managed_downloads is None or services.jobs is None
            or services.live_client is None or services.closing):
        raise ScheduleError('managed scheduler dispatch not available')
    if type(author_ids) not in (tuple, list) or len(author_ids) > 128:
        raise ValueError('invalid configured favourite authors')
    if type(max_items) is not int or not 1 <= max_items <= 50:
        raise ValueError('invalid scheduler queue capacity')
    verified_authors = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
    if not verified_authors:
        if ledger is not None:
            now = datetime.now(timezone.utc)
            slot = slot or _timestamp(now)
            ledger.begin_dispatch(slot, now, 0)
            ledger.finish_dispatch(slot, status='empty', accepted=0, deferred=0, now=now)
        return {'accepted': 0, 'deferred': 0, 'authors': 0}
    owner = uuid4().hex
    accepted, deferred = 0, 0
    now = datetime.now(timezone.utc)
    slot = slot or _timestamp(now)
    if ledger is not None:
        ledger.begin_dispatch(slot, now, len(verified_authors))
    from .job_service import UNFINISHED
    try:
        saved = {receipt['work_id']: (digest, date, relative)
                 for relative, receipt, digest, date
                 in services.managed_downloads.saved_receipts_with_dates()}
        full_reader = getattr(services.live_client, 'get_author_artworks_complete', None)
        if not callable(full_reader):
            raise ScheduleError('complete author inventory is required')
        services.live_client.verify_identity()
        for author_id in verified_authors:
            if cancellation.is_set() or services.closing:
                break
            # No fallback to partial/masked inventories: fail closed.
            works = full_reader(author_id)
            if ledger is not None:
                ledger.prune_deferred_inventory(author_id, {work['id'] for work in works})
            bridge = getattr(services, 'legacy_author_bridge', None)
            legacy_saved = {}
            if bridge is not None:
                # Read-only observation; never advance remote absence streaks.
                bridge.assert_source_unchanged()
                legacy_saved = bridge.observe_author(author_id, works).saved
            pending = {j.target_id for j in services.jobs.snapshot()
                       if j.kind == 'pixiv-illust' and j.state in UNFINISHED}
            deferred_ids = ledger.deferred_ids(author_id) if ledger is not None else frozenset()
            ordered_works = sorted(works, key=lambda work: (work['id'] not in deferred_ids, -int(work['id'])))
            for work_index, work in enumerate(ordered_works):
                if cancellation.is_set() or services.closing:
                    if ledger is not None:
                        for remaining in ordered_works[work_index:]:
                            if (remaining.get('user_id') == author_id
                                    and remaining.get('type') in (0, 1)
                                    and isinstance(remaining.get('id'), str)
                                    and isinstance(remaining.get('update_date'), str)
                                    and type(remaining.get('page_count')) is int):
                                ledger.defer_work(author_id=author_id, work=remaining,
                                                  now=datetime.now(timezone.utc))
                                deferred += 1
                    break
                if work['type'] not in (0, 1):
                    deferred += 1
                    continue
                wid = work['id']
                if work['user_id'] != author_id:
                    raise ScheduleError('complete inventory author changed')
                if wid in pending:
                    deferred += 1
                    if ledger is not None:
                        ledger.resolve_deferred(author_id, wid)
                    continue
                prior = saved.get(wid)
                if prior is None or prior[1] != work['update_date']:
                    # The active receipt snapshot precedes a potentially long
                    # complete-inventory GET. A prior job may publish while
                    # that GET runs; recheck any missing or stale-date pointer
                    # before making a new operation, not just NEW candidates.
                    latest = services.managed_downloads.verified_saved_receipt(wid, verify_pages=False)
                    if latest is not None:
                        relative, receipt, digest, latest_date, _ = latest
                        if (receipt['author_id'] != author_id
                                or receipt['illustration_type'] != work['type']):
                            raise ScheduleError('active managed receipt identity changed')
                        prior = (digest, latest_date, relative)
                        saved[wid] = prior
                        if latest_date == work['update_date']:
                            if receipt['page_count'] != work['page_count']:
                                raise ScheduleError('managed receipt page count changed without remote revision')
                            if ledger is not None:
                                ledger.resolve_deferred(author_id, wid)
                            continue
                    else:
                        prior = None
                        saved.pop(wid, None)
                legacy = legacy_saved.get(wid) if prior is None else None
                if legacy is not None:
                    difference = decide_difference(
                        saved=True, remote_update_date=work['update_date'],
                        saved_update_date=legacy.update_date, deleted=legacy.deleted,
                        files_present=legacy.files_present)
                else:
                    difference = decide_difference(
                        saved=prior is not None, remote_update_date=work['update_date'],
                        saved_update_date=prior[1] if prior else None,
                        files_present=True if prior else None)
                if difference not in (Difference.NEW, Difference.UPDATED):
                    if difference in (Difference.RESTORED, Difference.MISSING):
                        # No new-managed receipt exists yet. A verified old
                        # deletion/missing-media state may be restored into
                        # managed storage, never into the old path.
                        pass
                    elif difference == Difference.UNKNOWN:
                        deferred += 1
                        # An ambiguous legacy receipt cannot authorize an
                        # unattended retry. It may be reconsidered on the next
                        # scheduled full check, not spun in the durable backlog.
                        if ledger is not None:
                            ledger.resolve_deferred(author_id, wid)
                        continue
                    else:
                        deferred += 1
                        if ledger is not None:
                            ledger.resolve_deferred(author_id, wid)
                        continue
                if accepted >= max_items or len(pending) >= max_items:
                    deferred += 1
                    if ledger is not None:
                        ledger.defer_work(author_id=author_id, work=work, now=datetime.now(timezone.utc))
                    continue
                # Serialize the final active-receipt/pending check, the detail
                # GET and enqueue against both manual acceptance and the worker
                # publication path. Otherwise a same-ID job can finish during
                # the detail GET and leave this dispatch bound to a stale CAS.
                with services.managed_downloads.work_lock(wid):
                    current_pending = any(
                        j.kind == 'pixiv-illust' and j.target_id == wid and j.state in UNFINISHED
                        for j in services.jobs.snapshot())
                    if current_pending:
                        deferred += 1
                        if ledger is not None:
                            ledger.resolve_deferred(author_id, wid)
                        continue
                    latest = services.managed_downloads.verified_saved_receipt(wid, verify_pages=False)
                    if latest is None:
                        if prior is not None:
                            raise ScheduleError('active managed receipt disappeared during scheduler dispatch')
                    else:
                        relative, receipt, digest, latest_date, _ = latest
                        if (receipt['author_id'] != author_id
                                or receipt['illustration_type'] != work['type']):
                            raise ScheduleError('active managed receipt identity changed')
                        latest_prior = (digest, latest_date, relative)
                        if latest_date == work['update_date']:
                            if receipt['page_count'] != work['page_count']:
                                raise ScheduleError('managed receipt page count changed without remote revision')
                            saved[wid] = latest_prior
                            if ledger is not None:
                                ledger.resolve_deferred(author_id, wid)
                            continue
                        if prior != latest_prior:
                            raise ScheduleError('active managed receipt changed during scheduler dispatch')
                    # Reuse the same verified detail/DTO adapter as manual
                    # selection; masked metadata cannot become a job intent.
                    record = services.live_client.get_artwork_download_record(wid)
                    if (record.author_id != author_id or record.kind != 'illustration'
                            or record.illustration_type not in (0, 1)
                            or not record.page_count_known
                            or record.page_count != work['page_count']
                            or record.illustration_type != work['type']):
                        raise ScheduleError('remote author artwork identity changed')
                    (authorized,) = services.remember_managed_author_batch(owner, (record,))
                    intent = ManagedAuthorIntent(
                        authorized, 'follow', work['update_date'],
                        prior[0] if prior else None,
                        'updated' if prior else 'new', prior[2] if prior else None)
                    operation_id = uuid4().hex
                    if ledger is not None:
                        # Persist the idempotency key before JobService enqueue;
                        # after a crash we can discover the accepted job by ID.
                        ledger.prepare_dispatch_item(slot=slot, work_id=wid,
                                                     author_id=author_id, operation_id=operation_id)
                    try:
                        job, created = services.enqueue_managed_download(
                            intent, operation_id, owner=owner, return_created=True)
                    except Exception:
                        if ledger is not None:
                            ledger.fail_dispatch_item(slot=slot, work_id=wid, operation_id=operation_id)
                        raise
                    finally:
                        services.forget_accepted_managed_author_batch(owner, (wid,))
                pending.add(wid)
                if created:
                    accepted += 1
                else:
                    deferred += 1
                if ledger is not None:
                    from .managed_download_requests import accepted_author_fingerprint
                    fingerprint = accepted_author_fingerprint(intent, job.job_id)
                    ledger.bind_dispatch_job(slot=slot, work_id=wid,
                                             operation_id=operation_id, job_id=job.job_id,
                                             accepted_fingerprint=fingerprint)
                    ledger.resolve_deferred(author_id, wid)
        result = {'accepted': accepted, 'deferred': deferred, 'authors': len(verified_authors)}
        if ledger is not None:
            ledger.finish_dispatch(slot, status='queued' if accepted else ('empty' if not deferred else 'queued'),
                                   accepted=accepted, deferred=deferred)
        return result
    except Exception as exc:
        if ledger is not None:
            ledger.finish_dispatch(slot, status='failed', accepted=accepted, deferred=deferred,
                                   error_code=type(exc).__name__)
        raise
    finally:
        services.release_managed_owner(owner)


class ManagedScheduleRunner:
    """Single opt-in timer with separate wake and cancellation events.

    The dispatch callback must enqueue into the existing managed JobService;
    scheduler itself never executes Pixiv GET or writes downloaded images.
    """

    def __init__(self, ledger: ManagedScheduler, dispatch: Callable[[str, Event], None],
                 reconcile: Callable[[], None] | None = None,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.ledger = ledger
        self.dispatch = dispatch
        self.reconcile = reconcile
        self.clock = clock
        self.stop_event = Event()
        self._wake_event = Event()
        self._state_lock = RLock()
        self._thread: Thread | None = None
        self._running = False
        self._startup_requested = False
        self._claim_active = False

    def is_dispatching(self) -> bool:
        """True while the owned runner is inside an author dispatch callback."""
        with self._state_lock:
            return bool(getattr(self, '_dispatching', False))

    def can_acknowledge(self) -> bool:
        """No currently running claim may be acknowledged as a crashed one."""
        with self._state_lock:
            return not self._claim_active

    def acknowledge_interrupted(self, *, expected_revision: int) -> ScheduleState:
        # The same lock brackets claim+reservation and explicit recovery, so a
        # crash acknowledgement cannot race a claim that has not enqueued yet.
        with self._state_lock:
            if self._claim_active:
                raise ScheduleError('scheduler claim or dispatch is active')
            return self.ledger.acknowledge_interrupted(expected_revision=expected_revision)

    def _claim_reserved(self, now: datetime, *, startup: bool = False) -> str | None:
        with self._state_lock:
            if self.stop_event.is_set():
                return None
            self._claim_active = True
            try:
                slot = self.ledger.claim_due(now, startup=startup)
            except BaseException:
                self._claim_active = False
                raise
            if slot is None:
                self._claim_active = False
            return slot

    def _dispatch_slot(self, slot: str, *, claimed: bool = True) -> None:
        succeeded = False
        try:
            self.dispatch(slot, self.stop_event)
        except Exception:
            if claimed:
                try:
                    self.ledger.complete(slot, succeeded=False)
                except ScheduleError:
                    pass
        else:
            succeeded = True
            if claimed:
                try:
                    self.ledger.complete(slot, succeeded=True)
                except ScheduleError:
                    pass
        finally:
            with self._state_lock:
                self._claim_active = False
        return succeeded

    def start(self) -> bool:
        with self._state_lock:
            if self._running or (self._thread is not None and self._thread.is_alive()):
                return False
            self.stop_event.clear()
            self._wake_event.clear()
            self._running = True
            self._startup_requested = True
            self._thread = Thread(target=self._loop, daemon=True, name='pixivvault-managed-scheduler')
            self._thread.start()
            return True

    def settings_changed(self) -> None:
        # Waking a sleeping scheduler must never interrupt an accepted job.
        self._wake_event.set()

    def _loop(self) -> None:
        # Startup is a distinct opt-in event. The ordinary 0-hour OFF setting
        # does not suppress it, matching the original UI contract.
        if self._startup_requested:
            self._startup_requested = False
            self._wake_event.wait(5.0)
            if not self.stop_event.is_set():
                try:
                    startup_slot = self._claim_reserved(_utc(self.clock()), startup=True)
                    if startup_slot is not None:
                        self._dispatch_slot(startup_slot)
                except (ScheduleError, sqlite3.Error):
                    pass
            self._wake_event.clear()
        while not self.stop_event.is_set():
            try:
                capacity_available = self.reconcile is None
                if self.reconcile is not None:
                    try:
                        # The callback also reports whether JobService has a
                        # slot. This lets a restarted runner resume durable
                        # deferred work without requiring a fresh wake event.
                        capacity_available = self.reconcile() is not False
                    except Exception:
                        # Reconciliation errors remain visible in durable job
                        # state; never turn an unavailable receipt into success.
                        capacity_available = False
                state = self.ledger.snapshot()
                now = _utc(self.clock())
                due = _parse(state.next_due_utc)
                delay = max(0.0, (due - now).total_seconds()) if due is not None else 60.0
                if due is not None and delay == 0:
                    slot = self._claim_reserved(now)
                    if slot is None:
                        # A recovered interrupted slot deliberately blocks
                        # dispatch. Back off instead of spinning at 100% CPU.
                        self._wake_event.wait(60.0)
                        self._wake_event.clear()
                        continue
                    if slot is not None:
                        self._dispatch_slot(slot)
                    continue
                # Recovery acknowledgement is an explicit user action. The
                # backlog path must obey the same interrupted-state barrier as
                # scheduled claims, even when the next due time is in future.
                if (state.last_status != 'interrupted'
                        and capacity_available and self.ledger.has_deferred()
                        and not self.stop_event.is_set()):
                    with self._state_lock:
                        if not self._claim_active:
                            self._claim_active = True
                            backlog_slot = f'backlog-{uuid4().hex}'
                        else:
                            backlog_slot = None
                    if backlog_slot is not None:
                        prior_deferred = self.ledger.deferred_count()
                        succeeded = self._dispatch_slot(backlog_slot, claimed=False)
                        # Success may have accepted zero jobs and left the
                        # inventory unchanged (e.g. ambiguous legacy data).
                        # Never hammer Pixiv with repeated full GETs in that
                        # state; an explicit settings change may still wake us.
                        if not succeeded or self.ledger.deferred_count() >= prior_deferred:
                            self._wake_event.wait(60.0)
                            self._wake_event.clear()
                        continue
                self._wake_event.wait(min(delay, 5.0))
                self._wake_event.clear()
            except (ScheduleError, sqlite3.Error):
                # An inaccessible ledger must not cause a spin loop or a GET.
                self._wake_event.wait(60.0)
                self._wake_event.clear()
        with self._state_lock:
            self._running = False

    def stop(self, *, timeout: float = 12.0) -> None:
        with self._state_lock:
            self.stop_event.set()
            self._wake_event.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise ScheduleError('scheduler worker has not stopped')
        with self._state_lock:
            self._running = False
