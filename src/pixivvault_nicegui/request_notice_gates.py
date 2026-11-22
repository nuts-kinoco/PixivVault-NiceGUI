"""Observe acquisition gates for display without admitting a GET."""
from dataclasses import dataclass, field, replace
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from typing import Callable

from . import managed_network_stop_gate as gate_files
from . import managed_network_stop_history_readonly as history
from .fs_paths import path_entry_exists


# The sealed database has one schema page and one table page, at most 64 KiB each.
_MAX_GATE_BYTES = 2 * 65536
# SQLite's Windows rollback-journal VFS uses the pending byte at 1 GiB,
# then one reserved byte and a 510-byte shared lock range. A reader can
# coexist with a reserved lock. Probe only the pending and shared ranges.
_SQLITE_PENDING_BYTE = 0x40000000
_SQLITE_READ_LOCK_RANGES = ((_SQLITE_PENDING_BYTE, 1), (_SQLITE_PENDING_BYTE + 2, 510))
_FAILURES = (gate_files.ManagedNetworkGateStorageError, history._Unverified,
             OSError, ValueError, sqlite3.Error)


@dataclass(frozen=True, slots=True)
class GateNoticeState:
    authority: str
    state: str
    status: int | None = None
    inflight: bool | None = False
    selection_proof: bytes | None = field(default=None, repr=False, compare=False)
    cleanup_unverified: bool = False


@dataclass(frozen=True, slots=True)
class GateObservationSnapshot:
    states: tuple[GateNoticeState, ...]
    consistent: bool = True


def _exists(path):
    gate_files._reject_links(path)
    return path_entry_exists(path)


def _metadata(stream, identity):
    info = os.fstat(stream.fileno())
    if ((info.st_dev, info.st_ino) != identity or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1):
        raise history._Unverified()
    return info.st_size, info.st_mtime_ns


def _lock_fingerprint(path, identity):
    try:
        stream = gate_files._open_existing_stop_lock(path)
    except OSError as error:
        if gate_files._lock_open_contended(error):
            return 'share_busy', None
        raise
    with gate_files._gate_cleanup(stream.close, 'observed gate lock close failed'):
        try:
            metadata = gate_files._verify_open_stop_lock(path, stream, identity, probe=True)
        except OSError as error:
            if os.name == 'nt' and gate_files._byte_lock_contended(error):
                return 'byte_busy', _metadata(stream, identity)
            raise
        return 'open', (metadata.st_size, metadata.st_mtime_ns)


def _database_fingerprint(path, identity, *, writable):
    # Selected histories are opened immutable by actual GET admission too.
    # A failure opening either DB is a storage error, including Win32 32.
    stream = gate_files._open_existing_stop_lock(path) if writable else path.open('rb')
    with gate_files._gate_cleanup(stream.close, 'observed gate database handle close failed'):
        metadata = _metadata(stream, identity)
        if metadata[0] > _MAX_GATE_BYTES:
            raise history._Unverified()
        if writable and os.name == 'nt':
            for start, size in _SQLITE_READ_LOCK_RANGES:
                stream.seek(start)
                if gate_files._read_gate_probe_bytes(stream, size):
                    raise history._Unverified()
            stream.seek(0)
        data = stream.read(_MAX_GATE_BYTES + 1)
        if (len(data) != metadata[0] or metadata != _metadata(stream, identity)
                or len(data) < 100 or data[:16] != b'SQLite format 3\x00'
                or data[18:20] != b'\x01\x01'):
            raise history._Unverified()
        return metadata, hashlib.sha256(data).digest()


@dataclass(frozen=True, slots=True)
class _Target:
    authority: str
    path: Path
    lock: Path
    database_identity: tuple[int, int]
    lock_identity: tuple[int, int]
    verify_owner: Callable[[], tuple | None]
    gate: object = None
    read_owner: Callable[[], bytes | None] | None = None
    verify_sidecars: Callable[[], None] | None = None

    def fingerprint(self):
        owner_stamp = self.verify_owner()
        gate_files._reject_links(self.path)
        if gate_files._file_identity(self.lock) != self.lock_identity:
            raise history._Unverified()
        lock = _lock_fingerprint(self.lock, self.lock_identity)
        cache = getattr(self.gate, '_observed_stop_status', None)
        if lock[0] != 'open':
            # Admission stops at the same lock. A DB transaction and its
            # temporary journal can belong to the current lock holder.
            return lock, None, None, True, cache, owner_stamp
        try:
            if self.verify_sidecars is not None:
                self.verify_sidecars()
            elif any(_exists(self.path.with_name(self.path.name + suffix)) for suffix in history._SIDECARS):
                raise history._Unverified()
            if gate_files._file_identity(self.path) != self.database_identity:
                raise history._Unverified()
            db = _database_fingerprint(self.path, self.database_identity, writable=self.gate is not None)
        except _FAILURES:
            # A writer may have acquired the gate just after the first probe.
            lock = _lock_fingerprint(self.lock, self.lock_identity)
            if lock[0] != 'open':
                return lock, None, None, True, getattr(self.gate, '_observed_stop_status', None), owner_stamp
            raise
        marker = getattr(self.gate, 'inflight_path', None)
        inflight = marker is not None and self.gate._has_inflight()
        can_mark_inflight = True
        if marker is not None and not inflight:
            try:
                gate_files._probe_inflight_parent(marker.parent)
            except OSError:
                can_mark_inflight = False
        return lock, db, inflight, can_mark_inflight, cache, owner_stamp

    def read(self, fingerprint):
        lock, _, inflight, can_mark_inflight, cached_stop, _ = fingerprint
        if lock[0] != 'open':
            return GateNoticeState(self.authority, 'busy', cached_stop, inflight)
        selection_proof = self.read_owner() if self.read_owner is not None else None
        cleanup_unverified = False
        if self.gate is None:
            try:
                blocked, status, _ = history._read_gate_immutable(self.path, deny_stopped=True)
            except gate_files.ManagedNetworkStopped as stopped:
                blocked, status = True, stopped.status_code
                cleanup_unverified = bool(getattr(stopped, 'gate_cleanup_errors', ()))
        else:
            state = None
            uri = self.path.as_uri() + '?mode=ro&immutable=1'
            conn = sqlite3.connect(uri, uri=True)
            try:
                with gate_files._gate_cleanup(conn.close, 'observed gate database close failed'):
                    gate_files._verify_gate_schema(conn)
                    state = self.gate._read_state(conn, cached_stop)
            except gate_files.ManagedNetworkGateStorageError:
                if state is None:
                    raise
                cleanup_unverified = True
            blocked, status = state.blocked, state.status_code
            # SQLite's VFS opens the established file with no create flag.
            # With no SQL there is no transaction, byte lock or recovery.
            try:
                with gate_files._open_gate_database(self.path):
                    pass
            except gate_files.ManagedNetworkGateStorageError:
                cleanup_unverified = True
        if blocked:
            state = 'stopped'
        elif inflight:
            state = 'uncertain'
        elif cleanup_unverified or not can_mark_inflight:
            state = 'unverified'
        elif self.read_owner is not None and selection_proof is None:
            # A healthy gate can admit Draft while a saved reader has no selection.
            state = 'unselected'
        else:
            state = 'open'
        return GateNoticeState(self.authority, state, status, inflight, selection_proof, cleanup_unverified)


def _targets(scope, authority):
    from .managed_network_stop_profile_scope import (
        _read_stop_profile, _require_native_stop_owner, _selection_proof,
    )
    from .managed_network_stop_startup import _reject_pending_history, _verify_selected_history
    from .native_profile import PROFILE_DB

    gate = scope.gate
    profile_db = scope.root / PROFILE_DB if hasattr(scope, 'profile_identity') else None

    def read_profile():
        profile = _read_stop_profile(scope.root, scope.profile_identity)
        if profile.revision >= 1 and profile.cookie_file is not None:
            return _selection_proof(scope.profile_identity, profile.cookie_file)
        return None

    def verify_owner():
        if hasattr(gate, 'inflight_path'):
            scope._check_owner()
        if profile_db is not None:
            _require_native_stop_owner(scope.root, scope.profile_identity)
            metadata = profile_db.stat()
            return metadata.st_size, metadata.st_mtime_ns
        return None

    yield _Target(authority, gate.path, gate.lock_path, gate._database_identity,
                  gate._lock_identity, verify_owner, gate, read_profile if profile_db is not None else None)
    for name, (kind, root_id, db_id, lock_id) in getattr(scope, '_startup_histories', ()):
        root = Path(name)

        database = root / history._DB_NAME
        lock = root / (history._DB_NAME + '.lock')

        def verify_history(root=root, kind=kind, root_id=root_id,
                           database=database, db_id=db_id, lock=lock, lock_id=lock_id):
            owner_stamp = verify_owner()
            _verify_selected_history(root, kind, root_id, database, db_id, lock, lock_id)
            return owner_stamp

        yield _Target(authority, database, lock, db_id, lock_id, verify_history,
                      verify_sidecars=lambda root=root: _reject_pending_history(root))


def check_request_notice_gates(*scopes, _step=None):
    """Compare whole sweeps before and after reading the bound gate rows."""
    owners = tuple(scope for scope in scopes if scope is not None)
    shared = tuple(getattr(scope, 'shared_owner', None) for scope in owners)
    owners = dict.fromkeys(scope for scope in (*owners, *shared) if scope is not None)
    targets = tuple(target for scope in owners for target in _targets(
        scope, 'shared' if scope in shared or hasattr(scope.gate, 'inflight_path') else 'local'))

    def step(phase, target, operation, *args, report=True):
        if report and _step is not None:
            _step(phase, target.authority, 'before')
        try:
            return operation(*args)
        except _FAILURES:
            return None
        finally:
            if report and _step is not None:
                _step(phase, target.authority, 'after')

    def confirm_change(target, fingerprint, *, report):
        final = step('verify', target, target.read, fingerprint, report=report) if fingerprint is not None else None
        if step('verify_fingerprint', target, target.fingerprint, report=report) == fingerprint:
            if final is None:
                return GateNoticeState(target.authority, 'unverified', inflight=None)
            if final.status is not None or final.state in ('uncertain', 'unverified'):
                return final
        return GateNoticeState(target.authority, 'changed', inflight=None)

    def preserve_primary(previous, updated):
        known = previous is not None and (previous.status is not None or previous.state == 'uncertain')
        if known and updated.status is None and updated.state in ('changed', 'busy', 'unverified'):
            return replace(previous, cleanup_unverified=(previous.cleanup_unverified
                                                          or updated.state == 'unverified'))
        return updated

    before = [step('first', target, target.fingerprint) for target in targets]
    measured = [step('read', target, target.read, fingerprint) if fingerprint is not None else None
                for target, fingerprint in zip(targets, before)]
    after = [step('last', target, target.fingerprint) for target in targets]
    consistent = before == after
    if consistent:
        # A change after an earlier target's last sample must be visible
        # before a later target's sample can support clearing a notice.
        for index in reversed(range(len(targets))):
            target = targets[index]
            if after[index] is not None:
                confirmed = step('confirm', target, target.fingerprint)
                consistent = consistent and confirmed == after[index]
                after[index] = confirmed
    states = []
    for target, state, first, last in zip(targets, measured, before, after):
        previous = state
        if last is None:
            # A journal or SQLite lock can have disappeared by the time
            # the sentinel was sampled again. Confirm the failed probe.
            last = step('verify_fingerprint', target, target.fingerprint)
            if last is not None:
                consistent = False
            state = None
        if last is None:
            state = GateNoticeState(target.authority, 'unverified', inflight=None)
        elif state is None or first != last:
            state = confirm_change(target, last, report=True)
            # New verified evidence is published immediately. Its transition
            # does not count toward clearing an existing notice.
            if first != last or state.state != 'unverified':
                consistent = False
        states.append(preserve_primary(previous, state))

    if consistent:
        # Revisit every target after all diagnostic callbacks, including those
        # caused by a different target's stable fault. Seal without callbacks.
        for index, (target, state, last) in enumerate(zip(targets, states, after)):
            seal = step('seal', target, target.fingerprint, report=False)
            if seal != last:
                consistent = False
                states[index] = preserve_primary(state, confirm_change(target, seal, report=False))
            elif target.read_owner is not None and state.state in ('open', 'unselected', 'stopped'):
                try:
                    selection = target.read_owner()
                except _FAILURES:
                    states[index] = preserve_primary(
                        state, GateNoticeState(target.authority, 'unverified', inflight=None),
                    )
                    consistent = False
                else:
                    consistent = consistent and selection == state.selection_proof
    return GateObservationSnapshot(tuple(states) or (GateNoticeState('both', 'unverified'),), consistent)
