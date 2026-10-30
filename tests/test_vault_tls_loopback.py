"""Real localhost TCP/TLS P2-9 regression; never contacts public services."""
import http.client
import socket
import ssl
import threading
import json
import os
from contextlib import contextmanager
import pytest

import pixivvault_nicegui.external_vault_transport as transport_module
from pixivvault_nicegui.external_vault import ExternalVaultService,VaultError
from pixivvault_nicegui.external_vault_transport import ShaftTransport,UrlopenVaultRequest
from test_external_vault import FixtureCrypto,profile
from vault_tls_loopback_fixture import LoopbackTLS,CA


class Session:
    def __init__(self,tmp_path,monkeypatch,*,server_mode='normal',gate=None,status=200,host='localhost'):
        tmp_path.mkdir(parents=True,exist_ok=False)
        self.server=LoopbackTLS(server_mode,status);self.gate=gate
        self.vault=None
        try:
            self.entered=threading.Event();self.release=threading.Event();self.done=threading.Event()
            self.result=None;self.error=None;self.tls_socket=None;self.tls_version=None;self.worker=None
            self.destination='https://'+host+'/fixture'
            self.client_context=ssl.create_default_context(cafile=str(CA))
            assert self.client_context.verify_mode==ssl.CERT_REQUIRED and self.client_context.check_hostname
            real_socket=socket.socket;self.real_socket=real_socket;self.connects=[];self.registered=set()
            server_port=self.server.port;owner=self
            class RestrictedSocket(real_socket):
                def connect(self,address):
                    if not (isinstance(address,tuple) and address[0]=='127.0.0.1' and address[1]==server_port):
                        raise AssertionError('fixture forbids non-loopback connect')
                    if id(self) not in owner.registered:
                        raise AssertionError('connect preceded socket fence registration')
                    owner.connects.append(address)
                    return super().connect(address)
            def addresses(name,port,*args,**kwargs):
                if name not in ('localhost','wrong-host.invalid') or port!=443:
                    raise AssertionError('fixture forbids external DNS')
                return [(socket.AF_INET,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',('127.0.0.1',server_port))]
            class VerifiedContext:
                def wrap_socket(self,sock,server_hostname):
                    if owner.gate=='connected-before-tls':
                        owner.entered.set()
                        if not owner.release.wait(6):raise TimeoutError('fixture pre-TLS gate')
                    result=owner.client_context.wrap_socket(sock,server_hostname=server_hostname)
                    owner.tls_socket=result;owner.tls_version=result.version()
                    if owner.gate=='after-tls':
                        owner.entered.set()
                        if not owner.release.wait(6):raise TimeoutError('fixture post-TLS gate')
                    return result
            monkeypatch.setattr(socket,'getaddrinfo',addresses)
            monkeypatch.setattr(socket,'socket',RestrictedSocket)
            monkeypatch.setattr(ssl,'create_default_context',lambda:VerifiedContext())
            monkeypatch.setattr(transport_module,'ACCOUNT',self.destination)
            self.transport=ShaftTransport(UrlopenVaultRequest(),oauth_profiles=[profile()])
            original=self.transport._socket_fence.register
            @contextmanager
            def register(sock,epoch):
                if self.gate=='before-register':
                    self.entered.set()
                    if not self.release.wait(6):raise TimeoutError('fixture pre-registration gate')
                with original(sock,epoch):
                    self.registered.add(id(sock))
                    if self.gate=='registered-before-connect':
                        self.entered.set()
                        if not self.release.wait(6):raise TimeoutError('fixture pre-connect gate')
                    if self.gate=='blocked-send':sock.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
                    try:yield
                    finally:self.registered.discard(id(sock))
            monkeypatch.setattr(self.transport._socket_fence,'register',register)
            self.vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=self.transport,clock=lambda:1000)
            self.vault.initialize('synthetic-password','1','synthetic-secret')
        except BaseException:
            try:
                if self.vault is not None:self.vault.close()
            finally:self.server.close()
            raise
    def start(self,body=b'{}'):
        epoch,_=self.vault._snapshot()
        def valid():
            with self.vault._mutex:
                return not self.vault._closed and self.vault._data is not None and self.vault._epoch==epoch
        def run():
            try:
                with self.transport.session_guard(valid):
                    self.result=self.transport._request('POST',self.destination,{'Content-Type':'application/json'},body)
            except VaultError as error:self.error=error.code
            finally:self.done.set()
        self.worker=threading.Thread(target=run,name='pv-fixture-owned-client');self.worker.start()
    def close(self):
        self.release.set();self.server.allow_handshake.set();self.server.allow_read.set()
        try:
            if self.worker is not None:
                self.worker.join(7)
                if self.worker.is_alive():raise AssertionError('owned client did not stop')
        finally:
            try:self.vault.close()
            finally:self.server.close()


@pytest.fixture
def session_factory(tmp_path,monkeypatch,record_property):
    sessions=[]
    def make(**kwargs):
        session=Session(tmp_path/('run-'+str(len(sessions))),monkeypatch,**kwargs)
        sessions.append(session)
        # Recorded before assertions, including failing case listeners.
        record_property('owned_listener_port',str(session.server.port))
        record_property('owner_pid',str(os.getpid()))
        return session
    yield make
    errors=[]
    for session in reversed(sessions):
        details={'port':session.server.port,'pid':os.getpid(),
            'before_cleanup':{'client_done':session.done.is_set(),'requests':len(session.server.requests),
                'fence':session.transport._socket_fence.diagnostics()}}
        try:session.close()
        except BaseException as error:
            details['cleanup_error']=type(error).__name__;errors.append(error)
        finally:
            details['client_alive']=bool(session.worker and session.worker.is_alive())
            details['server_alive']=session.server.thread.is_alive()
            details['server_workers_alive']=sum(w.is_alive() for w in session.server.workers)
            details['listener_closed']=session.server.listener.fileno()==-1
            (tmp_path/('lifecycle-'+str(session.server.port)+'.json')).write_text(
                json.dumps(details,indent=2)+'\n',encoding='utf-8')
            record_property('lifecycle',json.dumps(details,sort_keys=True))
    if errors:raise errors[0]


def evidence(record_property,session,case):
    record_property('case',case);record_property('endpoint','127.0.0.1:'+str(session.server.port))
    record_property('hostname_check',str(session.client_context.check_hostname))
    record_property('verify_mode',str(session.client_context.verify_mode))
    record_property('tls_version',str(session.tls_version or session.server.tls_version))
    record_property('accepted_connections',str(session.server.accepted))
    record_property('client_connect_attempts',str(len(session.connects)))
    record_property('complete_fixture_requests',str(len(session.server.requests)))
    record_property('client_error',str(session.error))


def test_real_tls_validates_localhost_and_uses_owned_connection(session_factory,record_property):
    session=session_factory();session.start();assert session.done.wait(6)
    assert session.error is None and session.result.status==200
    assert session.tls_version and session.server.handshake.is_set()
    assert session.client_context.check_hostname and session.client_context.verify_mode==ssl.CERT_REQUIRED
    assert session.server.accepted==1 and len(session.server.requests)==1
    evidence(record_property,session,'verified-localhost')


def test_wrong_hostname_rejected_with_verification_enabled(session_factory,record_property):
    session=session_factory(host='wrong-host.invalid');session.start();assert session.done.wait(6)
    assert session.result is None and session.error=='transport-failed'
    assert session.client_context.check_hostname and session.client_context.verify_mode==ssl.CERT_REQUIRED
    assert not session.server.requests
    evidence(record_property,session,'hostname-rejected')


def test_old_epoch_cannot_register_after_lock_and_reunlock(session_factory,record_property):
    session=session_factory(gate='before-register');session.start();assert session.entered.wait(6)
    try:
        session.vault.lock();session.vault.unlock('synthetic-password')
    finally:session.release.set()
    assert session.done.wait(6) and session.result is None
    assert session.error in ('stale-session','transport-failed')
    assert session.server.accepted==0 and not session.server.requests
    evidence(record_property,session,'old-epoch-after-unlock')


def test_lock_after_registration_before_connect_returns_without_late_connect(session_factory,record_property):
    session=session_factory(gate='registered-before-connect');session.start();assert session.entered.wait(6)
    try:
        # A shutdown failure is a genuine failed acceptance, not a successful lock.
        session.vault.lock()
    finally:session.release.set()
    assert session.done.wait(6) and session.result is None and session.server.accepted==0
    evidence(record_property,session,'registered-before-connect')


def test_lock_after_connect_before_tls_prevents_tls_and_http(session_factory,record_property):
    session=session_factory(gate='connected-before-tls');session.start();assert session.entered.wait(6)
    try:session.vault.lock()
    finally:session.release.set()
    assert session.done.wait(6) and session.result is None
    assert not session.server.handshake.is_set() and not session.server.requests
    evidence(record_property,session,'connected-before-tls')


def test_lock_during_real_tls_handshake_rejects_later_request(session_factory,record_property):
    session=session_factory(server_mode='pause-handshake');session.start();assert session.server.hello.wait(6)
    try:session.vault.lock()
    finally:session.server.allow_handshake.set()
    assert session.done.wait(6) and session.result is None and not session.server.requests
    evidence(record_property,session,'tls-in-progress')


def test_lock_after_verified_tls_before_http_prevents_request(session_factory,record_property):
    session=session_factory(gate='after-tls');session.start();assert session.entered.wait(6)
    assert session.server.handshake.wait(6) and session.tls_version
    try:session.vault.lock()
    finally:session.release.set()
    assert session.done.wait(6) and session.result is None and not session.server.requests
    evidence(record_property,session,'verified-tls-before-http')


def test_blocked_real_tls_send_unblocks_after_lock(session_factory,monkeypatch,record_property):
    session=session_factory(server_mode='pause-read',gate='blocked-send')
    entered=threading.Event();original=http.client.HTTPConnection.send
    def monitored(connection,data):
        if isinstance(data,(bytes,bytearray)) and len(data)>=1024*1024:entered.set()
        return original(connection,data)
    monkeypatch.setattr(http.client.HTTPConnection,'send',monitored)
    session.start(b'x'*(8*1024*1024));assert session.server.handshake.wait(6)
    assert entered.wait(6) and not session.done.wait(.2), 'fixture did not reach a blocked send'
    try:session.vault.lock()
    finally:session.server.allow_read.set()
    assert session.done.wait(6) and session.result is None
    evidence(record_property,session,'blocked-tls-send')


def test_redirect_not_followed_and_no_external_dns(session_factory,record_property):
    session=session_factory(status=302);session.start();assert session.done.wait(6)
    assert session.error is None and session.result.status==302
    assert session.server.accepted==1 and len(session.server.requests)==1
    evidence(record_property,session,'redirect-not-followed')


def test_real_tls_socket_cannot_send_after_fence_shutdown(session_factory,record_property):
    session=session_factory();raw=socket.socket(socket.AF_INET,socket.SOCK_STREAM);raw.settimeout(4)
    fence=session.transport._socket_fence
    try:
        with fence.register(raw,fence.snapshot()):
            raw.connect(('127.0.0.1',session.server.port))
            fence.activate(raw,fence.snapshot())
            tls=session.client_context.wrap_socket(raw,server_hostname='localhost')
            try:
                assert session.server.handshake.wait(6) and tls.version()
                tls.sendall(b'pre-lock')
                session.vault.lock()
                with pytest.raises(OSError):tls.sendall(b'post-lock')
                assert not session.server.requests
            finally:tls.close()
    finally:raw.close()
    evidence(record_property,session,'real-ssl-socket-shutdown')


def test_dropped_tls_connection_does_not_reconnect(session_factory,record_property):
    session=session_factory(server_mode='drop-after-handshake');session.start()
    assert session.server.handshake.wait(6) and session.done.wait(6)
    assert session.result is None and session.error=='transport-failed'
    assert session.server.accepted==1 and len(session.connects)==1
    evidence(record_property,session,'no-reconnect-after-tls-drop')


def test_reused_descriptor_is_not_shutdown_after_registration_exits(session_factory,record_property):
    session=session_factory();fence=session.transport._socket_fence
    old=session.real_socket(socket.AF_INET,socket.SOCK_STREAM)
    old_id=old.fileno()
    with fence.register(old,fence.snapshot()):pass
    old.close();replacement=None
    for _ in range(256):
        candidate=session.real_socket(socket.AF_INET,socket.SOCK_STREAM)
        if candidate.fileno()==old_id:
            replacement=candidate;break
        candidate.close()
    if replacement is None:
        pytest.skip('OS did not recycle this socket handle; descriptor reuse remains unverified')
    try:
        fence.invalidate()
        replacement.settimeout(4)
        replacement.connect(('127.0.0.1',session.server.port))
        assert replacement.getpeername()[0]=='127.0.0.1'
        record_property('reused_socket_handle','observed')
    finally:replacement.close()
    evidence(record_property,session,'real-handle-reuse')
