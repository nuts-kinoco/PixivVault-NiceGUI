"""Isolation regressions for the third review of the acquisition notice."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, nullcontext
from itertools import product
import os
import random
import sqlite3
import stat
import subprocess
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate_module
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.request_notice_gates import (
    GateNoticeState, GateObservationSnapshot, check_request_notice_gates,
)
from pixivvault_nicegui.request_policy import is_pinned_notice, reconcile_gate_notices
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from stage5_generated_shared_owner import generated_shared_scope
from test_fix_notice_sweep_20261122 import header_client, reject, retry_policy
from test_stage5_r1_shared_stop_transport import generated_owners


def test_readable_shared_lock_with_denied_write_access_keeps_storage_notice(tmp_path, monkeypatch):
    shared = generated_shared_scope(tmp_path)
    lock = shared.gate.lock_path
    open_existing = gate_module._open_existing_stop_lock

    def deny_write(path):
        if path == lock:
            error = PermissionError('synthetic access denied')
            error.winerror = 5
            raise error
        return open_existing(path)

    monkeypatch.setattr(gate_module, '_open_existing_stop_lock', deny_write)
    assert lock.read_bytes() == b'0'
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
    with pytest.raises(ManagedNetworkGateStorageError):
        policy.execute(shared.require_allowed, deadline=1)
    for _ in range(2):
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        assert any(record.get('gate_problem') == 'storage' for record in policy.progress_snapshot())
    with pytest.raises(ManagedNetworkGateStorageError):
        shared.require_allowed()


def test_local_fault_reappearing_during_shared_final_check_keeps_polling(tmp_path, monkeypatch):
    local, _, _, _ = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    lock = local.gate.lock_path
    lock.write_bytes(b'!')
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with pytest.raises(ManagedNetworkGateStorageError):
        policy.execute(local.require_allowed, deadline=1)
    policy.recheck_gate_notices()
    lock.write_bytes(b'0')

    verify_owner = type(shared)._check_owner
    calls = 0

    def check_owner(owner):
        nonlocal calls
        calls += 1
        if calls == 2:
            lock.write_bytes(b'!')
        return verify_owner(owner)

    monkeypatch.setattr(type(shared), '_check_owner', check_owner)
    policy.recheck_gate_notices()
    assert calls == 3
    assert policy.has_gate_notices()
    policy.recheck_gate_notices()
    assert any(record.get('gate_problem') == 'storage' for record in policy.progress_snapshot())


def test_new_authority_for_same_stop_resets_ack_and_rejects_old_click(header_client):
    observations = [GateNoticeState('local', 'stopped', 401), GateNoticeState('shared', 'open')]
    policy = retry_policy([0.0], check_gate=lambda: tuple(observations))
    reject(policy, ManagedNetworkStopped(401))
    policy.recheck_gate_notices()
    with header_client:
        view = RequestStatusView(policy)
    try:
        view.dismiss()
        assert not view.dismiss_button.visible
        old_revision = str(view._dismiss_revision)
        observations[1] = GateNoticeState('shared', 'stopped', 401)
        policy.recheck_gate_notices()
        view._dismiss_rendered(SimpleNamespace(args={'revision': old_revision}))
        view.refresh()
        assert view.dismiss_button.visible
        assert '確認済み' not in view.label.text
    finally:
        view.close()


KINDS = ('durable_gate', 'storage', 'uncertain', 'gate_busy')
AUTHORITIES = ('local', 'shared')
OWNER_SETS = ((), ('local',), ('shared',), AUTHORITIES)
STATUSES = (401, 403, 429)


def notice(kind, owners=(), status=None, streak=0):
    record = dict(phase='stopped', attempt=1, limit=3, status=status, until=0,
                  stop_cause=kind, gate_owners=owners, clear_streak=streak)
    if kind in ('storage', 'uncertain'):
        record.update(stop_cause='gate_unverified', gate_problem=kind)
    return record


@pytest.mark.parametrize('kind', (*KINDS, 'mixed'))
def test_reconciler_transition_matrix(kind):
    local_states = [GateNoticeState('local', state) for state in ('open', 'busy', 'unverified')]
    shared_states = [GateNoticeState('shared', state) for state in ('open', 'busy', 'unverified')]
    local_states += [GateNoticeState('local', 'stopped', status) for status in STATUSES]
    shared_states += [GateNoticeState('shared', 'stopped', status) for status in STATUSES]
    shared_states += [GateNoticeState('shared', 'uncertain', inflight=True),
                      GateNoticeState('shared', 'stopped', 401, True)]
    observations = list(product(local_states, shared_states))
    observations += [(*pair, GateNoticeState('shared', 'busy', 401)) for pair in observations]
    observations += [(GateNoticeState('both', state, status),) for state, status in (
        ('open', None), ('unverified', None), ('busy', 401))]
    snapshots = [GateObservationSnapshot(states, consistent)
                 for states in observations for consistent in (False, True)]
    snapshots += [GateObservationSnapshot((GateNoticeState(owner, 'changed'),), False)
                  for owner in AUTHORITIES]
    for owners, code, streak, snapshot, now in product(
            OWNER_SETS, STATUSES, (0, 1), snapshots, (0, 601)):
        records = {1: notice(kind, owners, code if kind == 'durable_gate' else None, streak)}
        if kind == 'mixed':
            records = {1: notice('durable_gate', owners, code, streak),
                       2: notice('storage', ('local',), streak=streak),
                       3: notice('uncertain', ('shared',), streak=streak)}
        records[4] = dict(phase='requesting', status=None)
        original = {key: item.copy() for key, item in records.items()}
        updated, _ = reconcile_gate_notices(records, snapshot, now, 4)
        assert records == original and updated[4] is records[4]
        states = snapshot.states
        consistent = snapshot.consistent
        confirmed_clean = consistent and all(state.state == 'open' for state in states)
        for key, previous in records.items():
            cause = previous.get('stop_cause')
            if cause == 'gate_busy':
                contention = any(state.state == 'busy' for state in states)
                keep = not consistent or contention
                assert (key in updated) == keep
                if keep:
                    assert updated[key]['until'] == now
            if not is_pinned_notice(previous):
                continue
            if confirmed_clean:
                assert (key in updated) == (streak == 0)
                if streak == 0:
                    assert updated[key]['clear_streak'] == 1
                continue
            if streak == 0:
                assert key in updated
            related = previous['gate_owners']
            if previous.get('gate_problem') == 'uncertain':
                related = ('shared',)
            unresolved = any(state.state in ('busy', 'unverified', 'uncertain')
                             and (not related or state.authority in (*related, 'both')) for state in states)
            if not consistent or unresolved:
                assert updated[key]['clear_streak'] == 0
            if cause == 'durable_gate':
                sources = {state.authority for state in states if state.status == code}
                if sources:
                    assert sources - {'both'} <= set(updated[key]['gate_owners'])
                    assert updated[key]['clear_streak'] == 0
        for state in states:
            if state.status is not None:
                assert any(item.get('stop_cause') == 'durable_gate' and item['status'] == state.status
                           and (state.authority == 'both' or state.authority in item['gate_owners'])
                           for item in updated.values())
            if state.state == 'unverified':
                responsible = () if state.authority == 'both' else (state.authority,)
                assert any(item.get('gate_problem') == 'storage'
                           and item['gate_owners'] == responsible for item in updated.values())
            if state.inflight:
                assert any(item.get('gate_problem') == 'uncertain' for item in updated.values())
        if len(states) == 1 and states[0].state == 'changed':
            assert updated.keys() == records.keys()


@pytest.mark.parametrize('fault', ['clean', 'lock-denied', 'db-denied', 'lock-truncated',
                                   'lock-corrupt', 'lock-missing', 'lock-replaced', 'lock-link',
                                   'db-sidecar', 'db-schema', 'db-revision', 'cached-stop',
                                   'inflight', 'stopped-inflight', 'owner', 'byte-busy', 'share-busy',
                                   'db-share-busy'])
def test_probe_and_shared_admission_gate_equivalence(fault, tmp_path, monkeypatch):
    shared = generated_shared_scope(tmp_path)
    gate = shared.gate
    lock, database = gate.lock_path, gate.path
    lock_context = nullcontext()
    expected = 'unverified'
    if fault in ('lock-denied', 'db-denied'):
        if os.name != 'nt':
            pytest.skip('Windows read-only attribute')
        path = lock if fault == 'lock-denied' else database
        path.chmod(stat.S_IREAD)
    elif fault == 'lock-truncated':
        lock.write_bytes(b'')
    elif fault == 'lock-corrupt':
        lock.write_bytes(b'!')
    elif fault == 'lock-missing':
        lock.rename(lock.with_suffix('.gone'))
    elif fault == 'lock-replaced':
        lock.rename(lock.with_suffix('.old'))
        lock.write_bytes(b'0')
    elif fault == 'lock-link':
        os.link(lock, lock.with_suffix('.link'))
    elif fault == 'db-sidecar':
        database.with_name(database.name + '-shm').write_bytes(b'synthetic')
    elif fault in ('db-schema', 'db-revision'):
        with sqlite3.connect(database) as db:
            if fault == 'db-schema':
                db.execute('CREATE TABLE unexpected (value)')
            else:
                db.execute('UPDATE gate SET revision=2')
        db.close()
    elif fault == 'cached-stop':
        gate._observed_stop_status = 403
    elif fault in ('inflight', 'stopped-inflight'):
        with pytest.raises(RuntimeError), gate.request():
            raise RuntimeError('synthetic interruption')
        if fault == 'stopped-inflight':
            gate.trip(401)
        expected = 'stopped' if fault == 'stopped-inflight' else 'uncertain'
    elif fault == 'owner':
        from pixivvault_nicegui.managed_network_stop_shared_scope import _OWNER_FILE
        (shared.root / _OWNER_FILE).write_bytes(b'{}')
    elif fault in ('byte-busy', 'share-busy', 'db-share-busy'):
        if os.name != 'nt':
            pytest.skip('Windows lock error semantics')
        expected = 'unverified' if fault == 'db-share-busy' else 'busy'
        lock_context = hold_windows_lock(database if fault == 'db-share-busy' else lock,
                                         exclusive_open=fault != 'byte-busy')
    else:
        expected = 'open'

    def capture():
        return (gate._observed_stop_status,
                {p.name: p.stat().st_mtime_ns for p in shared.root.iterdir() if p.is_file()})

    def forbidden(*args, **kwargs):
        pytest.fail('notice tried to use the dispatch lock')

    try:
        with lock_context:
            with monkeypatch.context() as observing:
                observing.setattr(gate, '_exclusive', forbidden)
                before = capture()
                state, = check_request_notice_gates(shared).states
                assert state.state == expected
                assert capture() == before
            try:
                shared.require_allowed()
            except (ManagedNetworkStopped, ManagedNetworkGateStorageError) as error:
                assert state.state != 'open'
                if fault in ('share-busy', 'byte-busy'):
                    assert isinstance(error, ManagedNetworkGateBusy)
                if fault in ('db-share-busy', 'db-revision'):
                    assert type(error) is ManagedNetworkGateStorageError
            else:
                # SQLite can legally open the existing live DB read-only.
                # The observer still requires a writable handle for a clean gate.
                assert fault in ('clean', 'db-denied', 'db-sidecar')
    finally:
        if fault in ('lock-denied', 'db-denied'):
            path.chmod(stat.S_IREAD | stat.S_IWRITE)


@contextmanager
def hold_windows_lock(path, *, exclusive_open):
    if exclusive_open:
        handle = gate_module._create_file(str(path), 0xC0000000, 0, None, 3, 0x00200080, None)
        assert handle != gate_module.ctypes.c_void_p(-1).value
        try:
            yield
        finally:
            assert gate_module._close_handle(handle)
    else:
        with gate_module._open_existing_stop_lock(path) as stream:
            gate_module._set_stop_byte_lock(stream, lock=True)
            yield


def test_selected_history_db_can_remain_readonly(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows read-only attribute')
    from pixivvault_nicegui.managed_network_stop_shared_scope import _file_identity_directory
    from pixivvault_nicegui.managed_network_stop_startup import selected_history_guard

    local, _, _, _ = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    shared._startup_histories = ((str(local.root), ('native', _file_identity_directory(local.root),
                                 local.gate._database_identity, local.gate._lock_identity)),)
    local.gate.path.chmod(stat.S_IREAD)
    try:
        with selected_history_guard(shared):
            shared.require_allowed()
        snapshot = check_request_notice_gates(shared)
        assert snapshot.consistent and [state.state for state in snapshot.states] == ['open', 'open']
    finally:
        local.gate.path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_database_exclusive_lock_preserves_storage_notice_without_probe_locking(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows SQLite locking')
    shared = generated_shared_scope(tmp_path)
    database = shared.gate.path
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
    with closing(sqlite3.connect(database)) as db:
        db.execute('BEGIN EXCLUSIVE')
        assert not any(database.with_name(database.name + suffix).exists()
                       for suffix in ('-journal', '-shm', '-wal'))
        with pytest.raises(ManagedNetworkGateStorageError):
            policy.execute(shared.require_allowed, deadline=1)
        for _ in range(2):
            policy.recheck_gate_notices()
        assert policy.has_gate_notices()
    for _ in range(2):
        policy.recheck_gate_notices()
    assert not policy.has_gate_notices()


def test_database_reserved_lock_allows_open_observation(tmp_path):
    shared = generated_shared_scope(tmp_path)
    with closing(sqlite3.connect(shared.gate.path)) as db:
        db.execute('BEGIN IMMEDIATE')
        shared.require_allowed()
        assert check_request_notice_gates(shared).states == (GateNoticeState('shared', 'open'),)


@pytest.mark.parametrize('stopped', (False, True), ids=('open', 'stopped'))
def test_directory_write_denied_preserves_shared_gate_diagnosis(tmp_path, stopped):
    if os.name != 'nt':
        pytest.skip('Windows inherited file rights')
    shared = generated_shared_scope(tmp_path)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(shared))
    sent = []

    def get():
        with shared.gate.request():
            sent.append('synthetic')

    def acl(*arguments):
        result = subprocess.run(['icacls.exe', str(shared.root), *arguments],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0

    try:
        # Deny FILE_ADD_FILE on this directory only. Existing children retain their permissions.
        if stopped:
            shared.gate.trip(401)
        acl('/deny', '*S-1-1-0:(WD)')
        if stopped:
            # Verify the gate rejects before the intent is needed, and seed
            # only an unrelated notice so the poll has to discover the 401.
            with pytest.raises(ManagedNetworkStopped):
                get()
            reject(policy, ManagedNetworkGateStorageError(authority='local'))
        else:
            with pytest.raises(ManagedNetworkGateStorageError):
                policy.execute(get, deadline=1)
        assert sent == [] and not shared.gate.inflight_path.exists()
        for _ in range(2):
            policy.recheck_gate_notices()
        records = policy.progress_snapshot()
        if stopped:
            assert any(item['status'] == 401 and item.get('gate_owners') == ('shared',)
                       for item in records)
            assert not any(item.get('gate_problem') == 'storage' and item.get('gate_owners') == ('shared',)
                           for item in records)
        else:
            assert policy.has_gate_notices()
    finally:
        acl('/remove:d', '*S-1-1-0')
    if not stopped:
        for _ in range(2):
            policy.recheck_gate_notices()
        assert not policy.has_gate_notices()


def test_latch_changed_after_read_makes_whole_snapshot_inconsistent(tmp_path):
    shared = generated_shared_scope(tmp_path)
    original = shared.gate._observed_stop_status

    def trip_in_memory(phase, authority, moment):
        if (phase, authority, moment) == ('read', 'shared', 'after'):
            shared.gate._observed_stop_status = 429

    assert original is None and check_request_notice_gates(shared).consistent
    snapshot = check_request_notice_gates(shared, _step=trip_in_memory)
    assert not snapshot.consistent and snapshot.states[0].state == 'unverified'


@pytest.mark.parametrize('probe', ('gate-lock', 'database'))
def test_observation_does_not_delay_get_while_its_handle_is_open(tmp_path, monkeypatch, probe):
    if probe == 'database' and os.name != 'nt':
        pytest.skip('Windows SQLite read-lock probe')
    shared = generated_shared_scope(tmp_path)
    read_bytes = gate_module._read_gate_probe_bytes
    observing = Event()
    release = Event()

    def pause(stream, length):
        if length == (2 if probe == 'gate-lock' else 1):
            observing.set()
            assert release.wait(5)
        return read_bytes(stream, length)

    monkeypatch.setattr(gate_module, '_read_gate_probe_bytes', pause)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(check_request_notice_gates, shared)
        try:
            assert observing.wait(5)
            with shared.gate.request():
                assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=5).states == (GateNoticeState('shared', 'open'),)


BOUNDARIES = (*product(('first', 'read', 'last'), AUTHORITIES, ('before', 'after')),
              ('apply', None, None))


@pytest.mark.parametrize('seed', (*KINDS, 'busy-only'))
@pytest.mark.parametrize('fault', ('local-stop', 'shared-stop', 'storage', 'uncertain', 'publish'))
@pytest.mark.parametrize('boundary', BOUNDARIES)
def test_fault_at_every_observation_boundary_preserves_polling(tmp_path, seed, fault, boundary):
    local, _, _, _ = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    fired = False
    recovered = False

    def interrupt(*step):
        nonlocal fired
        if fired or step != boundary:
            return
        fired = True
        if fault == 'local-stop':
            local.gate.trip(401)
        elif fault == 'shared-stop':
            shared.gate.trip(403)
        elif fault == 'uncertain':
            with pytest.raises(RuntimeError), shared.gate.request():
                raise RuntimeError('synthetic interruption')
        else:
            local.gate.lock_path.write_bytes(b'!')
            if fault == 'publish':
                reject(policy, ManagedNetworkGateStorageError(authority='local'))

    def observe():
        if recovered:
            return GateObservationSnapshot(tuple(GateNoticeState(owner, 'open') for owner in AUTHORITIES))
        snapshot = check_request_notice_gates(local, _step=interrupt)
        interrupt('apply', None, None)
        return snapshot

    policy = retry_policy([0.0], check_gate=observe)
    if seed in ('gate_busy', 'busy-only'):
        reject(policy, ManagedNetworkGateBusy())
    seeds = {'durable_gate': ManagedNetworkStopped(429, authority='local'),
             'storage': ManagedNetworkGateStorageError(authority='shared'),
             'uncertain': SharedGetOutcomeUncertain(),
             'gate_busy': ManagedNetworkStopped(429, authority='shared')}
    if seed != 'busy-only':
        reject(policy, seeds[seed])
    policy.recheck_gate_notices()
    assert fired
    if seed == 'busy-only' and fault != 'publish' and boundary == ('apply', None, None):
        # The observation was already complete. The active watch will
        # diagnose this change even though the transient clears for now.
        assert not policy.has_gate_notices() and policy.should_watch_gate()
    else:
        assert policy.has_gate_notices()
    if seed != 'busy-only':
        assert any(is_pinned_notice(record) for record in policy.progress_snapshot())
    policy.recheck_gate_notices()
    records = policy.progress_snapshot()
    if fault in ('local-stop', 'shared-stop'):
        code = 401 if fault == 'local-stop' else 403
        assert any(record['status'] == code for record in records)
    else:
        problem = 'uncertain' if fault == 'uncertain' else 'storage'
        assert any(record.get('gate_problem') == problem for record in records)
    assert policy.has_gate_notices()
    recovered = True
    policy.recheck_gate_notices()
    assert policy.has_gate_notices()
    policy.recheck_gate_notices()
    assert not policy.has_gate_notices()


def test_publish_fences_stale_clear_and_other_publish_resets_clear_streak():
    observations = [GateNoticeState('local', 'open'), GateNoticeState('shared', 'open')]
    report_during_read = False
    calls = 0

    def observe():
        nonlocal calls, report_during_read
        calls += 1
        snapshot = GateObservationSnapshot(tuple(observations))
        if report_during_read:
            report_during_read = False
            observations[0] = GateNoticeState('local', 'unverified')
            reject(policy, ManagedNetworkGateStorageError(authority='local'))
        return snapshot

    policy = retry_policy([0.0], check_gate=observe)
    reject(policy, ManagedNetworkStopped(401, authority='shared'))
    policy.recheck_gate_notices()
    report_during_read = True
    policy.recheck_gate_notices()
    assert calls == 3
    assert any(item['status'] == 401 for item in policy.progress_snapshot())
    assert any(item.get('gate_problem') == 'storage' for item in policy.progress_snapshot())
    policy.recheck_gate_notices()
    assert not any(item['status'] == 401 for item in policy.progress_snapshot())


def test_recovered_authority_and_then_recurrence_each_invalidate_ack(header_client):
    observations = [GateNoticeState(owner, 'stopped', 401) for owner in AUTHORITIES]
    policy = retry_policy([0.0], check_gate=lambda: GateObservationSnapshot(tuple(observations)))
    reject(policy, ManagedNetworkStopped(401, authority='both'))
    with header_client:
        view = RequestStatusView(policy)
    try:
        for next_local in ('open', 'stopped'):
            view.dismiss()
            assert not view.dismiss_button.visible
            old_revision = str(view._dismiss_revision)
            observations[0] = GateNoticeState('local', next_local, 401 if next_local == 'stopped' else None)
            policy.recheck_gate_notices()
            if next_local == 'open':
                view.refresh()
                assert not view.dismiss_button.visible
                policy.recheck_gate_notices()
            view._dismiss_rendered(SimpleNamespace(args={'revision': old_revision}))
            assert view.dismiss_button.visible and '確認済み' not in view.label.text
    finally:
        view.close()


@pytest.mark.parametrize('seed', range(16))
def test_stateful_policy_and_header_converge_after_observation_and_ack(seed, header_client):
    rng = random.Random(seed)
    now = [0.0]
    states = {owner: GateNoticeState(owner, 'open') for owner in AUTHORITIES}
    sample = 'steady'
    rendezvous = None

    def observe():
        if rendezvous is not None:
            entered, release = rendezvous
            entered.set()
            assert release.wait(5)
        if sample == 'changed':
            return GateObservationSnapshot((GateNoticeState('both', 'changed'),), False)
        if sample == 'opaque':
            return GateObservationSnapshot((GateNoticeState('both', 'busy'),))
        return GateObservationSnapshot(tuple(states.values()))

    policy = retry_policy(now, check_gate=observe)
    with header_client:
        view = RequestStatusView(policy)
    try:
        for _ in range(40):
            previous_acks = view._acknowledged.copy()
            owner = rng.choice(AUTHORITIES)
            action = rng.choice(('open', 'busy', 'unverified', 'stopped', 'poll', 'ack', 'operation',
                                 'advance', 'expand', 'old-click', 'uncertain', 'parallel-poll', 'sample'))
            if action == 'uncertain':
                states['shared'] = GateNoticeState('shared', 'uncertain', inflight=True)
            elif action in ('open', 'busy', 'unverified', 'stopped'):
                status = rng.choice(STATUSES) if action == 'stopped' else None
                states[owner] = GateNoticeState(owner, action, status)
            elif action == 'poll':
                policy.recheck_gate_notices()
            elif action == 'sample':
                sample = rng.choice(('steady', 'changed', 'opaque'))
            elif action == 'parallel-poll':
                if policy.has_gate_notices():
                    rendezvous = (Event(), Event())
                    entered, release = rendezvous
                    with ThreadPoolExecutor(max_workers=1) as worker:
                        future = worker.submit(policy.recheck_gate_notices)
                        try:
                            assert entered.wait(5)
                            policy.recheck_gate_notices()
                        finally:
                            release.set()
                        future.result(timeout=5)
                    rendezvous = None
            elif action == 'operation':
                state = states[owner]
                if state.state == 'open':
                    policy.execute(lambda: 'synthetic success', deadline=now[0] + 1)
                else:
                    authority = rng.choice((owner, 'both', None))
                    error = {'stopped': ManagedNetworkStopped(state.status or 401, authority=authority),
                             'uncertain': SharedGetOutcomeUncertain(),
                             'unverified': ManagedNetworkGateStorageError(authority=authority),
                             'busy': ManagedNetworkGateBusy()}[state.state]
                    with pytest.raises(type(error)):
                        policy.execute(lambda: raise_operation(error), deadline=now[0] + 1)
            elif action == 'advance':
                now[0] += 601
            elif action == 'ack':
                view.dismiss()
            elif action == 'expand':
                view.toggle_more()
            else:
                view.refresh()
                confirmed = view._acknowledged.copy()
                view._dismiss_rendered(SimpleNamespace(args={'revision': str(view._dismiss_revision - 1)}))
                assert view._acknowledged == confirmed
            view.refresh()
            snapshot = policy.progress_snapshot()
            records = {item['notice_id']: item for item in snapshot if is_pinned_notice(item)}
            assert all(owners == frozenset(records[key].get('gate_owners', ()))
                       for key, owners in view._acknowledged.items())
            if action != 'ack':
                assert all(key in previous_acks for key in view._acknowledged)
            if len(records) == 1 and all(is_pinned_notice(item) or item['phase'] not in ('stopped', 'failed')
                                         for item in snapshot):
                assert view.dismiss_button.visible == (next(iter(records)) not in view._acknowledged)
        # Seed the observer as a real blocked attempt would, then demand convergence.
        with pytest.raises(ManagedNetworkGateBusy):
            policy.execute(lambda: raise_operation(ManagedNetworkGateBusy()), deadline=now[0] + 1)
        sample = 'steady'
        for _ in range(2):
            policy.recheck_gate_notices()
        records = policy.progress_snapshot()
        for owner, state in states.items():
            if state.state == 'stopped':
                assert any(item['status'] == state.status and owner in item.get('gate_owners', ())
                           for item in records)
            if state.state in ('unverified', 'uncertain'):
                problem = 'storage' if state.state == 'unverified' else 'uncertain'
                assert any(item.get('gate_problem') == problem and owner in item.get('gate_owners', ())
                           for item in records)
        states.update((owner, GateNoticeState(owner, 'open')) for owner in AUTHORITIES)
        for _ in range(2):
            policy.recheck_gate_notices()
        assert not policy.has_gate_notices()
    finally:
        view.close()


def raise_operation(error):
    raise error
