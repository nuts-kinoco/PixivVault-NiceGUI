"""Read-only PID/birth-pinned child-tree observer for synthetic browser tests.

It never sends a signal, kills a process, or discovers unrelated processes as
owned. A child is admitted only when its parent is a still-live pinned member.
Linux uses /proc; Windows uses the existing Win32 process identity helpers.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading


def _linux_entry(pid: int) -> tuple[int, int, bool] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        # comm (field 2) can contain spaces and parentheses. Parse after the
        # last ')' rather than splitting the complete record by spaces.
        tail = stat.rsplit(")", 1)[1].strip().split()
        return int(tail[1]), int(tail[19]), tail[0] not in {"Z", "X", "x"}
    except (OSError, ValueError, IndexError):
        return None


class SystemProcessBackend:
    def __init__(self):
        if os.name == "nt":
            from tools import check_native_window as native
            self.native = native
        elif sys.platform.startswith("linux"):
            self.native = None
        else:
            raise RuntimeError("owned browser process tree is not supported on this OS")

    def snapshot(self, anchors: dict[int, int | None] | None = None) -> dict[int, tuple[int, int, bool]]:
        if self.native is not None:
            pairs = self.native.process_pairs()
            parents = dict(pairs)
            result = {}
            frontier = set()
            # Pin *live* known parents before following any child PID links.
            # An expired/reused intermediate parent is never a bridge into an
            # unrelated process tree, even if its numeric PID was recycled.
            for pid, expected in (anchors or {}).items():
                birth = self.native.process_identity(pid)
                if birth is None or (expected is not None and birth != expected):
                    continue
                alive = pid in self.native.living_pids({pid: birth})
                result[pid] = (parents.get(pid, -1), birth, alive)
                if alive:
                    frontier.add(pid)
            while frontier:
                candidates = {pid: parent for pid, parent in pairs
                              if parent in frontier and pid not in result}
                discovered = {}
                for pid, parent in candidates.items():
                    birth = self.native.process_identity(pid)
                    if birth is not None and birth >= result[parent][1]:
                        discovered[pid] = (parent, birth)
                living = self.native.living_pids({pid: birth
                    for pid, (_, birth) in discovered.items()}) if discovered else set()
                frontier = set()
                for pid, (parent, birth) in discovered.items():
                    alive = pid in living
                    result[pid] = (parent, birth, alive)
                    if alive:
                        frontier.add(pid)
            return result
        result = {}
        for item in Path("/proc").iterdir():
            if item.name.isdecimal():
                pid = int(item.name)
                entry = _linux_entry(pid)
                if entry is not None:
                    result[pid] = entry
        return result


class OwnedProcessTree:
    """Observe a new owned launcher and descendants until their natural exit."""
    def __init__(self, process, backend=None, interval: float = 0.15):
        self.process = process
        self.backend = backend or SystemProcessBackend()
        self.interval = interval
        self.identities: dict[int, int] = {}
        self.errors: list[str] = []
        self.last_living: list[int] | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        try:
            entry = self.backend.snapshot({process.pid: None}).get(process.pid)
            if entry is None or not entry[2]:
                raise RuntimeError("root identity unavailable")
            self.identities[process.pid] = entry[1]
        except Exception:
            self.errors.append("root_identity_unknown")
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def sample(self) -> None:
        with self._lock:
            try:
                entries = self.backend.snapshot(dict(self.identities))
                if not self.identities:
                    self.last_living = None
                    return
                live = {pid for pid, birth in self.identities.items()
                        if (entry := entries.get(pid)) is not None
                        and entry[1] == birth and entry[2]}
                changed = True
                while changed:
                    changed = False
                    for pid, (parent, birth, alive) in entries.items():
                        if (not alive or pid in self.identities or parent not in live
                                or birth < self.identities[parent]):
                            continue
                        self.identities[pid] = birth
                        live.add(pid)
                        changed = True
                self.last_living = sorted(live)
            except Exception:
                if "process_snapshot_failed" not in self.errors:
                    self.errors.append("process_snapshot_failed")
                self.last_living = None

    def _watch(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def observed_endpoint(self, pid: int) -> bool:
        if type(pid) is not int or pid <= 0:
            return False
        self.sample()
        with self._lock:
            return not self.errors and self.last_living is not None and pid in self.last_living

    def finish(self) -> dict:
        self._stop.set()
        self._thread.join(timeout=3)
        if self._thread.is_alive():
            return {"observed_count": len(self.identities), "remaining_count": None,
                    "errors": ["observer_not_finished"]}
        self.sample()
        with self._lock:
            return {"observed_count": len(self.identities),
                    "remaining_count": None if self.last_living is None else len(self.last_living),
                    "errors": list(self.errors)}
