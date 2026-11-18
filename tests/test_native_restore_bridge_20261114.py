"""Synthetic N04 restore over the real tray bridge and NiceGUI executor."""
import sys,time
import pytest
from multiprocessing.reduction import ForkingPickler
from queue import Queue
from threading import Event
from types import SimpleNamespace
from nicegui import core
from nicegui.native import native,native_mode
from pixivvault_nicegui.native_lifecycle import NativeTrayBridge
from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit

@pytest.fixture(autouse=True)
def mock_native_prompt(monkeypatch):
    monkeypatch.setattr('pixivvault_nicegui.native_tray_exit._confirm_exit',
                        lambda window, active: window.create_confirmation_dialog('終了確認', 'synthetic'))

def test_minimize_restore_preserves_content_and_cancel_retry(monkeypatch):
    class Native:
        def __init__(self):self.start_args={};self.events={};self.main_window=None
        def on(self,name,callback):self.events[name]=callback
    closed=Event();stopped=[];marker=object()
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=closed)
        def __init__(self):self.hidden=False;self.minimized=False;self.content=marker;self.restore_count=0
        def hide(self):self.hidden=True;self.minimized=True
        def show(self):self.hidden=False
        def restore(self):self.minimized=False;self.restore_count+=1
        def create_confirmation_dialog(self,*args):return False
    window=Window();app=SimpleNamespace(native=Native());app.native.main_window=window
    monkeypatch.setattr(core,'app',app);monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[window]))
    commands,responses=Queue(),Queue();monkeypatch.setattr(native,'method_queue',commands)
    preferences=SimpleNamespace(get_setting=lambda *args:'1')
    bridge=NativeTrayBridge(app,preferences,lambda *args:SimpleNamespace(stop=lambda:stopped.append(True)))
    bridge.shown();remote=ForkingPickler.loads(ForkingPickler.dumps(bridge._sender))
    install_owned_tray_exit(bridge._exit_token,remote)
    for _ in range(2000):
        if bridge._child_ready:break
        time.sleep(.001)
    assert bridge._child_ready
    native_mode._start_window_method_executor(window,commands,responses,closed)
    try:
        app.native.events['minimized']()
        assert window.hidden and window.minimized and window.content is marker
        bridge.controller.restore()
        for _ in range(2000):
            if window.restore_count:break
            time.sleep(.001)
        assert not window.hidden and not window.minimized and window.content is marker
        assert bridge.controller.exit_from_tray()
        for _ in range(2000):
            if bridge.last_exit_result=='cancelled':break
            time.sleep(.001)
        assert bridge.last_exit_result=='cancelled' and not bridge.controller.exit_pending
        assert window.content is marker and not stopped and responses.empty()
        assert bridge.controller.exit_from_tray()
        for _ in range(2000):
            if not bridge.controller.exit_pending:break
            time.sleep(.001)
        assert not bridge.controller.exit_pending and window.content is marker and not stopped
    finally:
        closed.set();app.native.events['closed']();remote.close()
    assert stopped==[True]
