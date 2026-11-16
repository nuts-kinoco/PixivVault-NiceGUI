"""Synthetic socket fencing; only the explicit Windows test uses loopback."""
import os
import errno
import socket
import threading
from datetime import datetime,timezone
import pytest

from pixivvault_nicegui.vault_socket_fence import SocketFence,SocketFenceError
from pixivvault_nicegui.external_vault_transport import UrlopenVaultRequest,ShaftTransport,OAuthProfile,VaultError,ACCOUNT


class State:
    def __init__(self):self.stopped=False;self.sends=[];self.shutdowns=0
class FakeSocket:
    def __init__(self,state):self.state=state;self.closed=False
    def dup(self):return FakeSocket(self.state)
    def shutdown(self,how):self.state.stopped=True;self.state.shutdowns+=1
    def close(self):self.closed=True
    def settimeout(self,timeout):pass
    def connect(self,address):
        if self.state.stopped:raise OSError('fixture socket shut down')
    def sendall(self,data):
        if self.state.stopped:raise OSError('fixture socket shut down')
        self.state.sends.append(data)


def test_registration_and_invalidation_share_gate_and_reject_old_epoch():
    fence=SocketFence();old=fence.snapshot();state=State();sock=FakeSocket(state)
    with fence.register(sock,old):
        fence.invalidate()
        assert state.stopped and state.shutdowns==1
        with pytest.raises(OSError):sock.sendall(b'old')
        fence.invalidate()  # repeated lock does not repeat shutdown on the handle
    with pytest.raises(SocketFenceError):
        with fence.register(FakeSocket(State()),old):pass


def test_reused_descriptor_identity_never_shuts_unregistered_socket():
    fence=SocketFence();first=State();second=State()
    # Fake instances can reuse the same reported descriptor; the fence never
    # stores raw descriptor integers and owns its own duplicate objects.
    a=FakeSocket(first);a.fileno=lambda:42
    with fence.register(a,fence.snapshot()):pass
    b=FakeSocket(second);b.fileno=lambda:42
    with fence.register(b,fence.snapshot()):fence.invalidate()
    assert first.shutdowns==0 and second.shutdowns==1


def test_failed_shutdown_is_not_reported_as_successful_lock():
    class Failed(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise OSError('fixture shutdown failure')
    fence=SocketFence()
    with fence.register(Failed(State()),fence.snapshot()):
        with pytest.raises(SocketFenceError,match='not confirmed'):fence.invalidate()


@pytest.mark.parametrize('phase', ['pending', 'connected'])
def test_failed_revocation_poison_persists_and_blocks_fresh_session(monkeypatch, phase):
    """A failed lock is not permission to reconnect with a new epoch."""
    class RefusingShutdown(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise OSError(errno.EIO,'synthetic shutdown failure')

    fence=SocketFence();epoch=fence.snapshot();owner=RefusingShutdown(State())
    with fence.register(owner,epoch):
        if phase=='connected':fence.activate(owner,epoch)
        with pytest.raises(SocketFenceError,match='not confirmed'):
            fence.invalidate()
    assert any(d['operation']=='revoke-failed' and d['errno']==errno.EIO
               for d in fence.diagnostics())
    with pytest.raises(SocketFenceError,match='not confirmed'):
        fence.invalidate()
    new_epoch=fence.snapshot()
    with pytest.raises(SocketFenceError,match='not confirmed'):
        with fence.register(FakeSocket(State()),new_epoch):pass
    allocation=[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:allocation.append('dns'))
    monkeypatch.setattr(socket,'socket',lambda *a,**k:allocation.append('socket'))
    with pytest.raises(VaultError,match='stale-session'):
        UrlopenVaultRequest().fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
            fence=fence,epoch=new_epoch,validate=lambda:None)
    assert allocation==[]


def test_activate_rejects_poisoned_current_epoch_before_tls():
    fence=SocketFence();epoch=fence.snapshot();owner=FakeSocket(State())
    with fence.register(owner,epoch):
        fence.mark_close_unverified(OSError(errno.EIO,'synthetic close failure'))
        with pytest.raises(SocketFenceError,match='not confirmed'):
            fence.activate(owner,epoch)


@pytest.mark.parametrize('phase',['pending','connected'])
def test_non_os_shutdown_exception_poison_survives_later_epoch(phase):
    class InvalidShutdown(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise RuntimeError('synthetic unexpected shutdown failure')
    fence=SocketFence();owner=InvalidShutdown(State());epoch=fence.snapshot()
    with fence.register(owner,epoch):
        if phase=='connected':fence.activate(owner,epoch)
        with pytest.raises(SocketFenceError,match='not confirmed'):
            fence.invalidate()
    assert fence.revocation_unverified
    assert any(d['phase']==phase and d['operation']=='revoke-failed' for d in fence.diagnostics())
    with pytest.raises(SocketFenceError,match='not confirmed'):
        with fence.register(FakeSocket(State()),fence.snapshot()):pass
    with pytest.raises(SocketFenceError,match='not confirmed'):
        fence.invalidate()


def test_non_os_pending_owner_close_failure_also_poisoned():
    class InvalidClose(FakeSocket):
        def dup(self):return FakeSocket(self.state)
        def shutdown(self,how):self.state.stopped=True
        def close(self):raise RuntimeError('synthetic owner close failure')
    fence=SocketFence();owner=InvalidClose(State())
    with fence.register(owner,fence.snapshot()):
        with pytest.raises(SocketFenceError,match='not confirmed'):fence.invalidate()
    assert owner.state.stopped and fence.revocation_unverified
    with pytest.raises(SocketFenceError,match='not confirmed'):
        with fence.register(FakeSocket(State()),fence.snapshot()):pass


@pytest.mark.parametrize('signal',[KeyboardInterrupt,SystemExit])
def test_baseexception_signal_poisoned_then_reraised_without_masking(signal):
    class Interrupted(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise signal('synthetic interrupted shutdown')
    fence=SocketFence();owner=Interrupted(State());other=FakeSocket(State())
    with fence.register(owner,fence.snapshot()):
        with fence.register(other,fence.snapshot()):
            with pytest.raises(signal,match='synthetic interrupted shutdown'):
                fence.invalidate()
            assert other.state.stopped, 'interrupt must not skip the next registered socket'
    assert fence.revocation_unverified
    with pytest.raises(SocketFenceError,match='not confirmed'):
        with fence.register(FakeSocket(State()),fence.snapshot()):pass


def test_unsupported_scoped_request_fails_before_callback():
    calls=[];profile=OAuthProfile('ua','ios','1','1','salt','id','secret')
    transport=ShaftTransport(lambda *a,**k:calls.append(a),oauth_profiles=[profile],clock=lambda:datetime.now(timezone.utc))
    with transport.session_guard(lambda:True):
        with pytest.raises(VaultError,match='transport-fence-unavailable'):
            transport._request('POST',ACCOUNT,{},b'{}')
    assert calls==[]


def test_old_session_cannot_be_rebound_after_unlock():
    state={'epoch':1};calls=[]
    profile=OAuthProfile('ua','ios','1','1','salt','id','secret')
    class Request:
        def fenced_request(self,*args,**kwargs):calls.append(args);raise AssertionError('stale request started')
    transport=ShaftTransport(Request(),oauth_profiles=[profile])
    with transport.session_guard(lambda:state['epoch']==1):
        transport.clear_pending();state['epoch']=2
        with pytest.raises(VaultError,match='stale-session'):
            transport._request('POST',ACCOUNT,{},b'{}')
    assert calls==[]


@pytest.mark.parametrize('pause', ['created-before-register','registered-before-tls','tls-midway','blocked-send'])
def test_connect_tls_and_send_are_fenced_without_real_network(monkeypatch,pause):
    import http.client,ssl
    entered=threading.Event();release=threading.Event();state=State();calls=[];errors=[]
    fence=SocketFence();epoch=fence.snapshot();raw=FakeSocket(state)
    def create_socket(*args,**kwargs):
        if pause=='created-before-register':entered.set();assert release.wait(5)
        return raw
    class SSLContext:
        def wrap_socket(self,sock,server_hostname):
            calls.append('tls')
            if pause=='registered-before-tls':entered.set();assert release.wait(5)
            if pause=='tls-midway':
                sock.sendall(b'tls-first');entered.set();assert release.wait(5)
                sock.sendall(b'tls-next');return sock
            sock.sendall(b'tls');return sock
    class Response:
        status=200
        def read(self,n):return b'{}'
        def getheaders(self):return []
        def close(self):pass
    class Connection:
        def __init__(self,*args,context,**kwargs):self._context=context;self.sock=None;self.auto_open=1
        def request(self,*args,**kwargs):
            calls.append('request');assert self.auto_open==0
            if pause=='blocked-send':entered.set();assert release.wait(5)
            self.sock.sendall(b'http')
        def getresponse(self):return Response()
        def close(self):
            if self.sock:self.sock.close()
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('fixture',443))])
    monkeypatch.setattr(socket,'socket',create_socket)
    monkeypatch.setattr(ssl,'create_default_context',lambda:SSLContext())
    monkeypatch.setattr(http.client,'HTTPSConnection',Connection)
    request=UrlopenVaultRequest()
    def run():
        try:request.fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
                fence=fence,epoch=epoch,validate=lambda:None)
        except VaultError as error:errors.append(error.code)
    worker=threading.Thread(target=run);worker.start();assert entered.wait(5)
    fence.invalidate();release.set();worker.join(5)
    assert not worker.is_alive() and errors
    assert b'http' not in state.sends
    if pause not in ('blocked-send','tls-midway'):assert b'tls' not in state.sends
    if pause=='tls-midway':assert state.sends==[b'tls-first']
    if pause=='created-before-register':assert calls==[]


def test_redirect_response_is_not_followed_or_reconnected(monkeypatch):
    import http.client,ssl
    state=State();calls=[]
    class SSLContext:
        def wrap_socket(self,sock,server_hostname):return sock
    class Response:
        status=302
        def read(self,n):return b'{}'
        def getheaders(self):return [('Location','https://outside.invalid/')]
        def close(self):pass
    class Connection:
        def __init__(self,*args,context,**kwargs):self._context=context;self.sock=None;self.auto_open=1
        def request(self,method,path,**kwargs):calls.append((method,path,self.auto_open));self.sock.sendall(b'one')
        def getresponse(self):return Response()
        def close(self):self.sock.close()
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('fixture',443))])
    monkeypatch.setattr(socket,'socket',lambda *a,**k:FakeSocket(state))
    monkeypatch.setattr(ssl,'create_default_context',lambda:SSLContext())
    monkeypatch.setattr(http.client,'HTTPSConnection',Connection)
    fence=SocketFence();result=UrlopenVaultRequest().fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
        fence=fence,epoch=fence.snapshot(),validate=lambda:None)
    assert result.status==302 and len(calls)==1 and calls[0][2]==0 and state.sends==[b'one']


@pytest.mark.skipif(os.name!='nt',reason='Windows duplicate socket shutdown semantics')
def test_windows_duplicate_shutdown_blocks_original_loopback_send():
    left,right=socket.socketpair()
    try:
        left.settimeout(2);right.settimeout(2);fence=SocketFence()
        with fence.register(left,fence.snapshot()):
            left.sendall(b'a');assert right.recv(1)==b'a'
            fence.invalidate()
            with pytest.raises(OSError):left.sendall(b'b')
    finally:left.close();right.close()


def test_registered_socket_blocks_reconnect_after_close(monkeypatch):
    import http.client,ssl
    state=State();opens=[]
    class SSLContext:
        def wrap_socket(self,sock,server_hostname):return sock
    class Connection:
        def __init__(self,*args,context,**kwargs):self.sock=None;self.auto_open=1
        def request(self,*args,**kwargs):
            assert self.auto_open==0
            self.sock=None
            raise OSError('fixture dropped connection')
        def close(self):pass
    def connect(*args,**kwargs):opens.append(1);return FakeSocket(state)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('fixture',443))])
    monkeypatch.setattr(socket,'socket',connect)
    monkeypatch.setattr(ssl,'create_default_context',lambda:SSLContext())
    monkeypatch.setattr(http.client,'HTTPSConnection',Connection)
    fence=SocketFence()
    with pytest.raises(VaultError,match='transport-failed'):
        UrlopenVaultRequest().fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
            fence=fence,epoch=fence.snapshot(),validate=lambda:None)
    assert len(opens)==1


def test_vault_lock_does_not_claim_success_when_socket_shutdown_fails(tmp_path):
    from test_external_vault import FixtureCrypto,profile,Request
    from pixivvault_nicegui.external_vault import ExternalVaultService
    class Failed(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise OSError('fixture shutdown failure')
    transport=ShaftTransport(Request([]),oauth_profiles=[profile()])
    vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=transport)
    vault.initialize('fixture-password','1','fixture-secret')
    try:
        with transport._socket_fence.register(Failed(State()),transport._socket_fence.snapshot()):
            with pytest.raises(VaultError,match='socket-shutdown-unverified'):vault.lock()
            assert not vault.status()['open']
    finally:
        # A failed shutdown cannot become "confirmed" merely because the
        # request scope exited before another close/lock attempt.
        with pytest.raises(VaultError,match='socket-shutdown-unverified'):
            vault.close()


def test_custom_unfenced_vault_transport_does_not_start_refresh(tmp_path):
    from types import SimpleNamespace
    from test_external_vault import FixtureCrypto
    from pixivvault_nicegui.external_vault import ExternalVaultService
    calls=[]
    transport=SimpleNamespace(fetch_account=lambda *a:calls.append(a))
    vault=ExternalVaultService(tmp_path/'vault',crypto=FixtureCrypto(),transport=transport)
    vault.initialize('fixture-password','1','fixture-secret')
    try:
        with pytest.raises(VaultError,match='transport-fence-unavailable'):vault.refresh(1)
        assert calls==[]
    finally:vault.close()


@pytest.mark.parametrize("code", [errno.ENOTCONN,10057])
def test_pending_not_connected_requires_closing_actual_owner(code):
    import errno
    class Pending(FakeSocket):
        def shutdown(self,how):raise OSError(code,'fixture not connected')
        def connect(self,address):
            if self.closed:raise OSError('fixture closed owner')
    fence=SocketFence();sock=Pending(State());epoch=fence.snapshot()
    with fence.register(sock,epoch):
        fence.invalidate()
        assert sock.closed
        with pytest.raises(OSError):sock.connect(('fixture',443))
        with pytest.raises(SocketFenceError):fence.activate(sock,epoch)
    records=fence.diagnostics()
    assert any(x['operation']=='close-owned-socket' for x in records)
    assert any(x['errno']==code and x['phase']=='pending' for x in records)


def test_connected_pin_is_created_after_connect_and_survives_original_detach():
    class Resource:
        def __init__(self):self.connected=False;self.stopped=False;self.dups=[]
    class Handle:
        def __init__(self,resource,connected=False):self.r=resource;self.connected=connected;self.closed=False
        def shutdown(self,how):
            import errno
            if not self.connected:raise OSError(errno.ENOTCONN,'pre-connect duplicate')
            self.r.stopped=True
        def close(self):self.closed=True
    class Owner(Handle):
        def dup(self):
            h=Handle(self.r,self.r.connected);self.r.dups.append(h);return h
    r=Resource();owner=Owner(r);fence=SocketFence();epoch=fence.snapshot()
    with fence.register(owner,epoch):
        before=r.dups[0];r.connected=True
        fence.activate(owner,epoch)
        assert before.closed and len(r.dups)==2 and not r.dups[1].closed
        owner.closed=True  # SSL takes over original; fence retains its own pin.
        fence.invalidate()
        assert r.stopped
    assert r.dups[1].closed


def test_active_not_connected_is_not_treated_as_confirmed_shutdown():
    import errno
    class Failed(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise OSError(errno.ENOTCONN,'active fixture error')
    fence=SocketFence();sock=Failed(State());epoch=fence.snapshot()
    with fence.register(sock,epoch):
        fence.activate(sock,epoch)
        with pytest.raises(SocketFenceError,match='not confirmed'):fence.invalidate()
    assert any(x['operation']=='revoke-failed' and x['errno']==errno.ENOTCONN
        and x['phase']=='connected' for x in fence.diagnostics())


def test_pending_unexpected_os_error_is_not_hidden_and_diagnostics_have_no_message():
    import errno
    class Failed(FakeSocket):
        def dup(self):return self
        def shutdown(self,how):raise OSError(errno.EACCES,'private fixture path must never escape')
    fence=SocketFence();sock=Failed(State())
    with fence.register(sock,fence.snapshot()):
        with pytest.raises(SocketFenceError):fence.invalidate()
    facts=fence.diagnostics()
    assert facts and all(set(x)=={'phase','operation','errno','winerror'} for x in facts)
    assert any(x['errno']==errno.EACCES for x in facts)
    assert 'private' not in repr(facts)


def test_late_connect_cannot_activate_or_send_after_cancel():
    entered=threading.Event();release=threading.Event();errors=[];state=State()
    class Pending(FakeSocket):
        def connect(self,address):entered.set();assert release.wait(5)
    sock=Pending(state);fence=SocketFence();epoch=fence.snapshot()
    def run():
        try:
            with fence.register(sock,epoch):
                sock.connect(('fixture',443));fence.activate(sock,epoch);sock.sendall(b'TLS')
        except SocketFenceError:errors.append('stale')
    worker=threading.Thread(target=run);worker.start()
    try:
        assert entered.wait(5);fence.invalidate()
    finally:release.set();worker.join(5)
    assert not worker.is_alive() and errors==['stale'] and state.sends==[]


def test_activate_dup_failure_cleanup_and_lock_are_serialized(monkeypatch):
    import errno
    from threading import Event, Thread
    entered=Event();release=Event();locking=Event();locked=Event()
    state=State();request_errors=[];lock_errors=[];fence=SocketFence();epoch=fence.snapshot()
    class FailingOwner(FakeSocket):
        def __init__(self,state):super().__init__(state);self.duplicates=0
        def dup(self):
            self.duplicates+=1
            if self.duplicates==2:raise OSError(errno.EMFILE,'synthetic dup failure')
            return FakeSocket(self.state)
        def close(self):
            entered.set()
            if not release.wait(5):raise AssertionError('fixture cleanup gate timed out')
            super().close()
    owner=FailingOwner(state)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('fixture',443))])
    monkeypatch.setattr(socket,'socket',lambda *a,**k:owner)
    request=UrlopenVaultRequest()
    def run_request():
        try:request.fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
            fence=fence,epoch=epoch,validate=lambda:None)
        except VaultError as error:request_errors.append(error.code)
    def run_lock():
        locking.set()
        try:fence.invalidate()
        except BaseException as error:lock_errors.append(type(error).__name__)
        finally:locked.set()
    worker=Thread(target=run_request);worker.start()
    locker=None
    try:
        assert entered.wait(5)
        locker=Thread(target=run_lock);locker.start();assert locking.wait(5)
        assert not locked.wait(.2), 'lock crossed the unfinished close/unregister gate'
    finally:
        release.set();worker.join(5)
        if locker is not None:locker.join(5)
    assert not worker.is_alive() and locker is not None and not locker.is_alive()
    assert locked.is_set() and not lock_errors
    assert request_errors==['transport-failed'] and state.sends==[]
    assert owner.closed and not fence._handles
    with pytest.raises(SocketFenceError):
        with fence.register(FakeSocket(State()),epoch):pass
    assert any(x['operation']=='duplicate-failed' and x['errno']==errno.EMFILE for x in fence.diagnostics())


def test_connected_owner_close_failure_cannot_report_confirmed_lock(monkeypatch):
    """A failed owner close must stay visible to later revocation status."""
    import gc
    import http.client
    import ssl

    state = State()

    class RefusingOwner(FakeSocket):
        def close(self):
            raise OSError(errno.EIO, 'synthetic owner close failure')

    owner = RefusingOwner(state)

    class FixtureSSLContext:
        def wrap_socket(self, sock, server_hostname):
            return sock

    class FixtureResponse:
        status = 200

        def read(self, size):
            return b'{}'

        def getheaders(self):
            return []

        def close(self):
            pass

    class FixtureConnection:
        def __init__(self, *args, context, **kwargs):
            self.sock = None
            self.auto_open = 1

        def request(self, *args, **kwargs):
            assert self.auto_open == 0
            self.sock.sendall(b'fixture-only')

        def getresponse(self):
            return FixtureResponse()

        def close(self):
            self.sock.close()

    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('fixture', 443))])
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: owner)
    monkeypatch.setattr(ssl, 'create_default_context', lambda: FixtureSSLContext())
    monkeypatch.setattr(http.client, 'HTTPSConnection', FixtureConnection)
    fence = SocketFence()
    epoch = fence.snapshot()

    with pytest.raises(OSError):
        UrlopenVaultRequest().fenced_request(
            'POST', ACCOUNT, headers={}, body=b'{}', timeout=2,
            fence=fence, epoch=epoch, validate=lambda: None)

    assert not owner.closed
    gc.collect()  # do not rely on a pytest traceback retaining the generator
    assert len(fence._handles)==1
    assert len(fence._retained_registrations)==1
    with pytest.raises(SocketFenceError, match='not confirmed'):
        fence.invalidate()
    assert state.stopped, 'lock must still revoke the retained registered shutdown duplicate'
    assert any(x['operation'] == 'close-unverified' for x in fence.diagnostics())
    with pytest.raises(SocketFenceError, match='not confirmed'):
        fence.invalidate()  # a repeated lock cannot erase unverified closure
    with pytest.raises(SocketFenceError, match='not confirmed'):
        with fence.register(FakeSocket(State()), fence.snapshot()):
            pass  # new session cannot grow a permanently unverified fence
    allocation=[]
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: allocation.append('dns'))
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: allocation.append('socket'))
    with pytest.raises(VaultError, match='stale-session'):
        UrlopenVaultRequest().fenced_request(
            'POST', ACCOUNT, headers={}, body=b'{}', timeout=2,
            fence=fence, epoch=fence.snapshot(), validate=lambda: None)
    assert allocation==[], 'poisoned request must fail before any DNS/socket allocation'


def test_poison_during_dns_resolution_blocks_socket_creation(monkeypatch):
    fence=SocketFence();epoch=fence.snapshot();allocated=[]
    def resolve(*args):
        fence.mark_close_unverified(OSError(errno.EIO,'synthetic poisoned during DNS'))
        return [(2,1,6,'',('fixture',443))]
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(socket,'socket',lambda *a,**k:allocated.append('socket'))
    with pytest.raises(VaultError,match='stale-session'):
        UrlopenVaultRequest().fenced_request(
            'POST',ACCOUNT,headers={},body=b'{}',timeout=2,
            fence=fence,epoch=epoch,validate=lambda:None)
    assert allocated==[], 'poisoned fence must reject after DNS, before socket allocation'


def test_fence_duplicate_close_failure_remains_unverified():
    state = State()

    class FailedDuplicate(FakeSocket):
        def close(self):
            raise OSError(errno.EIO, 'synthetic duplicate close failure')

    class Owner(FakeSocket):
        def dup(self):
            return FailedDuplicate(self.state)

    fence = SocketFence()
    with pytest.raises(OSError):
        with fence.register(Owner(state), fence.snapshot()):
            pass

    with pytest.raises(SocketFenceError, match='not confirmed'):
        fence.invalidate()
    assert any(d['operation'] == 'close-unverified' and d['errno'] == errno.EIO
               for d in fence.diagnostics())
    with pytest.raises(SocketFenceError, match='not confirmed'):
        with fence.register(FakeSocket(State()), fence.snapshot()):
            pass


def test_failed_connect_with_failed_owner_close_preserves_revoke_proof(monkeypatch):
    state = State()

    class RefusingConnect(FakeSocket):
        def connect(self, address):
            raise OSError(errno.ECONNREFUSED, 'fixture refusing connect')

        def close(self):
            raise OSError(errno.EIO, 'fixture refusing close')

    owner = RefusingConnect(state)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('fixture', 443))])
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: owner)
    fence = SocketFence()

    with pytest.raises(VaultError, match='transport-failed'):
        UrlopenVaultRequest().fenced_request(
            'POST', ACCOUNT, headers={}, body=b'{}', timeout=2,
            fence=fence, epoch=fence.snapshot(), validate=lambda: None)

    import gc
    gc.collect()
    assert len(fence._handles)==1
    with pytest.raises(SocketFenceError, match='not confirmed'):
        fence.invalidate()
    assert state.stopped and any(d['operation'] == 'close-unverified' for d in fence.diagnostics())
