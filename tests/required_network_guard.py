"""New acceptance tests use synthetic readers only; accidental connects fail."""
import socket
from threading import local
import pytest

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    internal=local()
    original_pair,original_connect=socket.socketpair,socket.socket.connect
    def rejected(*args,**kwargs):
        raise AssertionError('network is forbidden in required-seven synthetic tests')
    def socketpair(*args,**kwargs):
        # Windows implements asyncio's internal socketpair with loopback TCP.
        previous=getattr(internal,'socketpair',False)
        internal.socketpair=True
        try:return original_pair(*args,**kwargs)
        finally:internal.socketpair=previous
    def connect(sock,address):
        if (getattr(internal,'socketpair',False) and isinstance(address,tuple)
                and address[0] in ('127.0.0.1','::1')):
            return original_connect(sock,address)
        return rejected()
    monkeypatch.setattr(socket,'socketpair',socketpair)
    monkeypatch.setattr(socket.socket,'connect',connect)
    monkeypatch.setattr(socket.socket,'connect_ex',rejected)
    monkeypatch.setattr(socket,'create_connection',rejected)
