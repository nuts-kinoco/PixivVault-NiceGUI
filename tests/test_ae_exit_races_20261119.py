import time
from multiprocessing.reduction import ForkingPickler
from queue import Queue
from threading import Event
from types import SimpleNamespace as NS

from nicegui import core
from nicegui.native import native, native_mode
from webview.event import Event as WebEvent


def build(monkeypatch, *, block_restore=False):
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.native_lifecycle import NativeTrayBridge
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    class Native:
        def __init__(self): self.start_args={};self.events={};self.main_window=object()
        def on(self, name, fn): self.events[name]=fn
    entered=Event(); release=Event()
    class Window:
        uid='master';confirm_close=True
        def __init__(self):
            self.events=NS(closed=Event());self.events.closing=WebEvent(self,True)
            self.shows=0;self.restores=0;self.maximized=True
        def show(self):
            self.shows+=1
            if block_restore:
                entered.set(); assert release.wait(5)
        def restore(self): self.restores+=1;self.maximized=False
        def destroy(self):
            assert not self.events.closing.set();self.events.closed.set()
    import sys
    window=Window(); app=NS(native=Native()); jobs=JobService(lambda *_:'receipt')
    monkeypatch.setattr(core,'app',app)
    monkeypatch.setitem(sys.modules,'webview',NS(windows=[window]))
    monkeypatch.setattr('pixivvault_nicegui.native_tray_exit._confirm_exit',lambda *a,**k:False)
    commands=Queue();responses=Queue();monkeypatch.setattr(native,'method_queue',commands)
    bridge=NativeTrayBridge(app,NS(get_setting=lambda *a:'1'),lambda *a:None,NS(jobs=jobs))
    remote=ForkingPickler.loads(ForkingPickler.dumps(bridge._sender))
    install_owned_tray_exit(bridge._exit_token,remote)
    native_mode._start_window_method_executor(window,commands,responses,window.events.closed)
    return window,bridge,jobs,remote,entered,release


def until(predicate):
    deadline=time.monotonic()+3
    while not predicate() and time.monotonic()<deadline:time.sleep(.005)
    assert predicate()


def test_restore_busy_close_releases_parent_fence_and_can_retry(monkeypatch):
    window,bridge,jobs,remote,entered,release=build(monkeypatch,block_restore=True)
    try:
        until(lambda:bridge._child_ready)
        assert bridge.restore(); assert entered.wait(3)
        assert window.events.closing.set()
        until(lambda:bridge.last_exit_result == 'failed')
        assert not jobs._native_exit_freeze and not bridge.controller.exit_pending
        release.set()
        until(lambda:window.restores == 1)
        time.sleep(.05)  # restore returns and releases the child gate
        assert window.events.closing.set()
        until(lambda:bridge.last_exit_result == 'cancelled')
        assert not jobs._native_exit_freeze and not bridge.controller.exit_pending
    finally:
        release.set(); window.events.closed.set(); bridge._on_closed();remote.close();jobs.close()


def test_x_cancel_preserves_maximized_state_without_show_or_restore(monkeypatch):
    window,bridge,jobs,remote,entered,release=build(monkeypatch)
    try:
        until(lambda:bridge._child_ready)
        assert window.events.closing.set()
        until(lambda:bridge.last_exit_result == 'cancelled')
        assert window.maximized and window.shows==0 and window.restores==0
        assert not jobs._native_exit_freeze
    finally:
        release.set();window.events.closed.set();bridge._on_closed();remote.close();jobs.close()
