from types import SimpleNamespace
from multiprocessing.reduction import ForkingPickler
from threading import Event
import time
from pixivvault_nicegui.native_lifecycle import NativeTrayBridge

def test_parent_cancel_failure_closed_and_unknown(monkeypatch):
    class Native:
        def __init__(self):self.start_args={};self.main_window=object();self.events={}
        def on(self,name,callback):self.events[name]=callback
    class Factory:
        def __call__(self,*args):return SimpleNamespace(stop=lambda:stopped.append(True))
    stopped=[]; app=SimpleNamespace(native=Native()); sent=[]
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',lambda app,token,confirm:sent.append(confirm) or True)
    b=NativeTrayBridge(app,SimpleNamespace(get_setting=lambda *x:'1'),Factory(),SimpleNamespace(jobs=None));b.shown()
    remote=ForkingPickler.loads(ForkingPickler.dumps(b._sender))
    remote.send('ready')
    for _ in range(1000):
        if b._child_ready:break
        time.sleep(.001)
    assert b.controller.exit_from_tray() and sent==[True]
    time.sleep(5.1)
    assert not b.controller.exit_from_tray() and b.controller.exit_pending
    remote.send('cancelled')
    for _ in range(1000):
        if not b.controller.exit_pending:break
        time.sleep(.001)
    assert b.last_exit_result=='cancelled' and not stopped
    assert b.controller.exit_from_tray()
    remote.send('failed')
    for _ in range(1000):
        if not b.controller.exit_pending:break
        time.sleep(.001)
    assert b.last_exit_result=='failed' and not stopped
    b.close(); assert not stopped
    b.app.native.events['closed'](); remote.close(); assert stopped==[True]

def test_active_job_cancel_and_closed_event(monkeypatch):
    class Native:
        def __init__(self):self.start_args={};self.main_window=object();self.events={}
        def on(self,name,callback):self.events[name]=callback
    stopped=[]; job=SimpleNamespace(state='running'); jobs=SimpleNamespace(faulted=False,snapshot=lambda:(job,))
    b=NativeTrayBridge(SimpleNamespace(native=Native()),SimpleNamespace(get_setting=lambda *x:'1'),lambda *a:SimpleNamespace(stop=lambda:stopped.append(True)),SimpleNamespace(jobs=jobs));b.shown()
    remote=ForkingPickler.loads(ForkingPickler.dumps(b._sender))
    remote.send('ready')
    for _ in range(1000):
        if b._child_ready:break
        time.sleep(.001)
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',lambda a,t,c:c is True)
    assert b.controller.exit_from_tray();remote.send('cancelled')
    for _ in range(1000):
        if not b.controller.exit_pending:break
        time.sleep(.001)
    assert job.state=='running' and not stopped
    assert b.controller.exit_from_tray();remote.send('closed')
    for _ in range(1000):
        if b.last_exit_result=='closed':break
        time.sleep(.001)
    assert not stopped and b.controller.exit_pending
    b.app.native.events['closed'](); remote.close(); assert stopped==[True]


def test_not_ready_rejects_without_cleanup(monkeypatch):
    class Native:
        def __init__(self):self.start_args={};self.main_window=object();self.events={}
        def on(self,name,callback):self.events[name]=callback
    app=SimpleNamespace(native=Native());stopped=[]; sent=[]
    b=NativeTrayBridge(app,SimpleNamespace(get_setting=lambda *x:'1'),lambda *a:SimpleNamespace(stop=lambda:stopped.append(True)));b.shown()
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',lambda *a:sent.append(a) or True)
    assert not b.controller.exit_from_tray() and not sent and not stopped
    assert b.exit_unconfirmed and not b.controller.exit_pending
    app.native.events['closed'](); assert stopped==[True]

def test_parent_to_real_executor_and_dedicated_pipe(monkeypatch):
    import sys
    from queue import Queue
    from nicegui import core
    from nicegui.native import native, native_mode
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    class Native:
        def __init__(self):self.start_args={};self.main_window=object();self.events={}
        def on(self,name,callback):self.events[name]=callback
    closed=Event(); stopped=[]; job=SimpleNamespace(state='running')
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=closed);result=False;failure=False
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*args):return self.result
        def destroy(self):
            if self.failure:raise OSError('synthetic')
            closed.set()
    window=Window();app=SimpleNamespace(native=Native())
    monkeypatch.setattr(core,'app',app);monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[window]))
    commands,responses=Queue(),Queue();monkeypatch.setattr(native,'method_queue',commands)
    b=NativeTrayBridge(app,SimpleNamespace(get_setting=lambda *a:'1'),lambda *a:SimpleNamespace(stop=lambda:stopped.append(True)),SimpleNamespace(jobs=SimpleNamespace(faulted=False,snapshot=lambda:(job,))))
    b.shown();remote=ForkingPickler.loads(ForkingPickler.dumps(b._sender));install_owned_tray_exit(b._exit_token,remote)
    for _ in range(1000):
        if b._child_ready:break
        time.sleep(.001)
    assert b._child_ready
    native_mode._start_window_method_executor(window,commands,responses,closed)
    try:
        assert b.controller.exit_from_tray()
        for _ in range(2000):
            if b.last_exit_result=='cancelled':break
            time.sleep(.001)
        assert b.last_exit_result=='cancelled' and not b.controller.exit_pending
        assert job.state=='running' and not stopped
        window.result=True;window.failure=True
        assert b.controller.exit_from_tray()
        for _ in range(2000):
            if b.last_exit_result=='failed':break
            time.sleep(.001)
        assert b.last_exit_result=='failed' and not b.controller.exit_pending and not stopped
        window.failure=False
        assert b.controller.exit_from_tray()
        for _ in range(2000):
            if b.last_exit_result=='closed':break
            time.sleep(.001)
        assert b.last_exit_result=='closed' and closed.is_set() and not stopped
        assert responses.empty()
        app.native.events['closed'](); assert stopped==[True]
    finally:
        closed.set()
        if not b.closed:app.native.events['closed']()
        remote.close()
