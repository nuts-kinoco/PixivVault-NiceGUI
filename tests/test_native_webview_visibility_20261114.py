import sys
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui.native_webview_visibility import bind_form_visibility, install_owned_visibility


class Event:
    def __init__(self): self.handlers = []
    def __iadd__(self, fn): self.handlers.append(fn); return self
    def __isub__(self, fn): self.handlers.remove(fn); return self
    def fire(self):
        for fn in tuple(self.handlers): fn()


def form():
    return NS(Visible=True, WindowState='normal', IsDisposed=False,
              IsHandleCreated=True, Resize=Event(), VisibleChanged=Event(),
              FormClosed=Event(), webview=NS(Visible=True, IsDisposed=False))


@pytest.mark.parametrize('restored', ['normal', 'maximized'])
def test_ordinary_and_tray_restore_keep_same_control_and_state(restored):
    f = form()
    control = f.webview
    # State represents the existing DOM/client; no reload API is supplied.
    control.state = {'draft': 'unsaved', 'jobs': ('done1', 'done2'), 'tray': False}
    state = control.state
    bind_form_visibility(f, 'minimized')
    for hide_to_tray in (False, True):
        f.WindowState = 'minimized'; f.Resize.fire()
        assert not control.Visible
        if hide_to_tray:
            f.Visible = False; f.VisibleChanged.fire()
            f.Visible = True; f.VisibleChanged.fire()
            assert not control.Visible  # Show must not reveal an iconic control.
        f.WindowState = restored; f.Resize.fire()
        assert control.Visible and f.webview is control and control.state is state
        assert state == {'draft': 'unsaved', 'jobs': ('done1', 'done2'), 'tray': False}
    f.FormClosed.fire()
    assert not f.Resize.handlers and not f.VisibleChanged.handlers and not f.FormClosed.handlers


def test_child_startup_binds_before_show_on_ui_thread_once(monkeypatch):
    f = form()
    window = NS(uid='master', gui=NS(renderer='edgechromium'), native=None,
                events=NS(before_show=Event()))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', NS(FormWindowState=NS(Minimized='minimized')))
    install_owned_visibility(window)
    assert len(window.events.before_show.handlers) == 1
    window.native = f
    window.events.before_show.fire()
    install_owned_visibility(window)
    window.events.before_show.fire()
    assert len(f.Resize.handlers) == 1
    f.WindowState = 'minimized'; f.Resize.fire()
    assert not f.webview.Visible
    f.WindowState = 'normal'; f.Resize.fire()
    assert f.webview.Visible


def test_hook_install_after_form_created_marshals_to_ui_thread(monkeypatch):
    f = form(); calls = []
    f.Invoke = lambda callback: (calls.append('UI'), callback())
    window = NS(uid='master', gui=NS(renderer='edgechromium'), native=f,
                events=NS(before_show=Event()))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'System', NS(Action=lambda fn: fn))
    monkeypatch.setitem(sys.modules, 'System.Windows.Forms', NS(FormWindowState=NS(Minimized='minimized')))
    install_owned_visibility(window)
    assert calls == ['UI'] and window._pixivvault_visibility_installed


def test_foreign_renderer_not_modified():
    window = NS(uid='master', gui=NS(renderer='other'))
    install_owned_visibility(window)
    assert not hasattr(window, '_pixivvault_visibility_installed')
