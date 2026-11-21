"""Neon follow-up verified on loopback TLS and generated gate/profile owners."""

from contextlib import closing
import gzip
from select import select
import socket
import ssl
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from d6_api_tls_fixture import BODY, CDN, EXTRA, HOST, PACKED, loopback, write_cookie
from pixivvault_nicegui import owned_https, pixiv_read_client as reads
from pixivvault_nicegui.cookie_expiry_refresh import CookieExpiryCoordinator
from pixivvault_nicegui.extension_cookie_store import ExtensionCookieStore
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.managed_network_stop_shared_transport import create_shared_profile_reader
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from stage5_generated_shared_owner import generated_shared_scope
from test_d6_api_https import fresh_context, gate, get, wire, _CONNECT
from test_stage5_r1_shared_stop_transport import generated_owners


def reader(tmp_path, gate):
    from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
    return create_exact_managed_read_client(write_cookie(tmp_path), gate)


def test_idle_expiry_closes_real_socket_before_next_get(tmp_path, gate, wire, monkeypatch):
    monkeypatch.setattr(owned_https, "_IDLE_SECONDS", .15, raising=False)
    with reader(tmp_path, gate) as client:
        assert get(client) == BODY
        assert wire.peers[0].finished.wait(1.5)
        assert len(wire.requests) == 1
        assert get(client) == BODY
        assert [row[0] for row in wire.requests] == [0, 1]


def test_header_names_match_urllib_wire_case(tmp_path, gate, wire):
    with reader(tmp_path, gate) as client:
        assert client._get("image", f"https://{CDN}/fixture.png", deadline=monotonic()+3) == BODY
        assert get(client) == get(client) == BODY
        one_shot, first, second = wire.header_names
        expected = {"User-Agent", "Accept-Language", "Accept-Encoding"}
        assert expected <= set(one_shot)
        assert expected <= set(first) and expected <= set(second)
        assert [row[0] for row in wire.requests] == [0, 1, 1]


def test_pool_discards_idle_headers_after_cookie_capture(tmp_path, gate, wire, monkeypatch):
    responses = []
    original = owned_https.OwnedHTTPSConnection.getresponse

    def observed(conn):
        response = original(conn)
        responses.append(response)
        return response

    monkeypatch.setattr(owned_https.OwnedHTTPSConnection, "getresponse", observed)
    with reader(tmp_path, gate) as client:
        header = ("Set-Cookie", "generated_only=fixture; Path=/")
        wire.response_headers = [header]
        first = client._get("identity", EXTRA, deadline=monotonic()+3, capture_cookies=True)
        assert first.set_cookie == (header[1],)
        assert responses and not (responses[0].headers and "Set-Cookie" in responses[0].headers)
        assert get(client) == BODY and wire.connections == 1


def test_cache_rollover_revalidates_connection_with_current_trust(tmp_path, gate, wire, monkeypatch):
    old_factory = ssl.create_default_context
    calls = []
    clock = [0.0]

    def context():
        calls.append(1)
        return old_factory()

    monkeypatch.setattr(owned_https, "_trust_clock", lambda: clock[0], raising=False)
    with monkeypatch.context() as scoped:
        scoped.setattr(ssl, "create_default_context", context)
        with reader(tmp_path, gate) as client:
            assert get(client) == get(client) == BODY
            assert len(calls) == 1 and wire.connections == 1
            clock[0] = 600.0
            assert get(client) == BODY
            assert len(calls) == 2
            assert [row[0] for row in wire.requests] == [0, 0, 1]
            assert wire.peers[0].finished.wait(2)


def test_idle_disposal_keeps_pool_state_query_available(tmp_path, gate, wire, monkeypatch):
    dispose, release, queried = Event(), Event(), Event()
    original = owned_https.HTTPSOwner.cleanup

    def paused(owner):
        dispose.set()
        release.wait(3)
        return original(owner)

    with reader(tmp_path, gate) as client:
        assert get(client) == BODY
        monkeypatch.setattr(owned_https.HTTPSOwner, "cleanup", paused)
        close = Thread(target=client.close)
        query = Thread(target=lambda: (client.shutdown_unverified, queried.set()))
        close.start()
        try:
            assert dispose.wait(1.5)
            query.start()
            assert queried.wait(.75)
        finally:
            release.set()
            close.join(4)
            if query.ident is not None:
                query.join(4)
        assert not close.is_alive() and not query.is_alive()
        assert wire.peers[0].finished.wait(2)


def test_late_body_read_uses_remaining_deadline_and_gate_drains(tmp_path, gate, wire, monkeypatch):
    original = ssl.SSLSocket.recv_into
    observed = []
    headers_read = False
    errors = []
    wire.reply = 200, "stalled-body", BODY

    def recv(sock, *args, **kwargs):
        nonlocal headers_read
        if not sock.server_side and headers_read:
            observed.append((sock.gettimeout(), deadline-monotonic()))
        count = original(sock, *args, **kwargs)
        if not sock.server_side and count:
            headers_read = True
        return count

    monkeypatch.setattr(ssl.SSLSocket, "recv_into", recv)
    with reader(tmp_path, gate) as client:
        def request():
            try:
                client._get("identity", EXTRA, deadline=deadline)
            except Exception as error:
                errors.append(type(error))

        deadline = monotonic()+1.4
        worker = Thread(target=request)
        worker.start()
        try:
            sleep(.65)
            wire.header_release.set()
            assert wire.headers_sent.wait(2)
            worker.join(3)
            assert observed and observed[0][0] <= observed[0][1] + .075
            assert errors == [reads.ReadDeadlineExceeded]
            assert not gate.snapshot().blocked
        finally:
            wire.release.set()
            worker.join(4)
        assert not worker.is_alive()


def test_two_concurrent_gets_lease_distinct_sockets_and_retain_one(tmp_path, wire):
    with reads.LivePixivReadClient(write_cookie(tmp_path)) as client:
        assert get(client) == BODY
        wire.reply = 200, "wait", BODY
        bodies = []
        worker = Thread(target=lambda: bodies.append(get(client, seconds=5)))
        worker.start()
        try:
            assert wire.entered.wait(2)
            wire.reply = 200, "identity", BODY
            assert get(client) == BODY
            assert [row[0] for row in wire.requests] == [0, 0, 1]
        finally:
            wire.release.set()
            worker.join(5)
        assert bodies == [BODY] and not worker.is_alive()
        assert wire.peers[0].finished.wait(2)
        assert get(client) == BODY and wire.requests[-1][0] == 1


def test_post_send_disconnect_on_reused_socket_is_never_replayed(tmp_path, wire):
    scope, _, _, cookie = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    waits = []
    with create_shared_profile_reader(scope, cookie, shared.gate,
                                      shared_owner_check=shared._check_owner) as client:
        client.set_retry_policy(RequestRetryPolicy(
            lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3.0),
            sleep=lambda delay: (waits.append(delay), pytest.fail("retry after wire dispatch")),
            jitter=lambda *_: 0,
        ))
        assert get(client) == BODY
        wire.reply = 200, "drop", b""
        with pytest.raises(reads.TransportFailed):
            get(client)
        assert not waits and [row[0] for row in wire.requests] == [0, 0]
        assert wire.peers[0].finished.wait(2)
        assert not shared.gate.snapshot().blocked and not scope.gate.snapshot().blocked
        wire.reply = 200, "negotiate", BODY
        assert get(client) == BODY and wire.requests[-1][0] == 1


@pytest.mark.parametrize("block", ["local", "shared", "uncertain", "fatal-header"])
def test_real_dual_gate_refuses_further_pooled_dispatch(tmp_path, wire, block):
    scope, _, _, cookie = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    with create_shared_profile_reader(scope, cookie, shared.gate,
                                      shared_owner_check=shared._check_owner) as client:
        assert get(client) == BODY
        expected = ManagedNetworkStopped
        if block == "local":
            scope.gate.trip(429)
        elif block == "shared":
            shared.gate.trip(429)
        elif block == "uncertain":
            with pytest.raises(RuntimeError, match="fixture interrupted"):
                with shared.gate.request():
                    raise RuntimeError("fixture interrupted")
            expected = SharedGetOutcomeUncertain
        else:
            wire.reply = 429, "fatal-header", b""
            with pytest.raises(ManagedNetworkStopped):
                get(client)
            assert wire.peers[0].finished.wait(2)
        with pytest.raises(expected):
            get(client)
        assert len(wire.requests) == (2 if block == "fatal-header" else 1)
        assert wire.connections == 1


def test_cookie_rotation_then_account_switch_through_real_reader(tmp_path, wire):
    scope, _, _, cookie = generated_owners(tmp_path)
    shared = generated_shared_scope(tmp_path)
    with (closing(NativeProfileStore(scope.root)) as profile,
          closing(ExtensionCookieStore(tmp_path/"generations", profile=profile)) as store,
          closing(CookieExpiryCoordinator(profile, lambda: store)) as coordinator,
          create_shared_profile_reader(scope, cookie, shared.gate) as client):
        coordinator.register(client)
        wire.homepage = b"""<meta id="meta-global-data" content='{"userData":{"id":"55"}}'>"""
        wire.reply = 200, "identity", b'{"error":false,"body":{}}'
        assert client.require_identity()
        old = wire.requests[-1][2]
        wire.response_headers = [("Set-Cookie",
            "PHPSESSID=55_rotatedfixture; Domain=.pixiv.net; Path=/; Secure; Max-Age=172800")]
        client._json("identity", EXTRA, deadline=monotonic()+4)
        assert profile.snapshot().cookie_file != cookie
        wire.response_headers = []
        get(client)
        rotated = wire.requests[-1][2]
        assert rotated is not None and rotated != old and wire.connections == 1
        switched = write_cookie(cookie.parent, 56)
        profile.update(cookie_file=switched, expected_revision=profile.snapshot().revision)
        with pytest.raises(reads.CookieSourceRejected):
            get(client)
        assert len(wire.requests) == 4
        with create_shared_profile_reader(scope, switched, shared.gate) as replacement:
            get(replacement)
            assert wire.requests[-1][0] != wire.requests[-2][0]
            assert wire.requests[-1][2] not in (None, old, rotated)


@pytest.mark.parametrize("signal", ["eof", "unsolicited"])
def test_idle_peer_signal_drops_socket_before_next_send(tmp_path, gate, wire, signal, monkeypatch):
    client_sockets = []
    original = ssl.SSLContext.wrap_socket

    def capture_socket(context, *args, **kwargs):
        sock = original(context, *args, **kwargs)
        if not sock.server_side:
            client_sockets.append(sock)
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", capture_socket)
    with reader(tmp_path, gate) as client:
        assert get(client) == BODY
        peer = wire.peers[0]
        if signal == "eof":
            socket.socket.shutdown(peer.connection, socket.SHUT_RDWR)
        else:
            peer.connection.sendall(b"unsolicited fixture bytes")
        # Observe the real client-side readiness before requesting the next
        # GET so even a slow loopback cannot race the arrival of this signal.
        assert select(client_sockets, [], [], 2)[0]
        assert get(client) == BODY
        assert client_sockets[0].fileno() == -1
        assert [row[0] for row in wire.requests] == [0, 1]
        assert peer.finished.wait(2)


@pytest.mark.parametrize("case", ["wire-over", "chunked-ok", "crc", "home", "duplicate"])
def test_gzip_wire_boundaries(tmp_path, gate, wire, case):
    with reader(tmp_path, gate) as client:
        if case == "chunked-ok":
            wire.reply = 200, "chunked-gzip", PACKED
            assert get(client) == get(client) == BODY and wire.connections == 1
            return
        if case == "wire-over":
            wire.reply = 200, "chunked-gzip", gzip.compress(b"x" * reads.JSON_LIMIT, compresslevel=0)
            expected = reads.ResponseLimitExceeded
        elif case == "crc":
            wire.reply = 200, "gzip", PACKED[:-8] + bytes([PACKED[-8] ^ 1]) + PACKED[-7:]
            expected = reads.ResponseRejected
        else:
            wire.reply = 200, "duplicate-gzip" if case == "duplicate" else "gzip", PACKED
            expected = reads.ResponseRejected
        with pytest.raises(expected):
            if case == "home":
                client._get("home", f"https://{HOST}/", deadline=monotonic()+3)
            else:
                get(client)
        assert wire.peers[0].finished.wait(2)


@pytest.mark.parametrize("status", [302, 404, 500])
def test_non_200_gzip_is_discarded_without_decompression(tmp_path, gate, wire, status, monkeypatch):
    def forbidden(*args):
        pytest.fail("decoded an error response")

    with reader(tmp_path, gate) as client:
        assert get(client) == BODY
        wire.reply = status, "gzip", PACKED
        monkeypatch.setattr(reads.zlib, "decompressobj", forbidden)
        with pytest.raises(reads.HTTPStatusRejected) as error:
            get(client)
        assert error.value.status_code == status
        assert [row[0] for row in wire.requests] == [0, 0]
        assert wire.peers[0].finished.wait(2)


@pytest.mark.parametrize("mismatch", [False, True], ids=["untrusted", "wrong-host"])
def test_verified_tls_rejects_bad_certificates(tmp_path, gate, monkeypatch, mismatch):
    names = ("unrelated.invalid",) if mismatch else (HOST,)
    with loopback(tmp_path, monkeypatch, _CONNECT, names=names, trusted=mismatch) as server:
        with reader(tmp_path, gate) as client:
            for _ in range(2):
                with pytest.raises(reads.TransportFailed):
                    get(client)
        assert not server.requests
