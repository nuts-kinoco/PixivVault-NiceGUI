"""Generated-only fail-closed checks for impossible durable STOP revisions.

This module never opens a real profile, Cookie, media path or network endpoint.
The production gate's only valid revisions are initial zero and first STOP one.
"""

import sqlite3

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
)


@pytest.mark.parametrize(
    ("blocked", "status", "revision"),
    [
        (0, None, 1),
        (0, None, 9),
        (1, 401, 0),
        (1, 403, 2),
        (1, 429, 9),
    ],
)
def test_impossible_revision_never_authorizes_generated_get(
    tmp_path, blocked, status, revision,
):
    path = tmp_path / "generated-network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    if blocked:
        gate.trip(status)

    # Model out-of-band mutation while preserving the same database file ID.
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE gate SET blocked=?,status=?,revision=? WHERE id=1",
            (blocked, status, revision),
        )
    callbacks = []
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            callbacks.append("must-not-dispatch")
    assert callbacks == []
    # No alternate gate entrance may accept the same impossible history, and
    # a new instance must not silently treat it as a fresh state.
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.snapshot()
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.trip(429)
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT blocked,status,revision FROM gate"
        ).fetchone() == (blocked, status, revision)
