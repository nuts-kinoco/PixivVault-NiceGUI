"""Bridge the owned NiceGUI native window to the original optional tray.

The x button keeps pywebview's confirmation. Tray exit uses a bounded, one-way
command to the owned child and waits for its real closed event before stopping
the tray. No optimistic app.shutdown call discards the recovery route.
"""
from importlib.util import find_spec
from secrets import token_hex
from threading import RLock, Timer

from .desktop_integration import TrayController, PystrayFactory
from .native_tray_exit import (
    configure_owned_tray_exit, release_owned_tray_exit, send_owned_tray_exit,
    send_owned_tray_restore,
)

TRAY_EXIT_ACK_SECONDS = 5.0


class NativeTrayBridge:
    def __init__(self,app,preferences,factory):
        self.app=app;self.closed=False
        self._exit_token=token_hex(32)
        self._exit_lock=RLock()
        self._exit_timer=None
        self._exit_generation=0
        self.exit_unconfirmed=False
        self.restore_unconfirmed=False
        self.failed=not configure_owned_tray_exit(app.native,self._exit_token)
        self.controller=TrayController(preferences,show=self.restore,hide=self.hide,
            request_shutdown=self.request_exit,factory=factory)
        app.native.on('shown',self.shown)
        app.native.on('minimized',self.minimized)
        app.native.on('closed',self.close)

    @property
    def available(self):
        return not self.closed and self.controller.icon is not None

    def shown(self,*_):
        if self.closed or self.failed:return
        try:self.controller.start()
        except Exception:self.failed=True

    def minimized(self,*_):
        if self.closed:return
        self.controller.minimize()

    def restore(self):
        window=self.app.native.main_window
        if not self.closed and window is not None:
            try:
                accepted=send_owned_tray_restore(self.app,self._exit_token) is True
            except Exception:
                accepted=False
            self.restore_unconfirmed=not accepted
            return accepted
        return False

    def hide(self):
        window=self.app.native.main_window
        if not self.closed and window is not None:window.hide()

    def request_exit(self):
        with self._exit_lock:
            if self.closed or self.failed:return False
            self._exit_generation+=1
            generation=self._exit_generation
            self.exit_unconfirmed=False
            try:
                accepted=send_owned_tray_exit(self.app,self._exit_token) is True
            except Exception:
                accepted=False
            if not accepted:
                self.exit_unconfirmed=True
                return False
            if self._exit_timer is not None:self._exit_timer.cancel()
            timer=Timer(TRAY_EXIT_ACK_SECONDS,self._exit_not_observed,args=(generation,))
            timer.daemon=True
            self._exit_timer=timer
            timer.start()
            return True

    def _exit_not_observed(self,generation):
        with self._exit_lock:
            if self.closed or generation!=self._exit_generation:return
            self._exit_timer=None
            self.exit_unconfirmed=True
            self.controller.exit_request_unconfirmed()

    def close(self,*_):
        with self._exit_lock:
            if self.closed and self.controller.icon is None:return
            self.closed=True
            self._exit_generation+=1
            if self._exit_timer is not None:self._exit_timer.cancel()
            self._exit_timer=None
            release_owned_tray_exit(self.app.native,self._exit_token)
        self.controller.complete_close()


def bind_native_tray(app,preferences,*,factory=None):
    if preferences is None:return None
    if factory is None:
        if find_spec('pystray') is None:return None
        factory=PystrayFactory()
    return NativeTrayBridge(app,preferences,factory)
