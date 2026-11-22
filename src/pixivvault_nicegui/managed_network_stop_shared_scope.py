"""Opening and enforcing a sealed Windows-user-wide Pixiv GET stop owner.

Normal first-use publication belongs to managed_network_stop_startup. Existing
historical STOPs are retained, and formal migration owners remain readable.

The anchor comes from the Windows Known Folder API, not an environment
variable, CLI switch, Cookie path, profile root, current directory or media
root. No secrets, URLs, account IDs or Cookie contents are written here.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from uuid import uuid4

from .managed_network_stop_gate import (
    ManagedNetworkGateBusy,
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
    _report_stop_authority,
    _run_gate_cleanup,
    _http_gate_rejection,
    _gate_storage_io,
    _gate_cleanup,
    _carry_gate_cleanup,
    STOP_STATUSES,
    _file_identity,
    _reject_links,
)
from .pixiv_read_client import HTTPStatusRejected, PixivReadError
from .request_policy import durable_stop_status


_OWNER_FILE = ".pixivvault-user-stop-owner.v1.json"
_GATE_FILE = ".pixivvault-user-stop.sqlite3"
_OWNER_SCHEMA = "pixivvault-managed-get-user-owner-v1"
_STARTUP_OWNER_SCHEMA = "pixivvault-managed-get-user-startup-owner-v1"
_INFLIGHT_FILE = ".pixivvault-user-stop-inflight.v1"
_INFLIGHT_BYTES = b"pixivvault-user-pixiv-get-inflight-v1\n"


class SharedGetOutcomeUncertain(ManagedNetworkGateStorageError):
    code = 'shared-get-outcome-uncertain'

    def __init__(self):
        super().__init__('prior shared GET outcome uncertain')


class SharedStopOwnerAbsent(ManagedNetworkGateStorageError):
    """No shared owner directory exists; this grants no network authority."""


@dataclass(frozen=True)
class SharedGetRecoveryPlan:
    marker_identity: tuple[int, int]
    marker_mtime_ns: int
    gate_revision: int


class ManagedUserNetworkStopGate(ManagedNetworkStopGate):
    """A durable pre-GET uncertainty fence atop the ordinary stopped gate.

    A fatal HTTP response may arrive while the shared SQLite commit fails.
    Before EVERY guarded GET, persist a no-secret in-flight marker under the
    same process/thread lock. A failed commit or interrupted GET preserves the
    marker, and every later CLI/Native admission refuses it after restart.
    Completed harmless requests remove their own markers. A separate explicit,
    owner-checked recovery may archive an abandoned intent without clearing STOP.
    """

    def __init__(self, path: Path):
        super().__init__(path)
        self.inflight_path = self.path.with_name(_INFLIGHT_FILE)

    def _has_inflight(self) -> bool:
        _reject_links(self.inflight_path)
        try:
            self.inflight_path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise ManagedNetworkGateStorageError("shared GET history unavailable") from None
        return True

    def _no_inflight(self) -> None:
        if self._has_inflight():
            raise SharedGetOutcomeUncertain()

    def require_allowed(self):
        with self._exclusive():
            self._require_open_locked()
            self._no_inflight()

    def _begin_inflight(self):
        self._no_inflight()
        try:
            stream = self.inflight_path.open("xb")
            with _gate_cleanup(stream.close, 'shared GET intent close failed', authority='shared'):
                stream.write(_INFLIGHT_BYTES)
                stream.flush()
                os.fsync(stream.fileno())
            return _file_identity(self.inflight_path)
        except (OSError, ValueError) as error:
            # Keep any partial marker and the failed resource's evidence.
            raise _carry_gate_cleanup(error, ManagedNetworkGateStorageError(
                "shared GET intent not durable")) from None

    def _finish_inflight(self, identity):
        if _file_identity(self.inflight_path) != identity:
            raise ManagedNetworkGateStorageError("shared GET intent replaced")
        try:
            self.inflight_path.unlink()
        except OSError:
            raise ManagedNetworkGateStorageError("shared GET completion not durable") from None

    def snapshot(self):
        state = super().snapshot()
        if not state.blocked:
            self._no_inflight()
        return state

    @contextmanager
    def request(self):
        with super().request():
            identity = self._begin_inflight()
            try:
                yield
            except HTTPStatusRejected as error:
                if not durable_stop_status(error.status_code):
                    _run_gate_cleanup(lambda: self._finish_inflight(identity),
                                      'shared GET completion not durable', primary=error)
                # Fatal: leave the pre-GET marker. Only a durable gate trip
                # may prove STOP; if that SQLite write fails the next reader
                # still sees the marker and refuses all further GETs.
                raise
            except HTTPError as error:
                rejected = _http_gate_rejection(error)
                if not isinstance(rejected, HTTPStatusRejected) or not durable_stop_status(rejected.status_code):
                    _run_gate_cleanup(lambda: self._finish_inflight(identity),
                                      'shared GET completion not durable', primary=rejected)
                raise rejected from None
            except ManagedNetworkStopped as stopped:
                if stopped.status_code not in STOP_STATUSES:
                    raise ManagedNetworkGateStorageError("unknown raw shared STOP") from None
                raise HTTPStatusRejected(stopped.status_code) from None
            except PixivReadError as error:
                if isinstance(error, ManagedNetworkGateStorageError):
                    # A failed STOP write or uncertain shared ownership is
                    # never evidence that this GET completed harmlessly.
                    raise
                # Real owned HTTPS transport now preserves any observed fatal
                # status before mapping cancel, timeout, response rejection or
                # other ordinary errors. These nonfatal PixivReadErrors may
                # safely free the intent and retain normal retry/cleanup.
                _run_gate_cleanup(lambda: self._finish_inflight(identity),
                                  'shared GET completion not durable', primary=error)
                raise
            else:
                self._finish_inflight(identity)


def _file_pair(value):
    if (type(value) is not list or len(value) != 2
            or any(type(i) is not int or i < 0 for i in value)):
        raise ManagedNetworkGateStorageError("shared owner file identity invalid")
    return tuple(value)


def _readonly_owner_json(path: Path) -> dict:
    _reject_links(path)
    try:
        if not path.is_file() or path.stat().st_size > 4096:
            raise ManagedNetworkGateStorageError("shared owner marker unavailable")
        stream = path.open("rb")
        with _gate_cleanup(stream.close, 'shared STOP owner marker close failed', authority='shared'):
            data = stream.read(4097)
        if len(data) > 4096:
            raise ManagedNetworkGateStorageError("shared owner marker too large")
        def unique_pairs(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ManagedNetworkGateStorageError("duplicate shared owner marker key")
                result[k] = v
            return result
        marker = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs)
        if (type(marker) is not dict
                or set(marker) != {"schema", "state", "root_identity", "gate_identity", "lock_identity", "history_proof"}
                or marker["schema"] not in (_OWNER_SCHEMA, _STARTUP_OWNER_SCHEMA)
                or marker["state"] != "sealed"
                or type(marker["history_proof"]) is not str
                or len(marker["history_proof"]) != 64
                or any(c not in "0123456789abcdef" for c in marker["history_proof"])):
            raise ManagedNetworkGateStorageError("shared owner migration proof missing")
        return marker
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise _carry_gate_cleanup(error, ManagedNetworkGateStorageError(
            "shared owner marker unreadable")) from None


class ManagedUserNetworkStopScope:
    """Open an existing sealed owner; never create or reset its STOP database."""

    def __init__(self, owner_root: Path):
        root = Path(owner_root)
        from .test_owner_runtime import require_inside
        require_inside(root)
        if not root.is_absolute() or root == root.parent:
            raise ManagedNetworkGateStorageError("absolute shared owner required")
        _reject_links(root)
        if not root.is_dir():
            raise ManagedNetworkGateStorageError("shared owner migration required")
        marker = _readonly_owner_json(root / _OWNER_FILE)
        database = root / _GATE_FILE
        lock = root / (_GATE_FILE + ".lock")
        # Check the sealed identities BEFORE constructing ManagedNetworkStopGate:
        # its legacy standalone constructor can initialize an absent DB+lock.
        # A seal that remains after loss of both files must not be reset.
        if (_file_pair(marker["root_identity"]) != _file_identity_directory(root)
                or _file_pair(marker["gate_identity"]) != _file_identity(database)
                or _file_pair(marker["lock_identity"]) != _file_identity(lock)):
            raise ManagedNetworkGateStorageError("shared STOP owner identity changed")
        gate = ManagedUserNetworkStopGate(database)
        if (gate._database_identity != _file_pair(marker["gate_identity"])
                or gate._lock_identity != _file_pair(marker["lock_identity"])):
            raise ManagedNetworkGateStorageError("shared STOP gate replaced")
        self.root = root
        self.gate = gate
        self._root_identity = _file_pair(marker["root_identity"])
        self._gate_identity = _file_pair(marker["gate_identity"])
        self._lock_identity = _file_pair(marker["lock_identity"])
        self._owner_schema = marker['schema']
        self._history_proof = marker['history_proof']
        from .managed_network_stop_startup import check_startup_provenance
        check_startup_provenance(self)

    @_gate_storage_io("shared STOP owner unavailable")
    def _check_owner(self):
        from .managed_network_stop_startup import check_startup_provenance
        check_startup_provenance(self)
        marker = _readonly_owner_json(self.root / _OWNER_FILE)
        if (marker['schema'] != self._owner_schema
                or marker['history_proof'] != self._history_proof
                or _file_pair(marker["root_identity"]) != self._root_identity
                or _file_identity_directory(self.root) != self._root_identity
                or _file_pair(marker["gate_identity"]) != self._gate_identity
                or _file_pair(marker["lock_identity"]) != self._lock_identity
                or _file_identity(self.gate.path) != self._gate_identity
                or _file_identity(self.gate.lock_path) != self._lock_identity):
            raise ManagedNetworkGateStorageError("shared STOP owner changed")

    def create_reader(self, cookie_path, *, transport=None):
        from .managed_network_stop_reader import create_exact_managed_read_client
        self._check_owner()
        client = create_exact_managed_read_client(cookie_path, self.gate, transport=transport)
        try:
            # The transport must be checked at every GET, not just creation.
            original = client._transport
            client._transport = _SealedOwnerTransport(self, original)
            return client
        except BaseException as error:
            _run_gate_cleanup(client.close, 'managed reader close failed', primary=error, authority='shared')
            raise

    def require_allowed(self):
        with _report_stop_authority('shared'):
            self._check_owner()
            self.gate.require_allowed()

    def _recovery_plan_locked(self):
        """Inspect an abandoned intent only while holding the ordinary GET lock."""
        self._check_owner()
        with self.gate._connection_locked() as conn:
            state = self.gate._read_locked(conn)
            if state.blocked:
                raise ManagedNetworkStopped(state.status_code)
        path = self.gate.inflight_path
        _reject_links(path)
        with _gate_storage_io('shared GET intent unavailable'):
            try:
                path.lstat()
            except FileNotFoundError:
                return None
        try:
            identity = _file_identity(path)
            with path.open('rb') as stream:
                info = os.fstat(stream.fileno())
                if ((info.st_dev, info.st_ino) != identity
                        or stream.read(len(_INFLIGHT_BYTES) + 1) != _INFLIGHT_BYTES):
                    raise ManagedNetworkGateStorageError('shared GET intent not recognized')
            if (_file_identity(path) != identity or path.stat().st_mtime_ns != info.st_mtime_ns):
                raise ManagedNetworkGateStorageError('shared GET intent changed')
            return SharedGetRecoveryPlan(identity, info.st_mtime_ns, state.revision)
        except OSError:
            raise ManagedNetworkGateStorageError('shared GET intent unavailable') from None

    def preview_uncertain_get_recovery(self):
        # An active GET holds this same cross-process lock: it cannot be
        # mistaken for an abandoned intent, even by a second app instance.
        with self.gate._exclusive():
            return self._recovery_plan_locked()

    def recover_uncertain_get(self, plan, *, confirmed=False):
        if type(plan) is not SharedGetRecoveryPlan or confirmed is not True:
            raise ValueError('explicit shared GET recovery confirmation required')
        with self.gate._exclusive():
            if self._recovery_plan_locked() != plan:
                raise ManagedNetworkGateStorageError('shared GET recovery preview changed')
            self._check_owner()
            # Retain the no-secret evidence rather than deleting history.
            # Never update the STOP row, recreate its DB, send, or retry here.
            archive = self.root / ('acknowledged-get-' + uuid4().hex + '.v1')
            _reject_links(archive)
            with _gate_storage_io('shared GET recovery destination unavailable'):
                if archive.exists():
                    raise ManagedNetworkGateStorageError('shared GET recovery destination exists')
            try:
                os.rename(self.gate.inflight_path, archive)
            except OSError:
                raise ManagedNetworkGateStorageError('shared GET recovery not completed') from None
            self.gate._no_inflight()

    def admit_job(self, job) -> bool:
        from .job_service import JobAdmissionDeferred
        try:
            self.require_allowed()
            return True
        except ManagedNetworkStopped:
            return False
        except ManagedNetworkGateBusy:
            raise JobAdmissionDeferred("shared stop admission pending") from None


class _SealedOwnerTransport:
    __slots__ = ("scope", "transport")

    def __init__(self, scope, transport):
        self.scope, self.transport = scope, transport

    def __call__(self, request, timeout, limit, deadline):
        from .managed_network_stop_startup import selected_history_guard
        with _report_stop_authority('shared'):
            self.scope._check_owner()
            with selected_history_guard(self.scope):
                return self.transport(request, timeout, limit, deadline)


@_gate_storage_io("shared owner directory unavailable")
def _file_identity_directory(root: Path):
    _reject_links(root)
    stat = root.stat()
    if not root.is_dir() or not stat.st_ino:
        raise ManagedNetworkGateStorageError("shared owner directory identity invalid")
    return (stat.st_dev, stat.st_ino)


def _windows_local_app_data() -> Path:
    if sys.platform != "win32":
        raise ManagedNetworkGateStorageError("Windows shared STOP owner unavailable")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
        ]

    # FOLDERID_LocalAppData = F1B32785-6FBA-4FCF-9D55-7B8E7F157091
    guid = GUID(0xF1B32785, 0x6FBA, 0x4FCF,
                (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91))
    value = ctypes.c_wchar_p()
    shell = ctypes.windll.shell32
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                          wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    hr = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(value))
    if hr != 0 or not value.value:
        raise ManagedNetworkGateStorageError("Windows shared STOP owner unavailable")
    try:
        path = Path(value.value)
        _reject_links(path)
        return path
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def shared_owner_root() -> Path:
    from .test_owner_runtime import owner_root
    selected = owner_root()
    return selected if selected is not None else (_windows_local_app_data() / "PixivVault-NiceGUI" / "managed-pixiv-get-stop-v1")


def open_existing_windows_user_stop() -> ManagedUserNetworkStopScope:
    """Only product entry; no path input, auto-initialize or auto-unlock."""
    root = shared_owner_root()
    try:
        _reject_links(root)
        root.lstat()
    except FileNotFoundError:
        raise SharedStopOwnerAbsent('shared STOP owner absent') from None
    except OSError:
        raise ManagedNetworkGateStorageError('shared STOP owner unavailable') from None
    return ManagedUserNetworkStopScope(root)
