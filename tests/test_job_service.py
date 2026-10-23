"""Deterministic execution, cancellation, restart and storage-failure regressions."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event, Thread

import pytest

from pixivvault_nicegui.job_service import JobCancelled, JobService, JobServiceError, JobStorageError


def find(service, job_id):
    return next((item for item in service.snapshot() if item.job_id == job_id), None)


def wait(service, job_id, state):
    with service._condition:
        assert service._condition.wait_for(
            lambda: find(service, job_id) is not None and find(service, job_id).state == state, timeout=5
        ), (state, service.snapshot())
    return find(service, job_id)


def test_real_completion_receipt_and_monotonic_progress():
    def runner(job, context):
        context.progress(0, 2)
        context.progress(1, 2)
        context.progress(2, 2)
        return "outputs/synthetic/receipt.json"
    service = JobService(runner)
    try:
        job = service.enqueue("demo", "demo-1", "title", operation_id="operation-1")
        finished = wait(service, job.job_id, "done")
        assert (finished.done, finished.total) == (2, 2)
        assert finished.result == "outputs/synthetic/receipt.json"
        assert service.enqueue("demo", "demo-1", "title", operation_id="operation-1") == finished
    finally:
        service.close()
    assert service.closed


@pytest.mark.parametrize("response", [None, False, "", 1, {}])
def test_runner_returning_without_receipt_is_not_success(response):
    service = JobService(lambda *_: response)
    try:
        job = service.enqueue("demo", "one")
        failed = wait(service, job.job_id, "error")
        assert failed.error_code == "runner_failed"
        assert failed.result is None
    finally:
        service.close()


def test_queued_pause_resumes_to_queued_and_duplicate_operation_is_retained():
    blocked, release = Event(), Event()
    seen = []
    def runner(job, context):
        seen.append(job.target_id)
        if job.target_id == "first":
            blocked.set()
            assert release.wait(5)
        context.checkpoint()
        return "receipt"
    service = JobService(runner)
    try:
        first = service.enqueue("demo", "first")
        assert blocked.wait(5)
        queued = service.enqueue("demo", "second", operation_id="first-click")
        assert service.enqueue("demo", "second", operation_id="second-click").job_id == queued.job_id
        assert service.control(queued.job_id, "pause", queued.revision)
        paused = find(service, queued.job_id)
        assert paused.state == "paused" and paused.paused_from == "queued"
        assert not service.control(queued.job_id, "resume", queued.revision)
        assert service.control(queued.job_id, "resume", paused.revision)
        assert find(service, queued.job_id).state == "queued"
        release.set()
        wait(service, first.job_id, "done")
        second = wait(service, queued.job_id, "done")
        assert service.enqueue("demo", "second", operation_id="second-click") == second
        assert seen == ["first", "second"]
    finally:
        release.set()
        service.close()


def test_paused_queued_does_not_block_later_queued_and_stays_paused():
    first_started, release_first, later_started = Event(), Event(), Event()
    seen = []
    def runner(job, context):
        seen.append(job.target_id)
        if job.target_id == "first":
            first_started.set()
            assert release_first.wait(5)
        if job.target_id == "later":
            later_started.set()
        context.checkpoint()
        return "receipt"
    service = JobService(runner)
    try:
        first = service.enqueue("demo", "first")
        assert first_started.wait(5)
        held = service.enqueue("demo", "held", operation_id="held-original")
        assert service.control(held.job_id, "pause")
        later = service.enqueue("demo", "later")
        release_first.set()
        assert later_started.wait(2), "paused queued job starved later queued work"
        wait(service, first.job_id, "done")
        wait(service, later.job_id, "done")
        assert find(service, held.job_id).state == "paused"
        assert seen == ["first", "later"]
        assert service.enqueue("demo", "held", operation_id="held-original").job_id == held.job_id
        assert service.control(held.job_id, "resume")
        wait(service, held.job_id, "done")
        assert seen == ["first", "later", "held"]
    finally:
        release_first.set()
        service.close()


def test_pause_then_stop_wakes_checkpoint_and_stopped_cannot_resume():
    entered, release = Event(), Event()
    def runner(job, context):
        context.progress(0, 1)
        entered.set()
        assert release.wait(5)
        context.checkpoint()
        context.progress(1, 1)
        return "must-not-complete"
    service = JobService(runner)
    try:
        job = service.enqueue("demo", "one")
        assert entered.wait(5)
        assert service.control(job.job_id, "pause")
        assert find(service, job.job_id).state == "paused"
        assert service.control(job.job_id, "stop")
        release.set()
        stopped = wait(service, job.job_id, "stopped")
        assert stopped.done == 0 and stopped.result is None
        assert not service.control(job.job_id, "resume")
        assert not service.control(job.job_id, "retry")
    finally:
        release.set()
        service.close()


def test_deleted_queued_job_never_executes_and_active_cannot_revive():
    entered, release = Event(), Event()
    seen, contexts = [], []
    def runner(job, context):
        seen.append(job.target_id)
        contexts.append(context)
        entered.set()
        assert release.wait(5)
        context.checkpoint()
        return "receipt"
    service = JobService(runner)
    try:
        first = service.enqueue("demo", "active")
        assert entered.wait(5)
        queued = service.enqueue("demo", "queued")
        assert service.control(queued.job_id, "delete")
        assert service.control(first.job_id, "delete")
        release.set()
        service.close()
        assert service.snapshot() == ()
        assert seen == ["active"]
        with pytest.raises(JobCancelled):
            contexts[0].progress(1, 1)
    finally:
        release.set()
        service.close()


def test_error_retry_rotates_attempt_and_old_context_is_rejected():
    contexts = []
    def runner(job, context):
        contexts.append(context)
        if job.attempt == 1:
            raise RuntimeError("private-cookie-value-must-not-escape")
        context.progress(1, 1)
        return "saved-receipt"
    service = JobService(runner)
    try:
        job = service.enqueue("demo", "one")
        failed = wait(service, job.job_id, "error")
        assert "private-cookie" not in repr(failed)
        assert service.control(job.job_id, "retry", failed.revision)
        finished = wait(service, job.job_id, "done")
        assert finished.attempt == 2
        with pytest.raises(JobCancelled):
            contexts[0].progress(999, 999)
        assert find(service, job.job_id) == finished
    finally:
        service.close()


def test_unmanaged_database_is_preserved(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE private_data(value TEXT)")
    connection.execute("INSERT INTO private_data VALUES ('important')")
    connection.commit()
    connection.close()
    original = path.read_bytes()
    with pytest.raises(JobStorageError, match="unmanaged"):
        JobService(lambda *_: "receipt", path)
    assert path.read_bytes() == original


def test_restart_preserves_receipts_and_recovers_interrupted_without_running(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    service = JobService(lambda *_: "stored-receipt", path)
    done = service.enqueue("demo", "done", operation_id="retained-operation")
    finished = wait(service, done.job_id, "done")
    service.close()
    interrupted = dict(asdict(finished), job_id="a" * 32, target_id="interrupted",
                       state="running", result=None)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO jobs VALUES (?,?,?)", (interrupted["job_id"], 1, json.dumps(interrupted)))
    calls = []
    restarted = JobService(lambda *_: calls.append(True), path)
    try:
        recovered = find(restarted, "a" * 32)
        assert recovered.state == "stopped" and recovered.error_code == "interrupted"
        assert recovered.result is None and calls == []
        assert find(restarted, done.job_id).result == "stored-receipt"
        assert restarted.enqueue("demo", "done", operation_id="retained-operation").job_id == done.job_id
        assert not restarted.control(recovered.job_id, "resume")
    finally:
        restarted.close()


def test_two_process_writers_cannot_own_same_database(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    owner = JobService(lambda *_: "receipt", path)
    try:
        code = ("import sys; from pathlib import Path; "
                "from pixivvault_nicegui.job_service import JobService,JobStorageError\n"
                "try:\n s=JobService(lambda *_:'receipt',Path(sys.argv[1]))\n"
                "except JobStorageError:\n print('rejected');sys.exit(0)\n"
                "else:\n s.close();sys.exit(1)\n")
        child = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=15)
        assert child.returncode == 0 and child.stdout.strip() == "rejected", child.stderr
    finally:
        owner.close()


def test_storage_failure_does_not_publish_uncommitted_control(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    def runner(job, context):
        entered.set()
        assert release.wait(5)
        return "receipt"
    service = JobService(runner, tmp_path / "jobs.sqlite3")
    try:
        job = service.enqueue("demo", "one")
        assert entered.wait(5)
        before = find(service, job.job_id)
        original = service._store.write
        def fail(*_):
            raise JobStorageError("job state could not be saved")
        monkeypatch.setattr(service._store, "write", fail)
        with pytest.raises(JobStorageError):
            service.control(job.job_id, "pause")
        assert find(service, job.job_id) == before
        monkeypatch.setattr(service._store, "write", original)
        release.set()
        wait(service, job.job_id, "done")
    finally:
        release.set()
        service.close()


def test_shutdown_cancels_pause_and_joins_worker():
    entered, release = Event(), Event()
    def runner(job, context):
        entered.set()
        assert release.wait(5)
        context.checkpoint()
        return "receipt"
    service = JobService(runner)
    job = service.enqueue("demo", "one")
    assert entered.wait(5)
    assert service.control(job.job_id, "pause")
    release.set()
    service.close()
    assert service.closed and not service._thread.is_alive()
    assert find(service, job.job_id).state == "stopped"
    with pytest.raises(JobServiceError):
        service.enqueue("demo", "two")


def test_pause_between_checkpoint_and_progress_is_not_cancellation():
    entered, go, window, paused, callback_done, progressed, release = (Event() for _ in range(7))
    contexts = []
    def runner(job, context):
        contexts.append(context)
        entered.set()
        assert go.wait(5)
        try:
            context.progress(1, 1)
            progressed.set()
        finally:
            callback_done.set()
        assert release.wait(5)
        return "receipt"
    service = JobService(runner)
    job = service.enqueue("demo", "one")
    assert entered.wait(5)
    context = contexts[0]
    original = context.checkpoint
    def checkpoint_at_window():
        original()
        window.set()
        # Under the fixed outer lock the UI cannot interleave here; the
        # timeout releases this instrumentation, not the paused job itself.
        paused.wait(0.2)
    context.checkpoint = checkpoint_at_window
    def pause_from_ui():
        assert window.wait(5)
        service.control(job.job_id, "pause")
        paused.set()
    ui_thread = Thread(target=pause_from_ui)
    ui_thread.start()
    try:
        go.set()
        assert callback_done.wait(5)
        assert paused.wait(5)
        assert progressed.is_set(), "pause incorrectly cancelled the progress callback"
        assert find(service, job.job_id).state == "paused"
        assert service.control(job.job_id, "resume")
        release.set()
        wait(service, job.job_id, "done")
    finally:
        go.set()
        release.set()
        service.close()
        ui_thread.join(timeout=5)


def test_worker_cannot_close_its_own_database(tmp_path):
    ready = Event()
    owner = []
    def runner(job, context):
        assert ready.wait(5)
        with pytest.raises(JobServiceError, match="cannot close"):
            owner[0].close()
        context.progress(1, 1)
        return "verified-receipt"
    service = JobService(runner, tmp_path / "jobs.sqlite3")
    owner.append(service)
    try:
        job = service.enqueue("demo", "one")
        ready.set()
        assert wait(service, job.job_id, "done").result == "verified-receipt"
        assert not service.closed
    finally:
        ready.set()
        service.close()
    assert service.closed and not service._thread.is_alive()


def test_replaced_database_identity_rejects_uncommitted_control(tmp_path, monkeypatch):
    """Exercise the inode/dev guard portably, even on Windows locked DB files."""
    entered, release = Event(), Event()
    def runner(job, context):
        entered.set()
        assert release.wait(5)
        return "receipt"
    service = JobService(runner, tmp_path / "jobs.sqlite3")
    try:
        job = service.enqueue("demo", "one")
        assert entered.wait(5)
        before = find(service, job.job_id)
        identity = service._store.identity
        monkeypatch.setattr(service._store, "identity", (identity[0], identity[1] + 1))
        with pytest.raises(JobStorageError, match="identity changed"):
            service.control(job.job_id, "pause")
        assert find(service, job.job_id) == before
        monkeypatch.setattr(service._store, "identity", identity)
        release.set()
        wait(service, job.job_id, "done")
    finally:
        release.set()
        service.close()


def test_progress_does_not_rewrite_operation_history(tmp_path):
    entered, release = Event(), Event()
    def runner(job, context):
        entered.set()
        assert release.wait(5)
        for done in range(3):
            context.progress(done, 2)
        return "receipt"
    service = JobService(runner, tmp_path / "jobs.sqlite3")
    try:
        job = service.enqueue("demo", "one", operation_id="recorded-once")
        assert entered.wait(5)
        statements = []
        service._store.connection.set_trace_callback(statements.append)
        release.set()
        wait(service, job.job_id, "done")
        assert not any("OPERATIONS" in statement.upper() for statement in statements)
    finally:
        release.set()
        service.close()


def test_operation_history_is_bounded_without_allowing_deleted_replay(monkeypatch):
    import pixivvault_nicegui.job_service as module
    monkeypatch.setattr(module, "MAX_OPERATION_IDS", 2)
    service = JobService(lambda *_: "receipt", max_jobs=1)
    try:
        first = service.enqueue("demo", "first", operation_id="op-1")
        wait(service, first.job_id, "done")
        second = service.enqueue("demo", "second", operation_id="op-2")
        wait(service, second.job_id, "done")
        with pytest.raises(JobServiceError, match="already used"):
            service.enqueue("demo", "first", operation_id="op-1")
        with pytest.raises(JobServiceError, match="history is full"):
            service.enqueue("demo", "third", operation_id="op-3")
        assert len(service._operations) == 2
    finally:
        service.close()


def test_terminal_storage_failure_keeps_last_committed_snapshot(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    def runner(job, context):
        entered.set()
        assert release.wait(5)
        return "receipt"
    path = tmp_path / "jobs.sqlite3"
    service = JobService(runner, path)
    try:
        job = service.enqueue("demo", "one")
        assert entered.wait(5)
        committed = find(service, job.job_id)
        original = service._store.write
        def fail_done(jobs, operations):
            if any(item.state == "done" for item in jobs.values()):
                raise JobStorageError("synthetic failure")
            return original(jobs, operations)
        monkeypatch.setattr(service._store, "write", fail_done)
        release.set()
        with service._condition:
            assert service._condition.wait_for(lambda: service.faulted, timeout=5)
        assert find(service, job.job_id) == committed
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
            persisted = json.loads(connection.execute("SELECT payload FROM jobs WHERE job_id=?", (job.job_id,)).fetchone()[0])
        assert persisted == asdict(committed)
        assert not service.control(job.job_id, "retry")
    finally:
        release.set()
        service.close()


def test_progress_updates_do_not_starve_pause_but_state_changes_expire_controls():
    entered, release = Event(), Event()
    contexts = []
    def runner(job, context):
        contexts.append(context)
        entered.set()
        assert release.wait(5)
        context.progress(2, 2)
        return "receipt"
    service = JobService(runner)
    try:
        job = service.enqueue("demo", "one")
        assert entered.wait(5)
        observed = find(service, job.job_id)
        contexts[0].progress(1, 2)
        updated = find(service, job.job_id)
        assert observed.revision != updated.revision
        assert observed.control_revision == updated.control_revision
        assert service.control(job.job_id, "pause", expected_control_revision=observed.control_revision)
        assert not service.control(job.job_id, "resume", expected_control_revision=observed.control_revision)
        paused = find(service, job.job_id)
        assert service.control(job.job_id, "resume", expected_control_revision=paused.control_revision)
        release.set()
        wait(service, job.job_id, "done")
    finally:
        release.set()
        service.close()


def test_snapshot_does_not_wait_for_an_inflight_database_commit(tmp_path, monkeypatch):
    service = JobService(lambda *_: "receipt", tmp_path / "jobs.sqlite3")
    entered, release, read_done = Event(), Event(), Event()
    observed, errors = [], []
    original = service._store.write

    def delayed_write(jobs, operations):
        entered.set()
        assert release.wait(5)
        original(jobs, operations)

    monkeypatch.setattr(service._store, "write", delayed_write)

    def enqueue():
        try:
            service.enqueue("demo", "not-yet-persisted")
        except Exception as exc:
            errors.append(exc)

    def read():
        observed.append(service.snapshot())
        read_done.set()

    writer = Thread(target=enqueue)
    reader = Thread(target=read)
    try:
        writer.start()
        assert entered.wait(5)
        reader.start()
        assert read_done.wait(0.5), "UI snapshot waited for the database writer"
        assert observed == [()], "uncommitted row was exposed to UI"
        release.set()
        writer.join(timeout=5)
        assert not writer.is_alive() and not errors
        assert [job.target_id for job in service.snapshot()] == ["not-yet-persisted"]
        assert observed == [()], "a previously returned immutable snapshot changed"
    finally:
        release.set()
        writer.join(timeout=5)
        if reader.ident is not None:
            reader.join(timeout=5)
        service.close()
    assert not errors and not writer.is_alive() and not reader.is_alive()
