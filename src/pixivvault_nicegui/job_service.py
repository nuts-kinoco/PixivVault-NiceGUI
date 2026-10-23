"""Application-owned serial job execution, with optional dedicated persistence.

No legacy app, network client, cookie, or default database is imported here.
The injected runner must raise on failure and return a completion receipt.
Views own only filters/selection; accepted jobs outlive individual views.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import Condition, RLock, Thread, current_thread
from uuid import uuid4


STATES = frozenset({"queued", "running", "paused", "stopping", "done", "error", "stopped"})
UNFINISHED = frozenset({"queued", "running", "paused", "stopping"})
APPLICATION_ID = 0x50564A31
SCHEMA_VERSION = 1
MAX_OPERATION_IDS = 10000


class JobServiceError(RuntimeError):
    pass


class JobCancelled(JobServiceError):
    pass


class JobStorageError(JobServiceError):
    pass


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    kind: str
    target_id: str
    title: str
    state: str = "queued"
    done: int = 0
    total: int = 0
    revision: int = 1
    attempt: int = 1
    error_code: str = ""
    result: str | None = None
    paused_from: str | None = None
    control_revision: int = 1


def _plain(value, *, maximum=256, empty=False) -> str:
    if (type(value) is not str or len(value) > maximum or (not empty and not value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid job field")
    return value


def _regular_unlinked(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise JobStorageError("job storage links are not allowed")
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise JobStorageError("job storage reparse points are not allowed")
        if item == path and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise JobStorageError("job storage hard links are not allowed")


class _Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.connection = None
        self.lock_file = None
        self._last_operations = None
        if not self.path.is_absolute():
            raise JobStorageError("job storage requires an absolute path")
        _regular_unlinked(self.path)
        if self.path.exists():
            self._verify()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        _regular_unlinked(lock)
        try:
            self.lock_file = lock.open("a+b")
            if os.fstat(self.lock_file.fileno()).st_size == 0:
                self.lock_file.write(b"0")
                self.lock_file.flush()
            self.lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError):
            if self.lock_file is not None:
                self.lock_file.close()
                self.lock_file = None
            raise JobStorageError("job storage is already owned or unavailable") from None
        try:
            _regular_unlinked(self.path)
            existed = self.path.exists()
            if existed:
                self._verify()
            else:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(descriptor)
            self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=0.1)
            if not existed:
                self.connection.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE jobs (job_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE operations (operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, fingerprint TEXT NOT NULL);
                    COMMIT;
                """)
            self.connection.execute("PRAGMA synchronous=FULL")
            identity = self.path.stat()
            self.identity = identity.st_dev, identity.st_ino
        except BaseException:
            self.close()
            raise

    def _verify(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
            if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
                raise JobStorageError("unmanaged job database rejected")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"jobs", "operations"}:
                raise JobStorageError("unmanaged job database rejected")
        except sqlite3.Error:
            raise JobStorageError("unmanaged job database rejected") from None
        finally:
            if connection is not None:
                connection.close()

    def load(self):
        try:
            jobs = {}
            for job_id, payload in self.connection.execute("SELECT job_id,payload FROM jobs ORDER BY ordinal"):
                if len(payload) > 8192:
                    raise ValueError()
                job = JobSnapshot(**json.loads(payload))
                if (job.job_id != job_id or not re.fullmatch(r"[a-f0-9]{32}", job_id)
                        or job.state not in STATES or type(job.attempt) is not int or job.attempt < 1
                        or type(job.revision) is not int or job.revision < 1
                        or type(job.control_revision) is not int or job.control_revision < 1
                        or type(job.done) is not int or type(job.total) is not int
                        or not 0 <= job.done <= job.total):
                    raise ValueError()
                _plain(job.kind, maximum=32)
                _plain(job.target_id, maximum=128)
                _plain(job.title, empty=True)
                jobs[job_id] = job
            operations = {op: (jid, fingerprint) for op, jid, fingerprint in self.connection.execute(
                "SELECT operation_id,job_id,fingerprint FROM operations")}
            if len(operations) > MAX_OPERATION_IDS:
                raise ValueError()
            self._last_operations = operations
            return jobs, operations
        except (ValueError, TypeError, sqlite3.Error):
            raise JobStorageError("saved job state rejected") from None

    def write(self, jobs, operations):
        try:
            _regular_unlinked(self.path)
            current = self.path.stat()
            if (current.st_dev, current.st_ino) != self.identity:
                raise JobStorageError("job storage identity changed")
            with self.connection:
                self.connection.execute("DELETE FROM jobs")
                self.connection.executemany("INSERT INTO jobs VALUES (?,?,?)", [
                    (job.job_id, index, json.dumps(asdict(job), ensure_ascii=True))
                    for index, job in enumerate(jobs.values())])
                if operations is not self._last_operations:
                    previous = self._last_operations or {}
                    if (len(operations) > MAX_OPERATION_IDS or any(
                            operations.get(key) != value for key, value in previous.items())):
                        raise JobStorageError("operation ledger mutation rejected")
                    self.connection.executemany("INSERT INTO operations VALUES (?,?,?)", [
                        (op, value[0], value[1]) for op, value in operations.items() if op not in previous])
            self._last_operations = operations
        except (OSError, sqlite3.Error):
            raise JobStorageError("job state could not be saved") from None

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.lock_file is not None:
            self.lock_file.close()
            self.lock_file = None


class JobContext:
    def __init__(self, service, job_id: str, attempt: int):
        self._service = service
        self._job_id = job_id
        self._attempt = attempt

    def checkpoint(self) -> None:
        service = self._service
        with service._condition:
            while True:
                job = service._jobs.get(self._job_id)
                if (service._closing or service._fatal or job is None or job.attempt != self._attempt
                        or job.state in {"stopping", "stopped", "done", "error"}):
                    raise JobCancelled("job no longer active")
                if job.state != "paused":
                    return
                service._condition.wait()

    def progress(self, done: int, total: int) -> None:
        if type(done) is not int or type(total) is not int or not 0 <= done <= total:
            raise ValueError("invalid job progress")
        service = self._service
        with service._condition:
            # Keep the checkpoint and progress publication in one lock scope.
            # Otherwise pause could land between them and look like a cancel.
            self.checkpoint()
            job = service._jobs.get(self._job_id)
            if (job is None or job.attempt != self._attempt or job.state != "running"
                    or service._closing or service._fatal):
                raise JobCancelled("job no longer active")
            if done < job.done or (job.total and total != job.total):
                raise ValueError("job progress cannot move backwards or change total")
            service._replace(job, done=done, total=total)


class JobService:
    """Serial execution; completed snapshots are published only after persistence."""

    def __init__(self, runner, storage: Path | None = None, *, max_jobs: int = 50):
        if not callable(runner) or type(max_jobs) is not int or max_jobs < 1:
            raise ValueError("invalid job service configuration")
        self._runner = runner
        self._max_jobs = max_jobs
        self._condition = Condition(RLock())
        self._jobs: dict[str, JobSnapshot] = {}
        self._published: tuple[JobSnapshot, ...] = ()
        self._operations = {}
        self._store = None
        self._thread = None
        self._active: tuple[str, int] | None = None
        self._closing = False
        self._closed = False
        self._fatal = False
        if storage is not None:
            store = _Store(Path(storage))
            try:
                jobs, operations = store.load()
                recovered = {key: replace(job, state="stopped", revision=job.revision + 1,
                                          control_revision=job.control_revision + 1,
                                          paused_from=None, error_code="interrupted")
                             if job.state in UNFINISHED else job for key, job in jobs.items()}
                store.write(recovered, operations)
                self._jobs, self._operations = recovered, operations
                self._published = tuple(reversed(tuple(recovered.values())))
                self._store = store
            except BaseException:
                store.close()
                raise

    @property
    def closed(self):
        return self._closed

    @property
    def faulted(self):
        return self._fatal

    def snapshot(self) -> tuple[JobSnapshot, ...]:
        # Readers run on the UI loop. Never wait for SQLite fsync or a writer's
        # control lock: publish one immutable tuple only after commit succeeds.
        return self._published

    def _commit(self, jobs, operations=None):
        operations = self._operations if operations is None else operations
        if self._store is not None:
            self._store.write(jobs, operations)
        self._jobs = jobs
        self._operations = operations
        self._published = tuple(reversed(tuple(jobs.values())))
        self._condition.notify_all()

    def _replace(self, job, **fields):
        if any(key in fields for key in ("state", "attempt")):
            fields["control_revision"] = job.control_revision + 1
        updated = replace(job, revision=job.revision + 1, **fields)
        self._commit(dict(self._jobs, **{job.job_id: updated}))
        return updated

    def enqueue(self, kind: str, target_id: str, title: str = "", operation_id: str | None = None) -> JobSnapshot:
        _plain(kind, maximum=32)
        _plain(target_id, maximum=128)
        _plain(title, empty=True)
        if operation_id is not None:
            _plain(operation_id, maximum=128)
        fingerprint = json.dumps((kind, target_id, title), ensure_ascii=True)
        with self._condition:
            if self._closing or self._fatal:
                raise JobServiceError("job service is unavailable")
            if operation_id in self._operations:
                jid, previous = self._operations[operation_id]
                if previous != fingerprint or jid not in self._jobs:
                    raise JobServiceError("operation already used")
                return self._jobs[jid]
            if operation_id is not None and len(self._operations) >= MAX_OPERATION_IDS:
                # Keep old idempotency tombstones instead of silently allowing
                # an old request to replay. The finite migration sandbox stops
                # accepting new operation IDs when its stated cap is reached.
                raise JobServiceError("job operation history is full")
            for job in self._jobs.values():
                if job.kind == kind and job.target_id == target_id and job.state in UNFINISHED:
                    if operation_id is not None:
                        operations = dict(self._operations)
                        operations[operation_id] = (job.job_id, fingerprint)
                        self._commit(dict(self._jobs), operations)
                    return job
            jobs = dict(self._jobs)
            while len(jobs) >= self._max_jobs:
                terminal = next((key for key, value in jobs.items() if value.state not in UNFINISHED), None)
                if terminal is None:
                    raise JobServiceError("job queue is full")
                del jobs[terminal]
            job = JobSnapshot(uuid4().hex, kind, target_id, title)
            jobs[job.job_id] = job
            operations = dict(self._operations)
            if operation_id is not None:
                operations[operation_id] = (job.job_id, fingerprint)
            self._commit(jobs, operations)
            if self._thread is None:
                self._thread = Thread(target=self._work, name="pixivvault-jobs", daemon=True)
                self._thread.start()
            return job

    def control(self, job_id: str, action: str, expected_revision: int | None = None,
                expected_control_revision: int | None = None) -> bool:
        with self._condition:
            if self._closing or self._fatal:
                return False
            job = self._jobs.get(job_id)
            if job is None or (expected_revision is not None and (
                    type(expected_revision) is not int or expected_revision != job.revision)):
                return False
            if (expected_control_revision is not None and
                    (type(expected_control_revision) is not int or expected_control_revision != job.control_revision)):
                return False
            if action == "delete":
                # The in-flight context keeps its identity and sees absence as
                # cancellation at its next checkpoint; it cannot revive the row.
                jobs = dict(self._jobs)
                del jobs[job_id]
                self._commit(jobs)
                return True
            if action == "pause" and job.state in {"queued", "running"}:
                self._replace(job, state="paused", paused_from=job.state)
            elif action == "resume" and job.state == "paused":
                self._replace(job, state=job.paused_from or "queued", paused_from=None)
            elif action == "stop" and job.state in {"queued", "running", "paused"}:
                self._replace(job, state="stopping", paused_from=None)
            elif action == "retry" and job.state == "error":
                if any(other.job_id != job_id and other.kind == job.kind and other.target_id == job.target_id
                       and other.state in UNFINISHED for other in self._jobs.values()):
                    return False
                self._replace(job, state="queued", attempt=job.attempt + 1, done=0, total=0,
                              error_code="", result=None, paused_from=None)
                if self._thread is None:
                    self._thread = Thread(target=self._work, name="pixivvault-jobs", daemon=True)
                    self._thread.start()
            else:
                return False
            return True

    def _work(self):
        while True:
            with self._condition:
                if self._closing or self._fatal:
                    return
                try:
                    for job in tuple(self._jobs.values()):
                        if job.state == "stopping":
                            self._replace(job, state="stopped")
                    # A queued-origin pause reserves its own job only; it must
                    # not prevent later queued jobs from running serially.
                    next_job = next((job for job in self._jobs.values() if job.state == "queued"), None)
                    if next_job is None:
                        self._condition.wait()
                        continue
                    job = self._replace(next_job, state="running")
                    self._active = (job.job_id, job.attempt)
                except JobStorageError:
                    self._fatal = True
                    self._condition.notify_all()
                    return
            context = JobContext(self, job.job_id, job.attempt)
            result, outcome, reason = None, "done", ""
            try:
                result = self._runner(job, context)
                if type(result) is not str or not result or len(result) > 2048:
                    raise ValueError("runner did not produce a completion receipt")
                context.checkpoint()
            except JobCancelled:
                outcome = "stopped"
            except Exception:
                outcome, reason = "error", "runner_failed"
            with self._condition:
                current = self._jobs.get(job.job_id)
                self._active = None
                if current is not None and current.attempt == job.attempt:
                    if self._closing or current.state in {"stopping", "stopped"}:
                        outcome, reason = "stopped", ""
                    try:
                        self._replace(current, state=outcome, paused_from=None, error_code=reason,
                                      result=result if outcome == "done" else None)
                    except JobStorageError:
                        self._fatal = True
                        # A storage fault is a separate volatile diagnostic.
                        # Keep the last committed snapshot; never publish a
                        # terminal state which the database did not accept.
                self._condition.notify_all()

    def close(self, *, timeout: float = 10.0) -> None:
        with self._condition:
            if self._closed:
                return
            if self._thread is current_thread():
                raise JobServiceError("job worker cannot close its owner")
            self._closing = True
            jobs = {key: replace(job, state="stopping" if self._active and self._active[0] == key else "stopped",
                                 revision=job.revision + 1, control_revision=job.control_revision + 1, paused_from=None)
                    if job.state in UNFINISHED else job for key, job in self._jobs.items()}
            try:
                self._commit(jobs)
            except JobStorageError:
                self._fatal = True
                self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise JobServiceError("job worker has not stopped")
        with self._condition:
            if self._store is not None:
                self._store.close()
            self._closed = True
