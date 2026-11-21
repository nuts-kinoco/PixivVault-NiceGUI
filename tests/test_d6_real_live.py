"""Offline checks of the D6 ranking measurement transport."""

from contextlib import nullcontext
from pathlib import Path
import socket
import sys
from time import monotonic
from types import SimpleNamespace
from urllib.request import Request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "verify" / "d6real-20261121"))
from live_gate import Ledger, RouteDenied, SocketFence, route
from rank_adapters import RankingWire
from timing import StopRun


BASE = "https://www.pixiv.net"
RANKING = BASE + "/ranking.php?mode=daily&content=illust&format=json&p=1"


@pytest.fixture(autouse=True)
def restore_shared_transport(monkeypatch):
    from pixivvault_nicegui.managed_network_stop_shared_transport import (
        SharedProfileStopTransport, _DraftAuthSharedTransport,
    )
    from pixivvault_nicegui.native_startup import NativeProfileBinding

    monkeypatch.setattr(SharedProfileStopTransport, "__call__", SharedProfileStopTransport.__call__)
    monkeypatch.setattr(_DraftAuthSharedTransport, "__call__", _DraftAuthSharedTransport.__call__)
    monkeypatch.setattr(NativeProfileBinding, "_verify_auth_result", NativeProfileBinding._verify_auth_result)


@pytest.mark.parametrize("url, method, cookie", [
    (RANKING, "POST", True),
    (RANKING + "&p=2", "GET", True),
    (RANKING + "&mode=weekly", "GET", True),
    (RANKING, "GET", False),
    ("https://www.pixiv.net.example.invalid/", "GET", True),
    (BASE + "/ajax/illusts/bookmarks/add", "GET", True),
    (BASE + ":443/", "GET", True),
])
def test_limits_network_to_the_selected_operation(url, method, cookie):
    with pytest.raises(RouteDenied):
        route(url, method, cookie)


def test_latched_status_stops_another_adapter(tmp_path):
    original = Ledger.create(tmp_path)
    nice = Ledger(tmp_path)
    touched = []
    with pytest.raises(StopRun, match="^http_429$"):
        original.attempt("original", "ranking", lambda: (429, False, None))
    with pytest.raises(StopRun, match="^http_429$"):
        nice.attempt("nice", "ranking", lambda: touched.append(True))
    assert touched == []
    state = nice.snapshot()
    assert state["count"] == 1
    assert state["stop"] == "http_429"
    assert state["events"][0]["status"] == 429


def test_records_an_abandoned_dispatch_as_stopped(tmp_path):
    ledger = Ledger.create(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        ledger.attempt("nice", "identity", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(StopRun, match="^transport$"):
        ledger.attempt("original", "identity", lambda: pytest.fail("new dispatch"))
    assert ledger.snapshot()["count"] == 1


def test_product_dns_helper_inherits_only_the_admitted_host(monkeypatch):
    from pixivvault_nicegui.owned_https import HTTPSOwner

    resolved = []
    address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))

    def dns(host, port, *_args, **_kwargs):
        resolved.append((host, port))
        return [address]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(socket.socket, "connect_ex", socket.socket.connect_ex)
    fence = SocketFence()
    fence.install()
    with fence.admitted("www.pixiv.net"):
        owner = HTTPSOwner(2, monotonic() + 2, "www.pixiv.net", 443)
        assert owner.addresses() == [address]
        with pytest.raises(RouteDenied):
            socket.getaddrinfo("example.invalid", 443)
    with pytest.raises(OSError, match="HTTPS address unavailable"):
        owner.addresses()
    assert resolved == [("www.pixiv.net", 443)]


def test_denied_image_does_not_leave_product_intent(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopGate
    from pixivvault_nicegui.pixiv_read_client import DestinationRejected, LivePixivReadClient, _OwnedReadTransport

    monkeypatch.setattr(_OwnedReadTransport, "__call__", _OwnedReadTransport.__call__)
    monkeypatch.setattr(LivePixivReadClient, "get_image_bytes", LivePixivReadClient.get_image_bytes)
    wire = object.__new__(RankingWire)
    wire.image = "https://i.pximg.net/first.jpg"
    wire.install_nice()
    denied_url = "https://i.pximg.net/second.jpg"
    with pytest.raises(DestinationRejected):
        LivePixivReadClient.get_image_bytes(object(), denied_url)
    gate = ManagedUserNetworkStopGate(tmp_path / "gate.sqlite3")
    with pytest.raises(DestinationRejected), gate.request():
        _OwnedReadTransport.__call__(None, Request(denied_url), 1, 1, monotonic() + 1)
    assert gate.snapshot().blocked is False


def test_portable_count_keeps_prior_attempts(tmp_path):
    ledger = Ledger.create(tmp_path, prior_attempts=179)
    ledger.attempt("original", "identity", lambda: (200, False, True))
    with pytest.raises(StopRun, match="^budget$"):
        ledger.attempt("nice", "identity", lambda: pytest.fail("budget escaped"))
    state = ledger.snapshot()
    assert state["count"] == 180
    assert state["prior_attempts"] == 179


def test_dispatch_spacing_uses_end_of_previous_attempt(tmp_path, monkeypatch):
    import live_gate

    now = 400.0
    started = []

    def advance(seconds):
        nonlocal now
        now += seconds

    def reply():
        started.append(now)
        advance(1.4)
        return 200, False, True

    monkeypatch.setattr(live_gate, "perf_counter", lambda: now)
    monkeypatch.setattr(live_gate, "sleep", advance)
    ledger = Ledger.create(tmp_path)
    ledger.attempt("original", "identity", reply)
    ledger.attempt("nice", "identity", reply)
    assert started == pytest.approx([400.0, 402.275])


def test_429_reaches_product_when_measurement_receipt_cannot_be_saved(tmp_path, monkeypatch):
    from pixivvault_nicegui.pixiv_read_client import (
        HTTPStatusRejected, LivePixivReadClient, _OwnedReadTransport,
    )

    def denied(*_args):
        raise HTTPStatusRejected(429)

    monkeypatch.setattr(_OwnedReadTransport, "__call__", denied)
    monkeypatch.setattr(LivePixivReadClient, "get_image_bytes", LivePixivReadClient.get_image_bytes)
    wire = object.__new__(RankingWire)
    wire.side = "nice"
    wire.fence = SimpleNamespace(admitted=lambda _host: nullcontext())
    wire.ledger = Ledger.create(tmp_path)
    save = wire.ledger._save

    def broken_save(state):
        if state["stop"]:
            raise OSError("evidence unavailable")
        save(state)

    monkeypatch.setattr(wire.ledger, "_save", broken_save)
    wire.install_nice()
    request = Request(RANKING, headers={"Cookie": "test-only"})
    with pytest.raises(HTTPStatusRejected) as error:
        _OwnedReadTransport.__call__(None, request, 1, 1, monotonic() + 1)
    assert error.value.status_code == 429
    assert wire.ledger.snapshot()["inflight"]


def test_created_process_is_cleaned_up_when_receipt_save_fails(tmp_path, monkeypatch):
    import rank_measure

    process = SimpleNamespace(pid=12345, poll=lambda: None, wait=lambda timeout: 1)
    probe = SimpleNamespace(bind=lambda _address: None)
    calls = []

    def fail_save(*_args):
        raise OSError("evidence unavailable")

    monkeypatch.setattr(rank_measure.socket, "socket", lambda: nullcontext(probe))
    monkeypatch.setattr(rank_measure.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(rank_measure.subprocess, "run", lambda command, **_kw: calls.append(command))
    monkeypatch.setattr(rank_measure, "save", fail_save)
    monkeypatch.setattr(rank_measure, "environment", lambda *_args: {})
    with pytest.raises(OSError, match="evidence unavailable"):
        with rank_measure.server(tmp_path, "original", {}):
            pytest.fail("untracked server yielded")
    assert calls == [["taskkill.exe", "/F", "/PID", "12345", "/T"]]


def test_unknown_product_get_outcome_stops_peer_adapter(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
    from pixivvault_nicegui.managed_network_stop_shared_transport import SharedProfileStopTransport
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, _OwnedReadTransport

    def unknown(*_args):
        raise SharedGetOutcomeUncertain()

    monkeypatch.setattr(SharedProfileStopTransport, "__call__", unknown)
    monkeypatch.setattr(_OwnedReadTransport, "__call__", _OwnedReadTransport.__call__)
    monkeypatch.setattr(LivePixivReadClient, "get_image_bytes", LivePixivReadClient.get_image_bytes)
    wire = object.__new__(RankingWire)
    wire.ledger = Ledger.create(tmp_path)
    wire.install_nice()
    with pytest.raises(SharedGetOutcomeUncertain):
        SharedProfileStopTransport.__call__(None)
    with pytest.raises(StopRun, match="^product_stop$"):
        Ledger(tmp_path).attempt("original", "ranking", lambda: pytest.fail("peer dispatched"))
    assert wire.ledger.snapshot()["count"] == 0


def test_startup_auth_uncertainty_latches_the_ledger_before_get(tmp_path, monkeypatch):
    from pixivvault_nicegui.native_auth_result import NativeAuthResult
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, _OwnedReadTransport

    monkeypatch.setattr(NativeProfileBinding, "_verify_auth_result",
                        lambda *_args: NativeAuthResult("uncertain"))
    monkeypatch.setattr(_OwnedReadTransport, "__call__", _OwnedReadTransport.__call__)
    monkeypatch.setattr(LivePixivReadClient, "get_image_bytes", LivePixivReadClient.get_image_bytes)
    wire = object.__new__(RankingWire)
    wire.ledger = Ledger.create(tmp_path)
    wire.install_nice()
    assert NativeProfileBinding._verify_auth_result(None).code == "uncertain"
    with pytest.raises(StopRun, match="^product_stop$"):
        Ledger(tmp_path).attempt("original", "ranking", lambda: pytest.fail("peer dispatched"))
    assert wire.ledger.snapshot()["count"] == 0


def test_pre_dispatch_check_runs_after_wait_and_blocks_send(tmp_path, monkeypatch):
    import live_gate

    now = 400.0

    def advance(seconds):
        nonlocal now
        now += seconds

    def gate_stopped():
        assert now == pytest.approx(400.875)
        raise StopRun("product_stop")

    monkeypatch.setattr(live_gate, "perf_counter", lambda: now)
    monkeypatch.setattr(live_gate, "sleep", advance)
    ledger = Ledger.create(tmp_path)
    ledger.attempt("nice", "ranking", lambda: (200, False, True))
    with pytest.raises(StopRun, match="^product_stop$"):
        ledger.attempt("original", "ranking", lambda: pytest.fail("peer dispatched"),
                       before_dispatch=gate_stopped)
    assert ledger.snapshot()["events"][-1]["status"] is None
