"""Keep-alive recovery and ownership tested with real loopback TLS."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from select import select
import socket
import ssl
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from d6_api_tls_fixture import BODY, CDN, write_cookie
from pixivvault_nicegui import owned_https, pixiv_read_client as reads
from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError, ManagedNetworkStopGate, ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.request_policy import RequestRetryPolicy, request_scope
from pixivvault_nicegui.ui.request_status_view import retry_status_text
from stage5_generated_shared_owner import generated_shared_scope
from test_d6_api_https import fresh_context, gate, get, wire
from test_d6_api_r2 import reader
from test_stage5_r1_shared_stop_transport import generated_owners


@contextmanager
def dual_reader(root, monkeypatch, *, policy):
    scope, _, _, cookie = generated_owners(root)
    shared = generated_shared_scope(root)
    admissions = []
    begin = shared.gate._begin_inflight

    def record():
        proof = begin()
        admissions.append(proof)
        return proof

    monkeypatch.setattr(shared.gate, "_begin_inflight", record)
    with create_shared_profile_reader(scope, cookie, shared.gate,
                                      shared_owner_check=shared._check_owner) as client:
        waits = []
        if policy:
            clock = [monotonic()]

            def advance(delay):
                waits.append(delay)
                clock[0] += delay

            client.set_retry_policy(RequestRetryPolicy(
                lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3.0),
                clock=lambda: clock[0], sleep=advance, jitter=lambda *_: 0,
            ))
        yield client, scope.gate, shared.gate, admissions, waits


@pytest.mark.parametrize("policy", [False, True], ids=["default", "configured"])
@pytest.mark.parametrize("next_reply", [
    (200, "identity", BODY), (200, "drop", b""), (404, "identity", b""),
    (500, "identity", b""), (429, "identity", b""),
], ids=["good", "drop", "404", "500", "429"])
def test_reused_eof_preserves_unknown_intent_without_fresh_attempt(tmp_path, wire, monkeypatch, policy, next_reply):
    with dual_reader(tmp_path, monkeypatch, policy=policy) as (client, local, shared, admissions, waits):
        assert get(client) == BODY
        replies = iter([(200, "drop", b""), next_reply])
        wire.on_request = lambda: next(replies)
        with pytest.raises(reads._PooledConnectionLost):
            get(client, seconds=10)
        assert [row[0] for row in wire.requests] == [0, 0]
        assert len(admissions) == 2 and not waits
        assert shared.inflight_path.is_file() and not local.snapshot().blocked
        with pytest.raises(SharedGetOutcomeUncertain):
            get(client)
        assert len(wire.requests) == 2


@pytest.mark.parametrize("warm", [False, True], ids=["fresh", "pooled"])
def test_partial_http_status_is_never_replayed(tmp_path, wire, monkeypatch, warm):
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits):
        if warm:
            assert get(client) == BODY
        wire.reply = 200, "partial-status", b"HTTP/1."
        with pytest.raises(reads.TransportFailed):
            get(client, seconds=10)
        assert len(wire.requests) == len(admissions) == (2 if warm else 1)
        assert not waits and shared.inflight_path.exists()


def confirm_fragment_before_reset(monkeypatch, wire):
    """Ensure a reset cannot discard the response fragment before TLS reads it."""
    recv_into = ssl.SSLSocket.recv_into

    def observe(sock, buffer, *args, **kwargs):
        count = recv_into(sock, buffer, *args, **kwargs)
        if not sock.server_side and b"cut-here" in bytes(memoryview(buffer)[:count]):
            wire.reset_release.set()
        return count

    monkeypatch.setattr(ssl.SSLSocket, "recv_into", observe)


@pytest.mark.parametrize("disposition", ["reset", "drop"], ids=["tcp-rst", "dirty-fin"])
def test_zero_response_on_reused_socket_requires_acknowledgement(tmp_path, wire, monkeypatch, disposition):
    from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope

    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits):
        assert get(client) == BODY
        wire.reply = 200, disposition, b""
        with pytest.raises(reads._PooledConnectionLost):
            get(client)
        assert shared.inflight_path.is_file()
        owner = ManagedUserNetworkStopScope(shared.path.parent)
        owner.recover_uncertain_get(owner.preview_uncertain_get_recovery(), confirmed=True)
        wire.reply = 200, "identity", BODY
        assert get(client) == BODY
        assert [row[0] for row in wire.requests] == [0, 0, 1]
        assert len(admissions) == 3 and not waits and not shared.inflight_path.exists()


_PARTIAL_BODY = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\ncut-here"


@pytest.mark.parametrize(("disposition", "fragment"), [
    ("reset", b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nX-Incomplete: cut-here"),
    ("reset", _PARTIAL_BODY),
    ("partial-status", b"HTTP/1.1 100 Continue\r\n\r\n"),
], ids=["header-rst", "body-rst", "continue-fin"])
def test_response_started_on_pooled_socket_prevents_replay(
        tmp_path, wire, monkeypatch, disposition, fragment):
    confirm_fragment_before_reset(monkeypatch, wire)
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits):
        assert get(client) == BODY
        wire.reply = 200, disposition, fragment
        with pytest.raises(reads.TransportFailed) as failure:
            get(client)
        assert not failure.value.retryable
        if disposition == "reset":
            assert wire.reset_release.is_set()
        assert [row[0] for row in wire.requests] == [0, 0]
        assert len(admissions) == 2 and not waits
        assert shared.inflight_path.exists() is (disposition == "partial-status")


@pytest.mark.parametrize(("kind", "path"), [
    ("image", "/fixture.png"), ("ugoira_zip", "/img-zip-ugoira/fixture.zip"),
], ids=["image", "ugoira"])
def test_partial_cdn_body_is_not_replayed_on_nonpersistent_connection(
        tmp_path, wire, monkeypatch, kind, path):
    confirm_fragment_before_reset(monkeypatch, wire)
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits):
        wire.reply = 200, "reset", _PARTIAL_BODY
        with pytest.raises(reads.TransportFailed) as failure:
            client._get(kind, f"https://{CDN}{path}", deadline=monotonic() + 5)
        assert not failure.value.retryable and wire.reset_release.is_set()
        assert [(row[0], row[1], row[2] is None) for row in wire.requests] == [(0, CDN, True)]
        assert len(admissions) == 1 and not waits and not shared.inflight_path.exists()


@pytest.mark.parametrize("coordinated", [False, True], ids=["profile-gate", "coordinator"])
def test_account_switch_after_pooled_disconnect_prevents_recovery(
        tmp_path, wire, monkeypatch, coordinated):
    with (dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits),
          ExitStack() as stack):
        profile = stack.enter_context(closing(NativeProfileStore(client._transport.profile_root)))
        if coordinated:
            coordinator = stack.enter_context(closing(CookieExpiryCoordinator(profile, lambda: None)))
            coordinator.register(client)
        assert get(client) == BODY
        selection = profile.snapshot()
        replacement = write_cookie(selection.cookie_file.parent, 56)
        wire.reply = 200, "drop", b""
        with pytest.raises(reads._PooledConnectionLost):
            get(client, seconds=10)
        assert shared.inflight_path.is_file()
        profile.update(cookie_file=replacement, expected_revision=selection.revision)
        error = reads.CookieSourceRejected if coordinated else ManagedNetworkGateStorageError
        with pytest.raises(error) as failure:
            get(client, seconds=10)
        assert type(failure.value) is error
        assert [row[0] for row in wire.requests] == [0, 0]
        assert len(admissions) == 2 and not waits and shared.inflight_path.is_file()


@pytest.mark.parametrize("peer_status", [200, 429])
def test_other_thread_cannot_send_after_pooled_disconnect(tmp_path, wire, monkeypatch, peer_status):
    with (dual_reader(tmp_path, monkeypatch, policy=True) as (client, local, shared, admissions, waits),
          ThreadPoolExecutor(max_workers=1) as worker):
        assert get(client) == BODY
        replies = iter([(200, "drop", b""), (peer_status, "identity", BODY)])
        wire.on_request = lambda: next(replies)
        with pytest.raises(reads._PooledConnectionLost):
            get(client, seconds=10)
        with pytest.raises(SharedGetOutcomeUncertain):
            worker.submit(get, client).result(timeout=5)
        assert [row[0] for row in wire.requests] == [0, 0]
        assert len(admissions) == 2 and not waits and shared.inflight_path.is_file()
        assert not local.snapshot().blocked


def test_fresh_zero_response_preserves_intent_without_retry(tmp_path, wire, monkeypatch):
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, _, shared, admissions, waits):
        wire.reply = 200, "drop", b""
        with pytest.raises(reads.TransportFailed):
            get(client, seconds=10)
        assert [row[0] for row in wire.requests] == [0]
        assert len(admissions) == 1 and not waits and shared.inflight_path.is_file()


@pytest.mark.parametrize("policy", [False, True])
def test_explicit_no_retry_scope_blocks_keep_alive_recovery(tmp_path, wire, monkeypatch, policy):
    with dual_reader(tmp_path, monkeypatch, policy=policy) as (client, _, shared, admissions, waits):
        assert get(client) == BODY
        wire.reply = 200, "drop", b""
        with request_scope(allow_retry=False), pytest.raises(reads.TransportFailed):
            get(client)
        assert [row[0] for row in wire.requests] == [0, 0]
        assert len(admissions) == 2 and not waits and shared.inflight_path.is_file()


@pytest.mark.parametrize("block", ["stopped", "uncertain"])
def test_recovery_reenters_gate_after_first_intent_settles(tmp_path, wire, monkeypatch, block):
    with (dual_reader(tmp_path, monkeypatch, policy=True) as (client, local, shared, admissions, waits),
          ThreadPoolExecutor(max_workers=1) as worker):
        assert get(client) == BODY
        entries = []
        raw = client._transport.transport

        def fail_before_send(*args):
            if len(entries) == 1:
                failure = reads._PooledConnectionLost()
                failure.get_outcome_known = True
                raise failure
            return raw(*args)

        client._transport.transport = fail_before_send

        def admit(_):
            entries.append(1)
            if len(entries) == 2:
                assert not shared.inflight_path.exists()
                if block == "stopped":
                    worker.submit(shared.trip, 403).result(timeout=5)
                else:
                    with pytest.raises(RuntimeError, match="fixture uncertainty"):
                        with shared.request():
                            raise RuntimeError("fixture uncertainty")

        client.set_request_admission(admit)
        error = ManagedNetworkStopped if block == "stopped" else SharedGetOutcomeUncertain
        with pytest.raises(error):
            get(client, seconds=10)
        assert len(entries) == 2 and not waits
        assert [row[0] for row in wire.requests] == [0]
        if block == "uncertain":
            assert shared.inflight_path.is_file()
        else:
            assert (local.snapshot().status_code, shared.snapshot().status_code) == (403, 403)
            record, = client._retry_policy.progress_snapshot()
            assert (record["phase"], record["status"]) == ("stopped", 403)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_normal_fatal_status_policy_with_reused_socket(tmp_path, wire, monkeypatch, status):
    with dual_reader(tmp_path, monkeypatch, policy=True) as (client, local, shared, admissions, waits):
        assert get(client) == BODY
        replies = iter([(status, "identity", b""), (200, "identity", BODY)])
        wire.on_request = lambda: next(replies)
        if status == 429:
            assert get(client, seconds=10) == BODY
            assert [row[0] for row in wire.requests] == [0, 0, 1]
            assert sum(waits) == pytest.approx(3, abs=.06)
            assert not shared.inflight_path.exists()
        else:
            with pytest.raises(ManagedNetworkStopped) as failure:
                get(client, seconds=10)
            assert failure.value.status_code == status
            assert len(wire.requests) == 2 and not waits
        assert len(admissions) == len(wire.requests)
        assert shared.snapshot().blocked == local.snapshot().blocked == (status != 429)


def test_expired_socket_is_discarded_at_checkout_if_timer_has_not_run(tmp_path, gate, wire, monkeypatch):
    monkeypatch.setattr(owned_https, "_IDLE_SECONDS", .1)
    monkeypatch.setattr(owned_https.OwnedHTTPSPool, "_arm_expiry", lambda *_: None)
    with reader(tmp_path, gate) as client:
        assert get(client) == BODY
        sleep(.16)
        assert not wire.peers[0].finished.is_set()
        assert get(client) == BODY
        assert [row[0] for row in wire.requests] == [0, 1]
        assert wire.peers[0].finished.wait(2)


def test_idle_tcp_reset_is_discarded_before_send(tmp_path, gate, wire, monkeypatch):
    original = ssl.SSLContext.wrap_socket
    sockets = []

    def collect(context, *args, **kwargs):
        sock = original(context, *args, **kwargs)
        if not sock.server_side:
            sockets.append(sock)
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", collect)
    with reader(tmp_path, gate) as client:
        wire.reply = 200, "reset-after", BODY
        assert get(client) == BODY and wire.reset_ready.wait(2)
        wire.reset_release.set()
        assert select(sockets, [], [], 2)[0]
        wire.reply = 200, "identity", BODY
        assert get(client) == BODY
        assert [row[0] for row in wire.requests] == [0, 1]
        assert sockets[0].fileno() == -1


def test_borrowed_old_context_cannot_evict_new_epoch(tmp_path, gate, wire, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(owned_https, "_trust_clock", lambda: clock[0])
    with monkeypatch.context() as scoped:
        factory = ssl.create_default_context
        handler = owned_https.OwnedHTTPSHandler.__init__
        created = []
        cross_epoch = False

        def context():
            created.append(clock[0])
            return factory()

        def rollover(instance, owner):
            nonlocal cross_epoch
            if cross_epoch:
                cross_epoch = False
                clock[0] = 600.
                assert get(other) == BODY
            handler(instance, owner)

        scoped.setattr(ssl, "create_default_context", context)
        scoped.setattr(owned_https.OwnedHTTPSHandler, "__init__", rollover)
        other_gate = ManagedNetworkStopGate(tmp_path/"other-gate.sqlite")
        with reader(tmp_path, gate) as first, reader(tmp_path, other_gate) as other:
            assert get(first) == BODY
            cross_epoch = True
            assert get(first) == BODY
            assert created == [0., 600.]
            assert [row[0] for row in wire.requests] == [0, 1, 0]


def test_close_revokes_active_socket_while_idle_disposal_is_blocked(tmp_path, wire, monkeypatch):
    from d6_api_tls_fixture import write_cookie

    entered, release, revoked = Event(), Event(), Event()
    original_cleanup = owned_https.HTTPSOwner.cleanup
    original_abort = owned_https.HTTPSOwner.abort

    def cleanup(owner):
        if not entered.is_set():
            entered.set()
            release.wait(4)
        return original_cleanup(owner)

    def abort(owner):
        original_abort(owner)
        revoked.set()

    outcomes = []
    with reads.LivePixivReadClient(write_cookie(tmp_path)) as client:
        # One GET is blocked on connection 0. Another finishes on connection
        # 1 and becomes idle, so the expiry timer can retire it independently.
        wire.reply = 200, "wait", BODY

        def active():
            try:
                get(client, seconds=6)
            except Exception as error:
                outcomes.append(type(error))

        request = Thread(target=active)
        close = Thread(target=client.close)
        request.start()
        try:
            assert wire.entered.wait(2)
            monkeypatch.setattr(owned_https, "_IDLE_SECONDS", .12)
            monkeypatch.setattr(owned_https.HTTPSOwner, "cleanup", cleanup)
            monkeypatch.setattr(owned_https.HTTPSOwner, "abort", abort)
            wire.reply = 200, "identity", BODY
            assert get(client) == BODY and entered.wait(2)
            close.start()
            assert revoked.wait(1)
            with pytest.raises(reads.ClientClosed):
                get(client)
            assert len(wire.requests) == 2
        finally:
            release.set()
            wire.release.set()
            if close.ident is not None:
                close.join(5)
            request.join(7)
        assert outcomes == [reads.ClientClosed] and not request.is_alive() and not close.is_alive()
