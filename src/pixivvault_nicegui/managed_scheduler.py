"""Isolated opt-in scheduling policy for managed-only author checks.

The source PixivVault database, credentials and media are never opened here.
Dispatch is injected; only an explicit caller may turn a due schedule into a
managed JobService enqueue. This module does not perform network requests.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
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

from .account_scope import AccountMismatch, AccountScope, BoundSchedulerLedger, OwnedListing, require_same_account


VALID_INTERVALS = frozenset({0, 6, 12, 24, 48})
# The original had no limit on the star authors of the periodic check (scheduler.py:182). The 128
# here only guarded the hand-typed ID box; the ledger's own limit is the follow list (10,000).
MAX_FAVOURITES = 100_000


class ScheduleError(RuntimeError):
    pass


class ScheduleRestartRequired(ScheduleError):
    pass


class ScheduleConfirmationUnavailable(ScheduleError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('scheduler confirmation unavailable')


class ScheduleAccountError(ScheduleError):
    def __init__(self, reason):
        if reason not in ('unknown', 'mismatch'):
            raise ValueError('invalid scheduler account failure')
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ScheduleState:
    interval_hours: int
    startup_check: bool
    revision: int
    last_due_utc: str | None
    next_due_utc: str | None
    last_status: str


@dataclass(frozen=True, slots=True)
class ScheduleOwner:
    """Proof for a displayed schedule, valid for one committed follow owner."""
    scope: AccountScope
    services: object
    following: object
    epoch: int

    @property
    def account_id(self) -> str:
        return self.scope.account_id

    def _verify_following(self):
        if (getattr(self.services, 'following_sync', self.following) is not self.following
                or self.following._owner_epoch != self.epoch):
            raise AccountMismatch()
        require_same_account(self.account_id, self.following.last_synced_account())

    @contextmanager
    def current(self):
        """Serialize a local presentation or commit with an owner switch."""
        with self.following._lock:
            self._verify_following()
            self.scope.verify_current(self.services)
            yield

    def verify_current(self) -> None:
        with self.current():
            pass

    def verify_stop_allowed(self) -> None:
        # The global OFF operation must still work while the same reader is
        # revalidating. A different reader or a committed switch revokes it.
        with self.following._lock:
            self._verify_following()
            reader = self.scope.reader
            if (self.services.live_client is not reader or
                    getattr(reader, '_verified_identity', None) not in (None, self.account_id)):
                raise AccountMismatch()


@dataclass(frozen=True, slots=True)
class OwnedScheduleEditor:
    owner: ScheduleOwner
    state: ScheduleState
    authors: tuple[str, ...]
    owns_history: bool
    unbound: bool


@dataclass(frozen=True, slots=True)
class OwnedScheduleHistory:
    owner: ScheduleOwner
    rows: tuple[dict, ...] | None  # None means this ledger is not owned by the editor.


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

    _favourites_source = None
    _account_guard = False

    def attach_favourites(self, book) -> None:
        """Use account-scoped stars from the following ledger."""
        with self._lock:
            self._favourites_source = book
            self._account_guard = True

    def _bound_account(self):
        with self._lock:
            row = self._db.execute('SELECT account_id FROM scheduler_account WHERE id=1').fetchone()
            return None if row is None else row[0]

    def favourite_account(self):
        book = self._favourites_source
        return None if book is None else book.account()

    def capture_owner(self, services, *, account_id) -> ScheduleOwner:
        """Capture the current editor owner without issuing an identity request."""
        scope = AccountScope(account_id, getattr(services, 'live_client', None))
        with self._lock:
            self._ensure_open()
            book = self._favourites_source
            if book is None or book.following is None:
                raise ScheduleAccountError('mismatch')
            with book.following._lock:
                owner = ScheduleOwner(scope, services, book.following, book.following._owner_epoch)
                owner.verify_current()
                return owner

    @contextmanager
    def _editor_access(self, owner, *, require_history=False, for_stop=False):
        # The following store publishes owner switches with the second lock.
        # Keep it through the scheduler read or commit. Never hold it across I/O
        # outside the two ledgers, or acquire the scheduler lock from the store.
        with self._lock:
            self._ensure_open()
            if owner is None and not self._account_guard and self._bound_account() is None:
                yield None  # standalone ledger
                return
            book = self._favourites_source
            if type(owner) is not ScheduleOwner or book is None or book.following is not owner.following:
                raise ScheduleAccountError('mismatch')
            with owner.following._lock:
                if for_stop:
                    owner.verify_stop_allowed()
                else:
                    owner.verify_current()
                if require_history:
                    self._require_owner(owner.account_id)
                yield owner.account_id

    @contextmanager
    def _history_transaction(self, owner):
        with self._editor_access(owner, require_history=True):
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                if owner is not None:
                    owner.verify_current()
                self._db.execute('COMMIT')
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise

    def read_editor(self, *, owner: ScheduleOwner) -> OwnedScheduleEditor:
        with self._editor_access(owner) as account_id:
            if account_id is None:
                raise ScheduleAccountError('mismatch')
            bound = self._bound_account()
            return OwnedScheduleEditor(owner, self.snapshot(), self.favourites(account_id),
                                       bound == account_id, bound is None)

    def _dispatch_favourites(self):
        """Resolve backlog membership against the scheduler owner's stars."""
        if self._account_guard:
            account_id = self._bound_account()
            if account_id is None:
                raise ScheduleAccountError('unknown')
            return self.favourites(account_id)
        return self.favourites()

    def _touch_revision(self) -> None:
        """A star changed outside the settings form: stale forms must fail their revision check."""
        with self._lock:
            self._ensure_open()
            self._db.execute('UPDATE schedule SET revision=revision+1 WHERE id=1')

    def _remove_deferred_authors(self, author_ids):
        self._db.executemany('DELETE FROM deferred_work WHERE author_id=?',
                             ((author_id,) for author_id in author_ids))

    def legacy_favourites(self) -> tuple[str, ...]:
        """The standalone scheduler list, kept separate from the following ledger."""
        with self._lock:
            self._ensure_open()
            exists = self._db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='favourite_authors'").fetchone()[0]
            return (tuple(row[0] for row in self._db.execute('SELECT author_id FROM favourite_authors ORDER BY author_id'))
                    if exists else ())

    def __init__(self, managed_root: Path):
        root = Path(managed_root)
        if not root.is_absolute() or not root.is_dir() or root.is_symlink():
            raise ScheduleError('scheduler requires existing isolated managed root')
        self.path = root / '.pixivvault-managed-scheduler.sqlite3'
        _safe_path(self.path)
        existing_database = self.path.exists()
        self._lock = RLock()
        self._closed = False
        self._active_batch_slots = set()
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
            schedule_columns = {row[1] for row in self._db.execute('PRAGMA table_info(schedule)')}
            if 'settings_origin' not in schedule_columns:
                self._db.execute('ALTER TABLE schedule ADD COLUMN settings_origin TEXT')
            self._db.execute(
                "INSERT OR IGNORE INTO schedule VALUES (1,24,0,1,NULL,?,'never',?)",
                (_timestamp(datetime.now(timezone.utc) + timedelta(hours=24)),
                 None if existing_database else 'app'))
            self._db.execute("UPDATE schedule SET next_due_utc=? WHERE id=1 AND interval_hours=24 AND last_due_utc IS NULL AND next_due_utc IS NULL AND last_status='never'",
                             (_timestamp(datetime.now(timezone.utc) + timedelta(hours=24)),))
            self._db.execute('CREATE TABLE IF NOT EXISTS dispatch_history (slot TEXT PRIMARY KEY, status TEXT NOT NULL, started_utc TEXT NOT NULL, finished_utc TEXT, authors INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0, deferred INTEGER NOT NULL DEFAULT 0, error_code TEXT)')
            self._db.execute('CREATE TABLE IF NOT EXISTS dispatch_items (slot TEXT NOT NULL, work_id TEXT NOT NULL, author_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, job_id TEXT, accepted_fingerprint TEXT, state TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(slot,work_id))')
            item_columns = {row[1] for row in self._db.execute('PRAGMA table_info(dispatch_items)')}
            if 'accepted_fingerprint' not in item_columns:
                self._db.execute('ALTER TABLE dispatch_items ADD COLUMN accepted_fingerprint TEXT')
            self._db.execute('CREATE TABLE IF NOT EXISTS deferred_work (author_id TEXT NOT NULL, work_id TEXT NOT NULL, update_date TEXT NOT NULL, page_count INTEGER NOT NULL, work_type INTEGER NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(author_id,work_id))')
            self._db.execute('CREATE TABLE IF NOT EXISTS favourite_authors (author_id TEXT PRIMARY KEY)')
            self._db.execute('CREATE TABLE IF NOT EXISTS scheduler_account (id INTEGER PRIMARY KEY CHECK(id=1), account_id TEXT NOT NULL)')
            row = self._db.execute('SELECT interval_hours,startup_check,revision,last_due_utc,next_due_utc,last_status,settings_origin FROM schedule WHERE id=1').fetchone()
            if row is None or row[0] not in VALID_INTERVALS or row[1] not in (0,1) or row[2] < 1:
                raise ScheduleError('invalid managed schedule ledger')
            self._unbound_unknown_schedule = row[6] != 'app'
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

    def configure(self, *, interval_hours: int, startup_check: bool, expected_revision: int,
                  now: datetime, account_id) -> ScheduleState:
        return self.configure_all(author_ids=None, interval_hours=interval_hours,
                                  startup_check=startup_check, expected_revision=expected_revision,
                                  now=now, account_id=account_id)

    def _disable(self, *, expected_revision: int, account_id) -> ScheduleState:
        """Save the global stop with the settings revision displayed by the editor."""
        with self._lock:
            self._ensure_open()
            owner = account_id
            # None explicitly selects the global stop in an unverified editor.
            ownership = self._editor_access(owner, for_stop=True) if owner is not None else nullcontext()
            with ownership:
                self._db.execute('BEGIN IMMEDIATE')
                try:
                    before = self.snapshot()
                    if before.revision != expected_revision:
                        raise ScheduleError('settings changed')
                    self._db.execute('UPDATE schedule SET interval_hours=0,startup_check=0,next_due_utc=NULL,'
                                     'revision=revision+1 WHERE id=1 AND (interval_hours!=0 OR startup_check!=0 '
                                     'OR next_due_utc IS NOT NULL)')
                    saved = self.snapshot()
                    if owner is not None:
                        owner.verify_stop_allowed()
                    self._db.execute('COMMIT')
                    return saved
                except BaseException:
                    self._db.execute('ROLLBACK')
                    raise

    # Timer-only lifecycle. These transitions reserve/settle a slot before first binding.
    def _claim_due(self, now: datetime, *, startup: bool = False) -> str | None:
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

    def _complete(self, slot: str, *, succeeded: bool) -> None:
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

    def acknowledge_interrupted(self, *, expected_revision: int, owner: ScheduleOwner | None) -> ScheduleState:
        """Explicitly acknowledge ambiguous crash recovery without replaying it."""
        with self._history_transaction(owner):
            state = self.snapshot()
            if state.revision != expected_revision or state.last_status != 'interrupted':
                raise ScheduleError('schedule acknowledgement changed')
            self._db.execute("UPDATE schedule SET last_status='unknown',revision=? WHERE id=1",
                             (state.revision + 1,))
            return self.snapshot()

    def begin_dispatch(self, slot: str, now: datetime, authors: int, *, account_id,
                       batch=False) -> None:
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            self._db.execute('INSERT OR IGNORE INTO dispatch_history(slot,status,started_utc,authors) VALUES(?,?,?,?)',
                             (slot, 'running', _timestamp(now), authors))
            if batch:
                self._active_batch_slots.add(slot)

    def finish_dispatch(self, slot: str, *, status: str, accepted: int, deferred: int,
                        error_code: str | None = None, now: datetime | None = None, account_id) -> None:
        if status not in {'queued', 'failed', 'interrupted', 'empty'}:
            raise ValueError('invalid dispatch state')
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            stamp = None if error_code == AccountMismatch.code else _timestamp(now or datetime.now(timezone.utc))
            self._db.execute('UPDATE dispatch_history SET status=?,finished_utc=COALESCE(?,finished_utc),'
                             'accepted=?,deferred=?,error_code=? WHERE slot=?',
                             (status, stamp, accepted, deferred, error_code, slot))
            self._active_batch_slots.discard(slot)
            self._reconcile_dispatch_status(slot)

    def update_dispatch_progress(self, slot: str, *, accepted: int, deferred: int, account_id) -> None:
        """Publish admission counts without declaring dispatch or saving complete."""
        if any(type(value) is not int or value < 0 for value in (accepted, deferred)):
            raise ValueError('invalid dispatch progress')
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            self._db.execute(
                'UPDATE dispatch_history SET accepted=?,deferred=? WHERE slot=?',
                (accepted, deferred, slot))

    def prepare_dispatch_item(self, *, slot: str, work_id: str, author_id: str,
                              operation_id: str, account_id) -> None:
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            self._db.execute('INSERT OR IGNORE INTO dispatch_items(slot,work_id,author_id,operation_id,job_id,state) VALUES(?,?,?,?,NULL,?)',
                             (slot, work_id, author_id, operation_id, 'submitting'))

    def dispatch_operation(self, slot, work_id):
        with self._lock:
            self._ensure_open()
            return self._db.execute('SELECT operation_id,job_id FROM dispatch_items WHERE slot=? AND work_id=?',
                                    (slot, work_id)).fetchone()

    def bind_dispatch_job(self, *, slot: str, work_id: str, operation_id: str,
                          job_id: str, accepted_fingerprint: str, account_id) -> None:
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            self._db.execute("UPDATE dispatch_items SET job_id=?,accepted_fingerprint=?,state='queued' WHERE slot=? AND work_id=? AND operation_id=?",
                             (job_id, accepted_fingerprint, slot, work_id, operation_id))

    def fail_dispatch_item(self, *, slot: str, work_id: str, operation_id: str, account_id) -> None:
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
            self._db.execute("UPDATE dispatch_items SET state='failed' WHERE slot=? AND work_id=? AND operation_id=? AND job_id IS NULL",
                             (slot, work_id, operation_id))

    def _require_owner(self, account_id):
        bound = self._bound_account()
        if self._account_guard or bound is not None:
            if account_id is None:
                raise ScheduleAccountError('mismatch')
            require_same_account(bound, account_id, error=ScheduleAccountError('mismatch'))

    @contextmanager
    def _deferred_transaction(self, account_id):
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                self._require_owner(account_id)
                yield
                self._db.execute('COMMIT')
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise

    def defer_work(self, *, author_id: str, work: dict, now: datetime, account_id) -> None:
        with self._deferred_transaction(account_id):
            # The favourite list may have changed while an author's complete
            # inventory was being fetched. Do not recreate a row pruned by a
            # concurrent settings transaction.
            try:
                if author_id not in self._dispatch_favourites():
                    return
            except Exception:
                pass  # an unavailable star list must not discard pending work
            self._db.execute('INSERT INTO deferred_work(author_id,work_id,update_date,page_count,work_type,created_utc) VALUES(?,?,?,?,?,?) ON CONFLICT(author_id,work_id) DO UPDATE SET update_date=excluded.update_date,page_count=excluded.page_count,work_type=excluded.work_type',
                             (author_id, work['id'], work['update_date'], work['page_count'], work['type'], _timestamp(now)))

    def deferred_ids(self, author_id: str) -> frozenset[str]:
        with self._lock:
            self._ensure_open()
            return frozenset(row[0] for row in self._db.execute('SELECT work_id FROM deferred_work WHERE author_id=?', (author_id,)))

    def has_deferred(self) -> bool:
        with self._lock:
            self._ensure_open()
            authors = {row[0] for row in self._db.execute('SELECT DISTINCT author_id FROM deferred_work')}
            if not authors:
                return False
            if self._account_guard and self._bound_account() is None:
                return False
            try:
                return bool(authors.intersection(self._dispatch_favourites()))
            except Exception:
                return True  # an unavailable star list must not hide pending work

    def deferred_count(self) -> int:
        with self._lock:
            self._ensure_open()
            return self._db.execute('SELECT COUNT(*) FROM deferred_work').fetchone()[0]

    def resolve_deferred(self, author_id: str, work_id: str, *, account_id) -> None:
        with self._deferred_transaction(account_id):
            self._db.execute('DELETE FROM deferred_work WHERE author_id=? AND work_id=?', (author_id, work_id))

    def history(self) -> tuple[dict, ...]:
        with self._lock:
            self._ensure_open()
            self._db.row_factory = sqlite3.Row
            try:
                return tuple(dict(row) for row in self._db.execute('SELECT * FROM dispatch_history ORDER BY started_utc DESC LIMIT 200'))
            finally:
                self._db.row_factory = None

    def history_for(self, *, owner: ScheduleOwner) -> OwnedScheduleHistory:
        """Read only the current editor's history, including an empty result."""
        with self._editor_access(owner) as account_id:
            if account_id is None:
                raise ScheduleAccountError('mismatch')
            rows = self.history() if self._bound_account() == account_id else None
            return OwnedScheduleHistory(owner, rows)

    def reconcile_jobs(self, jobs, receipt_fingerprints: dict[str, str],
                       operation_to_job: dict[str, str] | None = None,
                       operation_to_fingerprint: dict[str, str] | None = None, *, account_id) -> tuple[str, ...]:
        """Reconcile queued item states with durable JobService and CAS receipts."""
        by_id = {job.job_id: job for job in jobs}
        newly_saved = []
        with self._lock:
            self._ensure_open()
            self._require_owner(account_id)
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
                    if state == 'saved':
                        newly_saved.append(wid)
            for slot in {row[0] for row in rows}:
                self._reconcile_dispatch_status(slot)
            # Slots with no accepted jobs have no dispatch_items to aggregate.
            # Finalize unsupported-only/deferred-only runs instead of leaving
            # their history permanently queued.
            self._db.execute("UPDATE dispatch_history SET status='deferred', finished_utc=COALESCE(finished_utc, ?) WHERE accepted=0 AND deferred>0 AND status IN ('queued','running') AND slot NOT IN (SELECT DISTINCT slot FROM dispatch_items)",
                             (_timestamp(datetime.now(timezone.utc)),))
            return tuple(newly_saved)

    def _reconcile_dispatch_status(self, slot):
        # An open batch can still add admissions. After restart, the durable
        # receipts can finish its orphaned history.
        if slot in self._active_batch_slots:
            return
        states = {row[0] for row in self._db.execute(
            'SELECT state FROM dispatch_items WHERE slot=?', (slot,))}
        if states == {'saved'}:
            status = 'saved'
        elif states and states <= {'saved', 'failed'}:
            status = 'partial' if 'saved' in states else 'failed'
        elif states and states <= {'saved', 'failed', 'unknown'}:
            status = 'unknown'
        else:
            return
        self._db.execute("UPDATE dispatch_history SET status=?,finished_utc=COALESCE(finished_utc,?) "
                         "WHERE slot=? AND status IN ('queued','running','unknown','partial')",
                         (status, _timestamp(datetime.now(timezone.utc)), slot))

    def _unreconciled_operations(self, operations):
        with self._lock:
            self._ensure_open()
            rows = self._db.execute("SELECT operation_id FROM dispatch_items WHERE state!='saved'")
            return {row[0] for row in rows if row[0] in operations}

    def pending_notifications(self) -> tuple[str, ...]:
        with self._lock:
            self._ensure_open()
            return tuple(row[0] for row in self._db.execute("SELECT DISTINCT work_id FROM dispatch_items WHERE state='saved' AND notified=0 ORDER BY work_id"))

    def claim_pending_notifications(self, *, owner: ScheduleOwner | None) -> tuple[str, ...]:
        """Atomically claim durable completion notices across concurrent clients.

        UI notifications are transient, so delivery is deliberately at-most-once:
        claiming before emitting prevents duplicate toasts on overlapping refreshes
        or another client. The durable history remains available after a reload.
        """
        with self._history_transaction(owner):
            work_ids = tuple(row[0] for row in self._db.execute(
                "SELECT DISTINCT work_id FROM dispatch_items WHERE state='saved' AND notified=0 ORDER BY work_id"))
            if work_ids:
                self._db.execute("UPDATE dispatch_items SET notified=1 WHERE state='saved' AND notified=0")
            return work_ids

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

    def mark_notified(self, work_ids, *, owner: ScheduleOwner | None) -> None:
        with self._history_transaction(owner):
            self._db.executemany("UPDATE dispatch_items SET notified=1 WHERE work_id=? AND state='saved'", ((wid,) for wid in work_ids))

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()
                if self._file_owner is not None:
                    self._file_owner.close()

    def set_favourites(self, author_ids: tuple[str, ...], *, expected_revision: int, account_id) -> tuple[str, ...]:
        """Persist the explicitly selected star authors, never legacy DB favourites."""
        from .pixiv_read_client import _identifier
        if type(author_ids) is not tuple:
            raise ValueError('invalid favourite author selection')
        ids = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
        with self._settings_transaction(ids, account_id=account_id) as account:
            state = self.snapshot()
            if state.revision != expected_revision:
                raise ScheduleError('schedule changed in another client')
            ids, _ = self._store_favourites(ids, attached_account=account)
            self._db.execute('UPDATE schedule SET revision=? WHERE id=1', (state.revision + 1,))
        return ids

    @contextmanager
    def _settings_transaction(self, ids, *, account_id, book_override=None):
        """Commit the scheduler and following ledgers together when stars are edited."""
        with self._lock:
            self._ensure_open()
            book = book_override if book_override is not None else self._favourites_source
            if book is None and self._bound_account() is not None:
                raise ScheduleAccountError('mismatch')
            following = None if book is None else book.following
            owner = account_id if type(account_id) is ScheduleOwner else None
            if book_override is None and account_id is not None and owner is None:
                raise ScheduleAccountError('mismatch')
            ownership = (self._editor_access(owner) if owner is not None else
                         (following._lock if following is not None else nullcontext()))
            with ownership as owner_id:
                if owner is not None:
                    account_id = owner_id
                attached = False
                if book is not None:
                    if account_id is None:
                        raise ScheduleAccountError('mismatch')
                    require_same_account(account_id, book.account(), error=ScheduleAccountError('mismatch'))
                    if ids is not None:
                        following._ensure_open()
                        self._db.execute('ATTACH DATABASE ? AS following_settings', (str(following.path),))
                        attached = True
                try:
                    self._db.execute('BEGIN IMMEDIATE')
                    try:
                        yield account_id if attached else None
                        if owner is not None:
                            owner.verify_current()
                        self._db.execute('COMMIT')
                    except BaseException:
                        if self._db.in_transaction:
                            self._db.execute('ROLLBACK')
                        raise
                finally:
                    if attached:
                        self._db.execute('DETACH DATABASE following_settings')

    def set_star(self, book, user_id, favourite, *, account_id, expected=None):
        """Commit the star and form revision together under the settings lock."""
        from .pixiv_read_client import _identifier
        user_id = _identifier(user_id)
        with self._settings_transaction((), account_id=account_id, book_override=book):
            row = self._db.execute(
                'SELECT is_favorite FROM following_settings.following_authors WHERE account_id=? AND user_id=?',
                (account_id, user_id)).fetchone()
            if row is None or bool(row[0]) == favourite or (expected is not None and bool(row[0]) != expected):
                return False
            self._db.execute('UPDATE following_settings.following_authors SET is_favorite=? '
                             'WHERE account_id=? AND user_id=?', (int(favourite), account_id, user_id))
            self._db.execute('UPDATE following_settings.following_accounts SET revision=revision+1 '
                             'WHERE account_id=?', (account_id,))
            self._touch_revision()
            return True

    def configure_all(self, *, author_ids: tuple[str, ...], interval_hours: int,
                      startup_check: bool, expected_revision: int,
                      now: datetime, account_id, return_ignored: bool = False,
                      report_retained: bool = False):
        """One all-or-nothing settings transaction, using the client's CAS."""
        from .pixiv_read_client import _identifier
        if (not (author_ids is None or type(author_ids) is tuple)
                or type(interval_hours) is not int or interval_hours not in VALID_INTERVALS
                or type(startup_check) is not bool or type(expected_revision) is not int
                or expected_revision < 1):
            raise ValueError('invalid managed schedule settings')
        # author_ids=None leaves the stars exactly as they are (a form that never saw them must not save them).
        ids = None if author_ids is None else tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
        current_time = _utc(now)
        with self._settings_transaction(ids, account_id=account_id) as account:
            previous = self.snapshot()
            if previous.revision != expected_revision:
                raise ScheduleError('schedule changed in another client')
            applied, retained = (), ()
            if ids is not None:
                applied, retained = self._store_favourites(ids, attached_account=account)
            next_due = (None if interval_hours == 0 else
                        _timestamp(current_time + timedelta(hours=interval_hours)))
            self._db.execute('UPDATE schedule SET interval_hours=?,startup_check=?,revision=?,next_due_utc=? WHERE id=1',
                             (interval_hours, int(startup_check), expected_revision + 1, next_due))
            state = self.snapshot()
        if return_ignored:
            applied_ids = set(applied)
            ignored = () if ids is None else tuple(uid for uid in ids if uid not in applied_ids)
            return (state, ignored, retained) if report_retained else (state, ignored)
        return state

    def _store_favourites(self, ids, *, attached_account=None):
        """Store the selection in the attached account or standalone scheduler."""
        if attached_account is not None:
            rows = self._db.execute(
                'SELECT user_id,is_following,is_favorite FROM following_settings.following_authors WHERE account_id=?',
                (attached_account,)).fetchall()
            wanted = set(ids)
            applied_ids = {uid for uid, following, star in rows if following or star}
            inactive_stars = {uid for uid, following, star in rows if star and not following}
            removed = {uid for uid, _, star in rows if star and uid not in wanted}
            changed = False
            for uid, following, star in rows:
                new = uid in wanted and bool(following or star)
                if bool(star) != new:
                    self._db.execute('UPDATE following_settings.following_authors SET is_favorite=? '
                                     'WHERE account_id=? AND user_id=?', (int(new), attached_account, uid))
                    changed = True
            if changed:
                self._db.execute('UPDATE following_settings.following_accounts SET revision=revision+1 '
                                 'WHERE account_id=?', (attached_account,))
            if attached_account == self._bound_account():
                self._remove_deferred_authors(removed)
            return (tuple(uid for uid in ids if uid in applied_ids),
                    tuple(uid for uid in ids if uid in inactive_stars))
        old = set(self.legacy_favourites())
        self._db.execute('DELETE FROM favourite_authors')
        self._db.executemany('INSERT INTO favourite_authors(author_id) VALUES (?)', ((uid,) for uid in ids))
        self._remove_deferred_authors(old.difference(ids))
        return ids, ()

    def favourites(self, account_id=None) -> tuple[str, ...]:
        book = self._favourites_source
        return self.legacy_favourites() if book is None else book.ids(account_id)

    def bind_account(self, account_id: str, *, scope: ScheduleOwner | AccountScope) -> None:
        """Bind empty unowned history while the verified follow owner is current."""
        from .pixiv_read_client import _identifier
        account_id = _identifier(account_id)
        with self._lock:
            self._ensure_open()
            if type(scope) is ScheduleOwner:
                ownership = self._editor_access(scope)
                verify = scope.verify_current
            elif not self._account_guard and type(scope) is AccountScope:
                ownership = nullcontext()
                verify = scope.verify_observed
            else:
                raise AccountMismatch()
            with ownership:
                require_same_account(account_id, scope.account_id)
                self._db.execute('BEGIN IMMEDIATE')
                try:
                    verify()
                    row = self._db.execute('SELECT account_id FROM scheduler_account WHERE id=1').fetchone()
                    if row is None:
                        if self._unbound_unknown_schedule:
                            raise ScheduleAccountError('unknown')
                        for table in ('favourite_authors', 'deferred_work', 'dispatch_history', 'dispatch_items'):
                            if self._db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None:
                                raise ScheduleAccountError('unknown')
                        self._db.execute('INSERT INTO scheduler_account VALUES (1,?)', (account_id,))
                    else:
                        require_same_account(row[0], account_id, error=ScheduleAccountError('mismatch'))
                    verify()
                    self._db.execute('COMMIT')
                except BaseException:
                    if self._db.in_transaction:
                        self._db.execute('ROLLBACK')
                    raise

    def _sync_favourites(self, author_ids: tuple[str, ...]) -> None:
        """Refresh the standalone scheduler list without touching the following ledger."""
        if self._favourites_source is not None:
            return
        from .pixiv_read_client import _identifier
        ids = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
        with self._lock:
            self._ensure_open()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                if self._db.execute('SELECT 1 FROM scheduler_account WHERE id=1').fetchone() is None:
                    raise ScheduleAccountError('unknown')
                self._db.execute('DELETE FROM favourite_authors')
                self._db.executemany('INSERT INTO favourite_authors(author_id) VALUES (?)', ((uid,) for uid in ids))
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    @staticmethod
    def configured(root: Path) -> bool:
        """Only open a scheduler automatically when the user already opted in.

        Avoid opening a new SQLite database just because managed mode started.
        """
        path = Path(root) / '.pixivvault-managed-scheduler.sqlite3'
        _safe_path(path)
        return path.is_file()


def _require_managed_network_allowed(services):
    """Check the same durable GET stop used by managed reader/job admission.

    Never treat missing/indeterminate state as permission. Synthetic services
    without the owned remote stop retain their existing injected behavior.
    """
    integration = getattr(services, 'managed_network_stop', None)
    if integration is not None:
        integration.require_allowed()


def _dispatch_account(ledger):
    bound = ledger._bound_account() if ledger is not None else None
    if bound is None and ledger is not None and ledger._account_guard:
        raise ScheduleAccountError('mismatch')
    return bound


def _dispatch_admission(ledger, owner):
    if owner is None:
        return nullcontext()
    if ledger is None:
        raise ScheduleAccountError('mismatch')
    return ledger._editor_access(owner, require_history=True)


def submit_author_batch(services, author_ids, *, cancellation: Event,
                        max_items: int = 50, ledger: ManagedScheduler | None = None,
                        slot: str | None = None, displayed_owner: ScheduleOwner | None = None) -> dict[str, int]:
    """Bounded synchronous scheduler handoff into existing durable managed jobs.

    Only remote *complete* author lists may authorize an unattended enqueue;
    no remote absence scan is recorded. User-supplied favourite identifiers
    must be preconfigured in isolated managed settings, not inferred from a
    legacy database. A fresh owner capability is scoped to this one dispatch.
    """
    _require_managed_network_allowed(services)
    if getattr(services, 'author_media', None) is not None:
        return submit_author_media_batch(services, author_ids, cancellation=cancellation,
            max_items=max_items, ledger=ledger, slot=slot, displayed_owner=displayed_owner)
    from .author_download import Difference, decide_difference
    from .managed_download_requests import ManagedAuthorIntent
    from .pixiv_read_client import _identifier

    if (services.managed_downloads is None or services.jobs is None
            or services.live_client is None or services.closing):
        raise ScheduleError('managed scheduler dispatch not available')
    if type(author_ids) not in (tuple, list):
        raise ValueError('invalid configured favourite authors')
    if type(max_items) is not int or not 1 <= max_items <= 50:
        raise ValueError('invalid scheduler queue capacity')
    verified_authors = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
    bound = _dispatch_account(ledger)
    if not verified_authors:
        if ledger is not None:
            now = datetime.now(timezone.utc)
            slot = slot or _timestamp(now)
            with _dispatch_admission(ledger, displayed_owner):
                ledger.begin_dispatch(slot, now, 0, account_id=bound)
                ledger.finish_dispatch(slot, status='empty', accepted=0, deferred=0, now=now, account_id=bound)
        return {'accepted': 0, 'deferred': 0, 'authors': 0}
    owner = uuid4().hex
    accepted, deferred = 0, 0
    now = datetime.now(timezone.utc)
    slot = slot or _timestamp(now)
    if ledger is not None:
        with _dispatch_admission(ledger, displayed_owner):
            ledger.begin_dispatch(slot, now, len(verified_authors), account_id=bound)
    from .job_service import UNFINISHED
    try:
        saved = {receipt['work_id']: (digest, date, relative)
                 for relative, receipt, digest, date
                 in services.managed_downloads.saved_receipts_with_dates()}
        reader = services.live_client
        full_reader = getattr(reader, 'get_author_artworks_complete', None)
        if not callable(full_reader):
            raise ScheduleError('complete author inventory is required')
        _require_managed_network_allowed(services)
        scope = AccountScope(reader.verify_identity(), reader)
        if bound is not None:
            require_same_account(bound, scope.account_id)
        scope.verify_current(services)
        deferred_writer = BoundSchedulerLedger(ledger, scope, services) if ledger is not None else None
        for author_id in verified_authors:
            if cancellation.is_set() or services.closing:
                break
            _require_managed_network_allowed(services)
            scope.verify_current(services)
            # No fallback to partial/masked inventories: fail closed.
            listing = OwnedListing(scope, author_id, 'illust', tuple(full_reader(author_id)))
            listing.scope.verify_current(services)
            works = listing.rows
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
                _require_managed_network_allowed(services)
                scope.verify_current(services)
                if cancellation.is_set() or services.closing:
                    if ledger is not None:
                        for remaining in ordered_works[work_index:]:
                            if (remaining.get('user_id') == author_id
                                    and remaining.get('type') in (0, 1)
                                    and isinstance(remaining.get('id'), str)
                                    and isinstance(remaining.get('update_date'), str)
                                    and type(remaining.get('page_count')) is int):
                                deferred_writer.defer_work(author_id=author_id, work=remaining,
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
                    continue
                prior = saved.get(wid)
                if prior is None or prior[1] != work['update_date']:
                    # The active receipt snapshot precedes a potentially long
                    # complete-inventory GET. A prior job may publish while
                    # that GET runs; recheck any missing or stale-date pointer
                    # before making a new operation, not just NEW candidates.
                    latest = services.managed_downloads.verified_saved_receipt(wid, verify_pages=False)
                    scope.verify_current(services)
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
                                deferred_writer.resolve_deferred(author_id, wid)
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
                        # unattended retry or clear the held work.
                        continue
                    else:
                        deferred += 1
                        if ledger is not None:
                            deferred_writer.resolve_deferred(author_id, wid)
                        continue
                if accepted >= max_items or len(pending) >= max_items:
                    deferred += 1
                    if ledger is not None:
                        deferred_writer.defer_work(author_id=author_id, work=work, now=datetime.now(timezone.utc))
                    continue
                # Serialize the final active-receipt/pending check, the detail
                # GET and enqueue against both manual acceptance and the worker
                # publication path. Otherwise a same-ID job can finish during
                # the detail GET and leave this dispatch bound to a stale CAS.
                with getattr(services, "_media_admission_lock", nullcontext()), services.managed_downloads.work_lock(wid):
                    _require_managed_network_allowed(services)
                    if sum(j.state in UNFINISHED for j in services.jobs.snapshot()) >= max_items:
                        deferred += 1
                        if ledger is not None:
                            deferred_writer.defer_work(author_id=author_id, work=work, now=datetime.now(timezone.utc))
                        continue
                    current_pending = any(
                        j.kind == 'pixiv-illust' and j.target_id == wid and j.state in UNFINISHED
                        for j in services.jobs.snapshot())
                    scope.verify_current(services)
                    if current_pending:
                        deferred += 1
                        continue
                    latest = services.managed_downloads.verified_saved_receipt(wid, verify_pages=False)
                    scope.verify_current(services)
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
                                deferred_writer.resolve_deferred(author_id, wid)
                            continue
                        if prior != latest_prior:
                            raise ScheduleError('active managed receipt changed during scheduler dispatch')
                    # Reuse the same verified detail/DTO adapter as manual
                    # selection; masked metadata cannot become a job intent.
                    record = reader.get_artwork_download_record(wid)
                    scope.verify_current(services)
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
                                                     author_id=author_id, operation_id=operation_id, account_id=bound)
                    try:
                        _require_managed_network_allowed(services)
                        job, created = services.enqueue_managed_download(
                            intent, operation_id, owner=owner, return_created=True)
                        scope.verify_current(services)
                    except Exception:
                        if ledger is not None:
                            ledger.fail_dispatch_item(slot=slot, work_id=wid, operation_id=operation_id, account_id=bound)
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
                                             accepted_fingerprint=fingerprint, account_id=bound)
                    deferred_writer.resolve_deferred(author_id, wid)
                    ledger.update_dispatch_progress(slot, accepted=accepted, deferred=deferred, account_id=bound)
        result = {'accepted': accepted, 'deferred': deferred, 'authors': len(verified_authors)}
        if ledger is not None:
            ledger.finish_dispatch(slot, status='queued' if accepted else ('empty' if not deferred else 'queued'),
                                   accepted=accepted, deferred=deferred, account_id=bound)
        return result
    except Exception as exc:
        if ledger is not None:
            mismatch = isinstance(exc, AccountMismatch)
            ledger.finish_dispatch(slot, status='failed', accepted=accepted, deferred=deferred,
                                   error_code=AccountMismatch.code if mismatch else type(exc).__name__, account_id=bound)
        if isinstance(exc, AccountMismatch):
            raise ScheduleAccountError('mismatch') from exc
        raise
    finally:
        services.release_managed_owner(owner)


def submit_author_media_batch(services, author_ids, *, cancellation, max_items=50, ledger=None, slot=None,
                              displayed_owner: ScheduleOwner | None = None):
    """Original both=illustrations (including ugoira)+novels, bounded at 50.

    Complete inventories are fetched before admitting an author's first item.
    Deferred keys retain their media namespace; old bare static keys survive.
    """
    _require_managed_network_allowed(services)
    from .author_media import AuthorMediaCandidate, identifier
    from .author_download import Difference
    from .job_service import UNFINISHED
    if services.closing or services.jobs is None:
        raise ScheduleError('author media unavailable')
    if type(author_ids) not in (tuple,list):
        raise ValueError('invalid configured favourite authors')
    if type(max_items) is not int or not 1<=max_items<=50:
        raise ValueError('invalid scheduler queue capacity')
    authors=tuple(dict.fromkeys(identifier(uid) for uid in author_ids))
    slot=slot or _timestamp(datetime.now(timezone.utc))
    accepted=deferred=0
    bound = _dispatch_account(ledger)
    if ledger is not None:
        with _dispatch_admission(ledger, displayed_owner):
            ledger.begin_dispatch(slot, datetime.now(timezone.utc), len(authors), account_id=bound)
    try:
        for aid in authors:
            if cancellation.is_set() or services.closing: break
            _require_managed_network_allowed(services)
            listing = services.author_media_preview_owned(aid, expected_account=bound)
            if type(listing) is not OwnedListing or listing.author_id != aid:
                raise AccountMismatch()
            if bound is not None:
                require_same_account(bound, listing.account_id)
            listing.scope.verify_current(services)
            deferred_writer = BoundSchedulerLedger(ledger, listing.scope, services) if ledger is not None else None
            rows = listing.rows
            if any(type(c) is not AuthorMediaCandidate or c.author_id!=aid for c in rows):
                raise ScheduleError('complete author media identity changed')
            if len({c.key for c in rows})!=len(rows):
                raise ScheduleError('duplicate author media inventory key')
            backlog=ledger.deferred_ids(aid) if ledger else frozenset()
            rows=sorted(rows,key=lambda c:(c.key not in backlog,-int(c.work_id),c.kind))
            for c in rows:
                _require_managed_network_allowed(services)
                listing.scope.verify_current(services)
                if not c.eligible:
                    deferred+=1
                    if ledger is not None and c.difference == Difference.SKIPPED:
                        deferred_writer.resolve_deferred(aid,c.key)
                    continue
                if (cancellation.is_set() or services.closing or accepted>=max_items
                        or sum(j.state in UNFINISHED for j in services.jobs.snapshot())>=max_items):
                    deferred+=1
                    if ledger is not None:
                        deferred_writer.defer_work(author_id=aid,work=c.deferred_work(),now=datetime.now(timezone.utc))
                    continue
                with getattr(services, '_media_admission_lock', nullcontext()):
                    _require_managed_network_allowed(services)
                    if sum(j.state in UNFINISHED for j in services.jobs.snapshot())>=max_items:
                        deferred+=1
                        if ledger is not None:
                            deferred_writer.defer_work(author_id=aid,work=c.deferred_work(),now=datetime.now(timezone.utc))
                        continue
                    existing=ledger.dispatch_operation(slot,c.key) if ledger else None
                    operation=existing[0] if existing else uuid4().hex
                    if existing and existing[1]:
                        # Previously accepted in this slot; reconcile will decide
                        # saved/failed, never an automatic new operation/retry.
                        deferred+=1
                        if ledger is not None: deferred_writer.resolve_deferred(aid,c.key)
                        continue
                    if ledger is not None:
                        ledger.prepare_dispatch_item(slot=slot,work_id=c.key,author_id=aid,operation_id=operation, account_id=bound)
                    try:
                        _require_managed_network_allowed(services)
                        job,created,fingerprint=services.enqueue_author_media(c,operation,origin='follow')
                        listing.scope.verify_current(services)
                    except Exception:
                        if ledger is not None:
                            ledger.fail_dispatch_item(slot=slot,work_id=c.key,operation_id=operation, account_id=bound)
                        raise
                    accepted+=int(created); deferred+=int(not created)
                    if ledger is not None:
                        ledger.bind_dispatch_job(slot=slot,work_id=c.key,operation_id=operation,
                                                 job_id=job.job_id,accepted_fingerprint=fingerprint, account_id=bound)
                        deferred_writer.resolve_deferred(aid,c.key)
                        ledger.update_dispatch_progress(slot,accepted=accepted,deferred=deferred, account_id=bound)
        if ledger is not None:
            ledger.finish_dispatch(slot,status='queued' if accepted or deferred else 'empty',
                                   accepted=accepted,deferred=deferred, account_id=bound)
        return dict(accepted=accepted,deferred=deferred,authors=len(authors))
    except Exception as exc:
        if ledger is not None:
            ledger.finish_dispatch(slot, status='failed', accepted=accepted, deferred=deferred,
                                   error_code=AccountMismatch.code if isinstance(exc, AccountMismatch) else type(exc).__name__, account_id=bound)
        if isinstance(exc, AccountMismatch):
            raise ScheduleAccountError('mismatch') from exc
        raise


class ManagedScheduleRunner:
    """Single opt-in timer with separate wake and cancellation events.

    The dispatch callback must enqueue into the existing managed JobService;
    scheduler itself never executes Pixiv GET or writes downloaded images.
    """

    def __init__(self, ledger: ManagedScheduler, dispatch: Callable[[str, Event], None],
                 reconcile: Callable[[], None] | None = None,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 can_dispatch: Callable[[], bool] = lambda: True):
        self.ledger = ledger
        self.dispatch = dispatch
        self.reconcile = reconcile
        self.clock = clock
        self.can_dispatch = can_dispatch
        self.stop_event = Event()
        self._wake_event = Event()
        self._state_lock = RLock()
        self._thread: Thread | None = None
        self._running = False
        self._startup_requested = False
        self._claim_active = False
        self.display_state = None
        self.display_phase = 'waiting'
        self.display_failure = None
        self._display_outcome_slot = None
        self._dispatch_cancel = None

    def is_dispatching(self) -> bool:
        """True while the owned runner is inside an author dispatch callback."""
        with self._state_lock:
            return bool(getattr(self, '_dispatching', False))

    def can_acknowledge(self) -> bool:
        """No currently running claim may be acknowledged as a crashed one."""
        with self._state_lock:
            return not self._claim_active

    def acknowledge_interrupted(self, *, expected_revision: int, owner: ScheduleOwner | None) -> ScheduleState:
        # The same lock brackets claim+reservation and explicit recovery, so a
        # crash acknowledgement cannot race a claim that has not enqueued yet.
        with self._state_lock:
            if self._claim_active:
                raise ScheduleError('scheduler claim or dispatch is active')
            return self.ledger.acknowledge_interrupted(expected_revision=expected_revision, owner=owner)

    def _claim_reserved(self, now: datetime, *, startup: bool = False) -> str | None:
        with self._state_lock:
            if self.stop_event.is_set() or not self.can_dispatch():
                return None
            self._claim_active = True
            try:
                slot = self.ledger._claim_due(now, startup=startup)
            except BaseException:
                self._claim_active = False
                raise
            if slot is None:
                self._claim_active = False
            return slot

    def request_dispatch_stop(self) -> bool:
        """Cancel only the accepted automatic dispatch, leaving its timer enabled."""
        with self._state_lock:
            cancellation = self._dispatch_cancel
            if cancellation is None or cancellation.is_set():
                return False
            cancellation.set()
            self.display_phase = 'stopping'
            return True

    def _dispatch_slot(self, slot: str, *, claimed: bool = True) -> None:
        from .managed_network_stop_gate import ManagedNetworkStopped

        succeeded = False
        outcome = 'waiting'
        with self._state_lock:
            if self.stop_event.is_set():
                self._claim_active = False
                return False
            cancellation = self._dispatch_cancel = Event()
            self.display_phase = 'running'
        try:
            self.dispatch(slot, cancellation)
        except Exception as error:
            outcome = 'blocked' if isinstance(error, ManagedNetworkStopped) else 'failed'
            if claimed:
                try:
                    self.ledger._complete(slot, succeeded=False)
                except ScheduleError:
                    pass
        else:
            succeeded = True
            if claimed:
                try:
                    self.ledger._complete(slot, succeeded=True)
                except ScheduleError:
                    pass
        finally:
            with self._state_lock:
                if cancellation.is_set() and not self.stop_event.is_set():
                    outcome = 'stopped'
                self.display_phase = outcome
                if claimed:
                    self._display_outcome_slot = slot
                if outcome == 'failed':
                    self.display_failure = (slot, 'failed')
                elif outcome == 'waiting':
                    self.display_failure = None
                self._dispatch_cancel = None
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
                self.display_state = (state, False)
                if state.last_due_utc != self._display_outcome_slot:
                    if state.last_status in {'error', 'interrupted', 'unknown'}:
                        self.display_failure = (state.last_due_utc, state.last_status)
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
                if (state.interval_hours > 0 and state.last_status != 'interrupted'
                        and capacity_available and self.ledger.has_deferred()
                        and not self.stop_event.is_set()):
                    with self._state_lock:
                        if not self._claim_active and not self.stop_event.is_set() and self.can_dispatch():
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
            if self._dispatch_cancel is not None:
                self._dispatch_cancel.set()
            self._wake_event.set()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise ScheduleError('scheduler worker has not stopped')
        with self._state_lock:
            self._running = False


class _BatchDispatch:
    '''Bind batch admissions to one periodic slot before calling JobService.'''

    def __init__(self, ledger, slot, account, bound_account):
        self.ledger, self.slot = ledger, slot
        self.account, self.bound_account = account, bound_account

    def prepare(self, handle, candidate, operation):
        require_same_account(self.account, handle.account_id)
        existing = self.ledger.dispatch_operation(self.slot, candidate.key)
        if existing is not None:
            return existing[0]
        self.ledger.prepare_dispatch_item(slot=self.slot, work_id=candidate.key, author_id=candidate.author_id,
                                         operation_id=operation, account_id=self.bound_account)
        return operation

    def fail(self, handle, candidate, operation):
        require_same_account(self.account, handle.account_id)
        self.ledger.fail_dispatch_item(slot=self.slot, work_id=candidate.key,
                                      operation_id=operation, account_id=self.bound_account)


def run_author_check(services, author_ids, *, cancellation: Event, ledger: ManagedScheduler | None = None,
                     slot: str | None = None, poll: float = 0.5, wait: bool = True,
                     account_id: str, displayed_owner: ScheduleOwner | None = None) -> dict[str, int]:
    """The periodic check on the author batch engine (original scheduler.py:182 `_run_check`).

    Every star author (no 128 limit), differential, both media, stamped on completion, behind the
    same network stop gate as the screen's 「実行」. A run the user started is never joined by an
    automatic one. A run the gate paused is left paused for the user to resume; this slot ends.
    """
    from .pixiv_read_client import _identifier
    ids = tuple(dict.fromkeys(_identifier(uid) for uid in author_ids))
    now = datetime.now(timezone.utc)
    slot = slot or _timestamp(now)
    bound = _dispatch_account(ledger)
    account = AccountScope(account_id).account_id
    if bound is not None:
        require_same_account(bound, account)
    engine = services.batch_runs
    engine.port.verify_run_account(account)
    if ledger is not None:
        with _dispatch_admission(ledger, displayed_owner):
            ledger.begin_dispatch(slot, now, len(ids), account_id=bound, batch=True)

    def persist_outcome(state, code, totals):
        accepted = sum(totals[name] for name in ('n_new', 'n_updated', 'n_restored', 'n_missing'))
        if ledger is not None:
            status = ('queued' if state == 'done' and accepted else 'empty' if state == 'done'
                      else 'interrupted' if state in ('stopped', 'paused', 'interrupted') else 'failed')
            error_code = (code or None) if status in ('interrupted', 'failed') else None
            try:
                ledger.finish_dispatch(slot, status=status, accepted=accepted, deferred=0,
                                       error_code=error_code, account_id=bound)
            except (ScheduleError, sqlite3.Error):
                if not services.closing:
                    raise
        return accepted

    result = None

    def finished(own_result):
        nonlocal result
        require_same_account(account, own_result.handle.account_id)
        result = own_result
        if not wait:
            persist_outcome(result.state, result.error_code, result.totals)

    try:
        if not ids:
            if ledger is not None:
                ledger.finish_dispatch(slot, status='empty', accepted=0, deferred=0, account_id=bound)
            return {'accepted': 0, 'deferred': 0, 'authors': 0}
        if engine.active:
            if ledger is not None:
                ledger.finish_dispatch(slot, status='empty', accepted=0, deferred=0, error_code='run_active', account_id=bound)
            return {'accepted': 0, 'deferred': 0, 'authors': 0}
        _require_managed_network_allowed(services)
        dispatch = {} if ledger is None else {'dispatch': _BatchDispatch(ledger, slot, account, bound)}
        # The owned dispatch row is already reserved. Do not hold the following
        # lock across the batch ledger's own transaction and lock ordering.
        if displayed_owner is not None:
            displayed_owner.verify_current()
        handle = services.start_batch_run(ids, origin='auto', target_type='both',
                                          account_id=account, on_finish=finished, **dispatch)
    except Exception as error:
        if ledger is not None:
            code = AccountMismatch.code if isinstance(error, AccountMismatch) else type(error).__name__
            ledger.finish_dispatch(slot, status='failed', accepted=0, deferred=0, error_code=code, account_id=bound)
        raise
    if not wait:
        # 「今すぐチェック」: the run goes on in the background; progress is on the download screen.
        return {'accepted': 0, 'deferred': 0, 'authors': len(ids), 'started': True}
    paused = ''
    while not engine.join(poll, handle=handle):
        if cancellation.is_set() or services.closing:
            engine._stop_run(handle)
            continue
        status = engine.status(handle)
        if status is not None and status[0] == 'paused' and status[1] not in ('', 'user'):
            # A gated automatic run must release the engine for future slots.
            paused = status[1]
            engine._stop_run(handle)
    if result is None or result.handle != handle:
        raise ScheduleError('author check result unavailable')
    state = 'paused' if paused else result.state
    accepted = persist_outcome(state, paused or result.error_code, result.totals)
    if state == 'failed':
        if result.error_code == AccountMismatch.code:
            raise ScheduleAccountError('mismatch')
        raise ScheduleError('author check failed')
    return {'accepted': accepted, 'deferred': 0, 'authors': len(ids)}
