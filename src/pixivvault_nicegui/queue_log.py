"""Bounded, in-memory queue messages shared across UI clients and workers."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock


@dataclass(frozen=True, slots=True)
class QueueLogEntry:
    timestamp: str
    message: str
    level: str


class QueueLog:
    def __init__(self, limit=1000):
        self._entries = deque(maxlen=limit)
        self._lock = Lock()

    def append(self, message, *, level='normal'):
        entry = QueueLogEntry(datetime.now().strftime('%y%m%d %H:%M:%S'), message, level)
        with self._lock:
            self._entries.append(entry)

    def snapshot(self):
        with self._lock:
            return tuple(self._entries)
