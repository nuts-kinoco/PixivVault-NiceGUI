"""Bridge the owned NiceGUI native window to the original optional tray.

The x button keeps pywebview's confirmation. Tray exit sends a command to the owned child and waits for its real closed event before stopping
the tray. No optimistic app.shutdown call discards the recovery route.
"""
from importlib.util import find_spec
from secrets import token_hex
from threading import RLock, Thread
from multiprocessing import Pipe
from contextlib import ExitStack

from .desktop_integration import TrayController, PystrayFactory
from .native_tray_exit import (
    configure_owned_tray_exit, release_owned_tray_exit, send_owned_tray_exit,
    send_owned_tray_restore,
)



class NativeTrayBridge:
    def __init__(self,app,preferences,factory,services=None):
        self.app=app;self.closed=False;self.services=services;self._native_closed=False
        self._receiver,self._sender=Pipe(duplex=False)
        self.last_exit_result=None;self._child_ready=False;self._idle_lease=()
        self._exit_token=token_hex(32)
        self._exit_lock=RLock()
        self.exit_unconfirmed=False
        self.restore_unconfirmed=False
        self.failed=not configure_owned_tray_exit(app.native,self._exit_token,self._sender)
        self.controller=TrayController(preferences,show=self.restore,hide=self.hide,
            request_shutdown=self.request_exit,factory=factory)
        Thread(target=self._receive_results,daemon=True).start()
        app.native.on('shown',self.shown)
        app.native.on('minimized',self.minimized)
        app.native.on('closed',self._on_closed)

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

    def _try_idle_lease(self):
        """Fence new queue admissions only if every known queue is idle."""
        from .job_service import JobService, STATES, UNFINISHED
        from .media_jobs import MediaJobs
        jobs=self.services.jobs if self.services is not None else None
        if isinstance(jobs, JobService):
            owners=(jobs,)
        elif isinstance(jobs, MediaJobs):
            owners=[]
            for owner in jobs.owners:
                if isinstance(owner, JobService):
                    owners.append(owner)
                else:
                    from .extension_application import ExtensionApplication
                    if not isinstance(owner, ExtensionApplication) or not isinstance(owner.jobs, JobService):
                        return ()
                    owners.append(owner.jobs)
            owners=tuple(owners)
        else:
            return ()
        if not owners or len({id(owner) for owner in owners}) != len(owners):return ()
        owners=tuple(sorted(owners,key=id))
        with ExitStack() as stack:
            for owner in owners:stack.enter_context(owner._condition)
            if any(owner._closing or owner._closed or owner._fatal or owner._native_exit_freeze or owner._active is not None
                   or any(job.state not in STATES or job.state in UNFINISHED for job in owner._jobs.values())
                   for owner in owners):
                return ()
            for owner in owners:owner._native_exit_freeze=True
        return owners

    @staticmethod
    def _release_idle_lease(owners):
        with ExitStack() as stack:
            for owner in owners:stack.enter_context(owner._condition)
            for owner in owners:
                owner._native_exit_freeze=False
                owner._condition.notify_all()

    def request_exit(self):
        with self._exit_lock:
            if self.closed or self.failed:return False
            if not self._child_ready:
                self.exit_unconfirmed=True
                return False
            self.exit_unconfirmed=False
            try:
                self._idle_lease=self._try_idle_lease()
                accepted=send_owned_tray_exit(self.app,self._exit_token,not bool(self._idle_lease)) is True
            except Exception:
                accepted=False
            if not accepted:
                self.exit_unconfirmed=True
                self._release_idle_lease(self._idle_lease);self._idle_lease=()
            return accepted

    def _receive_results(self):
        while True:
            try:result=self._receiver.recv()
            except (EOFError,OSError):
                with self._exit_lock:
                    if not self.closed and self.last_exit_result != 'closed':
                        # The native closed event uses a separate channel and
                        # may still be in flight. EOF cannot prove cancellation
                        # or a failed close: keep any pending admission fence.
                        self.failed=True;self.exit_unconfirmed=True
                        self.last_exit_result='unconfirmed'
                return
            if result not in ('ready','cancelled','failed','closed'):continue
            with self._exit_lock:
                if self.closed:return
                if result=='ready':
                    self._child_ready=True
                    # The child now owns a duplicated writer. Closing ours lets
                    # EOF reveal child/channel loss instead of waiting forever.
                    self._sender.close()
                    continue
                self.last_exit_result=result
                if result in ('cancelled','failed'):
                    self._release_idle_lease(self._idle_lease);self._idle_lease=()
                    self.exit_unconfirmed=result=='failed'
                    self.controller.exit_request_unconfirmed()

    def _on_closed(self,*_):
        with self._exit_lock:
            self._native_closed=True
        self.close()

    def close(self,*_):
        with self._exit_lock:
            if not self._native_closed:return
            if self.closed and self.controller.icon is None:return
            self.closed=True
            release_owned_tray_exit(self.app.native,self._exit_token,self._sender)
        self.controller.complete_close()
        self._sender.close()
        self._receiver.close()


class _DefaultTrayPreferences:
    """Use the existing default when no persistent preference store is bound."""
    def get_setting(self, key, default=None):
        return default


def bind_native_tray(app,preferences,*,factory=None,services=None):
    if preferences is None:
        preferences = _DefaultTrayPreferences()
    if factory is None:
        if find_spec('pystray') is None:return None
        factory=PystrayFactory()
    return NativeTrayBridge(app,preferences,factory,services)
