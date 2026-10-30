"""Synthetic callbacks and OS-assigned loopback ports only; no external I/O."""
from contextlib import contextmanager
import http.client
import json
import socket
import threading
import pytest

from pixivvault_nicegui.extension_bridge import (
    ExtensionBridge, BridgeUnavailable, ExtensionDownload, allowed_origin, parse_download,
)

ORIGIN = "chrome-extension://" + "a" * 32


@contextmanager
def bridge_fixture(enqueue=None, work_status=None, author_status=None):
    calls = []
    bridge = ExtensionBridge(
        enqueue=enqueue or (lambda command: calls.append(command) or "job-fixture-1"),
        work_status=work_status or (lambda _: {"downloaded": True, "is_latest": True}),
        author_status=author_status or (lambda _: {"downloaded": 3}), port=0,
    )
    assert not bridge.running and bridge.bound_port is None
    port = bridge.start()
    try:
        assert bridge.start() == port
        yield bridge, port, calls
    finally:
        bridge.close()
    assert not bridge.running and bridge.bound_port is None


def request(port, method, path, payload=None, *, origin=ORIGIN, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    request_headers = {"Content-Type": "application/json"}
    if origin is not None:
        request_headers["Origin"] = origin
    request_headers.update(headers or {})
    body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    try:
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read()), dict(response.getheaders())
    finally:
        conn.close()


@pytest.mark.parametrize("origin", [ORIGIN, "https://pixiv.net", "https://www.pixiv.net", "https://touch.pixiv.net:443"])
def test_original_allowed_extension_and_content_script_origins(origin):
    assert allowed_origin(origin)


@pytest.mark.parametrize("origin", [None, "", "null", "http://www.pixiv.net", "https://pixiv.net.evil.com",
    "https://evilpixiv.net", "https://pixiv.net@evil.com", "https://evil.com@pixiv.net",
    "https://pixiv.net/path", "https://pixiv.net:123", "chrome-extension://bad-id",
    "chrome-extension://" + "a" * 32 + "/path", "https://www.pixiv.net\n"])
def test_other_sites_and_malformed_origins_are_rejected(origin):
    assert not allowed_origin(origin)


def test_work_novel_author_schema_preserves_original_flags():
    assert parse_download({"type": "work", "work_id": "123"}).is_novel is None
    assert parse_download({"type": "work", "work_id": "123", "is_novel": False}) == ExtensionDownload("work", "123")
    assert parse_download({"type": "work", "work_id": "123", "is_novel": True}).is_novel
    assert parse_download({"type": "user", "user_id": "42", "new_only": True}) == ExtensionDownload("author", "42", new_only=True)


@pytest.mark.parametrize("payload", [None, [], {}, {"type": "novel", "work_id": "1"},
    {"type": "work", "work_id": 123}, {"type": "work", "work_id": "../123"},
    {"type": "work", "work_id": "1", "is_novel": 1},
    {"type": "work", "work_id": "1", "url": "https://evil.invalid"},
    {"type": "user", "user_id": "1", "new_only": "true"},
    {"type": "user", "user_id": "1", "is_novel": True},
    {"type": "work", "work_id": "0"}, {"type": "work", "work_id": "9" * 21},
])
def test_payload_has_no_implicit_coercion_url_or_path_commands(payload):
    with pytest.raises(ValueError):
        parse_download(payload)


def test_original_save_status_and_preflight_routes_on_owned_loopback():
    with bridge_fixture() as (bridge, port, calls):
        status, body, headers = request(port, "POST", "/download", {"type": "work", "work_id": "123", "is_novel": True})
        assert (status, body) == (200, {"status": "ok", "jobId": "job-fixture-1"})
        assert calls == [ExtensionDownload("work", "123", is_novel=True)]
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert request(port, "GET", "/api/work/123")[1] == {"downloaded": True, "is_latest": True}
        assert request(port, "GET", "/api/user/42/status")[1] == {"downloaded": 3, "total": 3}
        assert request(port, "GET", "/ping")[1] == {"status": "ok"}
        preflight = request(port, "OPTIONS", "/download", origin="https://www.pixiv.net", headers={
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type",
            "Access-Control-Request-Private-Network": "true",
        })
        assert preflight[0] == 200 and preflight[2]["Access-Control-Allow-Private-Network"] == "true"
        assert len(calls) == 1
    bridge.close()
    with pytest.raises(BridgeUnavailable):
        bridge.start()


def test_rejected_cookie_cross_site_payload_and_unknown_routes_never_enqueue():
    with bridge_fixture() as (_, port, calls):
        payload = {"type": "work", "work_id": "123"}
        for origin in [None, "https://attacker.invalid", "null"]:
            assert request(port, "POST", "/download", payload, origin=origin)[0] == 403
        assert request(port, "POST", "/download", payload, headers={"Cookie": "synthetic=fixture"})[0] == 403
        assert request(port, "POST", "/download", payload, headers={"Host": "evil.invalid"})[0] == 403
        assert request(port, "POST", "/download", payload, headers={"Content-Type": "text/plain"})[0] == 415
        assert request(port, "POST", "/download", raw=b"x" * 4097)[0] == 413
        assert request(port, "POST", "/download", raw=b'{"type":"work","work_id":"1","work_id":"2"}')[0] == 400
        assert request(port, "POST", "/download", raw=b"{")[0] == 400
        for path in ["/api/cookie/sync", "/bookmark", "/jobs/1/stop", "/download?url=anything"]:
            assert request(port, "POST", path, payload)[0] == 404
        assert request(port, "GET", "/api/work/../123")[0] == 404
        assert request(port, "OPTIONS", "/download", headers={"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})[0] == 403
        assert calls == []


def test_backend_failure_is_generic_and_not_retried():
    calls = []
    def fail(command):
        calls.append(command)
        raise RuntimeError("private-cookie-or-path-never-in-response")
    with bridge_fixture(enqueue=fail) as (_, port, _):
        status, body, _ = request(port, "POST", "/download", {"type": "work", "work_id": "1"})
        assert status == 503 and "private" not in json.dumps(body)
        assert len(calls) == 1


def test_callback_status_projection_excludes_unrelated_fields():
    with bridge_fixture(work_status=lambda _: {"downloaded": False, "private_path": "not returned"}) as (_, port, _):
        assert request(port, "GET", "/api/work/1")[1] == {"downloaded": False}
    with bridge_fixture(author_status=lambda _: {"downloaded": True}) as (_, port, _):
        assert request(port, "GET", "/api/user/1/status")[0] == 503


def test_occupied_owned_fixture_port_is_not_reused_or_stopped():
    with bridge_fixture() as (_, port, _):
        other = ExtensionBridge(enqueue=lambda _: "unused", work_status=lambda _: {}, author_status=lambda _: {}, port=port)
        try:
            with pytest.raises(BridgeUnavailable):
                other.start()
            assert not other.running
            assert request(port, "GET", "/ping")[0] == 200
        finally:
            other.close()


def test_close_waits_for_accepted_callback_without_holding_status_lock():
    entered = threading.Event()
    finish = threading.Event()
    results = []
    holder = {}
    def enqueue(command):
        entered.set()
        assert finish.wait(3)
        assert not holder["bridge"].running
        return "job-accepted"
    with bridge_fixture(enqueue=enqueue) as (bridge, port, _):
        holder["bridge"] = bridge
        caller = threading.Thread(target=lambda: results.append(request(port, "POST", "/download", {"type": "work", "work_id": "123"})))
        closer = threading.Thread(target=bridge.close)
        caller.start()
        assert entered.wait(3)
        closer.start()
        # Synchronize on the state transition, without using a fixed sleep.
        for _ in range(100):
            if not bridge.running: break
            threading.Event().wait(0.01)
        assert not bridge.running and closer.is_alive()
        finish.set()
        caller.join(3); closer.join(3)
        assert not caller.is_alive() and not closer.is_alive()
        assert results[0][0] == 200
