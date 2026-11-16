"""Synthetic-only regression: a live STOP gate must reject lock-byte tampering."""

import os

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
)


@pytest.mark.parametrize("damage", ("overwrite", "append"))
def test_lock_bytes_modified_in_place_fail_closed(tmp_path, damage):
    """Keep the generated lock's identity while changing its contents."""
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    lock_path = gate.lock_path
    before = lock_path.stat()
    assert lock_path.read_bytes() == b"0"

    with lock_path.open("r+b") as stream:
        if damage == "append":
            stream.seek(0, os.SEEK_END)
            stream.write(b"1")
        else:
            stream.seek(0)
            stream.write(b"X")
        stream.flush()
        os.fsync(stream.fileno())

    after = lock_path.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert lock_path.read_bytes() != b"0"

    dispatched = []
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            dispatched.append("unexpected synthetic GET")
    assert dispatched == []
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.snapshot()
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.trip(429)
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)
    assert lock_path.read_bytes() != b"0"
