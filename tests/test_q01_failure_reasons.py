"""Synthetic Q01 reason propagation; no server, sockets or user files."""
import errno
import socket
from threading import Lock
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.job_service import JobCancelled, JobService, JobSnapshot
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads


PRIVATE = "synthetic-cookie=secret; token=secret; S:/private/example.png"
CASES = [
    (lambda: PermissionError(PRIVATE), "permission_denied", "アクセス権限"),
    (lambda: TimeoutError(PRIVATE), "operation_timeout", "制限時間"),
    (lambda: OSError(errno.ENOSPC, PRIVATE), "disk_full", "空き容量"),
    (lambda: FileNotFoundError(PRIVATE), "file_missing", "ファイル"),
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("network is forbidden in Q01 synthetic tests")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def run_failure(factory, *, managed=False, storage=None):
    def fail(*_):
        raise factory()
    if managed:
        # Exercise the actual product catch boundary before any external I/O.
        writer = object.__new__(ManagedPixivDownloads)
        writer._writer = Lock()
        writer.closed = False
        writer.resolve_record = fail
        runner = writer.run
    else:
        runner = fail
    service = JobService(runner, storage)
    try:
        service.enqueue("pixiv-illust", "1", title="synthetic")
        with service._condition:
            assert service._condition.wait_for(
                lambda: service.snapshot() and service.snapshot()[0].state in {"error", "stopped"},
                timeout=3)
        return service.snapshot()[0]
    finally:
        service.close()


@pytest.mark.parametrize("factory,code,text", CASES)
@pytest.mark.parametrize("managed", [False, True])
def test_reason_survives_real_worker_and_managed_boundary(factory, code, text, managed):
    snapshot = run_failure(factory, managed=managed)
    assert snapshot.state == "error"
    assert snapshot.error_code == code
    assert snapshot.result is None
    assert PRIVATE not in repr(snapshot)


def test_reason_survives_restart_without_reexecution(tmp_path):
    storage = tmp_path / "synthetic.sqlite3"
    first = run_failure(CASES[0][0], managed=True, storage=storage)
    calls = []
    service = JobService(lambda *_: calls.append(True), storage)
    try:
        assert service.snapshot()[0] == first
        assert service.snapshot()[0].error_code == "permission_denied"
        assert not calls
        assert PRIVATE.encode() not in storage.read_bytes()
    finally:
        service.close()


@pytest.mark.parametrize("failed_only", [False, True])
def test_actual_queue_and_failed_tooltips(failed_only):
    from nicegui import ui
    from pixivvault_nicegui.ui.job_views import QueueView
    jobs = tuple(JobSnapshot(str(i), "pixiv-illust", str(i + 1), "synthetic",
                             state="error", error_code=code)
                 for i, (_, code, _) in enumerate(CASES))
    service = SimpleNamespace(snapshot=lambda: jobs, faulted=False)
    with ui.column() as holder:
        view = QueueView(SimpleNamespace(jobs=service, artworks=()), failed_only=failed_only)
    try:
        messages = [view.rows[job.job_id]["tooltip"].text for job in jobs]
        assert len(set(messages)) == len(CASES)
        for message, (_, _, expected) in zip(messages, CASES):
            assert expected in message
            assert PRIVATE not in message
        for job, (_, _, expected) in zip(jobs, CASES):
            assert expected in view.rows[job.job_id]["result"].text
        rows = dict(view.rows)
        view.refresh()
        assert all(view.rows[key] is value for key, value in rows.items())
    finally:
        view.close()
        holder.delete()


@pytest.mark.parametrize("factory", [lambda: ValueError(PRIVATE), lambda: RuntimeError(PRIVATE)])
def test_unknown_failure_stays_generic(factory):
    assert run_failure(factory, managed=True).error_code == "runner_failed"


def test_cancellation_remains_stopped():
    job = run_failure(JobCancelled, managed=True)
    assert job.state == "stopped"
    assert job.error_code == ""


def test_classifier_never_reads_exception_text_or_cause():
    from pixivvault_nicegui.job_failure import JobFailure, job_failure_code
    class Hostile(ValueError):
        reason_code = "disk_full"  # duck typing must not authorize a reason
        def __str__(self):
            raise AssertionError("exception text must not be inspected")
        def __repr__(self):
            raise AssertionError("exception repr must not be inspected")
    error = Hostile()
    error.__cause__ = PermissionError(PRIVATE)
    assert job_failure_code(error) == "runner_failed"
    for unsafe in (PRIVATE, ["disk_full"], {"token": PRIVATE}, None):
        carried = JobFailure(PRIVATE, reason_code=unsafe)
        assert job_failure_code(carried) == "runner_failed"
        carried.reason_code = unsafe
        assert job_failure_code(carried) == "runner_failed"


def test_unknown_stored_codes_keep_legacy_tooltip_fallbacks():
    from nicegui import ui
    from pixivvault_nicegui.ui.job_views import QueueView
    codes = [PRIVATE, ["disk_full"], "storage_unavailable", "runner_failed"]
    jobs = tuple(JobSnapshot(str(i), "ugoira-mp4" if i == 3 else "pixiv-illust",
                             str(i + 1), "synthetic", state="error", error_code=code)
                 for i, code in enumerate(codes))
    with ui.column() as holder:
        view = QueueView(SimpleNamespace(
            jobs=SimpleNamespace(snapshot=lambda: jobs, faulted=False), artworks=()))
    try:
        messages = [view.rows[job.job_id]["tooltip"].text for job in jobs]
        assert messages[:2] == ["処理に失敗しました"] * 2
        assert messages[2] == "状態保存失敗"
        assert "OpenCV" in messages[3]
        assert all(PRIVATE not in message for message in messages)
    finally:
        view.close()
        holder.delete()


def test_explicit_retry_clears_reason_and_preserves_receipt_contract():
    calls = []
    def runner(job, context):
        calls.append(job.error_code)
        if len(calls) == 1:
            raise PermissionError(PRIVATE)
        return "synthetic-receipt"
    service = JobService(runner)
    try:
        service.enqueue("pixiv-illust", "1")
        with service._condition:
            assert service._condition.wait_for(
                lambda: service.snapshot()[0].state == "error", timeout=3)
        first = service.snapshot()[0]
        assert first.error_code == "permission_denied"
        assert service.control(first.job_id, "retry", expected_revision=first.revision)
        with service._condition:
            assert service._condition.wait_for(
                lambda: service.snapshot()[0].state == "done", timeout=3)
        done = service.snapshot()[0]
        assert done.error_code == "" and done.result == "synthetic-receipt"
        assert done.attempt == first.attempt + 1
        assert calls == ["", ""]
    finally:
        service.close()


def test_admission_stop_still_prevents_runner():
    calls = []
    service = JobService(lambda *_: calls.append(True), dispatch_admission=lambda *_: False)
    try:
        service.enqueue("pixiv-illust", "1")
        with service._condition:
            assert service._condition.wait_for(
                lambda: service.snapshot()[0].state == "paused", timeout=3)
        assert service.snapshot()[0].error_code == "admission_blocked"
        assert not calls
    finally:
        service.close()
