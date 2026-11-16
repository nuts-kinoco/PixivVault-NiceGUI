"""Only generated roots: never inspect a real old profile or Cookie root."""

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_history_readonly import (
    read_existing_stop_history_readonly as observe,
)


_DB = ".pixivvault-network-stop.sqlite3"


def _hashes(root):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.iterdir() if path.is_file()}


def _root(tmp_path, *, status=None):
    root = tmp_path / "generated-only"
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding="utf-8")
    gate = ManagedNetworkStopGate(root / _DB)
    if status is not None:
        gate.trip(status)
    return root


def test_unknown_root_never_initializes_gate(tmp_path):
    root = tmp_path / "not-a-history"
    before = sorted(tmp_path.iterdir())
    result = observe(root, owner_kind="images")
    assert result.state == "history_unverified"
    assert not result.authorizes_get
    assert sorted(tmp_path.iterdir()) == before
    assert not root.exists()


def test_owner_marker_without_db_or_lock_is_not_clean_history(tmp_path):
    root = tmp_path / "generated-only"
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding="utf-8")
    before = _hashes(root)
    result = observe(root, owner_kind="images")
    assert result.state == "history_unverified"
    assert not result.authorizes_get
    assert _hashes(root) == before
    assert not (root / _DB).exists()
    assert not (root / (_DB + ".lock")).exists()


@pytest.mark.parametrize("status", [None, 401, 403, 429])
def test_existing_generated_stop_is_observed_without_mutation(tmp_path, status):
    root = _root(tmp_path, status=status)
    original = _hashes(root)
    result = observe(root, owner_kind="images")
    assert result.state == ("observed_unblocked" if status is None else "observed_stopped")
    assert result.status_code == status
    assert result.revision == (0 if status is None else 1)
    assert not result.authorizes_get
    assert result.reason == "readonly_snapshot_not_migration_proof"
    assert _hashes(root) == original


@pytest.mark.parametrize(
    ("blocked", "status", "revision"),
    [(0, None, 1), (0, None, 2), (1, 429, 2), (1, 401, 0)],
)
def test_impossible_stop_revision_is_ambiguous_and_never_reported_clean(
    tmp_path, blocked, status, revision,
):
    root = _root(tmp_path, status=429 if blocked else None)
    # Mutate only the generated fixture to model an out-of-band edit/reset.
    with sqlite3.connect(root / _DB) as conn:
        conn.execute(
            "UPDATE gate SET blocked=?,status=?,revision=? WHERE id=1",
            (blocked, status, revision),
        )
    original = _hashes(root)
    result = observe(root, owner_kind="images")
    assert (result.state, result.reason) == (
        "history_unverified", "ambiguous_stop_revision",
    )
    assert not result.authorizes_get
    assert _hashes(root) == original


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_pending_sqlite_sidecars_are_unverified_and_untouched(tmp_path, suffix):
    root = _root(tmp_path, status=429)
    (root / (_DB + suffix)).write_bytes(b"generated-unknown-state")
    original = _hashes(root)
    result = observe(root, owner_kind="images")
    assert (result.state, result.reason) == (
        "history_unverified", "pending_or_unknown_sqlite_journal",
    )
    assert _hashes(root) == original


def test_missing_lock_or_unowned_gate_never_claims_clean(tmp_path):
    root = _root(tmp_path)
    (root / (_DB + ".lock")).unlink()  # generated fixture only
    before = _hashes(root)
    result = observe(root, owner_kind="images")
    assert result.state == "history_unverified"
    assert _hashes(root) == before
    (root / MARKER).write_bytes(b"wrong owner")
    before = _hashes(root)
    result = observe(root, owner_kind="images")
    assert result.state == "history_unverified"
    assert _hashes(root) == before


def test_wrong_schema_and_wal_header_are_unverified(tmp_path):
    root = tmp_path / "generated-only"
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding="utf-8")
    with sqlite3.connect(root / _DB) as conn:
        conn.execute("CREATE TABLE fake (id INTEGER)")
    (root / (_DB + ".lock")).write_bytes(b"0")
    before = _hashes(root)
    assert observe(root, owner_kind="images").state == "history_unverified"
    assert _hashes(root) == before
    # SQLite WAL header is treated as unknown even with no sidecar present.
    db = root / _DB
    data = bytearray(db.read_bytes())
    data[18:20] = b"\x02\x02"
    db.write_bytes(data)
    before = _hashes(root)
    assert observe(root, owner_kind="images").reason == "unknown_or_wal_sqlite_header"
    assert _hashes(root) == before


def test_unknown_owner_kind_is_not_trusted(tmp_path):
    root = _root(tmp_path)
    assert observe(root, owner_kind="unknown").state == "history_unverified"
    assert observe(root, owner_kind=[]).state == "history_unverified"
