"""Actual owned loopback sockets for POST deadlines/close; no public service."""
import json
import os
import socket
import ssl
import threading
import time
from contextlib import contextmanager
from urllib.request import Request

import pytest

from pixivvault_nicegui import pixiv_write_client as mod
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from test_pixiv_write_client import FakeRead, _cookie, intent
from vault_tls_loopback_fixture import LoopbackTLS, CA


class DripLoopback(LoopbackTLS):
    def __init__(self, *, use_tls, status, phase):
        self.use_tls, self.phase = use_tls, phase
        self.started = threading.Event()
        self.drips = 0
        super().__init__(status=status)

    def _handle(self, connection):
        channel = connection
        try:
            connection.settimeout(5)
            if self.use_tls:
                channel = self.context.wrap_socket(connection, server_side=True)
                with self._lock:
                    self.connections.append(channel)
                    self.tls_version = channel.version()
                self.handshake.set()
            data = b''
            while b'\r\n\r\n' not in data and len(data) < 65536:
                chunk = channel.recv(4096)
                if not chunk:
                    return
                data += chunk
            if b'\r\n\r\n' not in data:
                raise ValueError('fixture header limit')
            header, body = data.split(b'\r\n\r\n', 1)
            fields = header.split(b'\r\n')
            length = 0
            for field in fields[1:]:
                if field.lower().startswith(b'content-length:'):
                    length = int(field.split(b':', 1)[1].strip())
            if not 0 <= length <= 131072:
                raise ValueError('fixture request limit')
            while len(body) < length:
                chunk = channel.recv(min(4096, length - len(body)))
                if not chunk:
                    return
                body += chunk
            with self._lock:
                # No Cookie/header/body content retained in evidence.
                self.requests.append((fields[0], len(body)))
            status_line = f'HTTP/1.1 {self.status} Synthetic\r\n'.encode()
            if self.phase == 'complete':
                payload = json.dumps({'error': self.status != 200, 'body': {}}).encode()
                channel.sendall(status_line + b'Content-Type: application/json\r\n'
                    + f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode()
                    + payload)
                self.started.set()
                return
            if self.phase == 'headers':
                channel.sendall(status_line + b'X-Drip: x')
            else:
                channel.sendall(status_line + b'Content-Type: application/json\r\n'
                    + b'Content-Length: 2048\r\nConnection: close\r\n\r\nx')
            self.drips += 1
            self.started.set()
            for unused in range(2047):
                if self.stop.wait(.05):
                    return
                channel.sendall(b'x')
                self.drips += 1
        except (OSError, ValueError, TimeoutError) as error:
            with self._lock:
                self.errors.append(type(error).__name__)
        finally:
            channel.close()


class PlainTLSShim:
    """Test-only raw HTTP path; production code still requires HTTPS URLs."""
    def __init__(self, sock):
        self.sock = sock
    def __getattr__(self, name):
        return getattr(self.sock, name)
    def do_handshake(self):
        pass


class PlainContext:
    check_hostname = False
    verify_mode = ssl.CERT_NONE
    def wrap_socket(self, sock, server_hostname, do_handshake_on_connect=True):
        assert server_hostname == 'localhost'
        return PlainTLSShim(sock)


class Session:
    def __init__(self, tmp_path, monkeypatch, *, use_tls, status, phase, budget):
        self.server = DripLoopback(use_tls=use_tls, status=status, phase=phase)
        self.reader = self.client = self.worker = None
        self.done = threading.Event()
        self.result = self.error = None
        self.elapsed = None
        self.mode = 'verified-tls' if use_tls else 'raw-http-via-test-shim'
        self.connects, self.registered_before_connect = [], []
        self.registered, self.fences = set(), set()
        try:
            self.cookie = _cookie(tmp_path)
            self.cookie_before = self.cookie.read_bytes()
            self.reader = LivePixivReadClient(self.cookie, transport=FakeRead())
            self.client = mod.PixivWriteClient(self.reader)
            self.context = ssl.create_default_context(cafile=str(CA)) if use_tls else PlainContext()
            if use_tls:
                assert self.context.check_hostname and self.context.verify_mode == ssl.CERT_REQUIRED
            real_socket = socket.socket
            owner = self
            class RestrictedSocket(real_socket):
                def connect(self, address):
                    if not (isinstance(address, tuple) and address[0] == '127.0.0.1'
                            and address[1] == owner.server.port):
                        raise AssertionError('fixture forbids non-loopback connect')
                    owner.connects.append(address)
                    owner.registered_before_connect.append(id(self) in owner.registered)
                    return super().connect(address)
            def addresses(host, port, *args, **kwargs):
                if host != 'localhost' or port != 443:
                    raise AssertionError('fixture forbids external DNS')
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '',
                         ('127.0.0.1', owner.server.port))]
            original_register = mod.SocketFence.register
            @contextmanager
            def registered(fence, sock, epoch):
                owner.fences.add(fence)
                with original_register(fence, sock, epoch):
                    owner.registered.add(id(sock))
                    try:
                        yield
                    finally:
                        owner.registered.discard(id(sock))
            monkeypatch.setattr(socket, 'getaddrinfo', addresses)
            monkeypatch.setattr(socket, 'socket', RestrictedSocket)
            monkeypatch.setattr(ssl, 'create_default_context', lambda: self.context)
            monkeypatch.setattr(mod.SocketFence, 'register', registered)
            monkeypatch.setattr(mod, 'API_ORIGIN', 'https://localhost')
            monkeypatch.setattr(mod, 'MAX_MUTATION_SECONDS', budget)
            self.client._csrf_token = lambda account: 'a' * 32
            self.client._request = lambda action, csrf, bookmark: Request(
                'https://localhost/bookmark_add.php',
                data=b'mode=add&type=user&user_id=91&format=json', method='POST',
                headers={'Content-Type': 'application/x-www-form-urlencoded'})
        except BaseException:
            self.close()
            raise

    def start(self):
        def run():
            start = time.perf_counter()
            try:
                self.result = self.client.execute(intent('loopback', action='follow', target='91'))
            except BaseException as error:
                self.error = type(error).__name__
            finally:
                self.elapsed = time.perf_counter() - start
                self.done.set()
        self.worker = threading.Thread(target=run, name='pv-owned-write-client')
        self.worker.start()

    def close(self):
        try:
            if self.client is not None:
                self.client.close()
        finally:
            self.server.close()
            if self.worker is not None:
                self.worker.join(6)
                if self.worker.is_alive():
                    raise AssertionError('owned write worker did not stop')
            if self.reader is not None:
                self.reader.close()
            if hasattr(self, 'cookie_before'):
                assert self.cookie.read_bytes() == self.cookie_before


@pytest.fixture
def session_factory(tmp_path, monkeypatch, record_property):
    sessions = []
    def make(**kwargs):
        assert not sessions, 'one isolated endpoint per test'
        session = Session(tmp_path, monkeypatch, **kwargs)
        sessions.append(session)
        record_property('owned_listener_port', str(session.server.port))
        record_property('owner_pid', str(os.getpid()))
        record_property('platform', os.name)
        return session
    yield make
    for session in sessions:
        facts = dict(mode=session.mode, pid=os.getpid(), port=session.server.port,
            done_before_fixture_cleanup=session.done.is_set(),
            result_status=session.result.status if session.result is not None else None,
            result_reason=session.result.reason if session.result is not None else None,
            worker_error=session.error, elapsed_seconds=session.elapsed,
            accepted_connections=session.server.accepted,
            complete_requests=len(session.server.requests),
            connect_attempts=len(session.connects), drips=session.server.drips,
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


def assert_owned_finished(session):
    assert session.error is None and session.result is not None
    assert session.client._owned_transport.active_count == 0
    assert not session.client.shutdown_unverified
    assert session.server.accepted == 1 and len(session.server.requests) == 1
    assert len(session.connects) == 1 and session.registered_before_connect == [True]
    assert session.fences and all(not fence._handles for fence in session.fences)
    assert session.reader.require_identity() == '55'
    if session.server.use_tls:
        assert session.server.handshake.is_set() and session.server.tls_version
        assert session.context.check_hostname and session.context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('use_tls', [False, True], ids=['raw-http', 'verified-tls'])
@pytest.mark.parametrize('status', [200, 400])
@pytest.mark.parametrize('cause', ['deadline', 'close'])
def test_drip_body_stops_before_fixture_cleanup_with_one_post(session_factory, use_tls, status, cause):
    session = session_factory(use_tls=use_tls, status=status, phase='body',
                              budget=.75 if cause == 'deadline' else 3.0)
    session.start()
    assert session.server.started.wait(3), 'owned fixture never received its POST'
    if cause == 'close':
        time.sleep(.1)
        session.client.close()
    assert session.done.wait(2), 'write remained blocked beyond deadline/close'
    assert session.result.requires_reconciliation and not session.result.confirmed
    assert session.client.confirmed_state('55', 'user', '91') is None
    assert_owned_finished(session)


def test_drip_http_header_is_bounded_before_response_open_returns(session_factory):
    session = session_factory(use_tls=True, status=200, phase='headers', budget=.75)
    session.start()
    assert session.server.started.wait(3)
    assert session.done.wait(2), 'header drip bypassed the owned absolute deadline'
    assert session.result.requires_reconciliation
    assert_owned_finished(session)


@pytest.mark.parametrize('use_tls', [False, True], ids=['raw-http', 'verified-tls'])
def test_complete_owned_response_can_confirm_once_and_release_every_alias(session_factory, use_tls):
    session = session_factory(use_tls=use_tls, status=200, phase='complete', budget=3.0)
    session.start()
    assert session.done.wait(4)
    assert session.result.confirmed
    assert session.client.confirmed_state('55', 'user', '91') is True
    assert_owned_finished(session)

