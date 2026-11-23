"""Durable fail-closed managed GET gate, not yet wired into product readers.

Every remote GET entrance must explicitly use request(). Only 401, 403, 429,
and a revision are persisted, never URL, Cookie, response or personal IDs.
The storage cannot be cleared by this module: reauthentication and an explicit
unblock workflow require a separate owner-reviewed implementation.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import sqlite3
import stat
from threading import RLock
from urllib.error import HTTPError

from .pixiv_read_client import HTTPStatusRejected, PixivReadError, ResponseRejected


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
_RESOURCE_AUTHORITY = ContextVar('gate_resource_authority', default=None)


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _Overlapped(ctypes.Structure):
        _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                    ('offset', wintypes.DWORD), ('offset_high', wintypes.DWORD),
                    ('event', wintypes.HANDLE)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _create_file = _kernel.CreateFileW
    _create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = (wintypes.HANDLE,)
    _close_handle.restype = wintypes.BOOL
    _read_file = _kernel.ReadFile
    _read_file.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    _read_file.restype = wintypes.BOOL
    _lock_file = _kernel.LockFileEx
    _lock_file.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                           wintypes.DWORD, ctypes.POINTER(_Overlapped))
    _lock_file.restype = wintypes.BOOL
    _unlock_file = _kernel.UnlockFileEx
    _unlock_file.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(_Overlapped))
    _unlock_file.restype = wintypes.BOOL
else:
    import fcntl


def _open_existing_stop_lock(path):
    if os.name != 'nt':
        return path.open('r+b')
    # OPEN_EXISTING, read/write and normal read/write sharing. Open the link
    # itself if a reparse point raced the preceding path validation.
    handle = _create_file(str(path), 0xC0000000, 3, None, 3, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY | os.O_NOINHERIT)
    except BaseException as error:
        def close_handle():
            if not _close_handle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        _run_gate_cleanup(close_handle, "managed gate handle close failed", primary=error)
        raise
    try:
        return os.fdopen(fd, 'r+b')
    except BaseException as error:
        _run_gate_cleanup(lambda: os.close(fd), "managed gate descriptor close failed",
                          primary=error)
        raise


def _lock_open_contended(error):
    return getattr(error, 'winerror', None) == 32  # ERROR_SHARING_VIOLATION


def _read_gate_probe_bytes(stream, length):
    if os.name != 'nt':
        return stream.read(length)
    # The CRT can flatten ERROR_LOCK_VIOLATION to EACCES. Keep the Win32
    # error so the notice can distinguish contention from access denial.
    data = ctypes.create_string_buffer(length)
    read = wintypes.DWORD()
    if not _read_file(msvcrt.get_osfhandle(stream.fileno()), data, length, ctypes.byref(read), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return data.raw[:read.value]


def _probe_inflight_parent(path):
    if os.name != 'nt':
        return
    # FILE_ADD_FILE, normal read/write/delete sharing, OPEN_EXISTING,
    # FILE_FLAG_BACKUP_SEMANTICS (open directory) | FILE_FLAG_OPEN_REPARSE_POINT.
    handle = _create_file(str(path), 2, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    if not _close_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _set_stop_byte_lock(stream, *, lock):
    """Exclusively lock the first byte without waiting, or release that byte."""
    if os.name == 'nt':
        handle = msvcrt.get_osfhandle(stream.fileno())
        position = ctypes.byref(_Overlapped())
        if lock:
            # LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY
            success = _lock_file(handle, 3, 0, 1, 0, position)
        else:
            success = _unlock_file(handle, 0, 1, 0, position)
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        flags = (fcntl.LOCK_EX | fcntl.LOCK_NB) if lock else fcntl.LOCK_UN
        fcntl.flock(stream.fileno(), flags)


class ManagedNetworkStopped(PixivReadError):
    def __init__(self, status_code: int, *, authority: str | None = None):
        self.status_code = status_code
        self.authority = authority
        # PixivReadError.__init__ takes no message; preserve a safe numeric
        # reason while allowing LivePixivReadClient._get_once to propagate the
        # stop instead of converting it to a generic TransportFailed.
        Exception.__init__(self, f"managed network stopped ({status_code})")


class ManagedNetworkGateStorageError(PixivReadError):
    def __init__(self, message: str = "managed gate state unavailable", *, authority: str | None = None,
                 expected_selection: bytes | None = None):
        self.authority = authority
        self.expected_selection = expected_selection
        # All callers supply fixed, non-sensitive messages. Propagate the
        # storage failure across the production GET boundary without fallback.
        Exception.__init__(self, message)


class ManagedNetworkGateBusy(ManagedNetworkGateStorageError):
    """A local lock is contended; the durable STOP state is still unknown."""

    _known_stop: ManagedNetworkStopped | None = None


@contextmanager
def _gate_storage_io(message="managed gate state unavailable"):
    """Classify local gate operations before they reach a transport boundary."""
    try:
        yield
    except (OSError, ValueError, sqlite3.Error) as error:
        raise _carry_gate_cleanup(error, ManagedNetworkGateStorageError(message)) from None


def _attach_gate_cleanup(primary, failure):
    primary.add_note(str(failure))
    primary.gate_cleanup_errors = (*getattr(primary, 'gate_cleanup_errors', ()), failure)


def _carry_gate_cleanup(source, target):
    for failure in getattr(source, 'gate_cleanup_errors', ()):
        _attach_gate_cleanup(target, failure)
    return target


def _gate_cleanup_failure(message, *, authority=None, recheckable=True, error=None):
    failure = error if isinstance(error, ManagedNetworkGateStorageError) else ManagedNetworkGateStorageError(message)
    if failure.authority is None:
        failure.authority = authority or _RESOURCE_AUTHORITY.get()
    if not recheckable:
        failure.cleanup_unrecheckable = True
    return failure


def _run_gate_cleanup(action, message, *, primary=None, authority=None, recheckable=True):
    """Keep the primary outcome and attribute failure to the disposed resource."""
    try:
        action()
    except Exception as error:
        failure = _gate_cleanup_failure(message, authority=authority, recheckable=recheckable, error=error)
        if primary is None:
            if failure is error:
                raise
            raise failure from None
        _attach_gate_cleanup(primary, failure)
        _carry_gate_cleanup(failure, primary)
        if isinstance(primary, ManagedNetworkGateBusy) and primary._known_stop is not None:
            raise _carry_gate_cleanup(primary, primary._known_stop) from None


def _trip_before_admission(trip, stopped):
    """Retry transient transfer contention only before any teardown failure."""
    try:
        trip(stopped.status_code)
    except ManagedNetworkGateStorageError as failure:
        if (isinstance(failure, ManagedNetworkGateBusy)
                and not getattr(stopped, 'gate_cleanup_errors', ())
                and not getattr(failure, 'gate_cleanup_errors', ())):
            # Allow the existing bounded retry only if all outer cleanup
            # succeeds. A failed disposal must retain this known denial.
            failure._known_stop = stopped
            raise
        _attach_gate_cleanup(stopped, failure)
        _carry_gate_cleanup(failure, stopped)
    if getattr(stopped, 'gate_cleanup_errors', ()):
        raise stopped


def _http_gate_rejection(error: HTTPError):
    status = error.code
    primary = HTTPStatusRejected(status) if type(status) is int and 100 <= status <= 599 else ResponseRejected()
    _carry_gate_cleanup(error, primary)
    _run_gate_cleanup(error.close, 'managed GET response close failed', primary=primary, recheckable=False)
    return primary


@contextmanager
def _gate_cleanup(action, message, *, authority=None, recheckable=True):
    owner = authority or _RESOURCE_AUTHORITY.get()
    try:
        yield
    except BaseException as primary:
        _run_gate_cleanup(action, message, primary=primary, authority=owner, recheckable=recheckable)
        raise
    else:
        _run_gate_cleanup(action, message, authority=owner, recheckable=recheckable)


@contextmanager
def _report_stop_authority(authority):
    """Bind an authority before acquiring resources in this scope."""
    token = _RESOURCE_AUTHORITY.set(authority)
    try:
        yield
    except BaseException as error:
        for failure in getattr(error, 'gate_cleanup_errors', ()):
            if failure.authority is None:
                failure.authority = authority
        if (isinstance(error, (ManagedNetworkStopped, ManagedNetworkGateStorageError))
                and error.authority is None):
            error.authority = authority
        raise
    finally:
        _RESOURCE_AUTHORITY.reset(token)


def _byte_lock_contended(error: OSError) -> bool:
    # Call only for the nonblocking lock syscall. Opening or reading a file
    # can also report EACCES and does not establish lock contention.
    if os.name == 'nt':
        return getattr(error, 'winerror', None) == 33  # ERROR_LOCK_VIOLATION
    return error.errno in (errno.EAGAIN, errno.EACCES)


@contextmanager
def _bounded_thread_lock():
    """Fail closed rather than freezing a job/control lock behind a slow GET.

    A fatal HTTP result remains serialized and durable before a later request
    can enter. Temporary contention never clears or changes the stored latch.
    """
    if not _THREAD_LOCK.acquire(timeout=_THREAD_LOCK_TIMEOUT_SECONDS):
        raise ManagedNetworkGateBusy("managed gate thread lock unavailable")
    with _gate_cleanup(_THREAD_LOCK.release, 'managed gate thread lock release failed', recheckable=False):
        yield


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
        except OSError:
            raise ManagedNetworkGateStorageError("managed gate path unavailable") from None
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


def _verify_open_stop_lock(path, stream, expected_identity, *, probe=False):
    """Check an open handle without acquiring or releasing a byte lock."""
    metadata = os.fstat(stream.fileno())
    identity = (metadata.st_dev, metadata.st_ino)
    if (identity != _file_identity(path)
            or (expected_identity is not None and identity != expected_identity)):
        raise ManagedNetworkGateStorageError("managed gate lock identity changed")
    stream.seek(0)
    if metadata.st_size != 1:
        raise ManagedNetworkGateStorageError("managed gate lock content changed")
    # Observation needs the Win32 error number for byte-lock contention.
    sentinel = _read_gate_probe_bytes(stream, 2) if probe else stream.read(2)
    if sentinel != b"0":
        raise ManagedNetworkGateStorageError("managed gate lock content changed")
    return metadata


def _gate_database_uri(path, *, immutable=False):
    from .fs_paths import sqlite_readonly_uri
    return sqlite_readonly_uri(path, immutable=immutable).replace('?mode=ro', '?mode=rw', 1)


@contextmanager
def _open_gate_database(path, *, create=False, immutable=False):
    """Open and close a gate connection at the storage-error boundary."""
    with _gate_storage_io():
        if create:
            conn = sqlite3.connect(path, timeout=0.1)
        else:
            conn = sqlite3.connect(_gate_database_uri(path, immutable=immutable), uri=True, timeout=0.1)
        with _gate_cleanup(conn.close, 'managed gate database close failed'):
            yield conn


def _configure_gate_connection(conn):
    conn.execute("PRAGMA synchronous=FULL")


def _verify_gate_schema(conn):
    objects = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 2").fetchall()
    if (conn.execute("PRAGMA application_id").fetchone()[0] != _APP_ID
            or conn.execute("PRAGMA user_version").fetchone()[0] != _VERSION
            or objects != [("table", "gate", "gate", _GATE_SCHEMA)]):
        raise ManagedNetworkGateStorageError("unmanaged gate database")


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
        with _gate_storage_io():
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
        with _bounded_thread_lock(), ExitStack() as cleanup:
            _reject_links(self.path)
            _reject_links(self.lock_path)
            try:
                # Only the first, explicit initialization may create a lock.
                # On an established gate, a+b silently resurrects a deleted
                # lock and permits a previously open reader to dispatch GET
                # without the historical process-wide exclusion guarantee.
                # x+b also refuses concurrent first creators, fail closed.
                lock_file = (self.lock_path.open('x+b') if initializing
                             else _open_existing_stop_lock(self.lock_path))
            except OSError as error:
                if _lock_open_contended(error):
                    raise _carry_gate_cleanup(error, ManagedNetworkGateBusy(
                        "managed gate lock shared by another process")) from None
                raise _carry_gate_cleanup(error, ManagedNetworkGateStorageError(
                    "managed gate lock unavailable")) from None
            except ValueError as error:
                raise _carry_gate_cleanup(error, ManagedNetworkGateStorageError(
                    "managed gate lock unavailable")) from None
            cleanup.enter_context(_gate_cleanup(lock_file.close, 'managed gate lock close failed'))
            try:
                if os.fstat(lock_file.fileno()).st_size == 0:
                    # Only the explicit first-use path may seed the sentinel.
                    if not initializing:
                        raise ManagedNetworkGateStorageError("managed gate lock truncated")
                    lock_file.write(b"0")
                    lock_file.flush()
                    os.fsync(lock_file.fileno())
                lock_file.seek(0)
                try:
                    _set_stop_byte_lock(lock_file, lock=True)
                except OSError as error:
                    if _byte_lock_contended(error):
                        raise ManagedNetworkGateBusy("managed gate lock busy") from None
                    raise
            except (OSError, ValueError):
                raise ManagedNetworkGateStorageError("managed gate lock unavailable") from None
            cleanup.enter_context(_gate_cleanup(
                lambda: _set_stop_byte_lock(lock_file, lock=False), 'managed gate unlock failed',
                recheckable=False,
            ))
            with _gate_storage_io("managed gate lock unavailable"):
                _verify_open_stop_lock(self.lock_path, lock_file, self._lock_identity)
            yield

    @contextmanager
    def _connection_locked(self, *, allow_create: bool = False):
        with ExitStack() as cleanup, _gate_storage_io():
            _reject_links(self.path)
            existed = self.path.exists()
            if not existed and not allow_create:
                raise ManagedNetworkGateStorageError("managed gate state file missing")
            if (self._database_identity is not None
                    and _file_identity(self.path) != self._database_identity):
                raise ManagedNetworkGateStorageError("managed gate database identity changed")
            conn = cleanup.enter_context(_open_gate_database(self.path, create=not existed))
            _configure_gate_connection(conn)
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
            _verify_gate_schema(conn)
            _reject_links(self.path)
            if (self._database_identity is not None
                    and _file_identity(self.path) != self._database_identity):
                raise ManagedNetworkGateStorageError("managed gate database identity changed")
            yield conn

    @staticmethod
    def _read_state(conn, observed_stop=None) -> NetworkStopState:
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
        if observed_stop is not None and (blocked != 1 or status != observed_stop):
            raise ManagedNetworkGateStorageError("observed managed stop state changed")
        return NetworkStopState(bool(blocked), status, revision)

    def _read_locked(self, conn) -> NetworkStopState:
        state = self._read_state(conn, self._observed_stop_status)
        if state.blocked:
            self._observed_stop_status = state.status_code
        return state

    def _require_open_locked(self):
        with self._connection_locked() as conn:
            state = self._read_locked(conn)
            if state.blocked:
                raise ManagedNetworkStopped(state.status_code)

    def require_allowed(self):
        with self._exclusive():
            self._require_open_locked()

    def snapshot(self) -> NetworkStopState:
        with self._exclusive():
            with self._connection_locked() as conn:
                return self._read_locked(conn)

    def _trip_locked(self, status_code: int) -> NetworkStopState:
        completed = None
        try:
            with self._connection_locked() as conn:
                state = self._read_locked(conn)
                if not state.blocked:
                    conn.execute(
                        "UPDATE gate SET blocked=1,status=?,revision=revision+1 WHERE id=1",
                        (status_code,),
                    )
                    conn.commit()
                    state = self._read_locked(conn)
                completed = state
        except ManagedNetworkGateStorageError as error:
            # Only a failure after the STOP was read back is a cleanup error.
            if completed is not None:
                error.completed_stop = completed
            raise
        return completed

    def trip(self, status_code: int) -> NetworkStopState:
        if type(status_code) is not int or status_code not in STOP_STATUSES:
            raise ValueError("unsupported managed network stop status")
        with self._exclusive():
            return self._trip_locked(status_code)

    @contextmanager
    def request(self):
        """Fence a synchronous GET, persisting fatal HTTP status before release."""
        with self._exclusive():
            self._require_open_locked()
            try:
                yield
            except HTTPStatusRejected as error:
                from .request_policy import durable_stop_status
                if durable_stop_status(error.status_code):
                    try:
                        self._trip_locked(error.status_code)
                    except ManagedNetworkGateStorageError as storage_error:
                        if getattr(storage_error, 'completed_stop', None) is None:
                            # An uncommitted STOP remains a failure. The shared
                            # in-flight fence and outer local latch still apply.
                            _carry_gate_cleanup(error, storage_error)
                            raise
                        _attach_gate_cleanup(error, storage_error)
                raise
