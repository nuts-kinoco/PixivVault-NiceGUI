"""Generated Cookie/GET only; never contact Pixiv or original storage."""

import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_reader import ManagedLivePixivReadClient
from pixivvault_nicegui.pixiv_read_client import (
    ClientClosed,
    CookieSourceRejected,
    HTTPStatusRejected,
    LivePixivReadClient,
)
from pixivvault_nicegui.request_policy import RequestRetryPolicy


_IMAGE_URL = "https://i.pximg.net/generated-only.png"


def _cookie(tmp_path):
    source = tmp_path / "generated-cookie.txt"
    expires = int(time.time()) + 86400
    source.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{expires}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return source


def _get(reader):
    return reader._get_once("image", _IMAGE_URL, deadline=time.monotonic() + 8)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fatal_response_on_one_reader_fences_other_reader(tmp_path, status):
    gate_path = tmp_path / "generated-root" / "stop.sqlite"
    gate = ManagedNetworkStopGate(gate_path)
    cookie = _cookie(tmp_path)
    calls = []

    def rejecting(*_args):
        calls.append("first")
        raise HTTPStatusRejected(status)

    def succeeding(*_args):
        calls.append("second")
        return b"generated-image"

    first = ManagedLivePixivReadClient(cookie, gate, transport=rejecting)
    second = ManagedLivePixivReadClient(cookie, ManagedNetworkStopGate(gate_path), transport=succeeding)
    assert isinstance(first, LivePixivReadClient)
    try:
        with pytest.raises(ManagedNetworkStopped) as error:
            _get(first)
        assert error.value.status_code == status
        with pytest.raises(ManagedNetworkStopped) as error:
            _get(second)
        assert error.value.status_code == status
        assert calls == ["first"]
        assert ManagedNetworkStopGate(gate_path).snapshot().status_code == status
    finally:
        first.close()
        second.close()


def test_nonfatal_response_does_not_fence_other_reader(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    cookie = _cookie(tmp_path)
    calls = []

    def first(*_args):
        calls.append("first")
        raise HTTPStatusRejected(404)

    def second(*_args):
        calls.append("second")
        return b"generated"

    a = ManagedLivePixivReadClient(cookie, gate, transport=first)
    b = ManagedLivePixivReadClient(cookie, gate, transport=second)
    try:
        with pytest.raises(HTTPStatusRejected):
            _get(a)
        assert _get(b) == b"generated"
        assert calls == ["first", "second"]
        assert not gate.snapshot().blocked
    finally:
        a.close()
        b.close()


def test_state_loss_after_construction_fails_closed(tmp_path):
    path = tmp_path / "stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    calls = []
    client = ManagedLivePixivReadClient(
        _cookie(tmp_path), gate, transport=lambda *_args: calls.append(1)
    )
    try:
        path.unlink()  # Generated test-only state; lock identity remains.
        with pytest.raises(ManagedNetworkGateStorageError):
            _get(client)
        assert calls == []
    finally:
        client.close()


def test_injected_transport_is_borrowed_and_reader_closes(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")

    class Borrowed:
        closed = False

        def __call__(self, *_args):
            return b"generated"

        def close(self):
            self.closed = True

    transport = Borrowed()
    client = ManagedLivePixivReadClient(_cookie(tmp_path), gate, transport=transport)
    assert client._owned_transport is None
    assert _get(client) == b"generated"
    client.close()
    assert not transport.closed
    with pytest.raises(ClientClosed):
        _get(client)


def test_default_transport_keeps_original_close_ownership(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    client = ManagedLivePixivReadClient(_cookie(tmp_path), gate)
    owner = client._owned_transport
    assert owner is not None
    assert client._transport.transport is owner
    client.close()  # Never issue a request; this is a local ownership test.
    assert owner.revocation_unverified is False
    with pytest.raises(ClientClosed):
        _get(client)


def test_invalid_cookie_closes_newly_allocated_owner(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_reader as module

    owners = []

    class TrackedOwner:
        closed = False

        def __init__(self):
            owners.append(self)

        def __call__(self, *_args):
            pytest.fail("must not attempt HTTP")

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "_OwnedReadTransport", TrackedOwner)
    with pytest.raises(CookieSourceRejected):
        ManagedLivePixivReadClient(tmp_path / "missing-cookie", ManagedNetworkStopGate(tmp_path / "stop.sqlite"))
    assert len(owners) == 1 and owners[0].closed


def test_invalid_gate_does_not_open_cookie_or_transport(tmp_path):
    with pytest.raises(TypeError, match="stop gate"):
        ManagedLivePixivReadClient(tmp_path / "missing-cookie", object())


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fatal_stop_bypasses_retry_sleep_and_second_get(tmp_path, status):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    calls = []

    def reject(*_args):
        calls.append("attempt")
        raise HTTPStatusRejected(status)

    def forbidden_sleep(_seconds):
        pytest.fail("fatal managed status must not trigger retry wait")

    reader = ManagedLivePixivReadClient(_cookie(tmp_path), gate, transport=reject)
    reader.set_retry_policy(RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=5, rate_limit_wait_seconds=30.0),
        clock=time.monotonic, sleep=forbidden_sleep,
    ))
    try:
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429((reader,),gate,calls,_IMAGE_URL)
            return
        with pytest.raises(ManagedNetworkStopped) as error:
            reader._get("image", _IMAGE_URL, deadline=time.monotonic() + 15)
        assert error.value.status_code == status
        assert calls == ["attempt"]
        assert gate.snapshot().status_code == status
    finally:
        reader.close()


def test_nonfatal_retry_policy_remains_operational(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "stop.sqlite")
    calls, sleeps = [], []
    clock = [time.monotonic()]

    def advance(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def transient(*_args):
        calls.append("attempt")
        if len(calls) == 1:
            raise HTTPStatusRejected(503)
        return b"generated-success"

    reader = ManagedLivePixivReadClient(_cookie(tmp_path), gate, transport=transient)
    reader.set_retry_policy(RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3.0),
        clock=lambda: clock[0], sleep=advance, jitter=lambda *_args: 0.0,
    ))
    try:
        assert reader._get("image", _IMAGE_URL, deadline=time.monotonic() + 15) == b"generated-success"
        assert calls == ["attempt", "attempt"]
        # The request-scope checkpoint deliberately splits retries into
        # interruptible 0.1s pieces; preserve that behavior for nonfatal 5xx.
        assert sum(sleeps) == pytest.approx(1.0)
        assert all(0 < seconds <= 0.1 for seconds in sleeps)
        assert not gate.snapshot().blocked
    finally:
        reader.close()
