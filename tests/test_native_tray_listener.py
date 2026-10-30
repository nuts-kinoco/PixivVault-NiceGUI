"""Win32 listener ownership uses the listener-only TCP table.

Listener-only results can use dwState 0, 2 or 6 on the same Windows host.
Filtering by dwState is wrong; class-3 and exact address/PID are authoritative.
"""

import os
import socket
import sys
from types import SimpleNamespace

import pytest

from tools.check_native_tray_window import loopback_listener, _select_loopback_listener
from tools.native_tray_ui import TrayProbeError


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Win32 TCP ownership')


def test_ipv4_loopback_listener_is_exact_current_pid_and_disappears_after_close():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert loopback_listener(port) == os.getpid()
    assert loopback_listener(port) is None


def test_wildcard_listener_is_not_accepted_as_exclusive_loopback():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('0.0.0.0', 0))
        listener.listen(1)
        with pytest.raises(TrayProbeError, match='listener_is_not_exclusive_ipv4_loopback'):
            loopback_listener(listener.getsockname()[1])


@pytest.mark.parametrize('state', (0, 2, 6))
def test_listener_only_states_must_not_reject_valid_exact_loopback(state):
    port=50367
    row=SimpleNamespace(state=state,local_addr=0x0100007f,
                        local_port=socket.htons(port),pid=20704)
    assert _select_loopback_listener((row,),port)==20704
    assert _select_loopback_listener((row,),port+1) is None


def test_unique_pid_and_loopback_restrictions_remain_after_state_fix():
    port=50367
    def row(pid,address):
        return SimpleNamespace(state=6,local_addr=address,
                               local_port=socket.htons(port),pid=pid)
    with pytest.raises(TrayProbeError,match='listener_is_not_exclusive_ipv4_loopback'):
        _select_loopback_listener((row(20704,0),),port)
    with pytest.raises(TrayProbeError,match='loopback_listener_ambiguous'):
        _select_loopback_listener((row(20704,0x0100007f),
                                   row(31415,0x0100007f)),port)
