"""Product JobService admission must not permanently fault on a slow guarded GET.

Generated-only durable state and threads. No Cookie, network, or user files.
"""

from threading import Event, Thread

import pytest

import pixivvault_nicegui.job_service as job_service
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


def _owned_bundle(tmp_path):
    root = tmp_path / "generated-owned-root"
    writer = ManagedPixivDownloads(root, object(), resolve_record=lambda _id: None)
    writer.close()
    return ManagedNetworkStopIntegration(root)


@pytest.mark.parametrize("terminal_status", (None, 429))
def test_slow_guarded_get_defers_admission_without_permanent_queue_fault(tmp_path, terminal_status):
    integration = _owned_bundle(tmp_path)
    gate = integration.gate
    entered, release = Event(), Event()
    called = []
    holder_errors = []

    def holding_get():
        try:
            with gate.request():
                entered.set()
                if not release.wait(5):
                    raise AssertionError("generated GET was not released")
                if terminal_status is not None:
                    raise HTTPStatusRejected(terminal_status)
        except HTTPStatusRejected as exc:
            if exc.status_code != terminal_status:
                holder_errors.append(exc)
        except BaseException as exc:
            holder_errors.append(exc)

    holder = Thread(target=holding_get, name="generated-slow-get")
    holder.start()
    assert entered.wait(3)

    def runner(job, _context):
        called.append(job.target_id)
        return "generated-completion"

    queue = JobService(runner, dispatch_admission=integration.admit_job)
    try:
        pending = queue.enqueue("pixiv-illust", "generated")
        # The pre-fix callback raises after its 0.5 second bounded lock wait,
        # and JobService incorrectly latches _fatal forever.
        with queue._condition:
            queue._condition.wait(timeout=0.9)
        assert not queue.faulted, "temporary GET lock contention must not permanently fault JobService"
        assert called == []
        release.set()
        expected = "paused" if terminal_status is not None else "done"
        with queue._condition:
            assert queue._condition.wait_for(
                lambda: any(j.job_id == pending.job_id and j.state == expected
                            for j in queue.snapshot()), timeout=8,
            )
        assert called == ([] if terminal_status is not None else ["generated"])
        assert gate.snapshot().blocked is (terminal_status is not None)
    finally:
        release.set()
        holder.join(7)
        queue.close()
    assert not holder.is_alive()
    assert holder_errors == []


def test_corrupt_durable_gate_remains_fatal_and_never_runs_queued_job(tmp_path):
    integration = _owned_bundle(tmp_path)
    integration.gate.path.unlink()  # generated state only; leave lock marker
    called = []
    queue = JobService(
        lambda job, _context: called.append(job.target_id) or "generated",
        dispatch_admission=integration.admit_job,
    )
    try:
        queued = queue.enqueue("pixiv-illust", "generated")
        with queue._condition:
            assert queue._condition.wait_for(lambda: queue.faulted, timeout=5)
        assert any(j.job_id == queued.job_id and j.state == "queued" for j in queue.snapshot())
        assert called == []
        with pytest.raises(ManagedNetworkGateStorageError):
            integration.require_allowed()
    finally:
        queue.close()


def test_persistent_busy_gate_pauses_durably_until_explicit_resume(tmp_path, monkeypatch):
    integration = _owned_bundle(tmp_path)
    gate = integration.gate
    entered, release = Event(), Event()
    errors = []
    # Simulate the production 20s liveness boundary in under two seconds.
    monkeypatch.setattr(job_service, "_ADMISSION_DEFER_MAX_SECONDS", 0.7)

    def holding_get():
        try:
            with gate.request():
                entered.set()
                if not release.wait(5):
                    raise AssertionError("synthetic owner timeout")
        except BaseException as exc:
            errors.append(exc)

    holder = Thread(target=holding_get)
    holder.start()
    assert entered.wait(3)
    called = []
    queue = JobService(
        lambda job, _context: called.append(job.target_id) or "generated",
        dispatch_admission=integration.admit_job,
    )
    try:
        pending = queue.enqueue("pixiv-illust", "generated")
        with queue._condition:
            assert queue._condition.wait_for(
                lambda: any(j.job_id == pending.job_id and j.state == "paused"
                            for j in queue.snapshot()), timeout=5,
            )
        paused = next(j for j in queue.snapshot() if j.job_id == pending.job_id)
        assert paused.error_code == "admission_unavailable"
        assert not queue.faulted and not called
        release.set()
        holder.join(5)
        assert gate.snapshot().blocked is False
        assert queue.control(pending.job_id, "resume", paused.revision)
        with queue._condition:
            assert queue._condition.wait_for(
                lambda: any(j.job_id == pending.job_id and j.state == "done"
                            for j in queue.snapshot()), timeout=5,
            )
        assert called == ["generated"]
    finally:
        release.set()
        holder.join(5)
        queue.close()
    assert errors == []
