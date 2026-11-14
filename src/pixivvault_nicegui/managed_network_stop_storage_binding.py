"""Bind newly owned native storage to exactly one durable profile stop scope.

Only a previously verified native storage parent may receive this sidecar.
Legacy data/unknown owners cannot be claimed by adding a marker to a
nonempty directory. Existing media receipts and credentials are never read.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path

from .fs_paths import native_io_path
from .managed_network_stop_gate import ManagedNetworkGateStorageError
from .managed_network_stop_profile_scope import ManagedProfileNetworkStopScope
from .native_profile import (
    STORAGE_FOLDER, STORAGE_IDENTITY, STORAGE_MARKER, _owned_marker,
)

OWNER_MARKER = ".pixivvault-profile-stop-binding.v1"
_INTENT_PREFIX = ".pv-stop-bind-"


def _expected(scope: ManagedProfileNetworkStopScope) -> bytes:
    if type(scope) is not ManagedProfileNetworkStopScope:
        raise ManagedNetworkGateStorageError("owned native stop scope required")
    identity = scope.gate._database_identity
    if (type(identity) is not tuple or len(identity) != 2
            or any(type(v) is not int or v <= 0 for v in identity)):
        raise ManagedNetworkGateStorageError("native stop scope identity unavailable")
    # Save neither real profile path nor credential values. An independently
    # re-created profile at the same pathname has a different database inode.
    fingerprint = sha256(
        (str(scope.root.resolve()) + "|" + str(identity[0]) + "|" + str(identity[1]))
        .encode("utf-8")
    ).hexdigest()
    return (json.dumps(
        {"application": "pixivvault-nicegui", "purpose": "profile-stop-binding",
         "version": 1, "fingerprint": fingerprint},
        sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("ascii")


def _base(save_parent: Path) -> Path:
    parent = Path(save_parent)
    if not parent.is_absolute() or not parent.is_dir():
        raise ManagedNetworkGateStorageError("native save parent unavailable")
    base = parent / STORAGE_FOLDER
    try:
        _owned_marker(base, STORAGE_MARKER, STORAGE_IDENTITY)
    except Exception:
        raise ManagedNetworkGateStorageError("native storage ownership unavailable") from None
    return base


def _verify(base: Path, expected: bytes) -> bool:
    marker = base / OWNER_MARKER
    try:
        if marker.is_symlink():
            raise ManagedNetworkGateStorageError("native storage binding links forbidden")
        if marker.exists():
            if (not marker.is_file() or marker.stat().st_nlink != 1
                    or marker.stat().st_size != len(expected)
                    or marker.read_bytes() != expected):
                raise ManagedNetworkGateStorageError("native storage bound to another stop")
            return True
        return False
    except (OSError, ValueError):
        raise ManagedNetworkGateStorageError("native storage binding unavailable") from None


def _intent(scope: ManagedProfileNetworkStopScope, save_parent: Path,
            expected: bytes) -> tuple[Path, bytes]:
    parent = Path(save_parent)
    if not parent.is_absolute() or not parent.is_dir() or parent.is_symlink():
        raise ManagedNetworkGateStorageError("native save parent unavailable")
    base = (parent / STORAGE_FOLDER).absolute()
    target = sha256(str(base).encode("utf-8")).hexdigest()
    # Only the lookup filename is shortened for Windows path limits. The
    # durable payload retains the complete 256-bit storage-path digest, so a
    # lookup collision always fails closed rather than binding the wrong root.
    path = native_io_path(scope.root / (_INTENT_PREFIX + target[:24] + ".v1"))
    payload = (json.dumps(
        {"version": 1, "purpose": "pending-native-storage-binding",
         "profile": sha256(expected).hexdigest(), "storage": target},
        sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("ascii")
    return path, payload


def _matching_intent(path: Path, expected: bytes) -> bool:
    try:
        if path.is_symlink():
            raise ManagedNetworkGateStorageError("native binding intent links forbidden")
        if not path.exists():
            return False
        if (not path.is_file() or path.stat().st_nlink != 1
                or path.stat().st_size != len(expected)
                or path.read_bytes() != expected):
            raise ManagedNetworkGateStorageError("native binding intent mismatch")
        return True
    except (OSError, ValueError):
        raise ManagedNetworkGateStorageError("native binding intent unavailable") from None


def prepare_native_storage_binding(scope: ManagedProfileNetworkStopScope,
                                   save_parent: Path) -> None:
    """Durably record first-use intent *before* NativeProfile creates the base.

    A restart after provisioning but before binding can safely finish only
    the same profile's previously recorded, otherwise empty storage parent.
    A pre-existing parent without this record is never automatically adopted.
    """
    expected = _expected(scope)
    intent_path, payload = _intent(scope, save_parent, expected)
    base = Path(save_parent) / STORAGE_FOLDER
    if base.exists():
        _base(save_parent)
        if _verify(base, expected):
            return
        from .managed_stop_migration_binding import verified_storage
        if verified_storage(scope,base):return
        if not _matching_intent(intent_path, payload):
            raise ManagedNetworkGateStorageError(
                "existing native storage requires reviewed stop migration")
        if {item.name for item in base.iterdir()} != {STORAGE_MARKER}:
            raise ManagedNetworkGateStorageError(
                "ambiguous native storage requires reviewed migration")
        return
    if _matching_intent(intent_path, payload):
        return
    try:
        with intent_path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if not _matching_intent(intent_path, payload):
            raise ManagedNetworkGateStorageError(
                "native storage intent changed during setup")
    except (OSError, ValueError):
        raise ManagedNetworkGateStorageError(
            "native storage intent could not be persisted") from None


def bind_new_native_storage(scope: ManagedProfileNetworkStopScope,
                            save_parent: Path) -> None:
    """One-way first-use binding; an ambiguous pre-existing base is refused."""
    expected = _expected(scope)
    base = _base(save_parent)
    if _verify(base, expected):
        return
    intent_path, intent_bytes = _intent(scope, save_parent, expected)
    from .managed_stop_migration_binding import verified_storage
    if verified_storage(scope,base):return
    if not _matching_intent(intent_path, intent_bytes):
        # A previous profile could have used an apparently empty base and
        # recorded its 429 only in its *own* profile.
        raise ManagedNetworkGateStorageError(
            "existing native storage requires reviewed stop migration")
    try:
        # The first write is permitted only before any owner has created an
        # output root. A crash after this write stays associated with the same
        # profile and can safely resume; there is no deletion/reset method.
        entries = {item.name for item in base.iterdir()}
        if entries != {STORAGE_MARKER}:
            raise ManagedNetworkGateStorageError(
                "native storage requires reviewed stop ownership migration")
        with (base / OWNER_MARKER).open("xb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        _verify(base, expected)
    except FileExistsError:
        if not _verify(base, expected):
            raise ManagedNetworkGateStorageError(
                "native storage association changed during initialization")
    except (OSError, ValueError):
        raise ManagedNetworkGateStorageError("native storage binding unavailable") from None


def require_native_storage_binding(scope: ManagedProfileNetworkStopScope,
                                   media_root: Path) -> None:
    """Never let a second profile silently adopt another one's media root."""
    expected = _expected(scope)
    for ancestor in (Path(media_root), *Path(media_root).parents):
        marker = ancestor / STORAGE_MARKER
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise ManagedNetworkGateStorageError("native storage ownership unavailable") from None
        if not _verify(ancestor, expected) and not __import__(__package__+".managed_stop_migration_binding",fromlist=["verified_storage"]).verified_storage(scope,ancestor):
            raise ManagedNetworkGateStorageError(
                "native storage has no verified profile stop association")
        return
    # Absence of every owner marker is *not* evidence that a selected media
    # root belongs to this Native profile.  A missing marker previously fell
    # through to an implicit success, bypassing the startup ownership fence.
    raise ManagedNetworkGateStorageError(
        "native storage has no verified profile stop association")
