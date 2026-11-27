"""Exercise the real reader, request pacing and gate observation without network."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from itertools import product
from threading import Event, local as thread_local
import sqlite3
import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import managed_network_stop_gate as gate
from pixivvault_nicegui import owned_https, pixiv_read_client as reads
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.request_notice_gates import check_request_notice_gates
from pixivvault_nicegui.request_policy import RequestAdmission, RequestRetryPolicy, request_scope
from test_fix_notice_round6_20261122 import windows_io_error
from test_fix_notice_round7_20261122 import linked_owners
from test_fix_notice_sweep_20261122 import retry_policy
from test_stage5_r1_shared_stop_transport import IMAGE


def guarded_reader(local, shared, credential, transport=None):
    return create_shared_profile_reader(local, credential, shared.gate,
                                        shared_owner_check=shared._check_owner, transport=transport)


@pytest.mark.parametrize(('where', 'cleanup_limit'), (
    ('interval', None), ('interval', 1), ('interval', 3),
    ('gate', None), ('fresh-gate', None), ('retry-wait', None),
))
def test_an_already_started_get_refuses_to_send_after_429_is_latched(tmp_path, monkeypatch, where, cleanup_limit):
    local, shared, credential = linked_owners(tmp_path)
    thread = thread_local()
    first_sent, finish_first, waiting, resume = (Event() for _ in range(4))
    sent, close_failures = [], []
    now = [0.0]
    extra_attempt = where in ('fresh-gate', 'retry-wait')

    def pause():
        waiting.set()
        assert resume.wait(5)

    def sleep(_):
        pause()
        now[0] = 10.0

    policy = RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=cleanup_limit or 3, rate_limit_wait_seconds=3),
        clock=lambda: now[0], sleep=sleep, jitter=lambda *_: 0,
        check_gate=lambda: check_request_notice_gates(local),
    )

    def transport(*_):
        if not thread.peer:
            sent.append('429')
            first_sent.set()
            assert finish_first.wait(5)
            raise reads.HTTPStatusRejected(429)
        sent.append('peer')
        if extra_attempt and sent.count('peer') == 1:
            error = reads._PooledConnectionLost() if where == 'fresh-gate' else reads.TransportFailed()
            error.get_outcome_known = True  # Fixture: failure known to precede dispatch.
            raise error
        return b'synthetic image'

    guard = gate._bounded_thread_lock
    open_lock = gate._open_existing_stop_lock

    class CloseOnce:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            close_failures.append(True)
            raise windows_io_error(5)

    def inject_close(path):
        stream = open_lock(path)
        if path == shared.gate.lock_path and not thread.peer and not close_failures:
            return CloseOnce(stream)
        return stream

    @contextmanager
    def gate_entry():
        if getattr(thread, 'peer', False) and (where == 'gate' or
                                                where == 'fresh-gate' and 'peer' in sent):
            pause()
        with guard():
            yield

    with guarded_reader(local, shared, credential, transport) as reader:
        reader.set_retry_policy(policy)
        if where == 'interval':
            reader.set_request_admission(RequestAdmission(lambda: 0.5, clock=lambda: now[0],
                                                          sleep=sleep, jitter=lambda *_: 0.5))

        def request(peer):
            thread.peer = peer
            with request_scope(allow_retry=peer or cleanup_limit is not None):
                return reader.get_image_bytes(IMAGE)

        with monkeypatch.context() as patch:
            if cleanup_limit is not None:
                patch.setattr(gate, '_open_existing_stop_lock', inject_close)
            if where in ('gate', 'fresh-gate'):
                patch.setattr(gate, '_bounded_thread_lock', gate_entry)
            with ThreadPoolExecutor(max_workers=2) as workers:
                try:
                    if extra_attempt:
                        peer = workers.submit(request, True)
                        assert waiting.wait(5)
                        first = workers.submit(request, False)
                    else:
                        first = workers.submit(request, False)
                        assert first_sent.wait(5)
                        peer = workers.submit(request, True)
                        assert waiting.wait(5)
                    finish_first.set()
                    with pytest.raises(reads.HTTPStatusRejected) as stopped:
                        first.result(timeout=5)
                    assert stopped.value.status_code == 429 and policy._rate_stopped
                    resume.set()
                    with pytest.raises(reads.HTTPStatusRejected) as denied:
                        peer.result(timeout=5)
                    assert denied.value.status_code == 429
                finally:
                    finish_first.set()
                    resume.set()
    assert sent == (['peer', '429'] if extra_attempt else ['429'])
    assert not local.gate.snapshot().blocked and not shared.gate.snapshot().blocked
    assert not shared.gate.inflight_path.exists()
    assert close_failures == ([True] if cleanup_limit is not None else [])
    cause = 'shared_cleanup_429' if cleanup_limit == 3 else 'shared_limit'
    assert any(record.get('stop_cause') == cause for record in policy.progress_snapshot())


def test_owned_https_checks_429_after_connect_before_writing_http_bytes(tmp_path, monkeypatch):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    def rate_limit():
        raise reads.HTTPStatusRejected(429)

    def establish_connection(connection):
        with request_scope(allow_retry=False), pytest.raises(reads.HTTPStatusRejected):
            policy.execute(rate_limit, deadline=1000)
        connection.sock = SimpleNamespace(sendall=lambda data: sent.append(True), close=lambda: None)

    def respond(_opener, _request, _timeout, _limit, _deadline, *, owner, **_):
        connection = owned_https.OwnedHTTPSConnection(owner.host, owner=owner)
        connection.send(b'synthetic HTTP data')
        return b'synthetic image'

    monkeypatch.setattr(owned_https.OwnedHTTPSConnection, 'connect', establish_connection)
    monkeypatch.setattr(reads, '_read_response', respond)
    monkeypatch.setattr(reads, 'build_opener', lambda *_: object())
    monkeypatch.setattr(reads, 'OwnedHTTPSHandler', lambda *_: object())
    with guarded_reader(local, shared, credential) as reader:
        reader.set_retry_policy(policy)
        with pytest.raises(reads.HTTPStatusRejected) as denied:
            reader.get_image_bytes(IMAGE)
        assert denied.value.status_code == 429 and not sent
    assert not local.gate.snapshot().blocked and not shared.gate.snapshot().blocked
    assert not shared.gate.inflight_path.exists()


@pytest.mark.parametrize('outcome', ('persistent', 'uncertain'))
def test_gate_state_is_checked_after_admission_wait(tmp_path, outcome):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))

    def change_while_waiting(_):
        if outcome == 'persistent':
            shared.gate.trip(401)
        else:
            shared.gate.inflight_path.write_bytes(b'synthetic interrupted GET')

    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        reader.set_request_admission(change_while_waiting)
        expected = gate.ManagedNetworkStopped if outcome == 'persistent' else SharedGetOutcomeUncertain
        with pytest.raises(expected):
            reader.get_image_bytes(IMAGE)
    assert not sent


@pytest.mark.parametrize('public_entry', ('image', 'ranking'))
@pytest.mark.parametrize('authority', ('local', 'shared'))
def test_idle_pool_cleanup_fault_is_pinned_from_public_get(tmp_path, monkeypatch, public_entry, authority):
    local, shared, credential = linked_owners(tmp_path)
    sent = []
    policy = retry_policy([0.0], check_gate=lambda: check_request_notice_gates(local))
    reader = (guarded_reader(local, shared, credential) if authority == 'shared'
              else local.create_reader(credential))
    with reader:
        reader.set_retry_policy(policy)
        pool = reader._owned_transport
        stale = owned_https.HTTPSOwner(1.0, time.monotonic() + 10, 'www.pixiv.net', 443)
        pool._idle = stale

        def fail():
            raise OSError('synthetic idle cleanup failure')

        monkeypatch.setattr(stale, 'cleanup', fail)
        monkeypatch.setattr(reads, '_read_response', lambda *_, **__: sent.append(True))
        pool._expire_idle()
        assert pool.revocation_unverified

        with pytest.raises(reads.ClientClosed):
            if public_entry == 'image':
                reader.get_image_bytes(IMAGE)
            else:
                reader.get_ranking()
        assert not sent
        assert policy.has_gate_notices() and policy.should_watch_gate()
        for _ in range(3):
            policy.recheck_gate_notices()
        [notice] = [row for row in policy.progress_snapshot() if row.get('gate_problem') == 'storage']
        assert notice['gate_owners'] == (authority,)


BOUNDARIES = tuple(product(('first', 'read', 'last', 'confirm'), ('local', 'shared'), ('before', 'after')))


@pytest.mark.parametrize('authority', ('local', 'shared'))
@pytest.mark.parametrize('boundary', BOUNDARIES)
def test_sqlite_close_fault_recurs_during_any_observation_phase(tmp_path, monkeypatch, authority, boundary):
    assert_sqlite_recurrence_is_detected(tmp_path, monkeypatch, authority, boundary, diagnostic=False)


@pytest.mark.parametrize('authority', ('local', 'shared'))
@pytest.mark.parametrize('phase, when', tuple(product(('verify', 'verify_fingerprint'), ('before', 'after'))))
def test_sqlite_close_fault_recurs_during_opposite_gate_diagnosis(tmp_path, monkeypatch, authority, phase, when):
    other = 'local' if authority == 'shared' else 'shared'
    assert_sqlite_recurrence_is_detected(tmp_path, monkeypatch, authority, (phase, other, when), diagnostic=True)


def assert_sqlite_recurrence_is_detected(tmp_path, monkeypatch, authority, boundary, *, diagnostic):
    local, shared, credential = linked_owners(tmp_path)
    target, other = (local.gate, shared.gate) if authority == 'local' else (shared.gate, local.gate)
    uri = gate._gate_database_uri(target.path)
    armed, recurrence, sent = False, False, []
    snapshots = []
    connect = sqlite3.connect

    class FaultyClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise windows_io_error(5)

    def connect_with_fault(path, *args, **kwargs):
        if recurrence and path == uri:
            kwargs['factory'] = FaultyClose
        return connect(path, *args, **kwargs)

    def interrupt(*phase):
        nonlocal recurrence
        if armed and phase == boundary:
            recurrence = True

    def observe():
        snapshot = check_request_notice_gates(local, _step=interrupt)
        snapshots.append(snapshot)
        return snapshot

    policy = retry_policy([0.0], check_gate=observe)
    with guarded_reader(local, shared, credential, lambda *_: sent.append(True)) as reader:
        reader.set_retry_policy(policy)
        monkeypatch.setattr(sqlite3, 'connect', connect_with_fault)
        recurrence = True
        with pytest.raises(gate.ManagedNetworkGateStorageError):
            reader.get_image_bytes(IMAGE)
    recurrence = False
    policy.recheck_gate_notices()
    assert policy.has_gate_notices()
    if diagnostic:
        if boundary[0] == 'verify':
            with closing(sqlite3.connect(other.path)) as conn:
                conn.execute('PRAGMA user_version=0')
                conn.commit()
        else:
            other.lock_path.write_bytes(b'!')
    armed = True
    policy.recheck_gate_notices()
    assert recurrence and not sent
    state = next(row for row in snapshots[-1].states if row.authority == authority)
    assert state.state == 'unverified'
    assert any(row.get('gate_owners') == (authority,) for row in policy.progress_snapshot())
