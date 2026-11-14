"""Bounded fail-closed contention tests on generated-only managed gate roots."""

from threading import Event, Thread

import pytest

from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkGateStorageError,
    ManagedNetworkStopGate,
)


@pytest.mark.parametrize("probe", ("snapshot", "request"))
def test_inflight_get_does_not_hold_another_thread_indefinitely(tmp_path, probe):
    gate = ManagedNetworkStopGate(tmp_path / "generated-stop.sqlite")
    entered, release, finished = Event(), Event(), Event()
    observed, attempted_get = [], []
    holder_error = []

    def holder():
        try:
            with gate.request():
                entered.set()
                if not release.wait(6):
                    raise AssertionError("generated holder was not released")
        except BaseException as exc:
            holder_error.append(exc)

    def contender():
        try:
            if probe == "snapshot":
                gate.snapshot()
            else:
                with gate.request():
                    attempted_get.append("unsafe continuation")
        except ManagedNetworkGateStorageError:
            observed.append("busy-fail-closed")
        finally:
            finished.set()

    first = Thread(target=holder, name="generated-gate-holder")
    second = Thread(target=contender, name="generated-gate-probe")
    first.start()
    try:
        assert entered.wait(3), "generated holder did not acquire the gate"
        second.start()
        # An active GET may be slow. The UI/job lock must not wait for the
        # network call to complete. Contention is a temporary safe refusal,
        # not a persistent 401/403/429 stop.
        assert finished.wait(1.8), "gate probe blocked indefinitely on a live GET"
        assert observed == ["busy-fail-closed"]
        assert attempted_get == []
    finally:
        release.set()
        first.join(7)
        if second.ident is not None:
            second.join(7)
    assert not first.is_alive() and not second.is_alive()
    assert holder_error == []
    assert gate.snapshot().blocked is False
    with gate.request():
        pass


def test_short_healthy_concurrency_serializes_without_latching_stop(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "generated-stop.sqlite")
    entered, release, probing = Event(), Event(), Event()
    events, errors = [], []

    def holder():
        try:
            with gate.request():
                entered.set()
                assert release.wait(5)
        except BaseException as exc:
            errors.append(exc)

    def contender():
        try:
            probing.set()
            with gate.request():
                events.append("healthy second GET")
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=holder)
    second = Thread(target=contender)
    first.start()
    try:
        assert entered.wait(3)
        second.start()
        assert probing.wait(3)
    finally:
        release.set()
        first.join(6)
        if second.ident is not None:
            second.join(6)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert events == ["healthy second GET"]
    assert gate.snapshot().blocked is False
