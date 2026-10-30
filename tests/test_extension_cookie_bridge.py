"""Cookie sync HTTP boundary; generated payloads and owned loopback only."""
from contextlib import contextmanager
import http.client
import json
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.extension_bridge import ExtensionBridge


ORIGIN = "chrome-extension://" + "a" * 32
SECRET = "synthetic-private-cookie-value"
COOKIE = {"domain": ".pixiv.net", "path": "/", "name": "PHPSESSID",
          "value": SECRET, "secure": True, "session": True,
          "httpOnly": True, "sameSite": "no_restriction", "storeId": "0"}
PATH = "/api/cookie/sync"


@contextmanager
def environment(callback=None):
    arguments = dict(enqueue=lambda _: "fixture-job", work_status=lambda _: {"downloaded": False},
                     author_status=lambda _: {"downloaded": 0}, port=0)
    if callback is not None:
        arguments["cookie_sync"] = callback
    bridge = ExtensionBridge(**arguments)
    port = bridge.start()
    try:
        yield bridge, port
    finally:
        bridge.close()
    assert not bridge.running and bridge.bound_port is None


def request(port, *, method="POST", path=PATH, payload=None, raw=None, origin=ORIGIN, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    fields = {"Origin": origin, "Content-Type": "application/json"}
    fields.update(headers or {})
    data = raw if raw is not None else json.dumps(payload or {"cookies": [COOKIE]}).encode()
    if method != "POST":
        data = None
    try:
        connection.request(method, path, body=data, headers=fields)
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body), dict(response.getheaders()), body
    finally:
        connection.close()


def oversized_header_response(port, declared_length):
    """Reject by Content-Length before a massive body is transmitted.

    Windows can abort a client that sends multi-megabyte bytes to a listener
    which has already rejected/closed the request. That transport behavior is
    not a failure of the 413 framing boundary. This explicitly exercises the
    original server's bounded rejection, with no payload or callback.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        connection.putrequest("POST", PATH)
        connection.putheader("Origin", ORIGIN)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(declared_length))
        connection.endheaders()
        response = connection.getresponse()
        body = response.read()
        return response.status, json.loads(body)
    finally:
        connection.close()


def accepted(state="pending"):
    # A future DTO may contain private diagnostics; the HTTP projection must
    # select only the documented safe fields, never serialize arbitrary attrs.
    return SimpleNamespace(state=state, saved=True, cookie_file=SECRET,
                           sha256=SECRET, generation=SECRET, account_id=SECRET)


def test_cookie_receive_requires_the_explicit_callback_capability():
    with environment() as (_, port):
        assert request(port)[0] == 404
        assert request(port, method="OPTIONS", headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})[0] == 404


@pytest.mark.parametrize("state", ["unchanged", "pending", "applied"])
def test_cookie_receipt_projects_storage_state_without_claiming_authentication(state, capsys):
    calls = []
    def receive(cookies):
        calls.append(cookies)
        return accepted(state)
    with environment(receive) as (_, port):
        status, body, headers, raw = request(port)
        assert status == 200
        assert body == {"status": "ok", "state": state, "saved": True,
                        "message": "Cookie update received"}
        assert calls == [[COOKIE]]
        assert SECRET.encode() not in raw
        assert headers["Cache-Control"] == "no-store"
        assert headers["Access-Control-Allow-Origin"] == ORIGIN
        assert request(port, method="OPTIONS", headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
            "Access-Control-Request-Private-Network": "true"})[0] == 200
        assert len(calls) == 1  # OPTIONS never invokes the receiver.
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("raw", [
    b"null", b"[]", b"{}", b'{"cookies":[]}', b'{"cookies":null}',
    b'{"cookies":[null]}', b'{"cookies":[{}],"extra":true}',
    b'{"cookies":[{}],"cookies":[{}]}',
    b'{"cookies":[{"value":"first","value":"second"}]}',
    b'{"cookies":[{"expirationDate":NaN}]}',
    b'{"cookies":[{"expirationDate":Infinity}]}',
    b"{", b"\xff",
])
def test_cookie_envelope_rejects_ambiguous_or_nonfinite_json_before_callback(raw):
    calls = []
    with environment(lambda rows: calls.append(rows) or accepted()) as (_, port):
        status, body, _, response = request(port, raw=raw)
        assert status == 400
        assert body["status"] == "error" and SECRET.encode() not in response
    assert calls == []


def test_cookie_boundaries_do_not_enlarge_the_download_command_envelope():
    calls = []
    with environment(lambda rows: calls.append(rows) or accepted()) as (_, port):
        larger_than_download = dict(COOKIE, value="x" * 5000)
        assert request(port, payload={"cookies": [larger_than_download]})[0] == 200
        assert len(calls) == 1
        assert request(port, path="/download", raw=b" " * 4097)[0] == 413
        assert oversized_header_response(port, 2 * 1024 * 1024 + 1)[0] == 413
        assert request(port, payload={"cookies": [{}] * 1025})[0] == 400
        assert len(calls) == 1


@pytest.mark.parametrize("arguments,expected", [
    ({"origin": "https://attacker.invalid"}, 403),
    ({"headers": {"Cookie": "synthetic=forbidden"}}, 403),
    ({"headers": {"Host": "evil.invalid"}}, 403),
    ({"headers": {"Content-Type": "text/plain"}}, 415),
    ({"headers": {"Content-Encoding": "gzip"}}, 400),
    ({"headers": {"Transfer-Encoding": "chunked"}}, 400),
    ({"path": PATH + "?anything=1"}, 404),
    ({"method": "GET"}, 404),
])
def test_cookie_receiver_keeps_origin_host_method_and_framing_boundaries(arguments, expected):
    calls = []
    with environment(lambda rows: calls.append(rows) or accepted()) as (_, port):
        assert request(port, **arguments)[0] == expected
    assert calls == []


@pytest.mark.parametrize("code,expected", [
    ("cookie_sync_rejected", 400),
    ("cookie_sync_conflict", 409),
    ("cookie_sync_busy", 409),
    ("cookie_sync_commit_unconfirmed", 503),
    (None, 503),
])
def test_receiver_exception_is_fixed_private_and_never_replayed(code, expected, capsys):
    calls = []
    class ReceiverError(RuntimeError):
        pass
    def receive(rows):
        calls.append(rows)  # Models an already accepted side effect.
        error = ReceiverError(SECRET)
        error.code = code
        raise error
    with environment(receive) as (_, port):
        status, body, _, raw = request(port)
        assert status == expected and body["status"] == "error"
        assert len(calls) == 1
        assert SECRET.encode() not in raw
        if code in (None, "cookie_sync_commit_unconfirmed"):
            assert body["state"] == "unconfirmed"
            assert "saved" not in body
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize("receipt", [
    None, SimpleNamespace(state="authenticated", saved=True),
    SimpleNamespace(state="pending", saved=False),
    SimpleNamespace(state="applied", saved=1),
])
def test_unknown_receiver_result_is_not_retried_or_reported_as_rollback(receipt):
    calls = []
    with environment(lambda rows: calls.append(rows) or receipt) as (_, port):
        status, body, _, _ = request(port)
        assert status == 503 and body["state"] == "unconfirmed"
        assert "saved" not in body
    assert len(calls) == 1


def test_bridge_close_drains_one_accepted_receiver_without_holding_status_lock():
    entered, finish = Event(), Event()
    results, calls, holder = [], [], {}
    def receive(rows):
        calls.append(rows)
        entered.set()
        assert finish.wait(4)
        assert not holder["bridge"].running
        return accepted()
    with environment(receive) as (bridge, port):
        holder["bridge"] = bridge
        caller = Thread(target=lambda: results.append(request(port)))
        closer = Thread(target=bridge.close)
        caller.start()
        try:
            assert entered.wait(4)
            closer.start()
            for _ in range(100):
                if not bridge.running:
                    break
                Event().wait(0.01)
            assert not bridge.running and closer.is_alive()
        finally:
            finish.set()
            caller.join(4)
            if closer.ident is not None:
                closer.join(4)
        assert not caller.is_alive() and not closer.is_alive()
        assert len(calls) == 1 and results[0][0] == 200
