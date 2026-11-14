"""Opt-in owner boundary for a durable managed Pixiv GET stop.

The relevant managed media writer must establish and validate its root marker
*before* this integration is opened. This module never creates a managed
media root and never imports a real Cookie. Product startup must explicitly
share a gate with every GET reader and job/scheduler admission boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

from .fs_paths import logical_path, native_io_path
from .managed_downloads import (
    IDENTITY, MARKER, ManagedDownloadError, _json_unique, _read_bounded,
    _safe_path,
)
from .managed_network_stop_gate import (
    ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
    ManagedNetworkStopGate,
)
from .managed_network_stop_reader import create_exact_managed_read_client
from .job_service import JobAdmissionDeferred


_GATE_FILE = ".pixivvault-network-stop.sqlite3"


class ManagedNetworkStopIntegration:
    """One root's durable stop capability, after the writer owns the root.

    An unowned, missing or malformed root is rejected before any gate files
    are created. There is deliberately no reset/unblock method.
    """

    def __init__(self, managed_root: Path, *, owner_type: str = "images"):
        if type(owner_type) is not str or owner_type not in ("images", "novel", "ugoira"):
            raise ManagedNetworkGateStorageError("unknown managed root ownership")
        root = Path(managed_root)
        if not root.is_absolute() or root.parent == root:
            raise ManagedNetworkGateStorageError("owned managed root required")
        try:
            _safe_path(root)
            root = logical_path(native_io_path(root).resolve())
            _safe_path(root)
            if owner_type == "images":
                marker = root / MARKER
                evidence = json.loads(
                    _read_bounded(marker, 4096), object_pairs_hook=_json_unique,
                )
                if type(evidence) is not dict or evidence != IDENTITY:
                    raise ManagedDownloadError("managed marker rejected")
            elif owner_type == "novel":
                from .managed_novel_jobs import _MARKER as NOVEL_MARKER
                if _read_bounded(root / ".pixivvault-novel-jobs.json", 4096) != NOVEL_MARKER:
                    raise ManagedDownloadError("managed novel marker rejected")
            else:
                from .managed_ugoira import MARKER as UGOIRA_MARKER, _IDENTITY as UGOIRA_IDENTITY
                if _read_bounded(root / UGOIRA_MARKER, 4096) != UGOIRA_IDENTITY:
                    raise ManagedDownloadError("managed ugoira marker rejected")
        except (ManagedDownloadError, OSError, ValueError, UnicodeError, RecursionError):
            raise ManagedNetworkGateStorageError("owned managed root unavailable") from None
        self.root = root
        self.gate = ManagedNetworkStopGate(root / _GATE_FILE)

    def create_reader(self, cookie_path: Path, *, transport=None):
        """Return the exact base Pixiv reader type with this root's GET fence."""
        return create_exact_managed_read_client(
            cookie_path, self.gate, transport=transport,
        )

    def admit_job(self, job) -> bool:
        """Managed JobService admission: pause every job on a guarded queue.

        A corrupt/unavailable gate raises rather than admitting work; the
        JobService fail-closed fault path must not silently retry the job.
        Unrelated local queues must not install this callback. Do not classify
        unknown jobs as safe merely because they are not pixiv-illust: novels
        and ugoira can issue remote reads under other kind names.
        """
        try:
            return not self.gate.snapshot().blocked
        except ManagedNetworkGateBusy:
            # A concurrent GET has not completed, and may still latch a fatal
            # response. A queued job stays queued until admission can observe
            # the post-GET durable state; other storage faults stay fatal.
            raise JobAdmissionDeferred("managed gate admission pending") from None

    def require_allowed(self) -> None:
        """Scheduler admission checkpoint before any author's remote preview."""
        state = self.gate.snapshot()
        if state.blocked:
            raise ManagedNetworkStopped(state.status_code)
