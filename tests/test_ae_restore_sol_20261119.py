import sys
from threading import Event as ThreadEvent
from types import SimpleNamespace as NS

from test_native_webview_visibility_20261114 import Event, form


def test_form_restore_is_synchronous_dpi_scaled_and_only_while_visible():
    from pixivvault_nicegui.native_restore_size import bind_form_restore_size
    f=form(); f._scale=1.25; sizes=[]
    class SizeForm:
        def __init__(self, wrapped): self.__dict__['_wrapped']=wrapped
        def __getattr__(self, name): return getattr(self._wrapped,name)
        def __setattr__(self, name, value):
            if name=='Size':
                sizes.append((value,self._wrapped.WindowState,self._wrapped.Visible))
                self._wrapped.Resize.fire()
            else: setattr(self._wrapped,name,value)
    f=SizeForm(f)
    bind_form_restore_size(f,'normal','minimized','maximized',lambda w,h:(w,h))
    f.WindowState='minimized'; f.Resize.fire()
    assert not sizes
    f.WindowState='normal'; f.Resize.fire()
    assert sizes == [((1600,900),'normal',True)]
    f.WindowState='maximized'; f.Resize.fire()
    f.WindowState='minimized'; f.Resize.fire()
    f.Visible=False; f.WindowState='normal'; f.Resize.fire()
    assert len(sizes)==1
    f.Visible=True; f.WindowState='maximized'; f.Resize.fire()
    f.WindowState='normal'; f.Resize.fire()
    assert sizes[-1] == ((1600,900),'normal',True)
    f.FormClosed.fire(); assert not f.Resize.handlers and not f.FormClosed.handlers


def test_tray_unavailable_still_installs_owned_child_hook(monkeypatch):
    from pixivvault_nicegui import native_lifecycle, native_tray_exit
    monkeypatch.setattr(native_lifecycle, 'find_spec', lambda name: None)
    native=NS(start_args={}, on=lambda *args:None)
    bridge=native_lifecycle.bind_native_tray(NS(native=native),None)
    try:
        assert native.start_args['func'] is native_tray_exit.install_owned_tray_exit
        assert bridge is not None and not bridge.available
    finally:
        bridge._on_closed()


def test_tray_child_installs_restore_hook_and_existing_visibility(monkeypatch):
    from pixivvault_nicegui import native_tray_exit, native_restore_size, native_webview_visibility
    calls=[]
    monkeypatch.setattr(native_restore_size,'install_window_restore_size',lambda w:calls.append(('size',w)))
    monkeypatch.setattr(native_webview_visibility,'install_owned_visibility',lambda w:calls.append(('visibility',w)))
    window=NS(uid='master',events=NS(closed=ThreadEvent()))
    monkeypatch.setitem(sys.modules,'webview',NS(windows=[window]))
    native_tray_exit.install_owned_tray_exit('a'*64)
    assert calls == [('visibility',window),('size',window)]
    assert hasattr(window,'_pixivvault_request_tray_exit') and hasattr(window,'_pixivvault_request_tray_restore')


def test_child_form_bind_before_show_and_after_creation_on_ui_thread(monkeypatch):
    from pixivvault_nicegui.native_restore_size import install_window_restore_size
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules,'System.Drawing',NS(Size=lambda w,h:(w,h)))
    monkeypatch.setitem(sys.modules,'System.Windows.Forms',NS(FormWindowState=NS(Normal='normal',Minimized='minimized',Maximized='maximized')))
    monkeypatch.setitem(sys.modules,'System',NS(Action=lambda fn:fn))
    f=form(); f._scale=1.0; calls=[]
    f.Invoke=lambda callback:(calls.append('UI'),callback())
    w=NS(uid='master',gui=NS(renderer='edgechromium'),native=f,events=NS(before_show=Event()))
    install_window_restore_size(w)
    assert calls == ['UI'] and len(f.Resize.handlers)==1
    w.events.before_show.fire()
    assert len(f.Resize.handlers)==1
    f.WindowState='maximized'; f.Resize.fire()
    f.WindowState='normal'; f.Resize.fire()
    assert f.Size == (1280,720)
    f.FormClosed.fire(); assert not f.Resize.handlers
