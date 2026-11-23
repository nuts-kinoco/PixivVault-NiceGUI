"""Acquisition-gate diagnostic precedence and observation races."""
from contextlib import ExitStack, closing
import os
import sqlite3
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate_module, request_notice_gates as observer
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_profile_scope import _SCOPE_MARKER
from pixivvault_nicegui.managed_network_stop_shared_transport import (
    _DraftAuthSharedTransport, create_shared_draft_auth_reader, create_shared_profile_reader,
)
from pixivvault_nicegui.native_profile import PROFILE_DB
from pixivvault_nicegui.request_notice_gates import (
    GateNoticeState, GateObservationSnapshot, check_request_notice_gates,
)
from pixivvault_nicegui.request_policy import request_scope
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from stage5_generated_shared_owner import generated_shared_scope
from test_fix_notice_round4_20261122 import AUTHORITIES, hold_windows_lock
from test_fix_notice_sweep_20261122 import header_client, reject, retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE, generated_owners


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range and share modes')
@pytest.mark.parametrize('phase', ('complete', 'rollback-journal', 'sqlite-exclusive'))
def test_get_activity_keeps_busy_or_changed_without_a_storage_banner(tmp_path, phase):
    shared = generated_shared_scope(tmp_path)
    with ExitStack() as transaction:
        if phase == 'complete':
            transaction.enter_context(shared.gate.request())
        else:
            transaction.enter_context(hold_windows_lock(shared.gate.lock_path, exclusive_open=False))
            db = transaction.enter_context(closing(sqlite3.connect(shared.gate.path)))
            db.execute('BEGIN EXCLUSIVE' if phase == 'sqlite-exclusive' else 'BEGIN IMMEDIATE')
            if phase == 'rollback-journal':
                db.execute('UPDATE gate SET revision=1')
                assert shared.gate.path.with_name(shared.gate.path.name + '-journal').exists()

        def release_after_first(phase_name, authority, when):
            if phase == 'complete' and (phase_name, authority, when) == ('first', 'shared', 'after'):
                transaction.close()

        # A raw Windows lock makes admission return busy from the same thread.
        # For a real GET in this thread, seed the already reported busy notice.
        policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(
            shared, _step=release_after_first))
        if phase == 'complete':
            reject(policy, ManagedNetworkGateBusy())
        else:
            with pytest.raises(ManagedNetworkGateBusy):
                policy.execute(shared.require_allowed, deadline=1)
        policy.recheck_gate_notices()
        assert set(item['stop_cause'] for item in policy.progress_snapshot()) == {'gate_busy'}
    policy.recheck_gate_notices()
    assert policy.has_gate_notices()


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-range lock')
def test_commit_can_finish_between_last_journal_check_and_lock_reprobe(tmp_path, monkeypatch):
    shared = generated_shared_scope(tmp_path)
    database = shared.gate.path
    journal = database.with_name(database.name + '-journal')
    exists = observer._exists
    last_started = committed = False

    def begin_last(phase, authority, when):
        nonlocal last_started
        if (phase, authority, when) == ('last', 'shared', 'before'):
            last_started = True

    def observe_journal(path):
        nonlocal committed
        if last_started and path == journal and not committed:
            # The observer already sampled the unlocked sentinel. Have a
            # writer start and finish its actual SQLite transaction before
            # the observer can react to the journal it saw.
            with hold_windows_lock(shared.gate.lock_path, exclusive_open=False):
                with closing(sqlite3.connect(database)) as db:
                    db.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                    seen = exists(path)
                    assert seen
                    db.commit()
            committed = True
            return seen
        return exists(path)

    monkeypatch.setattr(observer, '_exists', observe_journal)
    snapshot = check_request_notice_gates(shared, _step=begin_last)
    assert committed and not snapshot.consistent
    assert snapshot.states == (GateNoticeState('shared', 'stopped', 401),)
    with pytest.raises(ManagedNetworkStopped) as error:
        shared.require_allowed()
    assert error.value.status_code == 401
    assert check_request_notice_gates(shared).states == (GateNoticeState('shared', 'stopped', 401),)


@pytest.mark.skipif(os.name != 'nt', reason='Windows file share modes')
def test_database_sharing_violation_adds_storage_notice_to_fixed_busy(tmp_path):
    shared = generated_shared_scope(tmp_path)
    now = [0.0]
    policy = retry_policy(now, check_gate=lambda: check_request_notice_gates(shared))
    reject(policy, ManagedNetworkGateBusy())
    now[0] = 601
    with hold_windows_lock(shared.gate.path, exclusive_open=True):
        with pytest.raises(ManagedNetworkGateStorageError) as error:
            shared.require_allowed()
        assert type(error.value) is ManagedNetworkGateStorageError
        assert check_request_notice_gates(shared).states[0].state == 'unverified'
        policy.recheck_gate_notices()
        records = policy.progress_snapshot()
        assert any(row['stop_cause'] == 'gate_busy' for row in records)
        record, = [row for row in records if row.get('gate_problem') == 'storage']
        assert record['gate_problem'] == 'storage' and record['gate_owners'] == ('shared',)
        assert policy.has_gate_notices()


@pytest.mark.parametrize(('winerror', 'state', 'admission_error'), (
    (32, 'busy', ManagedNetworkGateBusy),
    (33, 'unverified', ManagedNetworkGateStorageError),
    (5, 'unverified', ManagedNetworkGateStorageError),
    (87, 'unverified', ManagedNetworkGateStorageError),
))
def test_lock_open_error_class_matches_admission(tmp_path, monkeypatch, winerror, state, admission_error):
    shared = generated_shared_scope(tmp_path)
    open_existing = gate_module._open_existing_stop_lock

    def fail_lock_open(path):
        if path == shared.gate.lock_path:
            error = OSError('synthetic lock open failure')
            error.winerror = winerror
            raise error
        return open_existing(path)

    monkeypatch.setattr(gate_module, '_open_existing_stop_lock', fail_lock_open)
    with pytest.raises(admission_error) as error:
        shared.require_allowed()
    assert type(error.value) is admission_error
    snapshot = check_request_notice_gates(shared)
    assert snapshot.consistent and snapshot.states[0].state == state


@pytest.mark.parametrize('defect', ('removed', 'corrupt-lock', 'corrupt-row'))
def test_changed_snapshot_uses_final_health_for_the_storage_diagnosis(tmp_path, defect):
    shared = generated_shared_scope(tmp_path)
    lock = shared.gate.lock_path
    if defect == 'removed':
        lock.write_bytes(b'!')

    def change(phase, authority, when):
        if (phase, authority, when) != ('last', 'shared', 'before'):
            return
        if defect == 'corrupt-row':
            with closing(sqlite3.connect(shared.gate.path)) as db:
                db.execute('UPDATE gate SET revision=2')
                db.commit()
        else:
            lock.write_bytes(b'0' if defect == 'removed' else b'!')

    snapshot = check_request_notice_gates(shared, _step=change)
    assert not snapshot.consistent
    assert snapshot.states[0].state == ('changed' if defect == 'removed' else 'unverified')


@pytest.mark.parametrize(('point', 'when', 'diagnosis'), (
    ('verify', 'before', 'changed'),
    ('verify', 'after', 'changed'),
    ('verify_fingerprint', 'before', 'changed'),
    ('verify_fingerprint', 'after', 'unverified'),
))
def test_final_error_needs_a_fingerprint_that_survives_recovery(tmp_path, point, when, diagnosis):
    shared = generated_shared_scope(tmp_path)
    restored = False

    def revision(value):
        with closing(sqlite3.connect(shared.gate.path)) as db:
            db.execute('UPDATE gate SET revision=?', (value,))
            db.commit()

    def interrupt(phase, authority, moment):
        nonlocal restored
        if (phase, moment) == ('last', 'before'):
            revision(2)
        if (phase, moment) == (point, when):
            revision(0)
            restored = True

    sample = check_request_notice_gates(shared, _step=interrupt)
    assert restored and not sample.consistent
    assert sample.states[0].state == diagnosis
    assert check_request_notice_gates(shared).states == (GateNoticeState('shared', 'open'),)


@pytest.mark.parametrize('route', ('profile', 'dual', 'draft', 'draft-marker'))
def test_local_owner_failure_has_one_attributed_notice(tmp_path, route):
    local, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, shared))

    if route == 'draft-marker':
        with create_shared_draft_auth_reader(local, credential, shared) as reader:
            reader._transport.transport = lambda *_: sent.append(True)
            reader.set_retry_policy(policy)
            (local.root / _SCOPE_MARKER).write_bytes(b'broken')
            with request_scope(allow_retry=False), pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
    elif route == 'draft':
        transport = _DraftAuthSharedTransport(local, shared, lambda *_: sent.append(True))
        # Keep the original database identity available while swapping the path.
        db = local.root / PROFILE_DB
        db.rename(db.with_suffix('.original'))
        db.touch()
        with pytest.raises(ManagedNetworkGateStorageError):
            policy.execute(lambda: transport(None, 1, 1, 1), deadline=1)
    else:
        reader = (local.create_reader(credential, transport=lambda *_: sent.append(True)) if route == 'profile'
                  else create_shared_profile_reader(local, credential, shared.gate,
                                                    transport=lambda *_: sent.append(True)))
        with reader:
            reader.set_retry_policy(policy)
            (local.root / _SCOPE_MARKER).write_bytes(b'broken')
            with pytest.raises(ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)

    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    assert record.get('gate_problem') == 'storage' and record.get('gate_owners') == ('local',)
    assert not sent


@pytest.mark.parametrize('interruption', ('none', 'busy', 'changed', 'publish'))
def test_stopped_owner_set_does_not_shrink_during_observation(header_client, interruption):
    stopped = [GateNoticeState(owner, 'stopped', 401) for owner in AUTHORITIES]
    sample = [GateObservationSnapshot(tuple(stopped))]
    policy = retry_policy([0.0], check_gate=lambda: sample[0])
    reject(policy, ManagedNetworkStopped(401, authority='both'))
    with header_client:
        view = RequestStatusView(policy)
    try:
        view.dismiss()
        revision = view._dismiss_revision
        clean_side = GateObservationSnapshot((GateNoticeState('local', 'open'), stopped[1]))
        sample[0] = clean_side
        policy.recheck_gate_notices()
        view.refresh()
        assert policy.progress_snapshot()[0]['gate_owners'] == AUTHORITIES
        assert view._dismiss_revision == revision and not view.dismiss_button.visible
        if interruption in ('busy', 'changed'):
            sample[0] = GateObservationSnapshot((GateNoticeState('local', interruption), stopped[1]),
                                                 consistent=interruption != 'changed')
            policy.recheck_gate_notices()
        elif interruption == 'publish':
            reject(policy, ManagedNetworkGateBusy())
        sample[0] = clean_side
        policy.recheck_gate_notices()
        if interruption != 'none':
            assert policy.progress_snapshot()[0]['gate_owners'] == AUTHORITIES
            policy.recheck_gate_notices()
        view._dismiss_rendered(SimpleNamespace(args={'revision': str(revision)}))
        assert policy.progress_snapshot()[0]['gate_owners'] == AUTHORITIES
        assert policy.progress_snapshot()[0]['notice_id'] in view._acknowledged
    finally:
        view.close()
