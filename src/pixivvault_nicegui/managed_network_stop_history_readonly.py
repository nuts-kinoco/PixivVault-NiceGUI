"""Strictly read-only inspection of an explicitly named historical STOP root.

This is an inventory primitive, NOT a migration, seal, reset or GET admission.
In particular, an observed unblocked gate does not prove that other old roots
are free of 401/403/429 history, or that the inspected old owner is quiescent.
No result from this module may be used alone to mint a shared history_proof.

Never use ManagedNetworkStopGate(...) for historical inspection: its normal
constructor can create missing database and lock files.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import stat

from .managed_network_stop_gate import (
    STOP_STATUSES, ManagedNetworkGateStorageError, _APP_ID, _VERSION,
    ManagedNetworkStopped, _carry_gate_cleanup, _file_identity, _gate_cleanup, _reject_links,
)

_DB_NAME = ".pixivvault-network-stop.sqlite3"
_SIDECARS = ("-wal", "-shm", "-journal")
_OWNERS = frozenset(("images", "novel", "ugoira", "native"))


@dataclass(frozen=True, slots=True)
class HistoricalStopObservation:
    """No secrets, paths or authority to start network activity."""

    state: str  # history_unverified, observed_unblocked, observed_stopped
    reason: str
    status_code: int | None = None
    revision: int | None = None
    # A file's identity is observation metadata, not a proof of old-root coverage.
    database_identity: tuple[int, int] | None = None
    lock_identity: tuple[int, int] | None = None

    @property
    def authorizes_get(self) -> bool:
        return False


class _Unverified(Exception):
    pass


def _identity(path: Path) -> tuple[int, int]:
    try:
        return _file_identity(path)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        raise _Unverified("unsafe_or_missing_file") from error
    except Exception as error:
        # Includes ManagedNetworkGateStorageError; do not expose paths.
        raise _Unverified("unsafe_or_missing_file") from error


def _read_exact(path: Path, limit: int) -> bytes:
    original = _identity(path)
    try:
        if path.stat().st_size > limit:
            raise _Unverified("oversized_owner_marker")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit or original != _identity(path):
            raise _Unverified("owner_marker_changed")
        return data
    except (OSError, ValueError):
        raise _Unverified("unreadable_owner_marker") from None


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _Unverified("duplicate_owner_marker_key")
        result[key] = value
    return result


def _check_marker(root: Path, kind: str) -> None:
    # Import identity values from owners instead of guessing legacy marker
    # spelling. No constructor, writable store or Cookie reader is invoked.
    if kind == "images":
        from .managed_downloads import IDENTITY, MARKER
        try:
            observed = json.loads(
                _read_exact(root / MARKER, 4096).decode("utf-8"),
                object_pairs_hook=_unique_pairs,
            )
        except (ValueError, UnicodeError, TypeError):
            raise _Unverified("invalid_owner_marker") from None
        if type(observed) is not dict or observed != IDENTITY:
            raise _Unverified("invalid_owner_marker")
    elif kind == "novel":
        from .managed_novel_jobs import _MARKER
        if _read_exact(root / ".pixivvault-novel-jobs.json", 4096) != _MARKER:
            raise _Unverified("invalid_owner_marker")
    elif kind == "ugoira":
        from .managed_ugoira import MARKER, _IDENTITY
        if _read_exact(root / MARKER, 4096) != _IDENTITY:
            raise _Unverified("invalid_owner_marker")
    else:
        from .native_profile import PROFILE_MARKER, PROFILE_IDENTITY
        from .managed_network_stop_profile_scope import (
            _SCOPE_MARKER, _SCOPE_IDENTITY,
        )
        if (_read_exact(root / PROFILE_MARKER, 4096) != PROFILE_IDENTITY
                or _read_exact(root / _SCOPE_MARKER, 4096) != _SCOPE_IDENTITY):
            raise _Unverified("native_owner_or_scope_unverified")


def _fingerprint(path: Path) -> tuple[tuple[int, int], int, int, str]:
    identity = _identity(path)
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _Unverified("unsafe_file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if (_identity(path) != identity or
                (before.st_size, before.st_mtime_ns) !=
                (after.st_size, after.st_mtime_ns)):
            raise _Unverified("file_changed_during_inspection")
        return identity, after.st_size, after.st_mtime_ns, digest.hexdigest()
    except OSError:
        raise _Unverified("unreadable_file") from None


def _read_gate_immutable(path: Path, *, deny_stopped=False) -> tuple[bool, int | None, int]:
    # SQLite immutable is safe here ONLY after ruling out sidecars/WAL; it is
    # not a substitute for separately proving legacy process quiescence.
    try:
        with ExitStack() as cleanup:
            stream = path.open("rb")
            with _gate_cleanup(stream.close, 'historical STOP header close failed'):
                header = stream.read(100)
            if (len(header) != 100 or header[:16] != b"SQLite format 3\x00"
                    or header[18:20] != b"\x01\x01"):
                raise _Unverified("unknown_or_wal_sqlite_header")
            conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            cleanup.enter_context(_gate_cleanup(conn.close, 'historical STOP database close failed'))
            if (conn.execute("PRAGMA application_id").fetchone()[0] != _APP_ID
                    or conn.execute("PRAGMA user_version").fetchone()[0] != _VERSION):
                raise _Unverified("unrecognized_gate_schema")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if tables != {"gate"}:
                raise _Unverified("unrecognized_gate_schema")
            columns = [(row[1], row[2], row[3], row[5]) for row in
                       conn.execute("PRAGMA table_info(gate)")]
            if columns != [
                ("id", "INTEGER", 0, 1),
                ("blocked", "INTEGER", 1, 0),
                ("status", "INTEGER", 0, 0),
                ("revision", "INTEGER", 1, 0),
            ]:
                raise _Unverified("unrecognized_gate_schema")
            rows = conn.execute("SELECT id,blocked,status,revision FROM gate").fetchall()
            if len(rows) != 1:
                raise _Unverified("invalid_stop_state")
            item_id, blocked, status, revision = rows[0]
            if (type(item_id) is not int or item_id != 1
                    or type(blocked) is not int or blocked not in (0, 1)
                    or type(revision) is not int or revision < 0
                    or (blocked == 0 and status is not None)
                    or (blocked == 1 and (type(status) is not int
                                          or status not in STOP_STATUSES))):
                raise _Unverified("invalid_stop_state")
            # The only states this gate can write are its initial clean row
            # (0, NULL, 0) and its first irreversible fatal latch (1, status, 1).
            # Any later revision, including a cleared row or a repeated latch,
            # implies an out-of-band edit/reset or unknown history. Never describe
            # such a row as unblocked/stopped: that would launder a past 401/403/429.
            if (blocked == 0 and revision != 0) or (blocked == 1 and revision != 1):
                raise _Unverified("ambiguous_stop_revision")
            if blocked and deny_stopped:
                raise ManagedNetworkStopped(status)
            return bool(blocked), status, revision
    except sqlite3.Error as error:
        raise _carry_gate_cleanup(error, _Unverified("unreadable_gate_schema")) from None


def read_existing_stop_history_readonly(
    root: Path, *, owner_kind: str,
) -> HistoricalStopObservation:
    """Observe one explicit historical root without creating or writing files.

    No caller-supplied quiescence assertion can turn this into an authorization.
    Migration still needs a reviewed full-root inventory and independent proof.
    """
    if type(owner_kind) is not str or owner_kind not in _OWNERS:
        return HistoricalStopObservation("history_unverified", "unknown_owner_kind")
    try:
        root = Path(root)
        if not root.is_absolute() or root == root.parent:
            raise _Unverified("invalid_root")
        _reject_links(root)
        if not root.is_dir():
            raise _Unverified("missing_historical_root")
        original_root = root.stat()
        if not original_root.st_ino:
            raise _Unverified("historical_root_identity_unavailable")
        root_identity = (original_root.st_dev, original_root.st_ino)
        _check_marker(root, owner_kind)
        database = root / _DB_NAME
        lock = root / (_DB_NAME + ".lock")
        for suffix in _SIDECARS:
            sidecar = root / (_DB_NAME + suffix)
            _reject_links(sidecar)
            if sidecar.exists() or sidecar.is_symlink():
                raise _Unverified("pending_or_unknown_sqlite_journal")
        if not database.is_file() or not lock.is_file():
            raise _Unverified("missing_or_partial_stop_history")
        first_db = _fingerprint(database)
        first_lock = _fingerprint(lock)
        if first_lock[1] < 1:
            raise _Unverified("invalid_gate_lock")
        blocked, status, revision = _read_gate_immutable(database)
        _reject_links(root)
        updated_root = root.stat()
        if (updated_root.st_dev, updated_root.st_ino) != root_identity:
            raise _Unverified("historical_root_changed")
        _check_marker(root, owner_kind)
        if first_db != _fingerprint(database) or first_lock != _fingerprint(lock):
            raise _Unverified("history_changed_during_inspection")
        for suffix in _SIDECARS:
            if (root / (_DB_NAME + suffix)).exists():
                raise _Unverified("journal_appeared_during_inspection")
        return HistoricalStopObservation(
            "observed_stopped" if blocked else "observed_unblocked",
            "readonly_snapshot_not_migration_proof",
            status, revision, first_db[0], first_lock[0],
        )
    except _Unverified as error:
        return HistoricalStopObservation("history_unverified", str(error))
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error,
            ManagedNetworkGateStorageError):
        return HistoricalStopObservation("history_unverified", "historical_inspection_failed")
