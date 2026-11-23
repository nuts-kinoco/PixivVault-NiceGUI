"""Regression coverage for the eleventh acquisition-stop review."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.client
import os
from pathlib import Path
import sqlite3
from threading import Event, local as thread_local
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui import managed_network_stop_startup as startup
from pixivvault_nicegui import managed_stop_migration, owned_https, pixiv_read_client as reads
from pixivvault_nicegui.native_profile import PROFILE_MARKER
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from pixivvault_nicegui.request_policy import RequestRetryPolicy, request_scope
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import attach_history, linked_owners
from test_fix_notice_round9_20261122 import guarded_reader
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def _synthetic_https(monkeypatch, before_audit, after_send, respond):
    class Audit:
        def __getattr__(self, name):
            return getattr(system, name)

        def audit(self, event, *args):
            if event == 'http.client.send':
                before_audit()
            system.audit(event, *args)

    system = http.client.sys

    def connect(connection):
        connection.sock = SimpleNamespace(sendall=lambda _: after_send(), close=lambda: None)

    def read_response(_opener, _request, _timeout, _limit, _deadline, *, owner, **options):
        conn = owned_https.OwnedHTTPSConnection(owner.host, owner=owner)
        conn.request('GET', '/synthetic')
        return respond(options['observe_status'])

    audit = Audit()
    monkeypatch.setattr(http.client, 'sys', audit)
    # The GET-specific send path emits the same stdlib event through sys.
    monkeypatch.setattr(owned_https, 'sys', audit, raising=False)
    monkeypatch.setattr(owned_https.OwnedHTTPSConnection, 'connect', connect)
    monkeypatch.setattr(reads, '_read_response', read_response)


@pytest.mark.parametrize('cleanup_failure', (False, True))
def test_terminal_429_is_latched_before_releasing_both_get_gates(tmp_path, monkeypatch, cleanup_failure):
    local, shared, credential = linked_owners(tmp_path)
    worker = thread_local()
    released, resume_first, peer_at_audit_or_done, resume_peer = (Event() for _ in range(4))
    sent, close_failures, at_release = [], [], []
    limit = 3 if cleanup_failure else 1
    policy = RequestRetryPolicy(lambda: SimpleNamespace(retry_limit=limit, rate_limit_wait_seconds=3),
                                check_gate=lambda: check_request_notice_gates(local))

    def before_audit():
        if worker.peer:
            peer_at_audit_or_done.set()
            assert resume_peer.wait(8)

    def respond(observe_status):
        if worker.peer:
            return b'synthetic image'
        observe_status(429)
        raise reads.HTTPStatusRejected(429)

    _synthetic_https(monkeypatch, before_audit, lambda: sent.append(worker.peer), respond)
    original_request = local.gate.request
    original_open = gate._open_existing_stop_lock

    @contextmanager
    def pause_after_release():
        try:
            with original_request():
                yield
        except reads.HTTPStatusRejected as error:
            if not worker.peer and error.status_code == 429:
                at_release.append(policy._rate_stopped)
                released.set()
                assert resume_first.wait(8)
            raise

    class CloseOnce:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            close_failures.append(True)
            raise windows_io_error(5)

    def open_lock(path):
        stream = original_open(path)
        if (cleanup_failure and path == shared.gate.lock_path and not close_failures
                and getattr(worker, 'peer', None) is False):
            return CloseOnce(stream)
        return stream

    def get(reader, *, peer):
        worker.peer = peer
        return reader.get_image_bytes(IMAGE)

    with guarded_reader(local, shared, credential) as first, guarded_reader(local, shared, credential) as second:
        first.set_retry_policy(policy)
        second.set_retry_policy(policy)
        monkeypatch.setattr(local.gate, 'request', pause_after_release)
        monkeypatch.setattr(gate, '_open_existing_stop_lock', open_lock)
        with ThreadPoolExecutor(max_workers=2) as workers:
            try:
                one = workers.submit(get, first, peer=False)
                assert released.wait(8)
                two = workers.submit(get, second, peer=True)
                two.add_done_callback(lambda _: peer_at_audit_or_done.set())
                assert peer_at_audit_or_done.wait(8)
                resume_first.set()
                with pytest.raises(reads.HTTPStatusRejected) as denied:
                    one.result(timeout=8)
                assert denied.value.status_code == 429
                resume_peer.set()
                with pytest.raises(reads.HTTPStatusRejected) as denied:
                    two.result(timeout=8)
                assert denied.value.status_code == 429
            finally:
                resume_first.set()
                resume_peer.set()

    assert at_release == [True]
    assert sent == [False]
    assert close_failures == ([True] if cleanup_failure else [])
    assert not local.gate.snapshot().blocked and not shared.gate.snapshot().blocked
    assert not shared.gate.inflight_path.exists()


def test_selected_history_close_keeps_a_peer_out_until_429_is_latched(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    old = attach_history(tmp_path, shared)
    worker = thread_local()
    old_unlocked, finish_close = Event(), Event()
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    def respond(observe_status):
        if worker.peer:
            return b'synthetic image'
        observe_status(429)
        raise reads.HTTPStatusRejected(429)

    _synthetic_https(monkeypatch, lambda: None, lambda: sent.append(worker.peer), respond)
    close = managed_stop_migration._FileLock.close

    def close_with_error(ticket):
        close(ticket)
        if ticket.path == old.gate.lock_path and getattr(worker, 'peer', None) is False:
            old_unlocked.set()
            assert finish_close.wait(8)
            raise windows_io_error(5)

    def get(reader, *, peer):
        worker.peer = peer
        return reader.get_image_bytes(IMAGE)

    with guarded_reader(local, shared, credential) as first, guarded_reader(local, shared, credential) as second:
        first.set_retry_policy(policy)
        second.set_retry_policy(policy)
        monkeypatch.setattr(managed_stop_migration._FileLock, 'close', close_with_error)
        with ThreadPoolExecutor(max_workers=2) as workers:
            try:
                one = workers.submit(get, first, peer=False)
                assert old_unlocked.wait(8)
                two = workers.submit(get, second, peer=True)
                with pytest.raises(gate.ManagedNetworkGateBusy):
                    two.result(timeout=8)
            finally:
                finish_close.set()
            with pytest.raises(reads.HTTPStatusRejected) as denied:
                one.result(timeout=8)
            assert denied.value.status_code == 429
        with pytest.raises(reads.HTTPStatusRejected) as denied:
            second.get_image_bytes(IMAGE)
        assert denied.value.status_code == 429
    assert sent == [False] and policy._rate_stopped


def test_sendall_rechecks_429_after_the_stdlib_http_audit_event(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    sent, audit = [], []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    def stop():
        raise reads.HTTPStatusRejected(429)

    def before_audit():
        audit.append(True)
        with request_scope(allow_retry=False), pytest.raises(reads.HTTPStatusRejected):
            policy.execute(stop, deadline=1000)

    _synthetic_https(monkeypatch, before_audit, lambda: sent.append(True), lambda _: b'synthetic image')
    with guarded_reader(local, shared, credential) as reader:
        reader.set_retry_policy(policy)
        with pytest.raises(reads.HTTPStatusRejected) as denied:
            reader.get_image_bytes(IMAGE)
    assert denied.value.status_code == 429 and audit == [True] and not sent


@pytest.mark.parametrize('authority', ('local', 'shared'))
@pytest.mark.parametrize('sql_text', ('PRAGMA synchronous=FULL', 'FROM sqlite_master', 'FROM gate LIMIT',
                                      'physical-semantic'))
def test_physical_rw_sql_failure_needs_explicit_acquisition_confirmation(tmp_path, monkeypatch, authority, sql_text):
    local, shared, credential = linked_owners(tmp_path)
    owner = local if authority == 'local' else shared
    mutable = gate._gate_database_uri(owner.gate.path)
    immutable = gate._gate_database_uri(owner.gate.path, immutable=True)
    connect = sqlite3.connect
    sent, readonly_queries = [], []
    fail = True
    statement = 'FROM gate LIMIT' if sql_text == 'physical-semantic' else sql_text

    class BrokenMutable(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if fail and statement in sql:
                if sql_text == 'physical-semantic':
                    return SimpleNamespace(fetchall=lambda: [])
                raise sqlite3.OperationalError('synthetic physical R/W SQL failure')
            return super().execute(sql, *args, **kwargs)

    class WorkingImmutable(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if statement in sql:
                readonly_queries.append(True)
            return super().execute(sql, *args, **kwargs)

    def injected(path, *args, **kwargs):
        if path == mutable:
            kwargs['factory'] = BrokenMutable
        elif path == immutable:
            kwargs['factory'] = WorkingImmutable
        return connect(path, *args, **kwargs)

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    def respond(*_):
        sent.append(True)
        return b'synthetic image'

    with guarded_reader(local, shared, credential, respond) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(sqlite3, 'connect', injected)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        for _ in range(3):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        assert readonly_queries and not sent
        assert not policy.confirm_gate_notices()
        fail = False
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        assert reader.get_image_bytes(IMAGE) == b'synthetic image'
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        policy.recheck_gate_notices()
        assert policy.has_gate_notices()
        assert policy.confirm_gate_notices()
        assert not policy.has_gate_notices()
    assert sent == [True]


def test_same_physical_gate_can_prove_sql_recovery_after_reader_rebind(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    current_scope = local
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(current_scope))
    sent = []
    uri = gate._gate_database_uri(local.gate.path)
    connect = sqlite3.connect

    class FailedSQL(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == 'PRAGMA synchronous=FULL':
                raise sqlite3.OperationalError('synthetic physical R/W SQL failure')
            return super().execute(sql, *args, **kwargs)

    def injected(path, *args, **kwargs):
        if path == uri:
            kwargs['factory'] = FailedSQL
        return connect(path, *args, **kwargs)

    def respond(*_):
        sent.append(True)
        return b'synthetic image'

    with guarded_reader(local, shared, credential, respond) as old:
        old.set_retry_policy(policy)
        with monkeypatch.context() as fault:
            fault.setattr(sqlite3, 'connect', injected)
            with pytest.raises(gate.ManagedNetworkGateStorageError):
                old.get_image_bytes(IMAGE)

        current_scope = type(local)(local.root)
        current_scope.shared_owner = shared
        for _ in range(2):
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
        # Production reader construction performs a real require_allowed
        # on the rebound local gate. That is a valid recovery witness.
        with guarded_reader(current_scope, shared, credential, respond) as current:
            current.set_retry_policy(policy)
            assert current.get_image_bytes(IMAGE) == b'synthetic image'
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
            policy.recheck_gate_notices()
            assert policy.has_gate_notices()
            assert policy.confirm_gate_notices()
            assert not policy.has_gate_notices()
    assert sent == [True]


@pytest.mark.parametrize('outcome', ('stopped', 'uncertain'))
def test_last_fingerprint_publishes_first_known_shared_stop(tmp_path, monkeypatch, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    deny = True
    changed = installed = False
    original_open = gate._open_existing_stop_lock

    def open_lock(path):
        if deny and path == local.gate.lock_path:
            raise windows_io_error(5)
        return original_open(path)

    def interrupt(phase, authority, boundary):
        nonlocal changed, installed
        if (phase, authority, boundary) == ('last', 'shared', 'before') and not changed:
            path = shared.gate.path
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
            changed = True
        elif (phase, authority, boundary) == ('verify', 'shared', 'after') and not installed:
            installed = True
            if outcome == 'stopped':
                with closing(sqlite3.connect(shared.gate.path)) as conn:
                    conn.execute('UPDATE gate SET blocked=1,status=401,revision=1 WHERE id=1')
                    conn.commit()
            else:
                shared.gate.inflight_path.write_bytes(b'synthetic unfinished GET')

    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local, _step=interrupt))
    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(gate, '_open_existing_stop_lock', open_lock)
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
        deny = False
        policy.recheck_gate_notices()
        rows = policy.progress_snapshot()

    assert changed and installed and not sent
    if outcome == 'stopped':
        assert any(row.get('stop_cause') == 'durable_gate' and row['status'] == 401 for row in rows)
    else:
        assert any(row.get('gate_problem') == 'uncertain' for row in rows)


@pytest.mark.parametrize('read_failure', (False, True))
@pytest.mark.parametrize('initial_selection', (False, True))
def test_old_owner_marker_close_failure_is_reported_with_its_primary(
        tmp_path, monkeypatch, read_failure, initial_selection):
    local, shared, credential = linked_owners(tmp_path)
    if initial_selection:
        old_profile = tmp_path / 'old-profile-fixture'
        old_profile.mkdir()
        old, _, _ = linked_owners(old_profile)
    else:
        old = attach_history(tmp_path, shared)
    marker = old.root / PROFILE_MARKER
    open_original = Path.open
    sent = []

    class FailedMarker:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

        def read(self, limit):
            if read_failure:
                raise windows_io_error(5)
            return self.stream.read(limit)

        def close(self):
            self.stream.close()
            raise windows_io_error(5)

    def open_injected(path, mode='r', *args, **kwargs):
        stream = open_original(path, mode, *args, **kwargs)
        return FailedMarker(stream) if path == marker and mode == 'rb' else stream

    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local)))
        monkeypatch.setattr(Path, 'open', open_injected)
        expected = 'selected STOP ownership unavailable' if initial_selection else 'selected STOP owner changed'
        with pytest.raises(gate.ManagedNetworkGateStorageError, match=expected) as error:
            if initial_selection:
                startup.attach_selected_histories(shared, (('native', old.root),))
            else:
                reader.get_image_bytes(IMAGE)
    assert not sent
    cleanup, = getattr(error.value, 'gate_cleanup_errors', ())
    assert cleanup.authority == 'shared'
    assert str(cleanup) == 'selected STOP owner marker close failed'
