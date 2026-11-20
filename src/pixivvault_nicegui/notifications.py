"""Original enable_notifications gate with an injectable OS-toast boundary.

No notification is sent at import/construction, and missing native dependency
never triggers installation or a substitute subprocess.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module
from threading import RLock


class NotificationUnavailable(RuntimeError):
    pass


def windows_notifications_available():
    # A discoverable win11toast module may still be unusable when a WinRT
    # dependency is missing, including in a partially assembled build.
    try:
        return callable(getattr(import_module('win11toast'), 'toast', None))
    except (ImportError, OSError):
        return False


class NotificationSuppressed(RuntimeError):
    pass


def _authorize_delivery(authorize):
    if authorize is not None:
        try:
            authorize()
        except Exception:
            raise NotificationSuppressed() from None


class WindowsToastBackend:
    def send(self, title, body):
        self.send_authorized(title, body, authorize=None)

    def send_authorized(self, title, body, *, authorize):
        try:
            from win11toast import toast
        except ImportError:
            raise NotificationUnavailable('Windows通知の実行依存がありません。') from None
        _authorize_delivery(authorize)
        toast(title,body,app_id='PixivVault')


@dataclass(frozen=True)
class NotificationResult:
    status: str


class NotificationDispatcher:
    """Caller supplies original adopted events; polling must use stable event IDs."""
    def __init__(self,preferences,*,backend=None):
        self.preferences=preferences;self.backend=backend
        self._lock=RLock();self._closed=False;self._events=set()
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pixivvault-notification')

    @property
    def available(self):
        return self.backend is not None and not self._closed

    @property
    def supports_authorized_delivery(self):
        return callable(getattr(self.backend, 'send_authorized', None))

    def notify(self,title,body,*,event_id=None,authorize=None):
        """Queue a toast, optionally checking its authority in the send worker."""
        if type(title) is not str or type(body) is not str:
            raise ValueError('notification requires text')
        if event_id is not None and (type(event_id) is not str or not event_id):
            raise ValueError('notification event identity required')
        with self._lock:
            if self._closed or self.backend is None or self.preferences.get_setting('enable_notifications','1')!='1':return None
            if event_id is not None and event_id in self._events:return None
            if event_id is not None:self._events.add(event_id)
            return self._executor.submit(self._send,title,body,authorize)

    def _send(self,title,body,authorize):
        with self._lock:
            if self._closed or self.preferences.get_setting('enable_notifications','1')!='1':
                return NotificationResult('suppressed')
        try:
            # Check before backend setup, and pass the same authority to the
            # backend for its last check after setup and before delivery.
            _authorize_delivery(authorize)
            if authorize is None:
                self.backend.send(title, body)
            else:
                send = getattr(self.backend, 'send_authorized', None)
                if not callable(send):
                    return NotificationResult('suppressed')
                send(title, body, authorize=authorize)
            return NotificationResult('sent')
        except NotificationSuppressed:
            return NotificationResult('suppressed')
        except Exception:
            return NotificationResult('failed')

    def close(self):
        with self._lock:self._closed=True
        self._executor.shutdown(wait=False,cancel_futures=True)
