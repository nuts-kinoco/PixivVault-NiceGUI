"""Exercise acquisition, observation and cleanup with isolated resources."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from urllib.request import Request

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui import managed_network_stop_startup as startup
from pixivvault_nicegui import owned_https, pixiv_read_client as reads
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.managed_network_stop_shared_transport import (
    create_shared_draft_auth_reader, create_shared_profile_reader,
)
from pixivvault_nicegui.request_notice_gates import GateNoticeState, check_request_notice_gates
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.request_status_view import retry_status_text
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import attach_history, linked_owners
from test_fix_notice_sweep_20261122 import reject, retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def database_close_failure(monkeypatch, path, mode):
    connect = sqlite3.connect

    class FaultyClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise windows_io_error(5)

    def connect_with_fault(source, *args, **kwargs):
        uri = str(source)
        matches = source in (path, gate._gate_database_uri(path)) or uri.startswith(path.as_uri() + '?')
        if matches and ('immutable=1' in uri) == (mode == 'immutable'):
            kwargs['factory'] = FaultyClose
        return connect(source, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_with_fault)


def reader_for(local, shared, credential, *, transport=None):
    return create_shared_profile_reader(local, credential, shared.gate,
                                        shared_owner_check=shared._check_owner, transport=transport)


@pytest.mark.parametrize('authority', ('local', 'shared'))
def test_rw_sqlite_close_failure_stays_pinned_until_rechecked(tmp_path, monkeypatch, authority):
    local, shared, credential = linked_owners(tmp_path)
    target = local if authority == 'local' else shared
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with reader_for(local, shared, credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            database_close_failure(fault, target.gate.path, 'writable')
            with pytest.raises(gate.ManagedNetworkGateStorageError):
                reader.get_image_bytes(IMAGE)
            assert not sent
            for _ in range(3):
                policy.recheck_gate_notices()
                observed = {row.authority: row.state for row in check_request_notice_gates(local).states}
                assert observed[authority] == 'unverified'
                assert any(row.get('gate_owners') == (authority,) for row in policy.progress_snapshot())
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert not policy.has_gate_notices()


@pytest.mark.parametrize('mode', ('writable', 'immutable'))
def test_observable_stop_survives_its_database_close_error(tmp_path, monkeypatch, mode):
    local, _, _ = linked_owners(tmp_path)
    local.gate.trip(401)
    database_close_failure(monkeypatch, local.gate.path, mode)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    reject(policy, gate.ManagedNetworkGateStorageError(authority='local'))
    for _ in range(3):
        policy.recheck_gate_notices()
    records = policy.progress_snapshot()
    assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in records)
    assert any(row.get('gate_problem') == 'storage' for row in records)


@pytest.mark.parametrize('retry_limit', (1, 3))
@pytest.mark.parametrize('route', ('local', 'shared', 'draft'))
def test_default_https_cleanup_records_storage_and_stops_on_first_429(tmp_path, monkeypatch, retry_limit, route):
    local, shared, credential = linked_owners(tmp_path)
    sent, waited, next_operation = [], [], []
    now = [0.]

    def sleep(delay):
        waited.append(delay)
        now[0] += delay

    policy = RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=retry_limit, rate_limit_wait_seconds=3),
        clock=lambda: now[0], sleep=sleep, check_gate=lambda: check_request_notice_gates(local),
    )
    clean = owned_https.HTTPSOwner.cleanup

    def cleanup(owner):
        clean(owner)
        owner.fence.mark_close_unverified(TimeoutError('synthetic cleanup failed'))
        raise TimeoutError('synthetic cleanup failed')

    def respond(*_, observe_status, **__):
        sent.append(True)
        observe_status(429)
        raise reads.HTTPStatusRejected(429)

    monkeypatch.setattr(owned_https.HTTPSOwner, 'cleanup', cleanup)
    monkeypatch.setattr(reads, '_read_response', respond)
    monkeypatch.setattr(reads, 'build_opener', lambda *_: object())
    monkeypatch.setattr(reads, 'OwnedHTTPSHandler', lambda *_: object())

    if route == 'draft':
        reader = create_shared_draft_auth_reader(local, credential, shared)
    elif route == 'shared':
        reader = reader_for(local, shared, credential)
    else:
        reader = local.create_reader(credential)
    with reader:
        reader.set_retry_policy(policy)
        with pytest.raises(reads.HTTPStatusRejected) as stopped:
            reader.get_image_bytes(IMAGE)
        assert stopped.value.status_code == 429
        assert not waited and sent == [True]
        with pytest.raises(reads.HTTPStatusRejected):
            policy.execute(lambda: next_operation.append(True), deadline=1000)
        assert not next_operation
        for _ in range(3):
            policy.recheck_gate_notices()
        records = policy.progress_snapshot()
        cause = 'own_limit' if retry_limit == 1 else 'cleanup_429'
        assert any(row.get('stop_cause') == cause for row in records)
        if retry_limit == 3:
            assert 'HTTP 429' in retry_status_text(records, limit=None)
            assert '\u8a66\u884c\u4e0a\u9650\u306b\u9054\u3057\u305f' not in retry_status_text(records, limit=None)
        owners = ('local',) if route == 'local' else ('shared',)
        assert any(row.get('gate_problem') == 'storage' and row.get('gate_owners') == owners for row in records)


def fault_close_lock(monkeypatch, lock):
    opener = gate._open_existing_stop_lock

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, field):
            return getattr(self.stream, field)

        def close(self):
            self.stream.close()
            raise windows_io_error(5)

    monkeypatch.setattr(gate, '_open_existing_stop_lock',
                        lambda path: Stream(opener(path)) if path == lock else opener(path))


@pytest.mark.parametrize('outer', ('local-close', 'local-unlock', 'history'))
def test_nested_cleanup_is_attributed_to_the_failed_resource(tmp_path, monkeypatch, outer):
    from pixivvault_nicegui import managed_stop_migration as migration

    local, shared, credential = linked_owners(tmp_path)
    old = attach_history(tmp_path, shared) if outer == 'history' else None
    sent = []
    failed_owner, other = ('shared', 'local') if outer == 'history' else ('local', 'shared')
    observed = (GateNoticeState(failed_owner, 'busy'), GateNoticeState(other, 'open'))
    policy = retry_policy([0.0], check_gate=lambda: observed)
    with reader_for(local, shared, credential, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        if outer != 'history':
            shared.gate.inflight_path.write_bytes(b'synthetic unresolved request')
            if outer == 'local-close':
                fault_close_lock(monkeypatch, local.gate.lock_path)
            else:
                change_lock = gate._set_stop_byte_lock
                def fail_local_release(stream, *, lock):
                    change_lock(stream, lock=lock)
                    if not lock and os.fstat(stream.fileno()).st_ino == local.gate._lock_identity[1]:
                        raise windows_io_error(5)
                monkeypatch.setattr(gate, '_set_stop_byte_lock', fail_local_release)
            expected = SharedGetOutcomeUncertain
        else:
            local.gate.lock_path.write_bytes(b'!')
            close = migration._FileLock.close

            def fail_ticket(ticket):
                close(ticket)
                if ticket.path == old.gate.lock_path:
                    raise windows_io_error(5)

            monkeypatch.setattr(migration._FileLock, 'close', fail_ticket)
            expected = gate.ManagedNetworkGateStorageError
        with pytest.raises(expected) as stopped:
            reader.get_image_bytes(IMAGE)
        assert not sent
        assert {failure.authority for failure in stopped.value.gate_cleanup_errors} == {failed_owner}
        for _ in range(3):
            policy.recheck_gate_notices()
        assert any(row.get('gate_problem') == 'storage' and row.get('gate_owners') == (failed_owner,)
                   for row in policy.progress_snapshot())


@pytest.mark.parametrize('cycle', (2, 3))
@pytest.mark.parametrize('outcome', ('stopped', 'uncertain'))
def test_final_confirmation_publishes_readable_stop_on_same_tick(tmp_path, cycle, outcome):
    local, shared, _ = linked_owners(tmp_path)

    def interrupt(phase, authority, boundary):
        if current == cycle and (phase, authority, boundary) == ('confirm', 'shared', 'after'):
            if outcome == 'stopped':
                with closing(sqlite3.connect(shared.gate.path)) as writer:
                    writer.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                    writer.commit()
            else:
                shared.gate.inflight_path.write_bytes(b'synthetic unresolved request')

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    reject(policy, gate.ManagedNetworkGateStorageError(authority='shared'))
    for current in range(1, cycle + 1):
        policy.recheck_gate_notices()
    notices = policy.progress_snapshot()
    if outcome == 'stopped':
        assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in notices)
    else:
        assert any(row.get('gate_problem') == 'uncertain' for row in notices)


def test_final_profile_close_failure_keeps_previously_read_stop(tmp_path, monkeypatch):
    from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
    from pixivvault_nicegui.native_profile import PROFILE_DB

    local, _, _ = linked_owners(tmp_path)
    local.gate.trip(401)
    profile = sqlite_readonly_uri(local.root / PROFILE_DB)
    connect = sqlite3.connect
    reads = []

    class FaultyClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise windows_io_error(5)

    def fail_final_profile_read(path, *args, **kwargs):
        if path == profile:
            reads.append(True)
            if len(reads) == 2:
                kwargs['factory'] = FaultyClose
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', fail_final_profile_read)
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    reject(policy, gate.ManagedNetworkGateStorageError(authority='local'))
    policy.recheck_gate_notices()
    records = policy.progress_snapshot()
    assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in records)
    assert any(row.get('gate_problem') == 'storage' for row in records)


def test_parent_add_file_denial_is_observed_without_creating_intent(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    marker = shared.gate.inflight_path
    create = Path.open
    probe = gate._probe_inflight_parent

    def deny_open(path, mode='r', *args, **kwargs):
        if path == marker and mode == 'xb':
            raise windows_io_error(5)
        return create(path, mode, *args, **kwargs)

    def deny_parent(path):
        if path == marker.parent:
            raise windows_io_error(5)
        return probe(path)

    with reader_for(local, shared, credential, transport=lambda *_: sent.append(True)) as reader:
        monkeypatch.setattr(Path, 'open', deny_open)
        monkeypatch.setattr(gate, '_probe_inflight_parent', deny_parent)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert not sent and not marker.exists()
        assert {row.authority: row.state for row in check_request_notice_gates(local).states}['shared'] == 'unverified'


def test_rw_database_probe_opens_existing_files_without_sql_or_gate_locks(tmp_path, monkeypatch):
    local, shared, _ = linked_owners(tmp_path)
    paths = (local.gate.path, shared.gate.path)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    uris = {gate._gate_database_uri(path) for path in paths}
    opened, closed = [], []
    connect = sqlite3.connect

    class Probe(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    def record(path, *args, **kwargs):
        if path not in uris:
            return connect(path, *args, **kwargs)
        assert kwargs['uri'] and path.endswith('?mode=rw')
        kwargs['factory'] = Probe
        connection = connect(path, *args, **kwargs)
        connection.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        opened.append(path)
        return connection

    def forbid_lock(*_, **__):
        pytest.fail('the observer must not acquire a gate lock')

    monkeypatch.setattr(gate, '_set_stop_byte_lock', forbid_lock)
    monkeypatch.setattr(sqlite3, 'connect', record)
    assert [state.state for state in check_request_notice_gates(local).states] == ['open', 'open']
    assert set(opened) == uris and len(opened) == len(closed)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == before[path] for path in paths)
    assert not shared.gate.inflight_path.exists()


@pytest.mark.parametrize('error_kind', (TimeoutError, OSError, RuntimeError))
def test_failed_idle_cleanup_prevents_checkout_with_owned_storage_notice(monkeypatch, error_kind):
    transport = reads._OwnedReadTransport()
    deadline = time.monotonic() + 10
    stale = owned_https.HTTPSOwner(1.0, deadline, 'www.pixiv.net', 443)
    transport._idle = stale

    def fail_cleanup():
        raise error_kind('synthetic idle disposal failed')

    def forbid_response(*_, **__):
        pytest.fail('a poisoned HTTPS pool must not dispatch')

    monkeypatch.setattr(stale, 'cleanup', fail_cleanup)
    monkeypatch.setattr(reads, '_read_response', forbid_response)
    with gate._report_stop_authority('local'):
        with pytest.raises(gate.ManagedNetworkGateStorageError) as failure:
            transport(Request('https://www.pixiv.net/ajax/user/extra'), 1.0, 4096, deadline)
    assert failure.value.authority == 'local'
    assert failure.value.cleanup_unrecheckable and transport.revocation_unverified
    assert transport.active_count == 0
    transport.close()


@pytest.mark.parametrize('location', ('profile-sidecar', 'startup-anchor'))
def test_metadata_error_cannot_be_treated_as_an_absent_gate_file(tmp_path, monkeypatch, location):
    from pixivvault_nicegui.native_profile import PROFILE_DB

    local, shared, credential = linked_owners(tmp_path)
    if location == 'profile-sidecar':
        affected, authority = local.root / (PROFILE_DB + '-journal'), 'local'
    else:
        affected, authority = shared.root.parent / startup._ANCHOR, 'shared'
    sent = []

    def deny(original):
        def metadata(path, *args, **kwargs):
            if path == affected:
                raise windows_io_error(21)
            return original(path, *args, **kwargs)
        return metadata

    with reader_for(local, shared, credential, transport=lambda *_: sent.append(True)) as reader:
        monkeypatch.setattr(Path, 'stat', deny(Path.stat))
        monkeypatch.setattr(Path, 'lstat', deny(Path.lstat))
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert not sent
        assert {row.authority: row.state for row in check_request_notice_gates(local).states}[authority] == 'unverified'


@pytest.mark.parametrize('resource', ('owner-marker', 'intent'))
def test_shared_file_rejection_preserves_secondary_close_failure(tmp_path, monkeypatch, resource):
    from pixivvault_nicegui import managed_network_stop_shared_scope as owner_files

    local, shared, credential = linked_owners(tmp_path)
    path = (shared.root / owner_files._OWNER_FILE if resource == 'owner-marker'
            else shared.gate.inflight_path)
    open_file = Path.open
    sent = []

    class FailedFile:
        def __init__(self, stream):
            self.stream = stream

        def read(self, *_):
            raise windows_io_error(5)

        def write(self, *_):
            raise windows_io_error(5)

        def close(self):
            self.stream.close()
            raise windows_io_error(32)

    def fail_open(candidate, *args, **kwargs):
        stream = open_file(candidate, *args, **kwargs)
        return FailedFile(stream) if candidate == path else stream

    with reader_for(local, shared, credential, transport=lambda *_: sent.append(True)) as reader:
        monkeypatch.setattr(Path, 'open', fail_open)
        with pytest.raises(gate.ManagedNetworkGateStorageError) as failure:
            reader.get_image_bytes(IMAGE)
        assert not sent
        assert {error.authority for error in failure.value.gate_cleanup_errors} == {'shared'}


def test_setup_ticket_close_preserves_primary_cancellation(tmp_path, monkeypatch):
    opened = []
    primary = startup.StartupStopCancelled()

    def open_owner():
        opened.append(True)
        if len(opened) == 1:
            raise startup.shared.SharedStopOwnerAbsent()
        raise primary

    def close_ticket():
        raise windows_io_error(5)

    monkeypatch.setattr(startup.shared, 'shared_owner_root', lambda: tmp_path / 'isolated-owner')
    monkeypatch.setattr(startup.shared, 'open_existing_windows_user_stop', open_owner)
    monkeypatch.setattr(startup, 'shared_startup_ticket', lambda _: SimpleNamespace(close=close_ticket))
    with pytest.raises(startup.StartupStopCancelled) as cancelled:
        startup.open_or_initialize_windows_user_stop()
    assert cancelled.value is primary
    assert {error.authority for error in primary.gate_cleanup_errors} == {'shared'}


@pytest.mark.parametrize('status', (401, 403))
def test_post_with_committed_stop_and_gate_cleanup_failure_is_indeterminate(tmp_path, monkeypatch, status):
    from pixivvault_nicegui.managed_network_stop_write import write_admission
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient, WriteHTTPResponse, WriteIntent
    from test_ag_z04_20261119 import _home, reader as write_reader, setup

    _, credential, profile, store, coordinator = setup(tmp_path)
    reader, _ = write_reader(credential, coordinator, [])
    reader._transport = lambda *_: _home()
    shared = SimpleNamespace(gate=gate.ManagedNetworkStopGate(tmp_path / 'write-stop.sqlite'),
                             _check_owner=lambda: None)
    posts = []

    def post(*_):
        posts.append(True)
        return WriteHTTPResponse(status, b'{"error":true}')

    writer = PixivWriteClient(reader, transport=post)
    writer._post_admission = lambda: write_admission(reader, None, shared)
    try:
        with monkeypatch.context() as fault:
            fault_close_lock(fault, shared.gate.lock_path)
            result = writer.execute(WriteIntent('synthetic-oauth-failure', '777', '91', 'follow'))
        assert posts == [True] and shared.gate.snapshot().status_code == status
        assert result.status == 'indeterminate' and result.reason == 'transport_unknown'
    finally:
        writer.close()
        reader.close()
        coordinator.close()
        store.close()
        profile.close()
