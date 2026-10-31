"""No real outbound traffic: deliberate attempts are intercepted in Python."""
import socket
import pytest
from tools.stage5_offline_parent_fence import OfflineNetworkFence


@pytest.mark.parametrize('via', ['connect','connect_ex','create_connection','getaddrinfo','sendto'])
def test_external_attempt_is_blocked_without_real_network(via):
    originals=(socket.socket.connect,socket.socket.connect_ex,
               socket.create_connection,socket.getaddrinfo,socket.socket.sendto)
    with OfflineNetworkFence() as guard:
        try:
            if via=='getaddrinfo':
                socket.getaddrinfo('external.invalid',443)
            elif via=='create_connection':
                socket.create_connection(('203.0.113.55',443))
            else:
                sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if via=='sendto' else socket.SOCK_STREAM)
                try:
                    if via=='connect': sock.connect(('203.0.113.55',443))
                    elif via=='connect_ex': sock.connect_ex(('203.0.113.55',443))
                    else: sock.sendto(b'test',('203.0.113.55',443))
                finally:sock.close()
        except PermissionError:
            pass
        assert guard.attempt_count==1, 'caught exception still must register attempted outbound'
    assert originals==(socket.socket.connect,socket.socket.connect_ex,
                       socket.create_connection,socket.getaddrinfo,socket.socket.sendto)


def test_swallowed_dns_attempt_cannot_be_mistaken_for_offline_success():
    with OfflineNetworkFence() as guard:
        try: socket.getaddrinfo('outside.invalid',80)
        except PermissionError: pass
        # Caller could catch the exception, but receipt must fail closed.
        assert guard.attempt_count==1
        assert not guard.clean


def test_localhost_traffic_stays_permitted_without_external_attempts():
    with OfflineNetworkFence() as guard:
        result=socket.getaddrinfo('127.0.0.1',443)
        assert result
        assert guard.clean


def test_sendmsg_external_udp_is_denied_and_restored_on_supported_socket(monkeypatch):
    # Windows has no sendmsg. Emulate the *availability* only; the fake method
    # never performs network I/O, including against an unpatched old fence.
    original_class=socket.socket
    simulated_calls=[]
    class SimulatedSendmsg(original_class):
        def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
            simulated_calls.append(('would_send',address))
            return sum(len(x) for x in buffers)
    monkeypatch.setattr(socket,'socket',SimulatedSendmsg)
    original_method=SimulatedSendmsg.sendmsg
    with OfflineNetworkFence() as guard:
        target=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            with pytest.raises(PermissionError):
                target.sendmsg([b'probe'],[],0,('203.0.113.55',9999))
            assert guard.attempts==['socket.sendmsg']
        finally:target.close()
    assert simulated_calls==[], 'even the inert simulated send must not be called'
    assert SimulatedSendmsg.sendmsg is original_method


def test_sendmsg_restore_after_exception_and_loopback_allowed(monkeypatch):
    original_class=socket.socket
    calls=[]
    class SimulatedSendmsg(original_class):
        def sendmsg(self, buffers, ancdata=(), flags=0, address=None):
            calls.append(address)
            return sum(len(x) for x in buffers)
    monkeypatch.setattr(socket,'socket',SimulatedSendmsg)
    original_method=SimulatedSendmsg.sendmsg
    with pytest.raises(RuntimeError,match='test exception'):
        with OfflineNetworkFence() as guard:
            target=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            try:
                assert target.sendmsg([b'local'],[],0,('127.0.0.1',9999))==5
                assert guard.clean
                raise RuntimeError('test exception')
            finally:target.close()
    assert calls==[('127.0.0.1',9999)]
    assert SimulatedSendmsg.sendmsg is original_method


def test_s08_real_parent_phase_swallowed_attempt_fails_entire_generated_gui_run(monkeypatch):
    """Inject in PARENT create_services, not the independent --serve child."""
    from uuid import uuid4
    import json
    from tools import check_stage5_s08_frames_next_save as s08
    original=s08.create_services
    injected=[0]
    def attempted_but_caught(*args,**kwargs):
        injected[0]+=1
        try:socket.getaddrinfo('forbidden.invalid',443)
        except PermissionError:pass
        return original(*args,**kwargs)
    monkeypatch.setattr(s08,'create_services',attempted_but_caught)
    out=s08.ROOT/'artifacts'/('p3-377-s08-caught-parent-negative-'+uuid4().hex)
    code=s08.run(out)
    report=json.loads((out/'result.json').read_text(encoding='utf8'))
    assert injected==[1]
    assert code==1
    assert report['tested'] is True
    assert report['parent_outbound_attempts']==1
    assert report['parent_outbound_attempt_routes']==['socket.getaddrinfo']
    assert not report['success']
    assert report['app']['normal_exit'] is True
    assert report['app']['tree']['remaining_count']==0
