"""Offline acceptance of the optional durable JobService dispatch stop.

This is a single-owner synthetic queue/guard composition, NOT proof that
ApplicationServices or all production Pixiv GET entrants are wired to it.
"""

from threading import Event

import pytest

from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_network_stop_gate import (
    ManagedNetworkStopped,
    ManagedNetworkStopGate,
)


def _await_state(queue, job_id, state):
    with queue._condition:
        assert queue._condition.wait_for(
            lambda: any(j.job_id == job_id and j.state == state
                        for j in queue.snapshot()), timeout=10,
        ), f"generated job did not reach {state}"
    return next(j for j in queue.snapshot() if j.job_id == job_id)


@pytest.mark.parametrize("status", (401, 403, 429))
def test_fatal_gate_pauses_accepted_and_new_jobs_without_running_them(tmp_path, status):
    gate_path = tmp_path / "owned-stop" / "gate.sqlite3"
    gate = ManagedNetworkStopGate(gate_path)
    store = tmp_path / "owned-queue.sqlite3"
    started, release = Event(), Event()
    executed = []

    def run(job, _context):
        executed.append(job.target_id)
        if job.target_id == "first":
            started.set()
            assert release.wait(5), "synthetic queue handshake failed"
            gate.trip(status)
            raise ManagedNetworkStopped(status)
        return "generated-receipt"

    def admission(_job):
        return not gate.snapshot().blocked

    queue = JobService(run, store, dispatch_admission=admission)
    try:
        first = queue.enqueue("pixiv-illust", "first", operation_id="generated-first")
        assert started.wait(5), "first generated job did not start"
        second = queue.enqueue("pixiv-illust", "second", operation_id="generated-second")
        release.set()
        _await_state(queue, first.job_id, "error")
        paused = _await_state(queue, second.job_id, "paused")
        assert paused.paused_from == "queued"
        assert paused.error_code == "admission_blocked"
        assert executed == ["first"]
        assert gate.snapshot().status_code == status

        # A blocked gate still prevents execution after a user attempts Resume.
        assert queue.control(second.job_id, "resume", paused.revision)
        re_paused = _await_state(queue, second.job_id, "paused")
        assert re_paused.revision >= paused.revision + 2
        assert executed == ["first"]

        third = queue.enqueue("pixiv-illust", "third", operation_id="generated-third")
        _await_state(queue, third.job_id, "paused")
        assert executed == ["first"]
    finally:
        release.set()
        queue.close()

    # The state survives process-owner re-open; no stale queued job is replayed.
    reopened_gate = ManagedNetworkStopGate(gate_path)
    assert reopened_gate.snapshot().status_code == status
    reopened = JobService(run, store, dispatch_admission=lambda _job: not reopened_gate.snapshot().blocked)
    try:
        assert all(j.state != "queued" for j in reopened.snapshot())
        new = reopened.enqueue("pixiv-illust", "fourth", operation_id="generated-fourth")
        _await_state(reopened, new.job_id, "paused")
        assert executed == ["first"]
    finally:
        reopened.close()


def test_healthy_queue_and_unrelated_local_work_keep_running(tmp_path):
    gate = ManagedNetworkStopGate(tmp_path / "owned-stop" / "gate.sqlite3")
    seen = []

    def run(job, _context):
        seen.append(job.target_id)
        return "generated-receipt"

    def admission(job):
        return job.kind != "pixiv-illust" or not gate.snapshot().blocked

    queue = JobService(run, dispatch_admission=admission)
    try:
        first = queue.enqueue("pixiv-illust", "healthy")
        _await_state(queue, first.job_id, "done")
        gate.trip(429)
        blocked = queue.enqueue("pixiv-illust", "blocked")
        _await_state(queue, blocked.job_id, "paused")
        local = queue.enqueue("local-synthetic", "unrelated")
        _await_state(queue, local.job_id, "done")
        assert seen == ["healthy", "unrelated"]
    finally:
        queue.close()


def test_admission_fault_fails_closed_without_runner_or_implicit_retry():
    seen = []

    def run(job, _context):
        seen.append(job.target_id)
        return "generated-receipt"

    queue = JobService(run, dispatch_admission=lambda _job: None)
    try:
        pending = queue.enqueue("pixiv-illust", "generated")
        with queue._condition:
            assert queue._condition.wait_for(lambda: queue.faulted, timeout=10)
        assert next(j for j in queue.snapshot() if j.job_id == pending.job_id).state == "queued"
        assert seen == []
    finally:
        queue.close()


def test_non_callable_dispatch_admission_is_rejected():
    with pytest.raises(ValueError, match="configuration"):
        JobService(lambda _job, _context: "generated-receipt", dispatch_admission=True)
