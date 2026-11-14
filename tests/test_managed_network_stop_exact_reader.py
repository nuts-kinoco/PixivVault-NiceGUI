"""Synthetic-only exact-type live reader with a durable network stop fence.

Extension auto-detection requires type(reader) is LivePixivReadClient.
The existing guarded subclass cannot satisfy that contract. No real network,
personal Cookie, Pixiv account or original data is used here.
"""

import time
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
)
from pixivvault_nicegui.managed_network_stop_reader import create_exact_managed_read_client
from pixivvault_nicegui.pixiv_read_client import (
    ClientClosed,
    CookieSourceRejected,
    HTTPStatusRejected,
    LivePixivReadClient,
)
from pixivvault_nicegui.request_policy import RequestRetryPolicy


_URL = "https://i.pximg.net/generated-only.png"


def _cookie(tmp_path):
    path = tmp_path / "generated-cookie.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("status", (401, 403, 429))
def test_exact_live_type_stops_immediately_without_retry_or_second_get(tmp_path, status):
    gate_path = tmp_path / "generated-gate" / "gate.sqlite"
    gate = ManagedNetworkStopGate(gate_path)
    attempts = []

    def reject(*_args):
        attempts.append("first")
        raise HTTPStatusRejected(status)

    def no_sleep(_seconds):
        pytest.fail("fatal response must not enter the legacy retry wait")

    cookie = _cookie(tmp_path)
    first = create_exact_managed_read_client(cookie, gate, transport=reject)
    second = create_exact_managed_read_client(
        cookie, ManagedNetworkStopGate(gate_path),
        transport=lambda *_args: attempts.append("forbidden"),
    )
    assert type(first) is LivePixivReadClient
    assert type(second) is LivePixivReadClient
    first.set_retry_policy(RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=30.0),
        clock=time.monotonic, sleep=no_sleep,
    ))
    try:
        if status==429:
            from test_display_recovery_batch import assert_product_429
            assert_product_429((first,),gate,attempts,_URL)
            return
        with pytest.raises(ManagedNetworkStopped) as error:
            first._get("image", _URL, deadline=time.monotonic() + 8)
        assert error.value.status_code == status
        with pytest.raises(ManagedNetworkStopped):
            second._get_once("image", _URL, deadline=time.monotonic() + 8)
        assert attempts == ["first"]
        assert gate.snapshot().status_code == status
    finally:
        first.close()
        second.close()


def test_exact_reader_nonfatal_404_keeps_gate_open_and_borrows_transport(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "generated-gate" / "gate.sqlite")

    class Borrowed:
        calls = 0
        closed = False

        def __call__(self, *_args):
            self.calls += 1
            if self.calls == 1:
                raise HTTPStatusRejected(404)
            return b"generated-image"

        def close(self):
            self.closed = True

    transport = Borrowed()
    reader = create_exact_managed_read_client(_cookie(tmp_path), gate, transport=transport)
    assert type(reader) is LivePixivReadClient
    try:
        with pytest.raises(HTTPStatusRejected) as error:
            reader._get_once("image", _URL, deadline=time.monotonic() + 8)
        assert error.value.status_code == 404
        assert reader._get_once("image", _URL, deadline=time.monotonic() + 8) == b"generated-image"
        assert not gate.snapshot().blocked
    finally:
        reader.close()
    assert transport.calls == 2
    assert not transport.closed
    with pytest.raises(ClientClosed):
        reader._get_once("image", _URL, deadline=time.monotonic() + 8)


def test_exact_reader_owns_default_pool_and_closes_without_request(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "generated-gate" / "gate.sqlite")
    reader = create_exact_managed_read_client(_cookie(tmp_path), gate)
    assert type(reader) is LivePixivReadClient
    owner = reader._owned_transport
    assert owner is not None and reader._transport.transport is owner
    reader.close()
    assert owner.revocation_unverified is False
    with pytest.raises(ClientClosed):
        reader._get_once("image", _URL, deadline=time.monotonic() + 8)


def test_exact_reader_invalid_cookie_closes_allocated_owned_pool(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_network_stop_reader as module
    owners = []

    class FakeOwner:
        closed = False

        def __init__(self):
            owners.append(self)

        def __call__(self, *_args):
            pytest.fail("invalid Cookie must never issue an HTTP request")

        def close(self):
            self.closed = True

    monkeypatch.setattr(module, "_OwnedReadTransport", FakeOwner)
    gate = ManagedNetworkStopGate(tmp_path / "generated-gate" / "gate.sqlite")
    with pytest.raises(CookieSourceRejected):
        create_exact_managed_read_client(tmp_path / "missing-cookie", gate)
    assert len(owners) == 1 and owners[0].closed


def test_exact_reader_invalid_gate_does_not_allocate_transport(tmp_path):
    with pytest.raises(TypeError, match="stop gate"):
        create_exact_managed_read_client(tmp_path / "missing-cookie", object())
