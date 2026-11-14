"""Generated-only coverage for all four historical STOP owner marker formats.

This tests observation, not migration, clearance or permission to perform GET.
No existing profile, credential, Pixiv endpoint, or media root is opened.
"""

from hashlib import sha256
import json

import pytest

from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER as IMAGE_MARKER
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_history_readonly import (
    read_existing_stop_history_readonly,
)
from pixivvault_nicegui.managed_network_stop_profile_scope import (
    _SCOPE_IDENTITY, _SCOPE_MARKER,
)
from pixivvault_nicegui.managed_novel_jobs import _MARKER as NOVEL_MARKER
from pixivvault_nicegui.managed_ugoira import (
    MARKER as UGOIRA_MARKER, _IDENTITY as UGOIRA_IDENTITY,
)
from pixivvault_nicegui.native_profile import PROFILE_IDENTITY, PROFILE_MARKER


_KINDS = ("images", "novel", "ugoira", "native")
_DB = ".pixivvault-network-stop.sqlite3"


def _create_generated_owner(root, kind):
    root.mkdir()
    if kind == "images":
        (root / IMAGE_MARKER).write_text(json.dumps(IDENTITY), encoding="utf-8")
    elif kind == "novel":
        (root / ".pixivvault-novel-jobs.json").write_bytes(NOVEL_MARKER)
    elif kind == "ugoira":
        (root / UGOIRA_MARKER).write_bytes(UGOIRA_IDENTITY)
    elif kind == "native":
        (root / PROFILE_MARKER).write_bytes(PROFILE_IDENTITY)
        (root / _SCOPE_MARKER).write_bytes(_SCOPE_IDENTITY)
    else:
        raise AssertionError("test uses only known generated owner kinds")


def _file_digest_map(root):
    return {entry.name: sha256(entry.read_bytes()).hexdigest()
            for entry in root.iterdir() if entry.is_file()}


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("status", [None, 401, 403, 429])
def test_every_generated_legacy_owner_is_readonly_observation(tmp_path, kind, status):
    root = tmp_path / ("generated-" + kind)
    _create_generated_owner(root, kind)
    gate = ManagedNetworkStopGate(root / _DB)
    if status is not None:
        gate.trip(status)
    original = _file_digest_map(root)

    observation = read_existing_stop_history_readonly(root, owner_kind=kind)

    assert observation.state == (
        "observed_unblocked" if status is None else "observed_stopped"
    )
    assert observation.status_code == status
    assert observation.revision == (0 if status is None else 1)
    assert observation.reason == "readonly_snapshot_not_migration_proof"
    assert observation.authorizes_get is False
    assert _file_digest_map(root) == original


@pytest.mark.parametrize("kind", _KINDS)
def test_mismatched_generated_owner_refuses_without_touching_gate(tmp_path, kind):
    root = tmp_path / ("generated-" + kind)
    _create_generated_owner(root, kind)
    gate = ManagedNetworkStopGate(root / _DB)
    gate.trip(429)
    marker = {
        "images": IMAGE_MARKER,
        "novel": ".pixivvault-novel-jobs.json",
        "ugoira": UGOIRA_MARKER,
        "native": _SCOPE_MARKER,
    }[kind]
    (root / marker).write_bytes(b"generated-invalid-marker")
    original = _file_digest_map(root)

    observation = read_existing_stop_history_readonly(root, owner_kind=kind)

    assert observation.state == "history_unverified"
    assert observation.authorizes_get is False
    assert _file_digest_map(root) == original
