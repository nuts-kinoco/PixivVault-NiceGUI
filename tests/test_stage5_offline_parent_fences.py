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
