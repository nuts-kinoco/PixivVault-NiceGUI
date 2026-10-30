"""GET lifecycle regressions; synthetic cookies and owned TLS loopback only."""
import http.client
import io
import json
import os
import ssl
import threading
import time

import pytest

from pixivvault_nicegui import pixiv_read_client as mod
from test_pixiv_write_client import _cookie
from test_pixiv_write_loopback import Session


def test_get_response_drip_obeys_absolute_deadline(monkeypatch):
    clock = [0.0]
    prefix = b'HTTP/1.1 200 Synthetic\r\nContent-Length: 32\r\n\r\n'
    class Drip(io.RawIOBase):
        def __init__(self):
            super().__init__()
            self.prefix = bytearray(prefix)
            self.body_reads = 0
        def readable(self):
            return True
        def readinto(self, target):
            if self.prefix:
                count = min(len(target), len(self.prefix))
                target[:count] = self.prefix[:count]
                del self.prefix[:count]
                return count
            if self.body_reads == 32:
                return 0
            self.body_reads += 1
            clock[0] += .25
            target[0] = ord('x')
            return 1
    raw = Drip()
    class Socket:
        def makefile(self, *args, **kwargs):
            return io.BufferedReader(raw)
    response = http.client.HTTPResponse(Socket())
    response.begin()
    opens = []
    class Opener:
        def open(self, request, timeout):
            opens.append(request)
            return response
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    request = mod.Request('https://www.pixiv.net/', method='GET')
    with pytest.raises(mod.ReadDeadlineExceeded):
        mod._read_response(Opener(), request, 1.0, 128, 1.0)
    assert raw.body_reads <= 5, 'whole-body read overran the absolute deadline'
    assert raw.closed and opens == [request]


class GetSession(Session):
    """Reuse the proven POST fixture's DNS/socket/TLS containment and cleanup."""
    def __init__(self, tmp_path, monkeypatch, *, phase, status, budget):
        super().__init__(tmp_path, monkeypatch, use_tls=True, phase=phase,
                         status=status, budget=budget)
        self.reader.close()  # close only the unused synthetic injected reader
        # The old default urllib reader also gets the fixture CA. This makes
        # pre-fix RED reach a real drip instead of failing certificate setup.
        monkeypatch.setattr(ssl, '_create_default_https_context',
                            lambda *args, **kwargs: self.context)
        self.reader = mod.LivePixivReadClient(self.cookie)
        self.budget = budget
        self.payload = None
        self.status_code = None
        self.phase = phase

    def start(self):
        def run():
            start = time.perf_counter()
            try:
                request = mod.Request('https://localhost/', method='GET')
                self.payload = self.reader._transport(request, self.budget,
                    mod.HTML_LIMIT, time.monotonic() + self.budget)
            except BaseException as error:
                self.error = type(error).__name__
                self.status_code = getattr(error, 'status_code', None)
            finally:
                self.elapsed = time.perf_counter() - start
                self.done.set()
        self.worker = threading.Thread(target=run, name='pv-owned-get-client')
        self.worker.start()

    def close(self):
        try:
            if self.reader is not None:
                self.reader.close()
        finally:
            super().close()


@pytest.fixture
def get_session(tmp_path, monkeypatch, record_property):
    sessions = []
    def make(**kwargs):
        assert not sessions
        session = GetSession(tmp_path, monkeypatch, **kwargs)
        sessions.append(session)
        record_property('owner_pid', str(os.getpid()))
        record_property('owned_listener_port', str(session.server.port))
        record_property('platform', os.name)
        return session
    yield make
    for session in sessions:
        facts = dict(pid=os.getpid(), port=session.server.port,
            done_before_fixture_cleanup=session.done.is_set(),
            error_type=session.error, status_code=session.status_code,
            elapsed_seconds=session.elapsed, phase=session.phase,
            accepted_connections=session.server.accepted,
            requests=len(session.server.requests), drips=session.server.drips,
            tls_version=session.server.tls_version,
            registered_before_connect=session.registered_before_connect,
            fence_diagnostics=[fence.diagnostics() for fence in session.fences])
        try:
            session.close()
        finally:
            facts.update(client_alive=bool(session.worker and session.worker.is_alive()),
                server_alive=session.server.thread.is_alive(),
                server_workers_alive=sum(worker.is_alive() for worker in session.server.workers),
                listener_closed=session.server.listener.fileno() == -1)
            record_property('lifecycle', json.dumps(facts, sort_keys=True))


@pytest.mark.parametrize('phase', ['headers', 'body'])
@pytest.mark.parametrize('cause', ['deadline', 'close'])
def test_default_get_drip_stops_before_fixture_cleanup(get_session, phase, cause):
    session = get_session(phase=phase, status=200,
                          budget=.75 if cause == 'deadline' else 3.0)
    session.start()
    assert session.server.started.wait(3), 'fixture did not receive its GET'
    if cause == 'close':
        time.sleep(.1)
        session.reader.close()
    assert session.done.wait(2), 'GET remained blocked beyond deadline/close'
    assert session.payload is None
    assert session.error == ('ReadDeadlineExceeded' if cause == 'deadline' else 'ClientClosed')
    assert session.reader._owned_transport.active_count == 0
    assert not session.reader.shutdown_unverified
    assert session.server.accepted == 1 and len(session.server.requests) == 1
    assert session.server.requests[0][0].startswith(b'GET ')
    assert session.registered_before_connect == [True]
    assert session.fences and all(not fence._handles for fence in session.fences)
    assert session.server.handshake.is_set() and session.server.tls_version
    assert session.context.check_hostname and session.context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('status', [200, 404])
def test_default_get_preserves_payload_and_safe_http_error_type(get_session, status):
    session = get_session(phase='complete', status=status, budget=3.0)
    session.start()
    assert session.done.wait(4)
    if status == 200:
        assert session.error is None
        assert json.loads(session.payload) == {'error': False, 'body': {}}
    else:
        assert session.error == 'HTTPStatusRejected' and session.status_code == 404
        assert session.payload is None
    assert session.server.accepted == 1 and len(session.server.requests) == 1
    assert session.server.requests[0][0].startswith(b'GET ')


@pytest.mark.parametrize('client_kind', ['authenticated', 'cdn-only'])
def test_closing_reader_does_not_close_custom_injected_transport(tmp_path, client_kind):
    class Borrowed:
        close_calls = 0
        def __call__(self, *args):
            return b'synthetic'
        def close(self):
            self.close_calls += 1
    transport = Borrowed()
    reader = (mod.LivePixivReadClient(_cookie(tmp_path), transport=transport)
              if client_kind == 'authenticated' else mod.PublicImageReadClient(transport=transport))
    reader.close()
    assert transport.close_calls == 0
    with pytest.raises(mod.ClientClosed):
        reader.get_image_bytes('https://i.pximg.net/img-master/synthetic.jpg')
