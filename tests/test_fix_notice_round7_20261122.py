"""Acquisition and notice parity, including failures during cleanup."""
from contextlib import closing
from itertools import product
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui import managed_network_stop_shared_scope as shared_gate
from pixivvault_nicegui import managed_network_stop_startup as startup
from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
from pixivvault_nicegui.managed_network_stop_profile_scope import _SCOPE_MARKER
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.native_profile import NativeProfileStore, PROFILE_DB, PROFILE_MARKER
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, TransportFailed, _PooledConnectionLost
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from stage5_generated_shared_owner import generated_shared_scope
from test_fix_notice_round6_20261122 import advancing_policy, windows_io_error
from test_fix_notice_sweep_20261122 import reject, retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE, generated_owners


def linked_owners(tmp_path):
    local, _, _, credential = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    local.shared_owner = shared
    return local, shared, credential


@pytest.mark.parametrize('fault', (
    'profile-marker', 'profile-stop-marker', 'profile-id', 'profile-sidecar',
    'profile-open', 'saved-selection', 'selected-lstat', 'save-parent-lstat',
    'shared-marker', 'shared-provenance', 'shared-db-id', 'shared-lock-id',
    'local-db-id', 'local-lock-id', 'lock-open-5', 'lock-open-32', 'sentinel',
    'db-open-32', 'db-schema', 'db-row', 'db-sidecar', 'inflight',
))
def test_acquisition_and_observation_reject_the_same_isolated_fault(tmp_path, monkeypatch, fault):
    local, shared, credential = linked_owners(tmp_path)
    profile_db = local.root / PROFILE_DB
    db = local.gate.path
    lock = local.gate.lock_path
    parent = tmp_path / 'generated-save'
    parent.mkdir()
    with closing(NativeProfileStore(local.root)) as store:
        store.update(save_parent=parent, expected_revision=store.snapshot().revision)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    assert all(s.state == 'open' for s in check_request_notice_gates(local).states)

    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner,
                                      transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        markers = {'profile-marker': local.root / PROFILE_MARKER,
                   'profile-stop-marker': local.root / _SCOPE_MARKER,
                   'shared-marker': shared.root / shared_gate._OWNER_FILE}
        if fault == 'shared-provenance':
            def fail_provenance(scope):
                if scope is shared:
                    raise windows_io_error(5)
            monkeypatch.setattr(startup, 'check_startup_provenance', fail_provenance)
        elif fault in markers:
            markers[fault].write_bytes(b'!')
        elif fault in ('profile-sidecar', 'db-sidecar'):
            source = profile_db if fault.startswith('profile') else db
            source.with_name(source.name + '-wal').write_bytes(b'incomplete WAL')
            if fault == 'db-sidecar':
                # A WAL can otherwise be ignored by SQLite on a rollback-mode
                # database. Test the actual read failure from exclusive access.
                original = sqlite3.connect
                def connect(path, *args, **kwargs):
                    if path == gate._gate_database_uri(db):
                        raise sqlite3.OperationalError('database is locked')
                    return original(path, *args, **kwargs)
                monkeypatch.setattr(sqlite3, 'connect', connect)
        elif fault in ('profile-id', 'shared-db-id', 'shared-lock-id', 'local-db-id', 'local-lock-id'):
            path = {'profile-id': profile_db, 'shared-db-id': shared.gate.path,
                    'shared-lock-id': shared.gate.lock_path, 'local-db-id': db,
                    'local-lock-id': lock}[fault]
            data = path.read_bytes()
            path.rename(path.with_name('detached-test-file'))
            path.write_bytes(data)
        elif fault in ('selected-lstat', 'save-parent-lstat'):
            path = credential if fault == 'selected-lstat' else parent
            lstat = Path.lstat
            def denied(candidate, *args, **kwargs):
                if candidate == path:
                    raise windows_io_error(5)
                return lstat(candidate, *args, **kwargs)
            monkeypatch.setattr(Path, 'lstat', denied)
        elif fault == 'saved-selection':
            with closing(NativeProfileStore(local.root)) as store:
                store.update(cookie_file=None, expected_revision=store.snapshot().revision)
        elif fault in ('profile-open', 'db-open-32'):
            connect = sqlite3.connect
            rejected = sqlite_readonly_uri(profile_db) if fault == 'profile-open' else gate._gate_database_uri(db)
            def denied(path, *args, **kwargs):
                if path == rejected:
                    raise sqlite3.OperationalError('synthetic SQLite open rejection')
                return connect(path, *args, **kwargs)
            monkeypatch.setattr(sqlite3, 'connect', denied)
            if fault == 'db-open-32':
                # The readonly observer uses the Win32 R/W access probe
                # instead of asking SQLite to perform a recovery-capable open.
                opener = gate._open_existing_stop_lock
                def lock_or_db(path):
                    if path == db:
                        raise windows_io_error(32)
                    return opener(path)
                monkeypatch.setattr(gate, '_open_existing_stop_lock', lock_or_db)
        elif fault.startswith('lock-open'):
            opener = gate._open_existing_stop_lock
            def deny_lock(path):
                if path == lock:
                    raise windows_io_error(int(fault.rsplit('-', 1)[1]))
                return opener(path)
            monkeypatch.setattr(gate, '_open_existing_stop_lock', deny_lock)
        elif fault == 'sentinel':
            lock.write_bytes(b'!')
        elif fault in ('db-schema', 'db-row'):
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("PRAGMA user_version=0" if fault == 'db-schema'
                             else "UPDATE gate SET revision=7")
                conn.commit()
        else:
            shared.gate.inflight_path.write_bytes(b'prior GET intent')

        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert not sent
        affected = 1 if fault.startswith('shared-') or fault == 'inflight' else 0
        for _ in range(3):
            observation = check_request_notice_gates(local)
            assert observation.states[affected].state != 'open'
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()


def attach_history(tmp_path, shared):
    previous = tmp_path / 'previous'
    previous.mkdir()
    historical, _, _, _ = generated_owners(previous)
    startup.attach_selected_histories(shared, (('native', historical.root),))
    return historical


@pytest.mark.parametrize('fault', ('owner', 'db-identity', 'lock-sentinel', 'sidecar', 'row'))
def test_selected_legacy_history_is_checked_before_sending(tmp_path, fault):
    local, shared, credential = linked_owners(tmp_path)
    old = attach_history(tmp_path, shared)
    database = old.gate.path
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner,
                                      transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        if fault == 'owner':
            (old.root / _SCOPE_MARKER).write_bytes(b'!')
        elif fault == 'db-identity':
            database.rename(database.with_name('detached-test-history'))
            database.write_bytes(database.with_name('detached-test-history').read_bytes())
        elif fault == 'lock-sentinel':
            old.gate.lock_path.write_bytes(b'!')
        elif fault == 'sidecar':
            database.with_name(database.name + '-journal').write_bytes(b'synthetic pending journal')
        else:
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('UPDATE gate SET revision=7')
                conn.commit()
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        assert not sent
        assert check_request_notice_gates(local).states[-1].state == 'unverified'
        for _ in range(3):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()


def fail_lock_cleanup(monkeypatch, path, boundary):
    if boundary == 'unlock':
        unlock = gate._set_stop_byte_lock
        def fail(stream, *, lock):
            unlock(stream, lock=lock)
            if not lock:
                raise windows_io_error(5)
        monkeypatch.setattr(gate, '_set_stop_byte_lock', fail)
        return
    opener = gate._open_existing_stop_lock

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            raise windows_io_error(5)

    monkeypatch.setattr(gate, '_open_existing_stop_lock',
                        lambda candidate: Stream(opener(candidate)) if candidate == path else opener(candidate))


def fail_database_close(monkeypatch, path):
    connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def close(self):
            super().close()
            raise sqlite3.OperationalError('synthetic SQLite close refusal')

    def connect_with_fault(candidate, *args, **kwargs):
        source = gate._gate_database_uri(path) if isinstance(path, Path) else path
        if candidate == source:
            kwargs['factory'] = Connection
        return connect(candidate, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', connect_with_fault)


@pytest.mark.parametrize('boundary', ('lock-close', 'unlock'))
@pytest.mark.parametrize('outcome', ('uncertain', '429', '401', '500', 'success'))
def test_known_outcome_survives_gate_cleanup_and_never_retries(tmp_path, monkeypatch, boundary, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    now = [0.]
    policy = RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=1 if outcome == '429' else 3, rate_limit_wait_seconds=3),
        clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay),
    )

    def send(*_):
        sent.append(True)
        if outcome in ('429', '401', '500'):
            raise HTTPStatusRejected(int(outcome))
        return b'generated image'

    expected = {'uncertain': shared_gate.SharedGetOutcomeUncertain, '429': HTTPStatusRejected,
                '401': gate.ManagedNetworkStopped, '500': HTTPStatusRejected,
                'success': gate.ManagedNetworkGateStorageError}
    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner, transport=send) as reader:
        reader.set_retry_policy(policy)
        if outcome == 'uncertain':
            shared.gate.inflight_path.write_bytes(b'previous unknown outcome')
        with monkeypatch.context() as fault:
            fail_lock_cleanup(fault, shared.gate.lock_path, boundary)
            with pytest.raises(expected[outcome]) as captured:
                reader.get_image_bytes(IMAGE)
            if outcome != 'success':
                assert captured.value.__notes__
                assert any(row.get('gate_problem') == 'storage' for row in policy.progress_snapshot())
        if outcome == 'uncertain':
            assert not sent
            assert any(row.get('gate_problem') == 'uncertain' for row in policy.progress_snapshot())
        else:
            assert len(sent) == 1
        if outcome in ('429', '401', 'uncertain'):
            with pytest.raises(expected[outcome]):
                reader.get_image_bytes(IMAGE)
            assert len(sent) == (0 if outcome == 'uncertain' else 1)
        if outcome == '429':
            assert any(row.get('stop_cause') == 'own_limit' for row in policy.progress_snapshot())


def test_thread_release_preserves_an_already_read_stop(tmp_path, monkeypatch):
    local, _, _, _ = generated_owners(tmp_path)
    local.gate.trip(401)
    original = gate._THREAD_LOCK

    class FailingRelease:
        def acquire(self, **kwargs):
            return original.acquire(**kwargs)

        def release(self):
            original.release()
            raise RuntimeError('synthetic release failure')

    monkeypatch.setattr(gate, '_THREAD_LOCK', FailingRelease())
    with pytest.raises(gate.ManagedNetworkStopped) as denied:
        local.require_allowed()
    assert denied.value.status_code == 401 and denied.value.__notes__


def test_sqlite_close_preserves_an_already_read_stop(tmp_path, monkeypatch):
    local, _, _ = linked_owners(tmp_path)
    local.gate.trip(403)
    fail_database_close(monkeypatch, local.gate.path)
    with pytest.raises(gate.ManagedNetworkStopped) as denied:
        local.require_allowed()
    assert denied.value.status_code == 403
    assert denied.value.__notes__


def test_selected_history_database_close_keeps_its_stop_and_mirrors_it(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    old = attach_history(tmp_path, shared)
    old.gate.trip(401)
    sent = []
    policy = advancing_policy()
    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner,
                                      transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        fail_database_close(monkeypatch, old.gate.path.as_uri() + '?mode=ro&immutable=1')
        with pytest.raises(gate.ManagedNetworkStopped) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.status_code == 401 and denied.value.__notes__
        assert any(row.get('gate_problem') == 'storage' for row in policy.progress_snapshot())
    assert shared.gate.snapshot().status_code == 401 and not sent


def test_job_admission_keeps_a_refusal_on_database_close_failure(tmp_path, monkeypatch):
    local, _, _, _ = generated_owners(tmp_path)
    local.gate.trip(429)
    fail_database_close(monkeypatch, local.gate.path)
    assert local.admit_job(SimpleNamespace(kind='pixiv-image')) is False


@pytest.mark.parametrize('mirror_busy', (False, True))
def test_reader_admission_keeps_a_refusal_on_database_close_failure(tmp_path, monkeypatch, mirror_busy):
    local, shared, credential = linked_owners(tmp_path)
    local.gate.trip(403)
    fail_database_close(monkeypatch, local.gate.path)
    if mirror_busy:
        def busy(_):
            raise gate.ManagedNetworkGateBusy('synthetic shared STOP contention')
        monkeypatch.setattr(shared.gate, 'trip', busy)
    sent = []
    with pytest.raises(gate.ManagedNetworkStopped) as denied:
        create_shared_profile_reader(local, credential, shared.gate,
                                     shared_owner_check=shared._check_owner,
                                     transport=lambda *_: sent.append(True))
    assert denied.value.status_code == 403 and denied.value.__notes__
    assert shared.gate.snapshot().status_code == (None if mirror_busy else 403) and not sent


@pytest.mark.parametrize('boundary', ('reader', 'history'))
def test_outer_close_failure_prevents_retrying_a_known_stop_transfer(tmp_path, monkeypatch, boundary):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    reader = None

    def create_reader():
        return create_shared_profile_reader(local, credential, shared.gate,
                                            shared_owner_check=shared._check_owner,
                                            transport=lambda *_: sent.append(True))

    def busy(_):
        raise gate.ManagedNetworkGateBusy('synthetic shared STOP contention')

    def unexpected_retry(_):
        pytest.fail('a transfer with an outer teardown error must not be retried')

    monkeypatch.setattr(startup, 'sleep', unexpected_retry)
    monkeypatch.setattr(shared.gate, 'trip', busy)
    if boundary == 'reader':
        from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
        local.gate.trip(403)
        close_reader = LivePixivReadClient.close

        def fail_reader_close(reader):
            close_reader(reader)
            raise windows_io_error(5)

        monkeypatch.setattr(LivePixivReadClient, 'close', fail_reader_close)
        operation = create_reader
    else:
        from pixivvault_nicegui import managed_stop_migration as migration
        old = attach_history(tmp_path, shared)
        old.gate.trip(403)
        close_ticket = migration._FileLock.close

        def fail_history_close(ticket):
            close_ticket(ticket)
            if ticket.path == old.gate.lock_path:
                raise windows_io_error(5)

        monkeypatch.setattr(migration._FileLock, 'close', fail_history_close)
        reader = create_reader()
        operation = lambda: reader.get_image_bytes(IMAGE)
    try:
        with pytest.raises(gate.ManagedNetworkStopped) as denied:
            startup.retry_startup_lock(operation, deadline=startup.startup_lock_deadline())
        assert denied.value.status_code == 403 and denied.value.__notes__
        assert not sent
    finally:
        if reader is not None:
            reader.close()


def test_close_after_commit_keeps_both_fatal_stop_records(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    policy = advancing_policy()
    sent = []

    def send(*_):
        sent.append(True)
        fail_database_close(monkeypatch, shared.gate.path)
        raise HTTPStatusRejected(401)

    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner, transport=send) as reader:
        reader.set_retry_policy(policy)
        with pytest.raises(gate.ManagedNetworkStopped) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.status_code == 401 and denied.value.__notes__
        assert any(row.get('gate_problem') == 'storage' for row in policy.progress_snapshot())
    monkeypatch.undo()
    assert shared.gate.snapshot().status_code == local.gate.snapshot().status_code == 401
    assert len(sent) == 1


@pytest.mark.parametrize('source', ('history-lock', 'http-response', 'stop-transfer'))
def test_outer_cleanup_does_not_replace_a_committed_stop(tmp_path, monkeypatch, source):
    local, shared, credential = linked_owners(tmp_path)
    old = attach_history(tmp_path, shared)
    policy = advancing_policy()
    sent = []

    class FailingResponse:
        def close(self):
            raise windows_io_error(5)

    def send(*_):
        sent.append(True)
        if source == 'http-response':
            raise HTTPError(IMAGE, 401, 'synthetic status', None, FailingResponse())
        raise HTTPStatusRejected(401)

    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner, transport=send) as reader:
        reader.set_retry_policy(policy)
        if source == 'history-lock':
            from pixivvault_nicegui import managed_stop_migration as migration
            close = migration._FileLock.close
            def fail_ticket(ticket):
                close(ticket)
                if ticket.path == old.gate.lock_path:
                    raise windows_io_error(5)
            monkeypatch.setattr(migration._FileLock, 'close', fail_ticket)
        elif source == 'stop-transfer':
            local.gate.trip(401)
            def fail_trip(*_):
                raise gate.ManagedNetworkGateStorageError('synthetic mirror write refusal')
            monkeypatch.setattr(shared.gate, '_trip_locked', fail_trip)
        with pytest.raises(gate.ManagedNetworkStopped) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.status_code == 401 and denied.value.__notes__
        assert any(row.get('gate_problem') == 'storage' for row in policy.progress_snapshot())
        assert len(sent) == (0 if source == 'stop-transfer' else 1)


@pytest.mark.parametrize('outcome', ('429', 'network'))
def test_failed_intent_completion_keeps_primary_and_refuses_retry(tmp_path, monkeypatch, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    now = [0.]
    policy = RequestRetryPolicy(lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3),
                                clock=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay))

    def send(*_):
        sent.append(True)
        if outcome == '429':
            raise HTTPStatusRejected(429)
        raise TransportFailed()

    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner, transport=send) as reader:
        reader.set_retry_policy(policy)
        unlink = Path.unlink
        def fail(candidate, *args, **kwargs):
            if candidate == shared.gate.inflight_path:
                raise windows_io_error(5)
            return unlink(candidate, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', fail)
        with pytest.raises(HTTPStatusRejected if outcome == '429' else TransportFailed) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.__notes__
        assert len(sent) == 1 and shared.gate.inflight_path.exists()
        assert any(row.get('gate_problem') == 'storage' for row in policy.progress_snapshot())


def test_reader_construction_preserves_failure_even_if_disposal_fails(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_profile_scope as profile_scope
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient

    local, _, credential = linked_owners(tmp_path)
    sent = []
    primary = gate.ManagedNetworkGateStorageError('synthetic reader binding refusal')
    original_close = LivePixivReadClient.close

    def reject_binding(*_):
        raise primary

    def close_reader(reader):
        original_close(reader)
        raise windows_io_error(5)

    monkeypatch.setattr(profile_scope, '_ProfileBoundTransport', reject_binding)
    monkeypatch.setattr(LivePixivReadClient, 'close', close_reader)
    with pytest.raises(gate.ManagedNetworkGateStorageError) as denied:
        local.create_reader(credential, transport=lambda *_: sent.append(True))
    assert denied.value is primary and denied.value.__notes__
    assert not sent


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle conversion path')
@pytest.mark.parametrize('boundary', ('handle', 'descriptor'))
def test_failed_lock_handle_conversion_preserves_secondary_disposal_error(tmp_path, monkeypatch, boundary):
    local, _, _, _ = generated_owners(tmp_path)
    conversion = windows_io_error(5)
    conversion_calls = []
    monkeypatch.setattr(gate, '_create_file', lambda *_: 123)

    def reject_conversion(*_):
        conversion_calls.append(boundary)
        raise conversion

    if boundary == 'handle':
        def reject_close(_):
            gate.ctypes.set_last_error(5)
            return False
        monkeypatch.setattr(gate.msvcrt, 'open_osfhandle', reject_conversion)
        monkeypatch.setattr(gate, '_close_handle', reject_close)
    else:
        def reject_close(_):
            raise windows_io_error(32)
        monkeypatch.setattr(gate.msvcrt, 'open_osfhandle', lambda *_: 123)
        monkeypatch.setattr(os, 'fdopen', reject_conversion)
        monkeypatch.setattr(os, 'close', reject_close)
    with pytest.raises(gate.ManagedNetworkGateStorageError) as denied:
        local.require_allowed()
    assert conversion_calls == [boundary]
    assert denied.value.__context__ is conversion
    assert denied.value.__notes__


def test_old_reader_refusal_remains_bound_to_its_unreadable_selection(tmp_path, monkeypatch):
    local, _, previous = linked_owners(tmp_path)
    replacement = previous.with_name('generated-replacement.txt')
    replacement.write_bytes(previous.read_bytes())
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    sent = []
    with local.create_reader(previous, transport=lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        with closing(NativeProfileStore(local.root)) as store:
            store.update(cookie_file=replacement, expected_revision=store.snapshot().revision)
        lstat = Path.lstat

        def deny_previous(candidate, *args, **kwargs):
            if candidate == previous:
                raise windows_io_error(5)
            return lstat(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, 'lstat', deny_previous)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        for _ in range(3):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        assert not sent


def test_single_get_never_reopens_a_pool_after_gate_cleanup_failure(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    sent = []

    def send(*_):
        sent.append(True)
        if len(sent) == 1:
            raise _PooledConnectionLost()
        return b'generated image'

    with create_shared_profile_reader(local, credential, shared.gate,
                                      shared_owner_check=shared._check_owner, transport=send) as reader:
        fail_lock_cleanup(monkeypatch, shared.gate.lock_path, 'lock-close')
        with pytest.raises(_PooledConnectionLost) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.__notes__
        assert len(sent) == 1


def test_final_reference_failure_reinstates_fixed_notice_on_that_poll(tmp_path, monkeypatch):
    local, _, credential = linked_owners(tmp_path)
    invalid = False
    lstat = Path.lstat

    def deny(candidate, *args, **kwargs):
        if invalid and candidate == credential:
            raise windows_io_error(5)
        return lstat(candidate, *args, **kwargs)

    def interrupt(phase, authority, where):
        nonlocal invalid
        if reappear and (phase, authority, where) == ('read', 'local', 'after'):
            invalid = True

    monkeypatch.setattr(Path, 'lstat', deny)
    reappear = False
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    with local.create_reader(credential, transport=lambda *_: pytest.fail('unexpected send')) as reader:
        reader.set_retry_policy(policy)
        reject(policy, gate.ManagedNetworkGateStorageError(authority='local'))
        policy.recheck_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()
        reappear = True
        policy.recheck_gate_notices()
    assert invalid and policy.has_gate_notices()


@pytest.mark.parametrize('cycle', (1, 2, 3))
@pytest.mark.parametrize('affected', ('local', 'shared'))
@pytest.mark.parametrize('boundary', tuple(product(('first', 'read', 'last', 'confirm'),
                                                  ('local', 'shared'), ('before', 'after'))))
def test_recurrence_at_each_observation_hook_on_either_owner(tmp_path, cycle, affected, boundary):
    local, shared, _ = linked_owners(tmp_path)
    lock = (local if affected == 'local' else shared).gate.lock_path
    fired = False

    def interrupt(*phase):
        nonlocal fired
        if current == cycle and phase == boundary and not fired:
            lock.write_bytes(b'!')
            fired = True

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    reject(policy, gate.ManagedNetworkGateStorageError(authority=affected))
    for current in range(1, cycle + 1):
        policy.recheck_gate_notices()
    assert fired
    assert any(row.get('gate_problem') == 'storage' and row.get('gate_owners') == (affected,)
               for row in policy.progress_snapshot())


@pytest.mark.parametrize('cycle', (1, 2, 3))
@pytest.mark.parametrize('anchor', ('local', 'shared'))
@pytest.mark.parametrize('phase, boundary', tuple(product(('verify', 'verify_fingerprint'), ('before', 'after'))))
def test_other_owner_recurrence_during_diagnostic_recheck(tmp_path, cycle, anchor, phase, boundary):
    local, shared, _ = linked_owners(tmp_path)
    if anchor == 'local':
        invalid, affected, owner = local.gate, shared.gate, 'shared'
    else:
        invalid, affected, owner = shared.gate, local.gate, 'local'
    if phase == 'verify_fingerprint':
        invalid.lock_path.write_bytes(b'!')
    else:
        with closing(sqlite3.connect(invalid.path)) as conn:
            conn.execute('PRAGMA user_version=0')
            conn.commit()
    injected = False

    def interrupt(*step):
        nonlocal injected
        if not injected and current == cycle and step == (phase, anchor, boundary):
            affected.lock_path.write_bytes(b'!')
            injected = True

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    reject(policy, gate.ManagedNetworkGateStorageError(authority=owner))
    for current in range(1, cycle + 1):
        policy.recheck_gate_notices()
    assert injected
    assert any(row.get('gate_problem') == 'storage' and row.get('gate_owners') == (owner,)
               for row in policy.progress_snapshot())
