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
from .fs_paths import native_io_path, sqlite_readonly_uri


STATES = frozenset({"queued", "running", "paused", "stopping", "done", "error", "stopped"})
UNFINISHED = frozenset({"queued", "running", "paused", "stopping"})
APPLICATION_ID = 0x50564A31
SCHEMA_VERSION = 2
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
    # Inspect the same spelling used for IO, including every long-path ancestor.
    path = native_io_path(path)
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
        self._last_jobs = {};self.history = {}
        if not self.path.is_absolute():
            raise JobStorageError("job storage requires an absolute path")
        # Keep the caller's logical identity; only filesystem/VFS access uses
        # Windows extended spelling. Do not shorten or relocate the store.
        self._io_path = native_io_path(self.path)
        _regular_unlinked(self.path)
        if self._io_path.exists():
            self._verify()
        self._io_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._io_path.with_name(self._io_path.name + ".lock")
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
            existed = self._io_path.exists()
            if existed:
                self._verify()
            else:
                descriptor = os.open(self._io_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                os.close(descriptor)
            self.connection = sqlite3.connect(self._io_path, check_same_thread=False, timeout=0.1)
            if not existed:
                self.connection.executescript(f"""
                    BEGIN IMMEDIATE;
                    PRAGMA application_id={APPLICATION_ID};
                    PRAGMA user_version={SCHEMA_VERSION};
                    CREATE TABLE jobs (job_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, payload TEXT NOT NULL);
                    CREATE TABLE operations (operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, fingerprint TEXT NOT NULL);
                    CREATE TABLE job_history (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    COMMIT;
                """)
            self.connection.execute("PRAGMA synchronous=FULL")
            if self.connection.execute("PRAGMA user_version").fetchone()[0]==1:
                with self.connection:
                    self.connection.execute("BEGIN IMMEDIATE")
                    self.connection.execute("CREATE TABLE job_history (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                    self.connection.execute("PRAGMA user_version=2")
            identity = self._io_path.stat()
            self.identity = identity.st_dev, identity.st_ino
        except BaseException:
            self.close()
            raise

    def _verify(self):
        connection = None
        try:
            connection = sqlite3.connect(sqlite_readonly_uri(self.path), uri=True)
            if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] not in (1,SCHEMA_VERSION)):
                raise JobStorageError("unmanaged job database rejected")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version=connection.execute("PRAGMA user_version").fetchone()[0]
            if tables != ({"jobs", "operations"} if version==1 else {"jobs","operations","job_history"}):
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
                "SELECT operation_id,job_id,fingerprint FROM operations ORDER BY rowid")}
            if len(operations) > MAX_OPERATION_IDS:
                raise ValueError()
            history={}
            bindings=set(operations.values())
            bound={jid for jid,_ in bindings}
            for jid,payload in self.connection.execute('SELECT job_id,payload FROM job_history'):
                if len(payload)>8192:raise ValueError()
                item=JobSnapshot(**json.loads(payload))
                if (item.job_id!=jid or jid not in bound or item.state not in ('done','error','stopped')
                    or type(item.attempt) is not int or item.attempt<1
                    or type(item.revision) is not int or item.revision<1
                    or type(item.control_revision) is not int or item.control_revision<1
                    or type(item.done) is not int or type(item.total) is not int or not 0<=item.done<=item.total):raise ValueError()
                if re.fullmatch(r'[a-f0-9]{32}',jid) is None:raise ValueError()
                _plain(item.kind,maximum=32);_plain(item.target_id,maximum=128);_plain(item.title,empty=True)
                _plain(item.error_code,maximum=128,empty=True)
                if item.result is not None:_plain(item.result,maximum=2048,empty=True)
                if item.paused_from is not None:raise ValueError()
                fingerprint=json.dumps((item.kind,item.target_id,item.title),ensure_ascii=True)
                if (jid,fingerprint) not in bindings:raise ValueError()
                history[jid]=item
            self.history=history;self._last_jobs=dict(jobs)
            self._last_operations = operations
            return jobs, operations
        except (ValueError, TypeError, sqlite3.Error):
            raise JobStorageError("saved job state rejected") from None

    def write(self, jobs, operations):
        try:
            _regular_unlinked(self.path)
            current = self._io_path.stat()
            if (current.st_dev, current.st_ino) != self.identity:
                raise JobStorageError("job storage identity changed")
            history=dict(self.history)
            bound={jid for jid,_ in operations.values()}
            for jid,old in self._last_jobs.items():
                if jid in bound and jid not in jobs:
                    history[jid]=(replace(old,state='stopped',error_code='deleted',paused_from=None,
                        revision=old.revision+1,control_revision=old.control_revision+1)
                        if old.state in UNFINISHED else old)
            for jid,item in jobs.items():
                if jid in bound and item.state not in UNFINISHED:history[jid]=item
            updates=[(jid,json.dumps(asdict(item),ensure_ascii=True)) for jid,item in history.items()
                     if self.history.get(jid)!=item]
            with self.connection:
                self.connection.executemany('INSERT INTO job_history VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload',updates)
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
            self.history=history;self._last_jobs=dict(jobs)
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

    def publish(self, callback):
        """Fence an external durable CAS against pause/stop/close/stale attempts.

        The callback must be bounded local I/O and must not wait for a worker.
        Snapshot readers remain nonblocking while the control lock is held.
        """
        if not callable(callback):
            raise ValueError("invalid publication callback")
        with self._service._condition:
            self.checkpoint()
            job = self._service._jobs.get(self._job_id)
            if (job is None or job.attempt != self._attempt or job.state != "running"
                    or self._service._closing or self._service._fatal):
                raise JobCancelled("job no longer active")
            return callback()

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
                # `stopping` without the `interrupted` error marker means an
                # explicit user Stop. A graceful application close, however,
                # also uses `stopping` for an active worker; that transition
                # carries the explicit interrupted marker. Preserve the latter
                # for verified generation reconciliation on restart.
                recovered = {key: (
                    replace(job, state="stopped", revision=job.revision + 1,
                            control_revision=job.control_revision + 1,
                            paused_from=None, error_code=job.error_code)
                    if job.state == "stopping" else
                    replace(job, state="stopped", revision=job.revision + 1,
                            control_revision=job.control_revision + 1,
                            paused_from=None, error_code="interrupted")
                    if job.state in UNFINISHED else job)
                    for key, job in jobs.items()}
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

    @property
    def operation_count(self):
        return len(self._operations)

    def accepted_operations(self):
        """Durable acceptance order, including retired/deleted history.

        This is acceptance evidence only, never evidence of completion and
        never permission to replay an operation whose job has been retired.
        """
        result=[]
        for operation,(jid,raw) in self._operations.items():
            kind,target,title=json.loads(raw)
            result.append((operation,jid,kind,target,title))
        return tuple(result)

    def retained_job(self,job_id):
        """Exact terminal metadata, independent of the bounded display window."""
        with self._condition:
            return self._jobs.get(job_id) or (self._store.history.get(job_id) if self._store else None)

    def restore_retained(self,job_id):
        """Explicit owner/UI action; restore history without executing it."""
        with self._condition:
            if self._closing or self._fatal:raise JobServiceError('job service is unavailable')
            if job_id in self._jobs:return self._jobs[job_id]
            if self._active and self._active[0]==job_id:raise JobServiceError('job is still active')
            old=self._store.history.get(job_id) if self._store else None
            if old is None:return None  # legacy retirement before history existed
            if old.state not in ('error','stopped'):return old
            jobs=dict(self._jobs)
            while len(jobs)>=self._max_jobs:
                key=next((k for k,j in jobs.items() if j.state not in UNFINISHED),None)
                if key is None:raise JobServiceError('job queue is full')
                del jobs[key]
            recovered=replace(old,state='error',paused_from=None,revision=old.revision+1,
                control_revision=old.control_revision+1,error_code=old.error_code or 'restored_stopped')
            jobs[job_id]=recovered;self._commit(jobs)
            return recovered

    def operation_ids(self, job_id: str) -> tuple[str, ...]:
        """Read durable request identities for an existing job without doing I/O.

        Used by a managed executor to recover the exact accepted input. Deleted
        history keeps idempotency tombstones but does not authorize replay.
        """
        published, operations = self._published, self._operations
        if type(job_id) is not str or not any(job.job_id == job_id for job in published):
            return ()
        return tuple(key for key, value in operations.items() if value[0] == job_id)

    def reconcile_interrupted(self, job_id: str, *, receipt: str | None = None,
                              page_count: int | None = None) -> bool:
        """Durably resolve a recovered interrupted job after external verification.

        Only a stopped job marked by restart recovery as `interrupted` may be
        resolved. The owning service must independently validate its accepted
        intent and active receipt before supplying a completion receipt.
        Without one, the interrupted job becomes a retryable error rather
        than remaining permanently stopped with no Retry control.
        """
        if (type(job_id) is not str or
                (receipt is None and page_count is not None) or
                (receipt is not None and (type(receipt) is not str or
                    not receipt or len(receipt) > 2048 or type(page_count) is not int
                    or not 1 <= page_count <= 1000))):
            raise JobServiceError('invalid interrupted job reconciliation')
        with self._condition:
            if self._closing or self._fatal:
                raise JobServiceError('job service is unavailable')
            job = self._jobs.get(job_id)
            if job is None or job.state != 'stopped' or job.error_code != 'interrupted':
                return False
            if receipt is None:
                self._replace(job, state='error', done=0, total=0,
                              result=None, error_code='interrupted')
            else:
                self._replace(job, state='done', done=page_count, total=page_count,
                              result=receipt, error_code='')
            return True

    def reconcile_completed(self, job_id: str, *, expected_result: str, valid: bool) -> bool:
        """Owner-verified completion integrity gate, before exposing a reopened service.

        The runner owner must check its own typed receipt. An invalid result
        becomes a durable retryable error; stopped/cancelled rows are never revived.
        The expected result is a compare-and-set guard against stale inspection.
        """
        _plain(job_id, maximum=32)
        _plain(expected_result, maximum=2048)
        if type(valid) is not bool:
            raise JobServiceError('invalid completed job reconciliation')
        with self._condition:
            if self._closing or self._fatal:
                raise JobServiceError('job service is unavailable')
            job = self._jobs.get(job_id)
            if job is None or job.state != 'done' or job.result != expected_result:
                return False
            if not valid:
                self._replace(job, state='error', done=0, total=0,
                              result=None, error_code='receipt_invalid')
            return True

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
                        outcome = "stopped"
                        reason = ("interrupted" if self._closing and
                                  current.error_code == "interrupted" else "")
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
                                 revision=job.revision + 1, control_revision=job.control_revision + 1,
                                 paused_from=None,
                                 error_code=(job.error_code if job.state == "stopping" else "interrupted"))
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
