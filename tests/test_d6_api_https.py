"""D6 API regression through the real owned reader, gate and loopback TLS."""

import gzip
import socket
import ssl
from threading import Thread
from time import monotonic

import pytest

from d6_api_tls_fixture import BODY, CDN, EXTRA, HOST, PACKED, loopback, write_cookie
from pixivvault_nicegui import owned_https
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
from pixivvault_nicegui.pixiv_read_client import (
    ClientClosed, JSON_LIMIT, ReadDeadlineExceeded, ResponseLimitExceeded, ResponseRejected,
    TransportFailed,
)


_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def fresh_context():
    owned_https._default_context.cache_clear()
    yield
    owned_https._default_context.cache_clear()


@pytest.fixture
def wire(tmp_path, monkeypatch):
    with loopback(tmp_path, monkeypatch, _CONNECT) as server:
        yield server


@pytest.fixture
def gate(tmp_path):
    return ManagedNetworkStopGate(tmp_path / "gate.sqlite")


def get(client, seconds=3):
    return client._get("identity", EXTRA, deadline=monotonic() + seconds)


def test_context_created_once_with_default_verification(monkeypatch):
    calls = []
    original = ssl.create_default_context

    def create():
        calls.append(1)
        return original()

    monkeypatch.setattr(ssl, "create_default_context", create)
    first = owned_https.OwnedHTTPSHandler(None)._context
    second = owned_https.OwnedHTTPSHandler(None)._context
    assert first is second and calls == [1]
    assert first.check_hostname and first.verify_mode == ssl.CERT_REQUIRED


def test_reused_connection_still_requires_gate_admission(tmp_path, gate, wire):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        assert get(client) == get(client) == BODY
        assert wire.connections == 1
        assert [row[3] for row in wire.requests] == ["gzip", "gzip"]
        gate.trip(429)
        with pytest.raises(ManagedNetworkStopped):
            get(client)
        assert len(wire.requests) == 2


@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.parametrize("disposition", ["identity", "fatal-header"])
def test_fatal_response_on_reused_connection_trips_gate_and_discards(
        tmp_path, gate, wire, status, disposition):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        assert get(client) == BODY
        wire.reply = status, disposition, b""
        with pytest.raises(ManagedNetworkStopped) as error:
            get(client)
        assert error.value.status_code == status
        assert wire.peers[0].finished.wait(2)
        with pytest.raises(ManagedNetworkStopped):
            get(client)
        assert wire.connections == 1 and len(wire.requests) == 2


def test_pool_is_per_reader_and_cookie_free_cdn_never_reuses_api_socket(tmp_path, gate, wire):
    with (create_exact_managed_read_client(write_cookie(tmp_path, 55), gate) as first,
          create_exact_managed_read_client(write_cookie(tmp_path, 56), gate) as second):
        assert get(first) == get(second) == BODY
        assert first._get("image", f"https://{CDN}/fixture.png", deadline=monotonic() + 3) == BODY
        assert get(first) == BODY
        assert [(number, host, cookie is None) for number, host, cookie, _ in wire.requests] == [
            (0, HOST, False), (1, HOST, False), (2, CDN, True), (0, HOST, False),
        ]
        assert wire.requests[0][2] == wire.requests[3][2]
        assert wire.requests[0][2] != wire.requests[1][2]
        first.close()
        assert wire.peers[0].finished.wait(2) and not first.shutdown_unverified
        with pytest.raises(ClientClosed):
            get(first)
        assert get(second) == BODY and wire.connections == 3


@pytest.mark.parametrize(("disposition", "error"), [
    ("wait", ReadDeadlineExceeded), ("drop", TransportFailed), ("short", ResponseRejected),
])
def test_failed_reused_attempt_discards_without_implicit_replay(tmp_path, gate, wire, disposition, error):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        assert get(client) == BODY
        wire.reply = 200, disposition, b"{}"
        try:
            with pytest.raises(error):
                get(client, seconds=.25 if disposition == "wait" else 3)
        finally:
            wire.release.set()
        assert wire.peers[0].finished.wait(2)
        assert len(wire.requests) == 2 and wire.connections == 1
        wire.reply = 200, "negotiate", BODY
        assert get(client) == BODY and wire.connections == 2


@pytest.mark.parametrize(("payload", "error"), [
    (gzip.compress(b"x" * (JSON_LIMIT + 1)), ResponseLimitExceeded),
    (PACKED[:-1], ResponseRejected),
    (PACKED + b"trailing", ResponseRejected),
], ids=["oversize", "truncated", "trailing"])
def test_bad_gzip_discards_connection(tmp_path, gate, wire, payload, error):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        assert get(client) == BODY
        wire.reply = 200, "gzip", payload
        with pytest.raises(error):
            get(client)
        assert wire.peers[0].finished.wait(2)


def test_exact_gzip_limit_and_chunked_boundary(tmp_path, gate, wire):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        wire.reply = 200, "gzip", gzip.compress(b"x" * JSON_LIMIT)
        assert len(get(client)) == JSON_LIMIT
        wire.reply = 200, "chunked", BODY
        assert get(client) == get(client) == BODY
        assert wire.connections == 1


def test_close_revokes_reused_connection_while_waiting_for_headers(tmp_path, gate, wire):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        assert get(client) == BODY
        owner = client._owned_transport._idle
        idle = owner.connections[0].sock
        wire.reply = 200, "wait", b"{}"
        result = []

        def fetch():
            try:
                get(client)
            except Exception as error:
                result.append(type(error))

        worker = Thread(target=fetch)
        worker.start()
        try:
            assert wire.entered.wait(2)
            client.close()
            assert any(row["operation"] == "shutdown-connected-duplicate"
                       for row in owner.fence.diagnostics())
            # On Windows a read already waiting on the original TLS socket
            # can take until its absolute deadline to leave after shutdown.
            worker.join(4)
            assert not worker.is_alive() and result == [ClientClosed], owner.fence.diagnostics()
            assert not client.shutdown_unverified and idle.fileno() == -1
            assert len(wire.requests) == 2 and wire.connections == 1
        finally:
            wire.release.set()
            worker.join(4)


def test_connection_close_response_is_accepted_and_not_parked(tmp_path, gate, wire):
    with create_exact_managed_read_client(write_cookie(tmp_path), gate) as client:
        wire.reply = 200, "close", BODY
        assert get(client) == BODY
        assert wire.peers[0].finished.wait(2)
        assert get(client) == BODY and wire.connections == 2
