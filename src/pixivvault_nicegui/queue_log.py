"""Bounded, in-memory queue messages shared across UI clients and workers."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import hmac
import secrets
from threading import Lock
import time


@dataclass(frozen=True, slots=True)
class QueueLogEntry:
    timestamp: str
    message: str
    level: str


class QueueLog:
    def __init__(self, limit=1000):
        self._entries = deque(maxlen=limit)
        self._lock = Lock()
        self._cookie_key = secrets.token_bytes(32)
        self._last_cookie_digest = None
        self._last_cookie_notice = None

    def append(self, message, *, level='normal'):
        entry = QueueLogEntry(datetime.now().strftime('%y%m%d %H:%M:%S'), message, level)
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
