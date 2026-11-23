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
    admission_refused: bool = field(default=False, compare=False)
    owner_gate: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class GateObservationSnapshot:
    states: tuple[GateNoticeState, ...]
    consistent: bool = True


class _AdmissionRefused(Exception):
    """An existing acquisition check refused, as opposed to a sampling miss."""


def _admission_check(check, *args):
    try:
        return check(*args)
    except (OSError, ValueError, gate_files.ManagedNetworkGateStorageError):
        raise _AdmissionRefused() from None


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
    stream = (_admission_check(gate_files._open_existing_stop_lock, path)
              if writable else path.open('rb'))
    with gate_files._gate_cleanup(stream.close, 'observed gate database handle close failed'):
        metadata = _metadata(stream, identity)
        if metadata[0] > _MAX_GATE_BYTES:
            raise history._Unverified()
        if writable and os.name == 'nt':
            for start, size in _SQLITE_READ_LOCK_RANGES:
                stream.seek(start)
                if _admission_check(gate_files._read_gate_probe_bytes, stream, size):
                    raise history._Unverified()
            stream.seek(0)
        data = stream.read(_MAX_GATE_BYTES + 1)
        if (len(data) != metadata[0] or metadata != _metadata(stream, identity)
                or len(data) < 100 or data[:16] != b'SQLite format 3\x00'
                or data[18:20] != b'\x01\x01'):
            raise history._Unverified()
        return metadata, hashlib.sha256(data).digest()


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    lock: tuple
    cached_stop: int | None
    owner_stamp: tuple | None
    database: tuple | None = None
    inflight: bool | None = None
    can_mark_inflight: bool = True
    rw_verified: bool = True
    rw_state: gate_files.NetworkStopState | None = None
    acquisition_sql: tuple | None = None
    admission_refused: bool = False


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
    source_gate: object = None

    def fingerprint(self):
        try:
            return self._fingerprint()
        except _AdmissionRefused:
            return _Fingerprint(('refused', None), None, None, admission_refused=True)

    def _fingerprint(self):
        owner_stamp = self.verify_owner()
        _admission_check(gate_files._reject_links, self.path)
        if _admission_check(gate_files._file_identity, self.lock) != self.lock_identity:
            raise _AdmissionRefused()
        lock = _admission_check(_lock_fingerprint, self.lock, self.lock_identity)
        cache = getattr(self.gate, '_observed_stop_status', None)
        if lock[0] != 'open':
            # Admission stops at the same lock. A DB transaction and its
            # temporary journal can belong to the current lock holder.
            return _Fingerprint(lock, cache, owner_stamp)
        try:
            if self.verify_sidecars is not None:
                _admission_check(self.verify_sidecars)
            elif any(_exists(self.path.with_name(self.path.name + suffix)) for suffix in history._SIDECARS):
                raise history._Unverified()
            if _admission_check(gate_files._file_identity, self.path) != self.database_identity:
                raise _AdmissionRefused()
            db = _database_fingerprint(self.path, self.database_identity, writable=self.gate is not None)
        except _FAILURES + (_AdmissionRefused,):
            # A writer may have acquired the gate just after the first probe.
            lock = _admission_check(_lock_fingerprint, self.lock, self.lock_identity)
            if lock[0] != 'open':
                return _Fingerprint(lock, getattr(self.gate, '_observed_stop_status', None), owner_stamp)
            raise
        marker = getattr(self.gate, 'inflight_path', None)
        inflight = marker is not None and _admission_check(self.gate._has_inflight)
        can_mark_inflight = True
        if marker is not None and not inflight:
            try:
                gate_files._probe_inflight_parent(marker.parent)
            except OSError:
                can_mark_inflight = False
        rw_verified, rw_state = True, None
        admission_refused = not can_mark_inflight
        if self.gate is not None:
            # Check the real R/W handle without SQL. Validate the admission SQL
            # separately with the immutable VFS so it cannot lock or recover
            # the live file. Repeat both checks in the final seal.
            try:
                with gate_files._open_gate_database(self.path):
                    pass
            except gate_files.ManagedNetworkGateStorageError:
                admission_refused = True
                rw_verified = False
            try:
                with gate_files._open_gate_database(self.path, immutable=True) as conn:
                    gate_files._configure_gate_connection(conn)
                    gate_files._verify_gate_schema(conn)
                    rw_state = self.gate._read_state(conn, cache)
            except gate_files.ManagedNetworkGateStorageError:
                rw_verified = False
        acquisition_sql = None if self.gate is None else self.gate._acquisition_sql.stamp
        if acquisition_sql is not None and acquisition_sql[1] is not None:
            admission_refused = True
            rw_verified = False
        return _Fingerprint(lock, cache, owner_stamp, db, inflight, can_mark_inflight,
                            rw_verified, rw_state, acquisition_sql, admission_refused)

    def _read_immutable_fallback(self, cached_stop):
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
            return state.status_code, True
        return state.status_code, False

    def read(self, fingerprint):
        inflight = fingerprint.inflight
        if fingerprint.lock[0] == 'refused':
            return GateNoticeState(self.authority, 'unverified', inflight=None, admission_refused=True)
        if fingerprint.lock[0] != 'open':
            return GateNoticeState(self.authority, 'busy', fingerprint.cached_stop, inflight)
        status = selection_proof = None
        cleanup_unverified = not fingerprint.rw_verified
        try:
            if self.gate is None:
                _, status, _ = history._read_gate_immutable(self.path, deny_stopped=True)
            elif fingerprint.rw_state is not None:
                status = fingerprint.rw_state.status_code
            else:
                # A R/W SQL fault cannot establish permission. Try to recover
                # the denial for display through the existing read-only path.
                status, failed_close = self._read_immutable_fallback(fingerprint.cached_stop)
                cleanup_unverified = cleanup_unverified or failed_close
            if self.read_owner is not None:
                selection_proof = self.read_owner()
        except gate_files.ManagedNetworkStopped as stopped:
            status = stopped.status_code
            cleanup_unverified = cleanup_unverified or bool(getattr(stopped, 'gate_cleanup_errors', ()))
        except _FAILURES:
            if status is None and not inflight and not fingerprint.admission_refused:
                raise
            cleanup_unverified = True

        if status is not None:
            state = 'stopped'
        elif inflight:
            state = 'uncertain'
        elif cleanup_unverified or not fingerprint.can_mark_inflight:
            state = 'unverified'
        elif self.read_owner is not None and selection_proof is None:
            # A healthy gate can admit Draft while a saved reader has no selection.
            state = 'unselected'
        else:
            state = 'open'
        return GateNoticeState(self.authority, state, status, inflight, selection_proof,
                               cleanup_unverified, fingerprint.admission_refused)


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
            _admission_check(scope._check_owner)
        if profile_db is not None:
            _admission_check(_require_native_stop_owner, scope.root, scope.profile_identity)
            metadata = profile_db.stat()
            return metadata.st_size, metadata.st_mtime_ns
        return None

    yield _Target(authority, gate.path, gate.lock_path, gate._database_identity,
                  gate._lock_identity, verify_owner, gate, read_profile if profile_db is not None else None,
                  source_gate=gate)
    for name, (kind, root_id, db_id, lock_id) in getattr(scope, '_startup_histories', ()):
        root = Path(name)

        database = root / history._DB_NAME
        lock = root / (history._DB_NAME + '.lock')

        def verify_history(root=root, kind=kind, root_id=root_id,
                           database=database, db_id=db_id, lock=lock, lock_id=lock_id):
            owner_stamp = verify_owner()
            _admission_check(_verify_selected_history, root, kind, root_id, database, db_id, lock, lock_id)
            return owner_stamp

        yield _Target(authority, database, lock, db_id, lock_id, verify_history,
                      verify_sidecars=lambda root=root: _reject_pending_history(root), source_gate=gate)


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

    def sampled(fingerprint):
        return fingerprint is not None and fingerprint.lock[0] != 'refused'

    def unavailable(target, fingerprint):
        return GateNoticeState(target.authority, 'unverified', inflight=None,
                               admission_refused=fingerprint is not None and fingerprint.admission_refused)

    def confirm_change(target, fingerprint, *, report):
        final = step('verify', target, target.read, fingerprint, report=report) if sampled(fingerprint) else None
        checked = step('verify_fingerprint', target, target.fingerprint, report=report)
        agrees = checked == fingerprint
        known = final is not None and (final.status is not None or final.state == 'uncertain')
        if not agrees and checked is not None:
            latest = step('read', target, target.read, checked, report=False)
            if latest is not None and (latest.status is not None or latest.state == 'uncertain'):
                if not known:
                    return latest
                # A later result can add an intent or a durable STOP. Keep
                # both positives and the diagnosis that their samples differ.
                primary = final if final.status is not None else latest
                return replace(primary, inflight=final.inflight or latest.inflight, cleanup_unverified=True)
            if latest is not None and latest.admission_refused:
                return preserve_primary(final, latest)
        if known:
            # A subsequent inconclusive probe cannot undo a known denial.
            return final if agrees else replace(final, cleanup_unverified=True)
        if agrees:
            if final is None:
                return unavailable(target, checked)
            if final.state == 'unverified':
                return final
        return GateNoticeState(target.authority, 'changed', inflight=None)

    def preserve_primary(previous, updated):
        known = previous is not None and (previous.status is not None or previous.state == 'uncertain')
        if known and updated.status is None and updated.state in ('changed', 'busy', 'unverified'):
            return replace(previous, cleanup_unverified=(previous.cleanup_unverified
                                                          or updated.state == 'unverified'),
                           admission_refused=previous.admission_refused or updated.admission_refused)
        return updated

    before = [step('first', target, target.fingerprint) for target in targets]
    measured = [step('read', target, target.read, fingerprint) if sampled(fingerprint) else None
                for target, fingerprint in zip(targets, before)]
    after = [step('last', target, target.fingerprint) for target in targets]
    consistent = before == after
    if consistent:
        # A change after an earlier target's last sample must be visible
        # before a later target's sample can support clearing a notice.
        for index in reversed(range(len(targets))):
            target = targets[index]
            if sampled(after[index]):
                confirmed = step('confirm', target, target.fingerprint)
                consistent = consistent and confirmed == after[index]
                after[index] = confirmed
    states = []
    for target, state, first, last in zip(targets, measured, before, after):
        previous = state
        if not sampled(last):
            # Both a sampling miss and an actual refusal need the original
            # retry phase. Only a verified refusal carries the extra proof.
            last = step('verify_fingerprint', target, target.fingerprint)
            if sampled(last):
                consistent = False
            state = None
        if not sampled(last):
            state = unavailable(target, last)
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
    owned_states = tuple(replace(state, owner_gate=target.source_gate)
                         for state, target in zip(states, targets))
    return GateObservationSnapshot(owned_states or (GateNoticeState('both', 'unverified'),), consistent)
