"""Generated-only gate contract tests. Product GET paths remain unwired."""

from threading import Event, Thread
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
    ManagedNetworkStopped,
)
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


def test_fresh_and_successful_get(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "network-stop.sqlite")
    assert (gate.snapshot().blocked, gate.snapshot().revision) == (False, 0)
    called = []
    with gate.request():
        called.append("generated")
    assert called == ["generated"]
    assert gate.snapshot().status_code is None


@pytest.mark.parametrize("status", [401, 403, 429])
def test_fatal_status_is_persistent_and_fences_next_get(tmp_path, status):
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    with pytest.raises(HTTPStatusRejected):
        with gate.request():
            raise HTTPStatusRejected(status)
    state = gate.snapshot()
    assert (state.blocked, state.status_code, state.revision) == (True, status, 1)
    reopened = ManagedNetworkStopGate(path)
    called = []
    with pytest.raises(ManagedNetworkStopped) as error:
        with reopened.request():
            called.append("unwanted")
    assert error.value.status_code == status
    assert called == []
    assert reopened.trip(429).revision == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM gate").fetchall() == [(1, 1, status, 1)]


def test_other_http_status_not_latched(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "network-stop.sqlite")
    with pytest.raises(HTTPStatusRejected):
        with gate.request():
            raise HTTPStatusRejected(404)
    assert not gate.snapshot().blocked
    with gate.request():
        pass


def test_non_http_transport_failure_is_not_relabelled_as_lock_error(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "network-stop.sqlite")
    with pytest.raises(ConnectionResetError, match="offline synthetic"):
        with gate.request():
            raise ConnectionResetError("offline synthetic")
    assert not gate.snapshot().blocked


def test_two_gate_instances_fence_concurrent_get(tmp_path):
    path = tmp_path / "network-stop.sqlite"
    first, second = ManagedNetworkStopGate(path), ManagedNetworkStopGate(path)
    entered, release = Event(), Event()
    outcomes, later_gets = [], []

    def original():
        try:
            with first.request():
                entered.set()
                assert release.wait(5)
                raise HTTPStatusRejected(429)
        except HTTPStatusRejected:
            outcomes.append("original-rejected")

    def later():
        try:
            with second.request():
                later_gets.append("must-not-run")
        except ManagedNetworkStopped:
            outcomes.append("next-blocked")

    a = Thread(target=original)
    b = Thread(target=later)
    a.start()
    try:
        assert entered.wait(5)
        b.start()
    finally:
        release.set()
        a.join(5)
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    assert sorted(outcomes) == ["next-blocked", "original-rejected"]
    assert later_gets == []


def test_other_process_is_fenced_while_first_get_is_in_flight(tmp_path):
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    child_program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate\n"
        "from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected\n"
        "gate=ManagedNetworkStopGate(Path(sys.argv[1]))\n"
        "try:\n"
        "    with gate.request():\n"
        "        print('ENTERED', flush=True)\n"
        "        sys.stdin.readline()\n"
        "        raise HTTPStatusRejected(429)\n"
        "except HTTPStatusRejected:\n"
        "    print('LATCHED', flush=True)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    child = subprocess.Popen(
        [sys.executable, "-c", child_program, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    try:
        assert child.stdout.readline().strip() == "ENTERED"
        invoked = []
        with pytest.raises(ManagedNetworkGateStorageError):
            with gate.request():
                invoked.append("would-cross-inflight-process")
        assert invoked == []
        out, err = child.communicate(input="\n", timeout=10)
        assert child.returncode == 0, err
        assert "LATCHED" in out
        with pytest.raises(ManagedNetworkStopped) as rejection:
            with gate.request():
                pytest.fail("429 in a separate process must be durable")
        assert rejection.value.status_code == 429
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_foreign_storage_rejected(tmp_path):
    path = tmp_path / "network-stop.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)


def test_missing_database_cannot_silently_clear_a_durable_stop(tmp_path):
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    gate.trip(429)
    path.unlink()  # generated test DB only; leave its ownership lock intact
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            pytest.fail("missing state must never permit a GET")


def test_missing_lock_cannot_be_recreated_by_live_gate(tmp_path):
    """Only the first initialization owns lock creation, never an ordinary GET."""
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    lock_path = gate.lock_path
    lock_path.unlink()
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            pytest.fail("missing prior lock must never authorize a GET")
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.snapshot()
    with pytest.raises(ManagedNetworkGateStorageError):
        gate.trip(429)
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)
    assert not lock_path.exists()


def test_truncated_existing_lock_is_not_silently_reseeded(tmp_path):
    """A zero-byte lock after initialization is tampering, not a fresh owner.

    Truncate the generated lock in place to preserve its OS file identity.
    The next GET and gate reopening must deny rather than repair the lock.
    """
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    lock_path = gate.lock_path
    before = lock_path.stat()
    with lock_path.open("r+b") as stream:
        stream.truncate(0)
        stream.flush()
        os.fsync(stream.fileno())
    after = lock_path.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert after.st_size == 0

    dispatched = []
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            dispatched.append("unexpected synthetic GET")
    assert dispatched == []
    assert lock_path.stat().st_size == 0
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(path)
    assert lock_path.stat().st_size == 0


@pytest.mark.parametrize("replaced", ("lock", "database"))
def test_live_gate_rejects_replaced_durable_identity(tmp_path, replaced):
    """A replacement at the same path is not the gate a live reader opened.

    All files are generated under pytest's temporary root. The replacement
    database is a valid *old* unblocked copy, not foreign/corrupt input.
    """
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    original_copy = tmp_path / "generated-pristine-copy.sqlite"
    shutil.copyfile(path, original_copy)
    gate.trip(401)
    if replaced == "database":
        os.replace(original_copy, path)
    else:
        gate.lock_path.unlink()
        gate.lock_path.write_bytes(b"0")

    invoked = []
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            invoked.append("must-not-dispatch")
    assert invoked == []


def test_corrupt_latched_status_fails_closed(tmp_path):
    path = tmp_path / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(path)
    gate.trip(401)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE gate SET status=404")
    with pytest.raises(ManagedNetworkGateStorageError):
        with gate.request():
            pytest.fail("invalid durable reason must never permit a GET")


def test_path_must_be_absolute_and_not_symlink(tmp_path):
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate("relative.sqlite")
    path = tmp_path / "network-stop.sqlite"
    ManagedNetworkStopGate(path)
    alias = tmp_path / "alias.sqlite"
    try:
        alias.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(ManagedNetworkGateStorageError):
        ManagedNetworkStopGate(alias)


def test_trip_rejects_unexpected_status(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "network-stop.sqlite")
    for status in (400, 404, 500, True, "429"):
        with pytest.raises(ValueError):
            gate.trip(status)
    assert not gate.snapshot().blocked
