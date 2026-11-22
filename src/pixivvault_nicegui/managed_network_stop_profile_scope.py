"""Fail-closed, profile-anchored GET stop for *new* native profiles.

Unlike a media-root gate, this anchor is not affected when images, novels and
ugoira are enabled or disabled, or when their save parent changes.

Native product startup can pass this scope for a newly owned profile. Existing
configured profiles must have their prior root-bound stop histories reviewed
and migrated first; this module deliberately refuses to invent permission for
them. Profile-less CLI has a separate, conservative ownership boundary.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    _file_identity,
    _gate_storage_io,
    _run_gate_cleanup,
    _report_stop_authority,
)
from .managed_network_stop_integration import ManagedNetworkStopIntegration
from .managed_network_stop_transport import ManagedNetworkStopTransport
from .native_profile import (
    NativeProfileError,
    NativeProfileStore,
    PROFILE_DB,
    PROFILE_IDENTITY,
    PROFILE_MARKER,
    _absolute,
    _assert_chain,
    _owned_marker,
)


_SCOPE_MARKER = ".pixivvault-network-stop-scope.v1"
_SCOPE_IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"network-stop-scope","version":1}\n'
_GATE_NAME = ".pixivvault-network-stop.sqlite3"


@_report_stop_authority('local')
@_gate_storage_io('native STOP owner unavailable')
def _require_native_stop_owner(root: Path, profile_identity: tuple[int, int]) -> None:
    _owned_marker(root, PROFILE_MARKER, PROFILE_IDENTITY)
    _owned_marker(root, _SCOPE_MARKER, _SCOPE_IDENTITY)
    if _file_identity(root / PROFILE_DB) != profile_identity:
        raise ManagedNetworkGateStorageError("native profile database changed")


def _selection_proof(profile_identity: tuple[int, int], path: Path) -> bytes:
    """Compare references without opening the file or publishing its path."""
    return hashlib.sha256(os.fsencode(f'{profile_identity}\0{os.path.normcase(path)}')).digest()


def _read_stop_profile(root: Path, profile_identity: tuple[int, int]):
    """Read and validate saved references identically for GET and observation."""
    try:
        snapshot = NativeProfileStore.peek(root)
        if _file_identity(root / PROFILE_DB) != profile_identity:
            raise ManagedNetworkGateStorageError("native profile database changed")
        return snapshot
    except (NativeProfileError, OSError, ValueError, TypeError, UnicodeError):
        raise ManagedNetworkGateStorageError(
            "configured owned profile unavailable"
        ) from None


@_report_stop_authority('local')
def _require_selected_cookie(root: Path, cookie_path: Path,
                             profile_identity: tuple[int, int]) -> Path:
    """Never dispatch a profile-scoped GET with an unsaved/stale credential.

    The native profile owns the credential *reference*, not the secret bytes.
    Checking again at the transport boundary also protects an already-open
    reader after native settings are changed; it does not rely on a replaceable
    request-admission callback.
    """
    expected_selection = None
    try:
        # Bind refusal to the reader before inspecting the filesystem: an
        # unreadable old reference may differ from today's healthy selection.
        selected = Path(cookie_path)
        expected_selection = _selection_proof(profile_identity, selected)
        _require_native_stop_owner(root, profile_identity)
        snapshot = _read_stop_profile(root, profile_identity)
        if snapshot.revision < 1 or snapshot.cookie_file != selected:
            raise ManagedNetworkGateStorageError("configured owned profile credential unavailable")
        return _absolute(cookie_path, kind="file")
    except ManagedNetworkGateStorageError as error:
        error.expected_selection = expected_selection
        raise
    except (NativeProfileError, OSError, ValueError, TypeError, UnicodeError):
        raise ManagedNetworkGateStorageError(
            "configured owned profile credential unavailable", expected_selection=expected_selection,
        ) from None


class _ProfileBoundTransport(ManagedNetworkStopTransport):
    """Keep exact reader type and original gate while checking every GET."""

    __slots__ = ("profile_root", "cookie_path", "profile_identity")

    def __init__(self, profile_root: Path, cookie_path: Path,
                 profile_identity: tuple[int, int],
                 previous: ManagedNetworkStopTransport):
        if not isinstance(previous, ManagedNetworkStopTransport):
            raise ManagedNetworkGateStorageError("managed reader transport unavailable")
        super().__init__(previous.gate, previous.transport)
        self.profile_root = profile_root
        self.cookie_path = cookie_path
        self.profile_identity = profile_identity

    def __call__(self, request, timeout: float, limit: int,
                 deadline: float) -> bytes:
        _require_selected_cookie(self.profile_root, self.cookie_path,
                                 self.profile_identity)
        return super().__call__(request, timeout, limit, deadline)


class ManagedProfileNetworkStopScope(ManagedNetworkStopIntegration):
    """Persist one network-stop state in an explicitly owned native profile.

    Creating a scope is allowed only for a genuinely unconfigured revision-zero
    profile, where no prior save-root history exists in this profile. Any
    configured profile without an established scope needs a separate, reviewed
    migration: the old image/novel/ugoira gates cannot safely be guessed from
    whichever roots happen to be selected at this startup.

    The independent scope marker makes disappearance of BOTH the database and
    its lock fail closed. An interrupted first initialization also fails
    closed instead of silently starting a new unblocked state.
    """

    def __init__(self, profile_root: Path):
        try:
            root = Path(profile_root)
            if not root.is_absolute() or root.parent == root:
                raise NativeProfileError("absolute owned profile required")
            _owned_marker(root, PROFILE_MARKER, PROFILE_IDENTITY)
            profile_identity = _file_identity(root / PROFILE_DB)
            snapshot = NativeProfileStore.peek(root)
            if _file_identity(root / PROFILE_DB) != profile_identity:
                raise NativeProfileError("native profile database replaced during startup")
            identity = root / _SCOPE_MARKER
            gate_path = root / _GATE_NAME
            lock_path = root / (_GATE_NAME + ".lock")
            for path in (identity, gate_path, lock_path):
                _assert_chain(path, kind="file")

            if identity.exists():
                if identity.read_bytes() != _SCOPE_IDENTITY:
                    raise NativeProfileError("managed stop scope identity invalid")
                if not gate_path.is_file() or not lock_path.is_file():
                    raise NativeProfileError("managed stop scope history missing")
            else:
                # Do not claim an established historical profile is new merely
                # because its old gate files are absent in today's media root.
                if (snapshot.revision != 0
                        or snapshot.cookie_file is not None
                        or snapshot.save_parent is not None
                        or snapshot.library_readonly_db is not None
                        or snapshot.library_image_root is not None):
                    raise NativeProfileError("configured profile requires stop migration")
                if gate_path.exists() or lock_path.exists():
                    raise NativeProfileError("unmarked stop history exists")
                _owned_marker(root, PROFILE_MARKER, PROFILE_IDENTITY)
                with identity.open("xb") as stream:
                    stream.write(_SCOPE_IDENTITY)
                    stream.flush()
                    os.fsync(stream.fileno())
                # An independent settings writer may have committed after
                # the first revision-zero peek but before this new marker was
                # published. Never bless that now-configured profile with a
                # brand-new unblocked gate: keep the marker and fail closed.
                # A normal later configuration update (after initialization)
                # does not re-enter this first-creation branch.
                if NativeProfileStore.peek(root) != snapshot:
                    raise NativeProfileError("native profile changed while creating stop scope")
            if _file_identity(root / PROFILE_DB) != profile_identity:
                raise NativeProfileError("native profile database replaced during startup")
        except (NativeProfileError, ManagedNetworkGateStorageError,
                OSError, ValueError, UnicodeError):
            raise ManagedNetworkGateStorageError(
                "owned native stop scope unavailable or migration required"
            ) from None

        # A failed initialization after the marker is persisted remains
        # blocked on the next start; never auto-delete or reinitialize it.
        self.root = root
        self.profile_identity = profile_identity
        self.gate = ManagedNetworkStopGate(gate_path)
        self.shared_owner = None

    def require_allowed(self) -> None:
        if self.shared_owner is not None:
            self.shared_owner.require_allowed()
        super().require_allowed()

    def admit_job(self, job) -> bool:
        if self.shared_owner is not None and not self.shared_owner.admit_job(job):
            return False
        return super().admit_job(job)

    def create_reader(self, cookie_path: Path, *, transport=None):
        """Require the selected native credential, including on later GETs.

        A revision-zero profile has no persisted credential and must never
        issue a live request. The wrapper preserves the exact base reader type
        and the transport owner; deleting/changing the profile blocks a
        previously constructed reader before any further external dispatch.
        """
        selected = _require_selected_cookie(self.root, cookie_path,
                                            self.profile_identity)
        client = super().create_reader(selected, transport=transport)
        try:
            client._transport = _ProfileBoundTransport(
                self.root, selected, self.profile_identity, client._transport,
            )
        except BaseException as error:
            _run_gate_cleanup(client.close, 'managed reader close failed', primary=error, authority='local')
            raise
        return client
