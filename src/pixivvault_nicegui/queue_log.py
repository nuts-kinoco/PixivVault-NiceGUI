"""Bounded, in-memory queue messages shared across UI clients and workers."""
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from datetime import datetime
import hmac
import secrets
from threading import Lock
import time


if TYPE_CHECKING:
    from .managed_scheduler import ScheduleOwner


@dataclass(frozen=True, slots=True)
class QueueLogEntry:
    timestamp: str
    message: str
    level: str
    owner: 'ScheduleOwner | None' = field(default=None, repr=False)


class QueueLog:
    def __init__(self, limit=1000):
        self._entries = deque(maxlen=limit)
        self._lock = Lock()
        self._cookie_key = secrets.token_bytes(32)
        self._last_cookie_digest = None
        self._last_cookie_notice = None

    def append(self, message, *, level='normal'):
        self._append(message, level, None)

    def append_owned(self, message, *, owner: 'ScheduleOwner', level='normal'):
        from .managed_scheduler import ScheduleOwner
        if type(owner) is not ScheduleOwner:
            raise TypeError('schedule log requires an owner')
        self._append(message, level, owner)

    def _append(self, message, level, owner):
        entry = QueueLogEntry(datetime.now().strftime('%y%m%d %H:%M:%S'), message, level, owner)
        with self._lock:
            self._entries.append(entry)

    def snapshot(self):
        with self._lock:
            return tuple(self._entries)

    def cookie_sync_notice(self, rows):
        # Retain only a per-process keyed digest, never credential values.
        ordered = sorted(rows, key=lambda row: (row.get('domain', ''), row.get('path', '/'), row.get('name', '')))
        values = [row['value'] for row in ordered if row.get('name') == 'PHPSESSID']
        digest = hmac.digest(self._cookie_key, values[-1].encode('utf-8'), 'sha256') if values else None
        now = time.monotonic()
        with self._lock:
            if (digest == self._last_cookie_digest and self._last_cookie_notice is not None
                    and now - self._last_cookie_notice < 300):
                return
            self._last_cookie_digest = digest
            self._last_cookie_notice = now
            self._entries.append(QueueLogEntry(datetime.now().strftime('%y%m%d %H:%M:%S'),
                '[Cookie Auto-Sync] 拡張機能から最新のCookieを自動同期しました', 'success'))
