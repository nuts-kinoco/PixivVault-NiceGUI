"""Generated-only transport integration checks; no real Pixiv requests."""

from io import BytesIO
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_transport import ManagedNetworkStopTransport
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
from pixivvault_nicegui.request_policy import RequestRetryPolicy


_URL = "https://i.pximg.net/synthetic-managed-page.png"


def _client(gate, transport):
    """Exercise production GET validation without loading a Cookie source."""
    client = LivePixivReadClient.__new__(LivePixivReadClient)
    client._closed = False
    client._owned_transport = None
    client._transport = ManagedNetworkStopTransport(gate, transport)
    return client


def _get(client):
    return client._get_once("image", _URL, deadline=time.monotonic() + 10)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_typed_fatal_status_latched_before_caller_can_swallow(tmp_path, status):
    path = tmp_path / "stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    calls = []

    def reject(*_args):
        calls.append("attempt")
        raise HTTPStatusRejected(status)

    client = _client(gate, reject)
    with pytest.raises(ManagedNetworkStopped) as error:
        _get(client)
    assert error.value.status_code == status

    assert (gate.snapshot().blocked, gate.snapshot().status_code) == (True, status)
    with pytest.raises(ManagedNetworkStopped):
        _get(_client(ManagedNetworkStopGate(path), reject))
    assert calls == ["attempt"]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_raw_urllib_http_error_is_latched_before_client_conversion(tmp_path, status):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    response = BytesIO(b"generated-only")
    calls = []

    def reject(request, *_args):
        calls.append("attempt")
        raise HTTPError(request.full_url, status, "generated", {}, response)

    with pytest.raises(ManagedNetworkStopped) as error:
        _get(_client(gate, reject))
    assert error.value.status_code == status
    assert response.closed
    assert (gate.snapshot().blocked, gate.snapshot().status_code) == (True, status)
    with pytest.raises(ManagedNetworkStopped):
        _get(_client(gate, reject))
    assert calls == ["attempt"]


def test_unrelated_http_404_does_not_poison_following_get(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    calls = []

    def transport(request, *_args):
        calls.append("attempt")
        if len(calls) == 1:
            raise HTTPError(request.full_url, 404, "generated", {}, BytesIO())
        return b"synthetic-result"

    client = _client(gate, transport)
    with pytest.raises(HTTPStatusRejected) as error:
        _get(client)
    assert error.value.status_code == 404
    assert not gate.snapshot().blocked
    assert _get(client) == b"synthetic-result"
    assert calls == ["attempt", "attempt"]


def test_transport_failure_is_not_misclassified_as_auth_failure(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")

    def disconnect(*_args):
        raise ConnectionResetError("synthetic disconnection")

    # The production read client deliberately translates unknown exceptions.
    from pixivvault_nicegui.pixiv_read_client import TransportFailed
    with pytest.raises(TransportFailed):
        _get(_client(gate, disconnect))
    assert not gate.snapshot().blocked


def test_retry_policy_bounds_429_then_stops_in_memory(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    calls, simulated_sleeps = [], []
    virtual_clock = [time.monotonic()]

    def advance_clock(delay):
        simulated_sleeps.append(delay)
        virtual_clock[0] += delay

    def reject(*_args):
        calls.append("generated GET")
        raise HTTPStatusRejected(429)

    client = _client(gate, reject)
    client.set_retry_policy(RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3.0),
        clock=lambda: virtual_clock[0], sleep=advance_clock,
    ))
    with pytest.raises(HTTPStatusRejected) as error:
        client._get("image", _URL, deadline=time.monotonic() + 10)
    assert error.value.status_code == 429
    assert calls == ["generated GET"]*3
    assert sum(simulated_sleeps) == pytest.approx(6)
    assert not gate.snapshot().blocked
    with pytest.raises(HTTPStatusRejected):
        client._get("image", _URL, deadline=time.monotonic() + 10)
    assert len(calls) == 3



def test_missing_durable_state_never_invokes_transport(tmp_path):
    path = tmp_path / "stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    gate.trip(429)
    path.unlink()  # generated test storage only; durable lock stays intact
    called = []
    client = _client(gate, lambda *_args: called.append(True))
    with pytest.raises(ManagedNetworkGateStorageError):
        _get(client)
    assert called == []


def test_get_only_rejects_post_before_transport(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    called = []
    wrapper = ManagedNetworkStopTransport(gate, lambda *_args: called.append(True))
    request = Request(_URL, data=b"synthetic", method="POST")
    with pytest.raises(ValueError, match="GET only"):
        wrapper(request, 1.0, 1024, time.monotonic() + 10)
    assert called == []
    assert not gate.snapshot().blocked


def test_wrapper_requires_valid_gate_and_transport(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    with pytest.raises(TypeError, match="stop gate"):
        ManagedNetworkStopTransport(object(), lambda *_args: b"")
    with pytest.raises(TypeError, match="callable"):
        ManagedNetworkStopTransport(gate, object())
