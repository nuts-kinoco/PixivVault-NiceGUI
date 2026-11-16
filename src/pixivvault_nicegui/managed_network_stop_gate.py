"""Durable fail-closed managed GET gate, not yet wired into product readers.

Every remote GET entrance must explicitly use request(). Only 401, 403, 429,
and a revision are persisted, never URL, Cookie, response or personal IDs.
The storage cannot be cleared by this module: reauthentication and an explicit
unblock workflow require a separate owner-reviewed implementation.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock

from .pixiv_read_client import HTTPStatusRejected, PixivReadError


STOP_STATUSES = frozenset({401, 403, 429})
_APP_ID = 0x50564E53
_VERSION = 1
_GATE_SCHEMA = (
    "CREATE TABLE gate (id INTEGER PRIMARY KEY CHECK(id=1),"
    " blocked INTEGER NOT NULL CHECK(blocked IN (0,1)),"
    " status INTEGER, revision INTEGER NOT NULL)"
)
_THREAD_LOCK = RLock()
_THREAD_LOCK_TIMEOUT_SECONDS = 0.5


class ManagedNetworkStopped(PixivReadError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        # PixivReadError.__init__ takes no message; preserve a safe numeric
        # reason while allowing LivePixivReadClient._get_once to propagate the
        # stop instead of converting it to a generic TransportFailed.
        Exception.__init__(self, f"managed network stopped ({status_code})")


class ManagedNetworkGateStorageError(PixivReadError):
    def __init__(self, message: str = "managed gate state unavailable"):
        # All callers supply fixed, non-sensitive messages. Propagate the
        # storage failure across the production GET boundary without fallback.
        Exception.__init__(self, message)


class ManagedNetworkGateBusy(ManagedNetworkGateStorageError):
    """Temporary gate ownership contention; no permission to issue a GET."""


@contextmanager
def _bounded_thread_lock():
    """Fail closed rather than freezing a job/control lock behind a slow GET.

    A fatal HTTP result remains serialized and durable before a later request
    can enter. Temporary contention never clears or changes the stored latch.
    """
    if not _THREAD_LOCK.acquire(timeout=_THREAD_LOCK_TIMEOUT_SECONDS):
        raise ManagedNetworkGateBusy("managed gate thread lock unavailable")
    try:
        yield
    finally:
        _THREAD_LOCK.release()


@dataclass(frozen=True, slots=True)
class NetworkStopState:
    blocked: bool
    status_code: int | None
    revision: int


def _reject_links(path: Path) -> None:
    for entry in (path, *path.parents):
        try:
            metadata = entry.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                   & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ManagedNetworkGateStorageError("managed gate links forbidden")
        if entry == path and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise ManagedNetworkGateStorageError("managed gate hard links forbidden")


def _file_identity(path: Path) -> tuple[int, int]:
    """Pin the OS file identity, not merely a replaceable pathname.

    A live gate must not silently adopt a newly created lock or an older
    unblocked SQLite copy after the name is replaced. Filesystems that cannot
    supply a stable inode/file-index are not safe to use for this boundary.
    """
    _reject_links(path)
    try:
        metadata = path.stat()
    except (OSError, ValueError):
        raise ManagedNetworkGateStorageError("managed gate file unavailable") from None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or not metadata.st_ino):
        raise ManagedNetworkGateStorageError("managed gate file identity unavailable")
    return metadata.st_dev, metadata.st_ino


class ManagedNetworkStopGate:
    """Synchronous cross-thread/process guard; async clients need a separate fence.

    All managed network entry points must use the same database path. A one-byte
    OS lock is held across the GET so a typed fatal status is persisted before
    any subsequent guarded GET begins. Lock contention fails closed, not open.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ManagedNetworkGateStorageError("absolute gate path required")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._database_identity: tuple[int, int] | None = None
        self._lock_identity: tuple[int, int] | None = None
        # A live gate must never forget a STOP it has already seen, even if
        # its SQLite row is later rewritten to another syntactically valid
        # state while the file identity remains unchanged. This supplements,
        # but does not replace, a separately verified restart-time history.
        self._observed_stop_status: int | None = None
        _reject_links(self.path)
        _reject_links(self.lock_path)
        # A missing state file alongside a surviving lock/identity marker must
        # never silently reset a previously latched network stop.
        fresh = not self.path.exists() and not self.lock_path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._exclusive(initializing=fresh):
            with self._connection_locked(allow_create=fresh) as conn:
                self._read_locked(conn)
            # Capture under the same OS lock used by every later request.
            self._database_identity = _file_identity(self.path)
            self._lock_identity = _file_identity(self.lock_path)

    @contextmanager
    def _exclusive(self, *, initializing: bool = False):
        with _bounded_thread_lock():
            _reject_links(self.path)
            _reject_links(self.lock_path)
            try:
                # Only the first, explicit initialization may create a lock.
                # On an established gate, a+b silently resurrects a deleted
                # lock and permits a previously open reader to dispatch GET
                # without the historical process-wide exclusion guarantee.
                # x+b also refuses concurrent first creators, fail closed.
                lock_file = self.lock_path.open("x+b" if initializing else "r+b")
            except (OSError, ValueError):
                raise ManagedNetworkGateStorageError("managed gate lock unavailable") from None
            with lock_file:
                try:
                    if os.fstat(lock_file.fileno()).st_size == 0:
                        # Zero bytes are valid only while creating this gate.
                        # Re-seeding a truncated established lock would hide
                        # loss of its exclusion history at the same file id.
                        if not initializing:
                            raise ManagedNetworkGateStorageError(
                                "managed gate lock truncated"
                            )
                        lock_file.write(b"0")
                        lock_file.flush()
                        os.fsync(lock_file.fileno())
                    lock_file.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    # A second process may hold the byte lock across its GET.
                    # Its lifetime is not evidence of corrupt durable state.
                    # Permission/open errors remain an ordinary fatal storage
                    # error, and neither class authorizes any network I/O.
                    if error.errno in (errno.EAGAIN, errno.EACCES):
                        raise ManagedNetworkGateBusy("managed gate lock busy") from None
                    raise ManagedNetworkGateStorageError("managed gate lock unavailable") from None
                except ValueError:
                    raise ManagedNetworkGateStorageError("managed gate lock unavailable") from None
                try:
                    metadata = os.fstat(lock_file.fileno())
                    opened_identity = (metadata.st_dev, metadata.st_ino)
                    if (not metadata.st_ino
                            or _file_identity(self.lock_path) != opened_identity
                            or (self._lock_identity is not None
                                and opened_identity != self._lock_identity)):
                        raise ManagedNetworkGateStorageError(
                            "managed gate lock identity changed"
                        )
                    # The initialized lock must remain the exact one-byte
                    # sentinel. In-place overwrite/append preserves its file
                    # identity but destroys the durable exclusion invariant.
                    lock_file.seek(0)
                    if metadata.st_size != 1 or lock_file.read(2) != b"0":
                        raise ManagedNetworkGateStorageError(
                            "managed gate lock content changed"
                        )
                    yield
                finally:
                    try:
                        lock_file.seek(0)
                        if os.name == "nt":
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except (OSError, ValueError):
                        raise ManagedNetworkGateStorageError("managed gate unlock failed") from None

    @contextmanager
    def _connection_locked(self, *, allow_create: bool = False):
        conn = None
        try:
            _reject_links(self.path)
            existed = self.path.exists()
            if not existed and not allow_create:
                raise ManagedNetworkGateStorageError("managed gate state file missing")
            if (self._database_identity is not None
                    and _file_identity(self.path) != self._database_identity):
                raise ManagedNetworkGateStorageError("managed gate database identity changed")
            conn = sqlite3.connect(self.path, timeout=0.1)
            conn.execute("PRAGMA synchronous=FULL")
            if not existed:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(f"PRAGMA application_id={_APP_ID}")
                conn.execute(f"PRAGMA user_version={_VERSION}")
                conn.execute(_GATE_SCHEMA)
                conn.execute("INSERT INTO gate VALUES (1,0,NULL,0)")
                conn.commit()
            # Checking only table names is insufficient: a trigger can
            # silently suppress the irreversible STOP update while a changed
            # table declaration can remove its CHECK constraints. No views,
            # indexes or triggers are part of our explicitly sealed schema.
            schema_objects = conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 2"
            ).fetchall()
            if (conn.execute("PRAGMA application_id").fetchone()[0] != _APP_ID
                    or conn.execute("PRAGMA user_version").fetchone()[0] != _VERSION
                    or schema_objects != [("table", "gate", "gate", _GATE_SCHEMA)]):
                raise ManagedNetworkGateStorageError("unmanaged gate database")
            _reject_links(self.path)
            if (self._database_identity is not None
                    and _file_identity(self.path) != self._database_identity):
                raise ManagedNetworkGateStorageError("managed gate database identity changed")
            yield conn
        except (sqlite3.Error, OSError, ValueError):
            raise ManagedNetworkGateStorageError("managed gate state unavailable") from None
        finally:
            if conn is not None:
                conn.close()

    def _read_locked(self, conn) -> NetworkStopState:
        # Never ignore an extra row: a corrupt history is not evidence that
        # the ordinary id=1 row still gives safe permission for another GET.
        # LIMIT 2 bounds work even if someone injected millions of rows.
        rows = conn.execute("SELECT id,blocked,status,revision FROM gate LIMIT 2").fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1:
            raise ManagedNetworkGateStorageError("invalid managed gate state")
        _, blocked, status, revision = rows[0]
        if (type(blocked) is not int or blocked not in (0, 1)
                or type(revision) is not int or revision < 0
                or (blocked == 0 and status is not None)
                or (blocked == 1 and status not in STOP_STATUSES)
                # This gate can write only (0, NULL, 0) initially and a
                # single irreversible STOP transition to (1, status, 1).
                # Treat other revisions as possible out-of-band alteration,
                # never as permission to dispatch another managed GET.
                or (blocked == 0 and revision != 0)
                or (blocked == 1 and revision != 1)):
            raise ManagedNetworkGateStorageError("invalid managed gate state")
        if self._observed_stop_status is not None:
            if blocked != 1 or status != self._observed_stop_status:
                raise ManagedNetworkGateStorageError("observed managed stop state changed")
        elif blocked == 1:
            self._observed_stop_status = status
        return NetworkStopState(bool(blocked), status, revision)

    def snapshot(self) -> NetworkStopState:
        with self._exclusive():
            with self._connection_locked() as conn:
                return self._read_locked(conn)

    def _trip_locked(self, status_code: int) -> NetworkStopState:
        with self._connection_locked() as conn:
            state = self._read_locked(conn)
            if not state.blocked:
                conn.execute(
                    "UPDATE gate SET blocked=1,status=?,revision=revision+1 WHERE id=1",
                    (status_code,),
                )
                conn.commit()
                state = self._read_locked(conn)
            return state

    def trip(self, status_code: int) -> NetworkStopState:
        if type(status_code) is not int or status_code not in STOP_STATUSES:
            raise ValueError("unsupported managed network stop status")
        with self._exclusive():
            return self._trip_locked(status_code)

    @contextmanager
    def request(self):
        """Fence a synchronous GET, persisting fatal HTTP status before release."""
        with self._exclusive():
            with self._connection_locked() as conn:
                state = self._read_locked(conn)
            if state.blocked:
                raise ManagedNetworkStopped(state.status_code)
            try:
                yield
            except HTTPStatusRejected as error:
                from .request_policy import durable_stop_status
                if durable_stop_status(error.status_code):
                    self._trip_locked(error.status_code)
                raise
