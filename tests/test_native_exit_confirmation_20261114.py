import sys
from threading import Event, Thread
from types import SimpleNamespace
from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit

def test_confirmation_serialized_and_cancel_retry(monkeypatch):
    entered=Event(); release=Event(); closed=Event(); calls=[]
    class Window:
        uid='master'; confirm_close=True
        events=SimpleNamespace(closed=closed)
        def show(self):calls.append('show')
        def restore(self):calls.append('restore')
        def create_confirmation_dialog(self,*args):
            calls.append('confirm'); entered.set(); release.wait(2); return False
        def destroy(self):
            raise AssertionError('cancel must not destroy')
    w=Window(); monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[w])); token='a'*64
    install_owned_tray_exit(token); request=w._pixivvault_request_tray_exit
    t=Thread(target=request,args=(token,));t.start();assert entered.wait(1)
    request(token);assert calls.count('confirm')==1
    release.set();t.join();request(token);assert calls.count('confirm')==2 and not closed.is_set()

def test_child_result_distinguishes_cancel_failure_closed(monkeypatch):
    from multiprocessing import Pipe
    receiver,sender=Pipe(False); closed=Event()
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=closed);answer=False;fail=False
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*a):return self.answer
        def destroy(self):
            if self.fail:raise OSError('synthetic')
            closed.set()
    w=Window();monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[w]));token='b'*64
    install_owned_tray_exit(token,sender);assert receiver.recv()=='ready';request=w._pixivvault_request_tray_exit
    request(token);assert receiver.recv()=='cancelled' and not closed.is_set()
    w.answer=True;w.fail=True;request(token);assert receiver.recv()=='failed' and not closed.is_set()
    w.fail=False;request(token);assert receiver.recv()=='closed' and closed.is_set()
    receiver.close();sender.close()


def test_retry_can_begin_as_soon_as_cancel_is_reported(monkeypatch):
    closed=Event(); calls=[]
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=closed)
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*args):calls.append('confirm');return False
    class Sender:
        def send(self,outcome):
            if len(calls)==1:window._pixivvault_request_tray_exit(token)
    window=Window(); token='c'*64
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[window]))
    install_owned_tray_exit(token,Sender());window._pixivvault_request_tray_exit(token)
    assert calls==['confirm','confirm']

def spawn_fake_native(sender):
    from types import SimpleNamespace
    from threading import Event
    import sys
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=Event())
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*a):return False
    window=Window();sys.modules['webview']=SimpleNamespace(windows=[window])
    from pixivvault_nicegui.native_tray_exit import install_owned_tray_exit
    install_owned_tray_exit('d'*64,sender)
    window._pixivvault_request_tray_exit('d'*64)

def test_spawn_pipe_and_nicegui_forwarding():
    from multiprocessing import get_context
    from nicegui.native.native_mode import _split_picklable
    ctx=get_context('spawn');receiver,sender=ctx.Pipe(False)
    accepted,dropped=_split_picklable({'func':install_owned_tray_exit,'args':('d'*64,sender)})
    assert not dropped and set(accepted)=={'func','args'}
    process=ctx.Process(target=spawn_fake_native,args=(sender,));process.start()
    assert receiver.poll(15) and receiver.recv()=='ready'
    assert receiver.poll(15) and receiver.recv()=='cancelled'
    process.join(15);assert process.exitcode==0
    receiver.close();sender.close()

def test_installed_executor_routes_without_shared_response(monkeypatch):
    from multiprocessing import Pipe
    from queue import Queue, Empty
    from nicegui.native.native_mode import _start_window_method_executor
    receiver,sender=Pipe(False); closed=Event()
    class Window:
        uid='master';confirm_close=True;events=SimpleNamespace(closed=closed)
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*args):return False
    window=Window();token='e'*64
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(windows=[window]))
    install_owned_tray_exit(token,sender)
    assert receiver.recv()=='ready'
    commands,responses=Queue(),Queue()
    _start_window_method_executor(window,commands,responses,closed)
    commands.put(('_pixivvault_request_tray_exit',(token,True),{}))
    try:
        assert receiver.poll(3) and receiver.recv()=='cancelled'
        try:responses.get_nowait();assert False, 'shared response queue must remain empty'
        except Empty:pass
    finally:
        closed.set();receiver.close();sender.close()

def test_real_nicegui_open_window_wires_hook_and_executor(monkeypatch):
    from multiprocessing import Pipe
    from queue import Queue, Empty
    from nicegui.native import native_mode
    receiver,sender=Pipe(False); commands,responses=Queue(),Queue()
    class Hook:
        def __iadd__(self,other): return self
    class Window:
        uid='master';confirm_close=True
        events=SimpleNamespace(closed=Hook())
        def show(self):pass
        def restore(self):pass
        def create_confirmation_dialog(self,*args):return False
    window=Window(); closed=Event()
    # Forward the closed callback used by the real executor.
    class ClosedHook:
        def __init__(self):self.handlers=[]
        def __iadd__(self,handler):self.handlers.append(handler);return self
        def is_set(self):return closed.is_set()
        def fire(self):
            closed.set()
            for handler in self.handlers:handler()
    window.events.closed=ClosedHook()
    fake=SimpleNamespace(windows=[window],settings=SimpleNamespace(update=lambda **kw:None),
        create_window=lambda **kw:window)
    token='f'*64
    def start(**args):
        args['func'](*args['args'])
        assert receiver.poll(3) and receiver.recv()=='ready'
        commands.put(('_pixivvault_request_tray_exit',(token,True),{}))
        assert receiver.poll(3) and receiver.recv()=='cancelled'
        assert responses.empty()
        window.events.closed.fire()
    fake.start=start
    monkeypatch.setitem(sys.modules,'webview',fake)
    monkeypatch.setattr(native_mode,'webview',fake)
    monkeypatch.setattr(native_mode.helpers,'is_port_open',lambda *args:True)
    monkeypatch.setattr(native_mode,'_bind_pywebview_events',lambda *args:None)
    monkeypatch.setattr(native_mode,'_warn_if_esm_unsupported',lambda *args:None)
    monkeypatch.setattr(native_mode.core.app.native,'start_args',{})
    monkeypatch.setattr(native_mode.core.app.native,'window_args',{})
    monkeypatch.setattr(native_mode.core.app.native,'settings',{})
    native_mode._open_window('http','127.0.0.1',12345,'synthetic',800,600,False,False,
        commands,responses,sender,start_args={'func':install_owned_tray_exit,'args':(token,sender)})
    receiver.close();sender.close()
