"""Regression coverage for expiry, observation windows and gate I/O errors."""
from contextlib import closing
from itertools import product
import os
from pathlib import Path
import sqlite3
from threading import Event

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate_module
from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import _file_identity_directory
from pixivvault_nicegui.managed_network_stop_profile_scope import _SCOPE_MARKER
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_draft_auth_reader
from pixivvault_nicegui.managed_network_stop_startup import _ANCHOR
from pixivvault_nicegui.native_profile import NativeProfileStore, PROFILE_DB, PROFILE_MARKER
from pixivvault_nicegui.native_startup import NativeProfileBinding
from pixivvault_nicegui.pixiv_read_client import TransportFailed
from pixivvault_nicegui.request_notice_gates import (
    GateNoticeState, GateObservationSnapshot, check_request_notice_gates,
)
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from stage5_generated_shared_owner import generated_shared_scope
from test_fix_notice_round4_20261122 import hold_windows_lock
from test_fix_notice_sweep_20261122 import header_client, reject, retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE, generated_owners


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range lock')
@pytest.mark.parametrize('diagnosis', ('stopped', 'uncertain'))
def test_expired_busy_keeps_watching_a_committed_stop_or_inflight(tmp_path, diagnosis):
    scope = generated_shared_scope(tmp_path)
    now = [0.0]
    policy = retry_policy(now, check_gate=lambda: check_request_notice_gates(scope))
    reject(policy, ManagedNetworkGateBusy(authority='shared'))
    if diagnosis == 'uncertain':
        with pytest.raises(RuntimeError), scope.gate.request():
            raise RuntimeError('synthetic interrupted GET')
    now[0] = 601
    with hold_windows_lock(scope.gate.lock_path, exclusive_open=False):
        if diagnosis == 'stopped':
            # A second writer persisted STOP. The observer's cache is still empty.
            with closing(sqlite3.connect(scope.gate.path)) as writer:
                writer.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                writer.commit()
        for tick in (601, 1202):
            now[0] = tick
            policy.recheck_gate_notices()
            record, = policy.progress_snapshot()
            assert record['stop_cause'] == 'gate_busy' and record['until'] == tick
            assert policy.has_gate_notices()
    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    if diagnosis == 'stopped':
        assert record['stop_cause'] == 'durable_gate' and record['status'] == 401
    else:
        assert record['stop_cause'] == 'gate_unverified' and record['gate_problem'] == 'uncertain'


@pytest.mark.parametrize('observation', ('busy', 'changed', 'missing', 'disabled', 'open'))
def test_expiry_success_and_snapshot_cleanup_need_evidence_for_a_gate_busy(observation):
    outcomes = {
        'busy': GateObservationSnapshot((GateNoticeState('shared', 'busy'),)),
        'changed': GateObservationSnapshot((GateNoticeState('shared', 'changed'),), False),
        'missing': GateObservationSnapshot((GateNoticeState('local', 'open'),)),
        'open': GateObservationSnapshot((GateNoticeState('shared', 'open'),)),
    }
    now = [0.0]
    watcher = None if observation == 'disabled' else lambda: outcomes[observation]
    policy = retry_policy(now, check_gate=watcher)
    reject(policy, ManagedNetworkGateBusy(authority='shared'))
    now[0] = 601
    assert policy.execute(lambda: 'synthetic success', deadline=1000) == 'synthetic success'
    assert policy.progress_snapshot()
    policy.recheck_gate_notices()
    assert bool(policy.progress_snapshot()) == (observation != 'open')


LOCAL_BOUNDARIES = (*product(('first', 'read', 'last'), ('local', 'shared'), ('before', 'after')),
                    ('confirm', 'shared', 'before'), ('confirm', 'shared', 'after'),
                    ('confirm', 'local', 'before'))


@pytest.mark.parametrize('cycle', (1, 2, 3))
@pytest.mark.parametrize('boundary', LOCAL_BOUNDARIES)
def test_refault_at_each_phase_before_during_and_after_retirement(tmp_path, cycle, boundary):
    local, _, _, _ = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    lock = local.gate.lock_path
    current = 0
    fired = False

    def interrupt(*phase):
        nonlocal fired
        if current == cycle and phase == boundary and not fired:
            lock.write_bytes(b'!')
            fired = True

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    reject(policy, ManagedNetworkGateStorageError(authority='local'))
    for current in range(1, cycle + 1):
        policy.recheck_gate_notices()
    assert fired
    assert policy.has_gate_notices()
    assert any(record.get('gate_problem') == 'storage' for record in policy.progress_snapshot())


@pytest.mark.asyncio
async def test_ui_polls_after_a_pin_disappears_and_publishes_new_fault(header_client):
    current = [GateNoticeState('shared', 'open')]
    policy = retry_policy([0.0], check_gate=lambda: tuple(current))
    reject(policy, ManagedNetworkGateStorageError(authority='shared'))
    with header_client:
        view = RequestStatusView(policy)
    try:
        await view.recheck_gate()
        await view.recheck_gate()
        assert not policy.has_gate_notices() and not view.label.visible
        current[0] = GateNoticeState('shared', 'unverified')
        await view.recheck_gate()
        assert policy.has_gate_notices() and view.label.visible
    finally:
        view.close()


@pytest.mark.parametrize('cycle', (1, 2, 3))
@pytest.mark.parametrize('boundary', ('confirm', 'snapshot'))
def test_refault_after_all_samples_is_discovered_by_the_next_tick(tmp_path, cycle, boundary):
    local, _, _, _ = generated_owners(tmp_path)
    lock = local.gate.lock_path
    calls = 0

    def interrupt(phase, authority, when):
        if boundary == 'confirm' and calls == cycle and (phase, authority, when) == ('confirm', 'local', 'after'):
            lock.write_bytes(b'!')

    def observe():
        nonlocal calls
        calls += 1
        snapshot = check_request_notice_gates(local, _step=interrupt)
        if boundary == 'snapshot' and calls == cycle:
            lock.write_bytes(b'!')
        return snapshot

    policy = retry_policy([0.0], check_gate=observe)
    policy.recheck_gate_notices()
    assert calls == 0
    reject(policy, ManagedNetworkGateStorageError(authority='local'))
    for _ in range(cycle):
        policy.recheck_gate_notices()
    assert calls == cycle and policy.should_watch_gate()
    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    assert (record['gate_problem'], record['gate_owners']) == ('storage', ('local',))


def windows_io_error(code):
    error = FileNotFoundError('synthetic missing gate path') if code in (2, 3) else OSError('synthetic gate I/O refusal')
    error.winerror = code
    return error


def advancing_policy():
    now = [0.0]
    policy = retry_policy(now)

    def sleep(seconds):
        now[0] += seconds

    policy.sleep = sleep
    return policy


@pytest.mark.parametrize('code', (5, 32, 33, 2, 3, 1117))
def test_draft_owner_lstat_failure_is_not_retried_as_transport(tmp_path, monkeypatch, code):
    local, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    database = local.root / PROFILE_DB
    lstat, stat = Path.lstat, Path.stat
    calls = []
    sent = []
    policy = advancing_policy()

    def owner_lstat(path):
        if path == database:
            calls.append(True)
            raise windows_io_error(code)
        return lstat(path)

    def missing_stat(path, **kwargs):
        if path == database and code in (2, 3):
            raise windows_io_error(code)
        return stat(path, **kwargs)

    with create_shared_draft_auth_reader(local, credential, shared) as reader:
        reader._transport.transport = lambda *_: sent.append(True)
        reader.set_retry_policy(policy)
        monkeypatch.setattr(Path, 'lstat', owner_lstat)
        monkeypatch.setattr(Path, 'stat', missing_stat)
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.require_identity()
    assert calls == [True] and not sent
    record, = policy.progress_snapshot()
    assert (record['gate_problem'], record['gate_owners']) == ('storage', ('local',))


def test_shared_startup_proof_io_failure_is_a_shared_storage_notice(tmp_path, monkeypatch):
    _, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    proof = shared.root.parent / _ANCHOR
    lstat = Path.lstat
    checked, sent = [], []
    policy = advancing_policy()

    def check_proof(path, **kwargs):
        if path == proof:
            checked.append(True)
            raise windows_io_error(32)
        return lstat(path, **kwargs)

    with shared.create_reader(credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(Path, 'lstat', check_proof)
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
    assert checked == [True] and not sent
    record, = policy.progress_snapshot()
    assert (record['gate_problem'], record['gate_owners']) == ('storage', ('shared',))


def test_selected_history_sidecar_io_failure_is_a_shared_storage_notice(tmp_path, monkeypatch):
    local, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    shared._startup_histories = ((local.root, ('native', _file_identity_directory(local.root),
                                             local.gate._database_identity, local.gate._lock_identity)),)
    sidecar = local.gate.path.with_name(local.gate.path.name + '-journal')
    lstat = Path.lstat
    calls, sent = [], []
    policy = advancing_policy()

    def check_sidecar(path, **kwargs):
        if path == sidecar:
            calls.append(True)
            raise windows_io_error(5)
        return lstat(path, **kwargs)

    with shared.create_reader(credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(Path, 'lstat', check_sidecar)
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
    assert calls == [True] and not sent
    record, = policy.progress_snapshot()
    assert (record['gate_problem'], record['gate_owners']) == ('storage', ('shared',))


@pytest.mark.parametrize('source', ('profile-marker', 'stop-marker', 'revision-db'))
def test_native_auth_preflight_reports_owner_io_as_storage(tmp_path, monkeypatch, source):
    local, _, _, credential = generated_owners(tmp_path)
    binding = NativeProfileBinding(NativeProfileStore(local.root))
    binding.managed_network_stop_scope = local
    binding.shared_network_stop_scope = generated_shared_scope(tmp_path)
    binding.auth_stop_required = binding.auth_shared_stop_required = True
    from pixivvault_nicegui import managed_network_stop_shared_transport as transport

    def reader(*args):
        pytest.fail('native preflight proceeded past a storage fault')

    monkeypatch.setattr(transport, 'create_shared_draft_auth_reader', reader)
    try:
        with monkeypatch.context() as fault:
            if source == 'revision-db':
                def reject_snapshot():
                    raise windows_io_error(5)

                binding.recheck_startup_stop = lambda: None
                fault.setattr(binding.store, 'snapshot', reject_snapshot)
            else:
                path = local.root / (PROFILE_MARKER if source == 'profile-marker' else _SCOPE_MARKER)
                lstat = Path.lstat

                def reject_marker(candidate):
                    if candidate == path:
                        raise windows_io_error(5)
                    return lstat(candidate)

                fault.setattr(Path, 'lstat', reject_marker)
            result, payload = binding._verify_auth_result(credential, Event())
            assert result.code == 'local_unavailable' and payload is None
    finally:
        binding.close()


@pytest.mark.parametrize('source', ('journal', 'sqlite-read'))
def test_fixed_profile_storage_notice_stays_until_its_own_problem_is_repaired(tmp_path, monkeypatch, source):
    local, _, _, credential = generated_owners(tmp_path)
    profile_db = local.root / PROFILE_DB
    journal = profile_db.with_name(profile_db.name + '-journal')
    uri = sqlite_readonly_uri(profile_db, immutable=True)
    connect = sqlite3.connect
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    sent = []

    def reject_read(path, *args, **kwargs):
        if path == uri:
            raise sqlite3.OperationalError('synthetic profile read error')
        return connect(path, *args, **kwargs)

    with local.create_reader(credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            if source == 'sqlite-read':
                fault.setattr(sqlite3, 'connect', reject_read)
            else:
                journal.write_bytes(b'synthetic journal')
            with pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
            for _ in range(3):
                policy.recheck_gate_notices()
                record, = policy.progress_snapshot()
                assert record['gate_problem'] == 'storage' and record['gate_owners'] == ('local',)
        if source == 'journal':
            journal.unlink()
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert not policy.has_gate_notices()
        assert policy.should_watch_gate() and not sent


def test_observing_healthy_storage_does_not_clear_notice_for_a_stale_reader_binding(tmp_path):
    local, _, _, credential = generated_owners(tmp_path)
    replacement = credential.with_name('synthetic-replacement.txt')
    replacement.write_bytes(credential.read_bytes())
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    with local.create_reader(credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        store = NativeProfileStore(local.root)
        try:
            store.update(cookie_file=replacement, expected_revision=store.snapshot().revision)
        finally:
            store.close()
        with pytest.raises(ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        reject(policy, ManagedNetworkGateStorageError(authority='local'))
        for _ in range(3):
            assert check_request_notice_gates(local).states[0].state == 'open'
            policy.recheck_gate_notices()
            record, = policy.progress_snapshot()
            assert (record['gate_problem'], record['gate_owners']) == ('storage', ('local',))
            assert 'requires_rebind' not in record
            assert 'expected_selections' not in record
        store = NativeProfileStore(local.root)
        try:
            store.update(cookie_file=credential, expected_revision=store.snapshot().revision)
        finally:
            store.close()
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert not policy.has_gate_notices()
        assert not sent


@pytest.mark.parametrize('failure', ('post-lock-fstat', 'lock-close', 'db-close', 'network'))
def test_gate_file_boundaries_keep_real_transport_errors_retryable(tmp_path, monkeypatch, failure):
    local, _, _, credential = generated_owners(tmp_path)
    calls = []
    policy = advancing_policy()

    def dispatch(*_):
        calls.append(True)
        if failure == 'network':
            raise OSError('synthetic send failure')
        return b'fake image'

    with local.create_reader(credential, transport=dispatch) as reader:
        reader.set_retry_policy(policy)
        if failure == 'post-lock-fstat':
            active = []
            original_lock, fstat = gate_module._set_stop_byte_lock, os.fstat

            def watch_lock(stream, *, lock: bool):
                result = original_lock(stream, lock=lock)
                active[:] = [stream.fileno()] if lock else []
                return result

            def fault_fstat(fd):
                if fd in active:
                    raise windows_io_error(5)
                return fstat(fd)

            monkeypatch.setattr(gate_module, '_set_stop_byte_lock', watch_lock)
            monkeypatch.setattr(os, 'fstat', fault_fstat)
        elif failure == 'lock-close':
            opener = gate_module._open_existing_stop_lock

            class FailingClose:
                def __init__(self, stream):
                    self.stream = stream

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    self.close()

                def close(self):
                    self.stream.close()
                    raise windows_io_error(32)

            monkeypatch.setattr(gate_module, '_open_existing_stop_lock', lambda path: FailingClose(opener(path)))
        elif failure == 'db-close':
            connect = sqlite3.connect

            class FailingClose(sqlite3.Connection):
                def close(self):
                    super().close()
                    raise sqlite3.OperationalError('synthetic SQLite close failure')

            def failing_connection(path, *args, **kwargs):
                if path == gate_module._gate_database_uri(local.gate.path):
                    kwargs['factory'] = FailingClose
                return connect(path, *args, **kwargs)

            monkeypatch.setattr(sqlite3, 'connect', failing_connection)

        expected = TransportFailed if failure == 'network' else ManagedNetworkGateStorageError
        with pytest.raises(expected):
            reader.get_image_bytes(IMAGE)
    record, = policy.progress_snapshot()
    if failure != 'network':
        assert len(calls) == (1 if failure == 'lock-close' else 0)
        assert (record['gate_problem'], record['gate_owners']) == ('storage', ('local',))
    else:
        assert len(calls) == 3
        assert record['phase'] == 'stopped' and 'stop_cause' not in record
