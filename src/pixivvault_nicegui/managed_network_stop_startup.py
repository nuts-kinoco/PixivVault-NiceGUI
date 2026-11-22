"""Normal first-use ownership, with selected legacy STOPs retained in place.

This is not a declaration that every historical folder has been migrated.
Each selected historical media gate remains locked around GET, and its STOP
is copied monotonically into the user gate. No source file is reset or moved.
"""
from contextlib import ExitStack, contextmanager
from functools import partial
import hashlib
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from .managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError,
    ManagedNetworkStopGate, _file_identity, _reject_links,
)
from . import managed_network_stop_shared_scope as shared

_ANCHOR = '.pixivvault-user-stop-startup.v1.json'
_PROVENANCE = 'startup-record.json'
_LOCAL_GATE = '.pixivvault-network-stop.sqlite3'
_STARTUP_LOCK_RETRY_SECONDS = 8.0
_STARTUP_LOCK_POLL_SECONDS = 0.25


def _write_new(path, value):
    with path.open('xb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def check_startup_provenance(scope):
    """A missing/replaced normal-start owner must never become a fresh gate."""
    anchor = scope.root.parent / _ANCHOR
    record = scope.root / _PROVENANCE
    if (scope._owner_schema != shared._STARTUP_OWNER_SCHEMA
            and not anchor.exists() and not record.exists()):
        return  # Existing migration-format owners keep their original contract.
    try:
        _reject_links(anchor)
        _reject_links(record)
        if anchor.stat().st_size > 4096 or record.stat().st_size > 4096:
            raise ValueError()
        payload = record.read_bytes()
        seal = shared._readonly_owner_json(scope.root / shared._OWNER_FILE)
        expected = json.loads(anchor.read_bytes())
        if (hashlib.sha256(payload).hexdigest() != seal['history_proof']
                or expected != {'schema': 1, 'root_identity': list(scope._root_identity),
                                'history_proof': seal['history_proof']}
                or json.loads(payload) != {'schema': 'pixivvault-normal-start-v1',
                                          'operation': 'initialize-user-stop'}):
            raise ValueError()
    except (OSError, ValueError, TypeError):
        raise ManagedNetworkGateStorageError('shared STOP startup record unavailable') from None


def _acquire_file_lock(path):
    from .managed_stop_migration import _FileLock, _FileLockBusy, MigrationRejected
    try:
        return _FileLock(path)
    except _FileLockBusy:
        raise ManagedNetworkGateBusy('startup STOP lock is busy') from None
    except (OSError, MigrationRejected):
        raise ManagedNetworkGateStorageError('startup STOP lock unavailable') from None


def _seed_startup_lock(path):
    """Publish a complete sentinel; a competing launcher never sees it empty."""
    from .managed_stop_migration import MigrationRejected, _rename_new
    _reject_links(path)
    if path.exists():
        return
    stage = path.with_name(path.name + '.' + uuid4().hex)
    try:
        stream = stage.open('xb')
        try:
            with stream:
                stream.write(b'0')
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Use the existing atomic no-replace rename, including on POSIX.
                _rename_new(stage, path, directory=True)
            except FileExistsError:
                pass  # Another launcher published a complete sentinel first.
        finally:
            stage.unlink(missing_ok=True)  # Only the unpublished sentinel.
    except (OSError, MigrationRejected):
        raise ManagedNetworkGateStorageError('shared STOP setup lock unavailable') from None


def startup_lock_deadline():
    return monotonic() + _STARTUP_LOCK_RETRY_SECONDS


def retry_startup_lock(operation, *, deadline):
    """Retry local lock contention within this startup's shared time budget."""
    while True:
        try:
            return operation()
        except ManagedNetworkGateBusy:
            if deadline is None or monotonic() + _STARTUP_LOCK_POLL_SECONDS >= deadline:
                raise
            sleep(_STARTUP_LOCK_POLL_SECONDS)
            if monotonic() >= deadline:
                raise


def open_or_initialize_windows_user_stop(*, selected_roots=(), profile_scope=None, deadline=None):
    """Product startup only: serialize first creation; never replace an owner.

    The durable parent anchor is written before publication. Interruption or
    disappearance is a repair condition, never permission to clear history.
    Selected-root validation occurs before reader/service construction.
    """
    if deadline is None:
        deadline = startup_lock_deadline()
    retry_lock = partial(retry_startup_lock, deadline=deadline)

    def prepare(scope):
        if type(scope) is shared.ManagedUserNetworkStopScope:
            retry_lock(lambda: attach_selected_histories(scope, selected_roots))
            if profile_scope is not None:
                state = retry_lock(profile_scope.gate.snapshot)
                if state.blocked:
                    retry_lock(lambda: scope.gate.trip(state.status_code))
        return scope
    try:
        return prepare(retry_lock(shared.open_existing_windows_user_stop))
    except shared.SharedStopOwnerAbsent:
        pass
    from .managed_stop_migration import MigrationRejected, _rename_new
    root = shared.shared_owner_root()
    from .test_owner_runtime import require_inside
    require_inside(root)
    _reject_links(root)
    parent = root.parent
    _reject_links(parent)
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ManagedNetworkGateStorageError('shared STOP setup directory unavailable') from None
    lock = parent / '.pixivvault-user-stop-startup.lock'
    _seed_startup_lock(lock)
    ticket = retry_lock(lambda: _acquire_file_lock(lock))
    try:
        # A competing launcher may have published while we were waiting.
        try:
            return prepare(retry_lock(shared.open_existing_windows_user_stop))
        except shared.SharedStopOwnerAbsent:
            pass
        anchor = parent / _ANCHOR

        def require_no_prior_setup():
            _reject_links(anchor)
            # A prior lifecycle or formal-migration journal proves this isn't a
            # virgin owner. Preserve it for repair instead of inventing a new one.
            if (anchor.exists() or (parent / '.pixivvault-stop-migrations').exists()
                    or (parent / '.pixivvault-stop-migration.lock').exists()):
                raise ManagedNetworkGateStorageError('shared STOP owner missing after prior setup')

        require_no_prior_setup()
        # Acquire selected legacy locks before publishing ANY first-use intent.
        # A temporary busy writer must remain a retryable condition.
        from types import SimpleNamespace
        statuses = []
        history = SimpleNamespace(gate=SimpleNamespace(trip=statuses.append))
        retry_lock(lambda: attach_selected_histories(history, selected_roots))
        if profile_scope is not None:
            state = retry_lock(profile_scope.gate.snapshot)
            if state.blocked:
                statuses.append(state.status_code)
        with ExitStack() as histories:
            retry_lock(lambda: histories.enter_context(selected_history_guard(history)))
            stage = parent / ('.pv-stop-' + uuid4().hex)
            stage.mkdir()
            payload = _json_bytes({'schema': 'pixivvault-normal-start-v1',
                                   'operation': 'initialize-user-stop'})
            proof = hashlib.sha256(payload).hexdigest()
            _write_new(stage / _PROVENANCE, payload)
            # Formal migration has a different lock. If it started during our
            # wait, do not write an anchor that could invalidate its owner.
            require_no_prior_setup()
            # This intent survives crashes even before gate initialization.
            _write_new(anchor, _json_bytes({'schema': 1,
                        'root_identity': list(shared._file_identity_directory(stage)),
                        'history_proof': proof}))
            try:
                gate = retry_lock(lambda: ManagedNetworkStopGate(stage / shared._GATE_FILE))
                if statuses:
                    retry_lock(lambda: gate.trip(statuses[0]))
            except ManagedNetworkGateBusy:
                # First-use intent is already durable. Exhausting the lock
                # budget here requires repair of the interrupted setup.
                raise ManagedNetworkGateStorageError(
                    'shared STOP setup incomplete; preserve setup records') from None
            _write_new(stage / shared._OWNER_FILE, _json_bytes({
                'schema': shared._STARTUP_OWNER_SCHEMA, 'state': 'sealed',
                'root_identity': list(shared._file_identity_directory(stage)),
                'gate_identity': list(_file_identity(gate.path)),
                'lock_identity': list(_file_identity(gate.lock_path)),
                'history_proof': proof,
            }))
            _rename_new(stage, root, directory=True)
        return prepare(retry_lock(shared.open_existing_windows_user_stop))
    except (OSError, MigrationRejected):
        raise ManagedNetworkGateStorageError('shared STOP setup incomplete; preserve setup records') from None
    finally:
        ticket.close()


def attach_selected_histories(scope, roots):
    """Validate explicit media roots before writers open them, without mutation.

    Missing local gates are ordinary first-use roots. Partial/corrupt gates
    are local repair conditions. Valid legacy gates stay present and active.
    """
    selected = dict(getattr(scope, '_startup_histories', ()))
    from .managed_network_stop_history_readonly import _check_marker
    for kind, root in roots:
        if root is None:
            continue
        root = Path(root)
        _reject_links(root)
        database = root / _LOCAL_GATE
        lock = root / (_LOCAL_GATE + '.lock')
        _reject_links(database)
        _reject_links(lock)
        if not database.exists() and not lock.exists():
            _reject_pending_history(root)
            continue
        if not database.is_file() or not lock.is_file():
            raise ManagedNetworkGateStorageError('selected STOP history incomplete')
        try:
            _check_marker(root, kind)
            key = str(root.absolute())
            value = (kind, shared._file_identity_directory(root),
                     _file_identity(database), _file_identity(lock))
            if key in selected and selected[key] != value:
                raise ValueError()
            selected[key] = value
        except Exception:
            raise ManagedNetworkGateStorageError('selected STOP ownership unavailable') from None
    scope._startup_histories = tuple(sorted(selected.items()))
    with selected_history_guard(scope):
        pass


def _reject_pending_history(root):
    for suffix in ('-journal', '-wal', '-shm'):
        sidecar = root / (_LOCAL_GATE + suffix)
        _reject_links(sidecar)
        if sidecar.exists():
            raise ManagedNetworkGateStorageError('selected STOP history is pending')


@contextmanager
def selected_history_guard(scope):
    """Lock selected old writers until this GET completes; never write to them."""
    from .managed_network_stop_history_readonly import _check_marker, _read_gate_immutable
    with ExitStack() as locks:
        for name, (kind, root_id, database_id, lock_id) in getattr(scope, '_startup_histories', ()):
            root = Path(name)
            database, lock = root / _LOCAL_GATE, root / (_LOCAL_GATE + '.lock')
            ticket = _acquire_file_lock(lock)
            locks.callback(ticket.close)
            try:
                _check_marker(root, kind)
            except Exception:
                raise ManagedNetworkGateStorageError('selected STOP owner changed') from None
            if (shared._file_identity_directory(root) != root_id
                    or _file_identity(database) != database_id or _file_identity(lock) != lock_id):
                raise ManagedNetworkGateStorageError('selected STOP identity changed')
            _reject_pending_history(root)
            try:
                blocked, status, _ = _read_gate_immutable(database)
            except Exception:
                raise ManagedNetworkGateStorageError('selected STOP state unavailable') from None
            if blocked:
                scope.gate.trip(status)
        yield
