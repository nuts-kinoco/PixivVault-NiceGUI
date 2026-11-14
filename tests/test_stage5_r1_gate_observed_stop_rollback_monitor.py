"""Synthetic-only: a live gate must not forget a STOP it already observed.

This does not prove restart-time authenticity of previously persisted history.
All SQLite changes are confined to pytest's generated tmp_path.
"""

import sqlite3

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
)


@pytest.mark.parametrize("mutation", ("rollback-to-initial", "change-stop-reason"))
def test_observed_stop_cannot_be_rewritten_to_another_valid_state(tmp_path, mutation):
    path = tmp_path / "generated-network-stop.sqlite"
    original = ManagedNetworkStopGate(path)
    original.trip(401)
    already_open = ManagedNetworkStopGate(path)
    assert original.snapshot().status_code == 401
    assert already_open.snapshot().status_code == 401

    # Both forged states pass the existing static row/revision validation;
    # only a live gate's observation can reveal the change in this scenario.
    forged = (0, None, 0) if mutation == "rollback-to-initial" else (1, 429, 1)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE gate SET blocked=?,status=?,revision=? WHERE id=1", forged
        )

    for gate in (original, already_open):
        dispatched = []
        with pytest.raises(ManagedNetworkGateStorageError):
            with gate.request():
                dispatched.append("unexpected synthetic GET")
        assert dispatched == []
        with pytest.raises(ManagedNetworkGateStorageError):
            gate.snapshot()
        with pytest.raises(ManagedNetworkGateStorageError):
            gate.trip(429)

    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT blocked,status,revision FROM gate WHERE id=1"
        ).fetchone() == forged
