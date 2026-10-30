"""Original enable_notifications gate with an injectable OS-toast boundary.

No notification is sent at import/construction, and missing native dependency
never triggers installation or a substitute subprocess.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib.util import find_spec
from threading import RLock


class NotificationUnavailable(RuntimeError):
    pass


def windows_notifications_available():
    return find_spec('win11toast') is not None


class WindowsToastBackend:
    def send(self,title,body):
        try:
            from win11toast import toast
        except ImportError:
            raise NotificationUnavailable('Windows通知の実行依存がありません。') from None
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

    def notify(self,title,body,*,event_id=None):
        if type(title) is not str or type(body) is not str:
            raise ValueError('notification requires text')
        if event_id is not None and (type(event_id) is not str or not event_id):
            raise ValueError('notification event identity required')
        with self._lock:
            if self._closed or self.backend is None or self.preferences.get_setting('enable_notifications','1')!='1':return None
            if event_id is not None and event_id in self._events:return None
            if event_id is not None:self._events.add(event_id)
            return self._executor.submit(self._send,title,body)

    def _send(self,title,body):
        with self._lock:
            if self._closed or self.preferences.get_setting('enable_notifications','1')!='1':
                return NotificationResult('suppressed')
        try:
            self.backend.send(title,body)
            return NotificationResult('sent')
        except Exception:
            return NotificationResult('failed')

    def close(self):
        with self._lock:self._closed=True
        self._executor.shutdown(wait=False,cancel_futures=True)
