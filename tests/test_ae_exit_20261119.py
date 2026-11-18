import sys
from threading import Event
from types import SimpleNamespace as NS
from webview.event import Event as WebEvent


def test_window_close_is_vetoed_and_forwarded_to_parent(monkeypatch):
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    class Sender:
        def __init__(self): self.messages=[]
        def send(self, message): self.messages.append(message)
    window=NS(uid='master',events=NS(closed=Event()),confirm_close=True)
    window.events.closing=WebEvent(window,True)
    sender=Sender();monkeypatch.setitem(sys.modules,'webview',NS(windows=[window]))
    install_owned_tray_exit('a'*64,sender)
    assert window.events.closing.set() is True
    assert sender.messages == ['ready','close_request']


def test_original_yes_no_dialog_labels_and_message_for_owned_window(monkeypatch):
    from pixivvault_nicegui.native_tray_exit import _confirm_exit
    calls=[]
    class MessageBox:
        @staticmethod
        def Show(*args): calls.append(args); return 'yes'
    forms=NS(MessageBox=MessageBox,MessageBoxButtons=NS(YesNo='yesno'),
             MessageBoxIcon=NS(Question='question'),DialogResult=NS(Yes='yes',No='no'))
    monkeypatch.setitem(sys.modules,'System.Windows.Forms',forms)
    monkeypatch.setitem(sys.modules,'System',NS(Action=lambda f:f))
    window=NS(native=NS(Invoke=lambda func:func()))
    assert _confirm_exit(window,active=False) is True
    assert calls == [(window.native,'アプリを終了します。よろしいですか？','終了確認','yesno','question')]


def test_no_tray_still_installs_close_bridge(monkeypatch):
    from pixivvault_nicegui import native_lifecycle
    native=NS(start_args={},main_window=object(),on=lambda *args:None)
    monkeypatch.setattr(native_lifecycle,'find_spec',lambda _:None)
    bridge=native_lifecycle.bind_native_tray(NS(native=native),None,services=NS(jobs=None))
    try:
        assert bridge is not None and native.start_args['func'].__name__ == 'install_owned_tray_exit'
        assert not bridge.available
    finally:
        if bridge is not None:
            bridge._on_closed()


def test_x_close_uses_parent_idle_fence_cancel_then_yes(monkeypatch):
    import time
    from multiprocessing.reduction import ForkingPickler
    from queue import Queue
    from nicegui import core
    from nicegui.native import native, native_mode
    from pixivvault_nicegui import native_tray_exit
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.native_lifecycle import NativeTrayBridge
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    class Native:
        def __init__(self): self.start_args={}; self.events={}; self.main_window=object()
        def on(self, name, cb): self.events[name]=cb
    class Window:
        uid='master'; confirm_close=True
        def __init__(self):
            self.events=NS(closed=Event());self.events.closing=WebEvent(self,True);self.destroyed=0
        def show(self): pass
        def restore(self): pass
        def destroy(self):
            assert self.events.closing.set() is False
            self.destroyed+=1; self.events.closed.set()
    window=Window(); app=NS(native=Native()); jobs=JobService(lambda *_:'receipt')
    monkeypatch.setattr(core,'app',app)
    monkeypatch.setitem(sys.modules,'webview',NS(windows=[window]))
    commands=Queue(); monkeypatch.setattr(native,'method_queue',commands)
    responses=Queue(); calls=[]; answers=[False, True]
    def prompt(w, *, active):
        calls.append((active,jobs._native_exit_freeze)); return answers.pop(0)
    monkeypatch.setattr(native_tray_exit,'_confirm_exit',prompt)
    bridge=NativeTrayBridge(app,NS(get_setting=lambda *a:'1'),lambda *a:None,NS(jobs=jobs))
    remote=ForkingPickler.loads(ForkingPickler.dumps(bridge._sender));install_owned_tray_exit(bridge._exit_token,remote)
    native_mode._start_window_method_executor(window,commands,responses,window.events.closed)
    def until(predicate):
        end=time.monotonic()+5
        while not predicate() and time.monotonic()<end: time.sleep(.005)
        assert predicate()
    try:
        until(lambda:bridge._child_ready)
        assert window.events.closing.set() is True
        until(lambda:bridge.last_exit_result=='cancelled')
        assert calls==[(False,True)] and not jobs._native_exit_freeze and not window.destroyed
        assert window.events.closing.set() is True
        until(lambda:window.events.closed.is_set())
        assert calls==[(False,True),(False,True)] and window.destroyed==1
    finally:
        window.events.closed.set(); bridge._on_closed(); remote.close(); jobs.close()


def test_confirm_exit_actual_active_and_unknown_messages(monkeypatch):
    from pixivvault_nicegui.native_tray_exit import _confirm_exit
    calls=[]
    class MessageBox:
        @staticmethod
        def Show(*args): calls.append(args); return 'no'
    forms=NS(MessageBox=MessageBox,MessageBoxButtons=NS(YesNo='yesno'),
             MessageBoxIcon=NS(Question='question'),DialogResult=NS(Yes='yes',No='no'))
    monkeypatch.setitem(sys.modules,'System.Windows.Forms',forms)
    monkeypatch.setitem(sys.modules,'System',NS(Action=lambda f:f))
    window=NS(native=NS(Invoke=lambda func:func()))
    assert _confirm_exit(window,active=True) is False
    assert _confirm_exit(window,active=None) is False
    assert calls[0][1] == 'ダウンロードが実行中です。アプリを終了しますか？'
    assert calls[1][1] == '処理中または状態を確認できません。終了しますか？'


def test_close_request_channel_failure_never_allows_close(monkeypatch):
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    class Broken:
        def send(self, value):
            if value == 'close_request': raise ValueError('closed pipe')
    window=NS(uid='master',events=NS(closed=Event()),confirm_close=True)
    window.events.closing=WebEvent(window,True)
    monkeypatch.setitem(sys.modules,'webview',NS(windows=[window]))
    install_owned_tray_exit('b'*64,Broken())
    assert window.events.closing.set() is True


def test_window_activity_distinguishes_running_from_unknown():
    import time
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.native_lifecycle import NativeTrayBridge
    started=Event(); release=Event()
    def runner(job,context):
        started.set(); release.wait(5); context.checkpoint(); return 'receipt'
    jobs=JobService(runner)
    native=NS(start_args={},main_window=object(),on=lambda *args:None)
    bridge=NativeTrayBridge(NS(native=native),NS(get_setting=lambda *a:'1'),lambda *a:None,NS(jobs=jobs))
    try:
        jobs.enqueue('synthetic','1'); assert started.wait(5)
        assert bridge._window_activity() == 'active'
        bridge.services.jobs=None
        assert bridge._window_activity() == 'unknown'
    finally:
        release.set(); jobs.close(); bridge._on_closed()
