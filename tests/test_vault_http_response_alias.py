"""Response ownership regressions using synthetic socket, TLS and HTTP aliases."""
import errno
import http.client
import socket
import ssl

import pytest

from pixivvault_nicegui.external_vault_transport import ACCOUNT, UrlopenVaultRequest, VaultError
from pixivvault_nicegui.vault_socket_fence import SocketFence, SocketFenceError


@pytest.mark.parametrize('close_fails', [False, True])
def test_oversized_response_fp_alias_closes_before_fence_unregister(monkeypatch, close_fails):
    events = []
    sends = []
    class Socket:
        def __init__(self):self.closed=False;self.stopped=False
        def dup(self):return Socket()
        def settimeout(self,value):pass
        def connect(self,address):pass
        def close(self):self.closed=True
        def shutdown(self,how):self.stopped=True
        def sendall(self,data):
            if self.closed or self.stopped:raise OSError('socket closed')
            sends.append(data)

    owner = Socket()
    class Reader:
        def __init__(self):self.socket=Socket();self.closed=False
        def close(self):self.socket.close();self.closed=True
        def send(self,data):self.socket.sendall(data)
    reader = Reader()
    fence = SocketFence()

    class Response:
        status=200
        def __init__(self):self.fp=reader;self.closes=0;self.read_sizes=[]
        def read(self,size):
            self.read_sizes.append(size)
            return b'x' * (2*1024*1024+1)
        def getheaders(self):return []
        def close(self):
            events.append(('response',bool(fence._handles)))
            self.closes+=1
            if close_fails:raise OSError(errno.EIO,'synthetic response close failure')
            self.fp.close()

    response=Response()
    class TLS:
        def wrap_socket(self,sock,server_hostname):return sock
    class Connection:
        def __init__(self,*args,context,**kwargs):self.sock=None;self.auto_open=1
        def request(self,*args,**kwargs):
            assert self.auto_open==0
            self.sock.sendall(b'one-request')
        def getresponse(self):return response
        def close(self):
            events.append(('connection',bool(fence._handles)))
            self.sock.close()

    dns=[];allocations=[]
    def lookup(*args):dns.append(1);return [(2,1,6,'',('fixture',443))]
    def make_socket(*args):allocations.append(1);return owner
    monkeypatch.setattr(socket,'getaddrinfo',lookup)
    monkeypatch.setattr(socket,'socket',make_socket)
    monkeypatch.setattr(ssl,'create_default_context',lambda:TLS())
    monkeypatch.setattr(http.client,'HTTPSConnection',Connection)

    if close_fails:
        with pytest.raises(OSError,match='synthetic response close failure'):
            UrlopenVaultRequest().fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
                fence=fence,epoch=fence.snapshot(),validate=lambda:None)
        assert fence.revocation_unverified
        assert any(d['operation']=='close-unverified' for d in fence.diagnostics())
        with pytest.raises(SocketFenceError,match='not confirmed'):fence.invalidate()
        with pytest.raises(SocketFenceError,match='not confirmed'):
            fence.require_healthy(fence.snapshot())
    else:
        with pytest.raises(VaultError,match='response-too-large'):
            UrlopenVaultRequest().fenced_request('POST',ACCOUNT,headers={},body=b'{}',timeout=2,
                fence=fence,epoch=fence.snapshot(),validate=lambda:None)
        assert reader.closed and not fence.revocation_unverified
        assert not fence._handles
        fence.invalidate()
        with pytest.raises(OSError,match='socket closed'):
            reader.send(b'late-send')

    assert response.closes==1
    assert response.read_sizes==[2*1024*1024+1]
    assert events[:2]==[('response',True),('connection',True)]
    assert dns==[1] and allocations==[1] and sends==[b'one-request']
