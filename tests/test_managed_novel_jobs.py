"""Fixture-to-JobService-to-disk integration; run only in a test-owned root."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread, current_thread

import pytest

from pixivvault_nicegui.job_service import JobCancelled, JobContext, JobServiceError
from pixivvault_nicegui.managed_novel_jobs import (
    FixtureNovelSource, ManagedNovelError, ManagedNovelJobs, NovelJobIntent,
)
import pixivvault_nicegui.managed_novel_jobs as managed
import pixivvault_nicegui.novel_storage as storage
from pixivvault_nicegui.services import ApplicationServices, get_services


def source(**changes):
    body = {"id": "123", "userId": "91", "title": "Synthetic <novel>",
            "userName": "Synthetic Author", "content": "line one\n<script>& inert",
            "seriesNavData": None, "textEmbeddedImages": None, "coverUrl": ""}
    body.update(changes)
    return FixtureNovelSource({"123": body})


def find(owner, job_id):
    return next(item for item in owner.jobs.snapshot() if item.job_id == job_id)


def wait(owner, job_id, state):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(
            lambda: find(owner, job_id).state == state, timeout=10), owner.jobs.snapshot()
    return find(owner, job_id)


def outputs(root):
    return list((root / "exports" / "outputs").iterdir())


@pytest.mark.parametrize("fmt,names", [("TXT", ("novel.txt",)), ("EPUB", ("novel.epub",)),
                                       ("BOTH", ("novel.epub", "novel.txt"))])
def test_application_enqueue_fixture_save_receipt_reopen_and_operation_replay(tmp_path, fmt, names):
    root = tmp_path / "novels"
    intent = NovelJobIntent("123", "91", fmt)
    services = ApplicationServices(count=1, novel_fixture_root=root, novel_fixture_source=source())
    try:
        job = services.enqueue_novel_export(intent, "accept-one")
        done = wait(services.novel_jobs, job.job_id, "done")
        saved = services.current_novel_export("123", fmt)
        assert (done.done, done.total, done.attempt) == (1, 1, 1)
        assert done.kind == "novel-export" and done.target_id == "123:" + fmt
        assert done.result == saved.relative_receipt and saved.output_names == names
        assert services.jobs.operation_ids(job.job_id) == ("accept-one",)
        assert len(outputs(root)) == 1
        journal_bytes = (root / "novel-journal.json").read_bytes()
        assert b"Synthetic" not in journal_bytes and b"<script>" not in journal_bytes
    finally:
        services.close()
    reopened = ApplicationServices(count=1, novel_fixture_root=root, novel_fixture_source=source())
    try:
        replay = reopened.enqueue_novel_export(intent, "accept-one")
        assert replay == done
        assert reopened.current_novel_export("123", fmt) == saved
        assert len(outputs(root)) == 1
    finally:
        reopened.close()


def test_durable_intent_collision_and_generation_path_plus_digest_cas(tmp_path):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    try:
        intent = NovelJobIntent("123", "91", "BOTH")
        first = owner.enqueue(intent, "first")
        wait(owner, first.job_id, "done")
        old = owner.current("123", "BOTH")
        old_bytes = (root / "exports" / old.relative_receipt).read_bytes()
        with pytest.raises(ManagedNovelError):
            owner.enqueue(replace(intent, output_format="TXT"), "first")
        with pytest.raises(ManagedNovelError):
            owner.enqueue(intent, "unacknowledged-update")
        update = replace(intent, expected_relative_receipt=old.relative_receipt,
                         expected_receipt_sha256=old.receipt_sha256)
        second = owner.enqueue(update, "second")
        wait(owner, second.job_id, "done")
        new = owner.current("123", "BOTH")
        assert new.relative_receipt != old.relative_receipt
        # Byte-identical content can have the SAME receipt SHA. Its path must
        # still change: digest-only CAS would incorrectly accept this stale pin.
        assert new.receipt_sha256 == old.receipt_sha256
        with pytest.raises(ManagedNovelError):
            owner.enqueue(update, "stale-predecessor")
        assert (root / "exports" / old.relative_receipt).read_bytes() == old_bytes
        assert owner.store.reconcile(old.relative_receipt).saved == old
    finally:
        owner.close()


def block_fetch(monkeypatch):
    entered, release = Event(), Event()
    original = FixtureNovelSource.fetch

    def fetch(self, work_id):
        entered.set()
        assert release.wait(10)
        return original(self, work_id)
    monkeypatch.setattr(FixtureNovelSource, "fetch", fetch)
    return entered, release


def test_unfinished_aliases_and_conflicting_authorship_are_durable(tmp_path, monkeypatch):
    entered, release = block_fetch(monkeypatch)
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    try:
        intent = NovelJobIntent("123", "91")
        job = owner.enqueue(intent, "one")
        assert entered.wait(10)
        alias = owner.enqueue(intent, "two")
        assert alias.job_id == job.job_id
        with pytest.raises(ManagedNovelError):
            owner.enqueue(replace(intent, author_id="92"), "conflict")
        assert owner.jobs.operation_ids(job.job_id) == ("one", "two")
        release.set()
        wait(owner, job.job_id, "done")
        assert len(outputs(owner.root)) == 1
    finally:
        release.set()
        owner.close()


def test_pause_blocks_publication_and_stale_control_revision_cannot_resume(tmp_path, monkeypatch):
    entered, release = block_fetch(monkeypatch)
    paused_checkpoint = Event()
    original = JobContext.checkpoint

    def checkpoint(context):
        if any(item.state == "paused" for item in context._service.snapshot()):
            paused_checkpoint.set()
        return original(context)
    monkeypatch.setattr(JobContext, "checkpoint", checkpoint)
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "pause")
        assert entered.wait(10)
        running = find(owner, job.job_id)
        assert owner.jobs.control(job.job_id, "pause", expected_control_revision=running.control_revision)
        paused = find(owner, job.job_id)
        release.set()
        assert paused_checkpoint.wait(10)
        assert outputs(owner.root) == [] and owner.current("123") is None
        assert not owner.jobs.control(job.job_id, "resume", expected_control_revision=running.control_revision)
        assert owner.jobs.control(job.job_id, "resume", expected_control_revision=paused.control_revision)
        wait(owner, job.job_id, "done")
    finally:
        release.set()
        owner.close()


def test_stop_before_storage_never_publishes_or_becomes_done_on_restart(tmp_path, monkeypatch):
    entered, release = block_fetch(monkeypatch)
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "stop")
        assert entered.wait(10)
        assert owner.jobs.control(job.job_id, "stop")
        release.set()
        wait(owner, job.job_id, "stopped")
        assert outputs(root) == []
    finally:
        release.set()
        owner.close()
    reopened = ManagedNovelJobs(root, source())
    try:
        assert find(reopened, job.job_id).state == "stopped"
        assert reopened.current("123") is None
    finally:
        reopened.close()


def test_stop_after_rename_keeps_proof_but_does_not_activate_or_restore_success(tmp_path, monkeypatch):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    published, release = Event(), Event()
    original = owner.store.save

    def save(*args, **kwargs):
        result = original(*args, **kwargs)
        published.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(owner.store, "save", save)
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "stop-published")
        assert published.wait(10)
        assert owner.jobs.control(job.job_id, "stop")
        release.set()
        stopped = wait(owner, job.job_id, "stopped")
        assert stopped.result is None and owner.current("123") is None
        assert len(outputs(root)) == 1
    finally:
        release.set()
        owner.close()
    reopened = ManagedNovelJobs(root, source())
    try:
        assert find(reopened, job.job_id).state == "stopped"
        assert reopened.current("123") is None
    finally:
        reopened.close()


def test_retry_increments_attempt_and_operation_stays_the_same(tmp_path, monkeypatch):
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    original, calls = owner.store.save, []

    def save(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("synthetic private diagnostic")
        return original(*args, **kwargs)
    monkeypatch.setattr(owner.store, "save", save)
    try:
        intent = NovelJobIntent("123", "91")
        job = owner.enqueue(intent, "retry")
        error = wait(owner, job.job_id, "error")
        assert error.error_code == "runner_failed" and error.result is None
        assert owner.enqueue(intent, "retry") == error
        assert owner.jobs.control(job.job_id, "retry", expected_control_revision=error.control_revision)
        done = wait(owner, job.job_id, "done")
        assert done.attempt == 2 and owner.jobs.operation_ids(job.job_id) == ("retry",)
        assert len(outputs(owner.root)) == 1
    finally:
        owner.close()


@pytest.mark.parametrize("changes", [{"id": "124"}, {"userId": "92"}, {"isMasked": True},
                                      {"textEmbeddedImages": {"1": {}}}, {"userName": "bad\nheader"},
                                      {"content": ""}])
def test_invalid_fixture_is_error_without_output_or_sensitive_diagnostic(tmp_path, changes):
    owner = ManagedNovelJobs(tmp_path / "novels", source(**changes))
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "invalid")
        error = wait(owner, job.job_id, "error")
        assert error.error_code == "runner_failed" and error.result is None
        assert owner.current("123") is None and outputs(owner.root) == []
    finally:
        owner.close()


@pytest.mark.parametrize("point,expected", [("before-rename", "error"), ("after-rename", "done"),
                                           ("after-active", "done")])
def test_actual_process_exit_reconciles_only_exact_durable_proof(tmp_path, point, expected):
    root = tmp_path / "novels"
    script = r'''
import os, sys
from pathlib import Path
from threading import Event
from pixivvault_nicegui.managed_novel_jobs import ManagedNovelJobs, FixtureNovelSource, NovelJobIntent
from pixivvault_nicegui.job_service import JobContext
body = {"id":"123", "userId":"91", "userName":"Synthetic Author", "title":"Synthetic", "content":"fixture"}
owner = ManagedNovelJobs(Path(sys.argv[1]), FixtureNovelSource({"123":body}))
point = sys.argv[2]
original = owner.store.save
def save(*args, **kwargs):
    if point == "before-rename":
        journal = kwargs["before_publish"]
        def stop(candidate):
            journal(candidate)
            os._exit(73)
        kwargs["before_publish"] = stop
    result = original(*args, **kwargs)
    if point == "after-rename":
        os._exit(73)
    return result
owner.store.save = save
progress = JobContext.progress
def stop_after_active(self, done, total):
    if done == 1 and point == "after-active":
        os._exit(73)
    return progress(self, done, total)
JobContext.progress = stop_after_active
owner.enqueue(NovelJobIntent("123", "91"), "crash-operation")
Event().wait(15)
os._exit(74)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(root), point],
                            env=env, capture_output=True, timeout=25)
    assert result.returncode == 73, (result.returncode, result.stderr)
    owner = ManagedNovelJobs(root, source())
    try:
        job = owner.jobs.snapshot()[0]
        assert job.state == expected
        assert owner.jobs.operation_ids(job.job_id) == ("crash-operation",)
        if expected == "done":
            assert job.result == owner.current("123").relative_receipt
            assert len(outputs(root)) == 1
            assert owner.enqueue(NovelJobIntent("123", "91"), "crash-operation") == job
        else:
            assert job.error_code == "interrupted" and outputs(root) == []
            assert owner.jobs.control(job.job_id, "retry", expected_control_revision=job.control_revision)
            done = wait(owner, job.job_id, "done")
            assert done.attempt == 2 and len(outputs(root)) == 1
            assert len(list((root / "exports" / ".staging").iterdir())) == 1
    finally:
        owner.close()


def test_visible_publish_with_directory_fsync_failure_is_reconciled_without_duplicate(tmp_path, monkeypatch):
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    original = storage._fsync_dir
    failed = []

    def fsync(path):
        if path == owner.store.root / "outputs" and not failed:
            failed.append(True)
            raise OSError("synthetic fsync")
        return original(path)
    monkeypatch.setattr(storage, "_fsync_dir", fsync)
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "uncertain")
        wait(owner, job.job_id, "done")
        assert failed and len(outputs(owner.root)) == 1
        assert owner.current("123") is not None
    finally:
        owner.close()


def test_journal_fsync_uncertainty_fences_writer_until_reopen(tmp_path, monkeypatch):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    original = managed._fsync_dir

    def fsync(path):
        if path == root:
            value = json.loads((root / "novel-journal.json").read_bytes())
            if value["active"]:
                raise OSError("synthetic active-pointer durability failure")
        return original(path)
    monkeypatch.setattr(managed, "_fsync_dir", fsync)
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "journal-uncertain")
        wait(owner, job.job_id, "error")
        with pytest.raises(ManagedNovelError):
            owner.current("123")
        with pytest.raises(ManagedNovelError):
            owner.enqueue(NovelJobIntent("124", "91"), "another")
    finally:
        owner.close()
    monkeypatch.setattr(managed, "_fsync_dir", original)
    reopened = ManagedNovelJobs(root, source())
    try:
        error = find(reopened, job.job_id)
        assert reopened.jobs.control(job.job_id, "retry", expected_control_revision=error.control_revision)
        done = wait(reopened, job.job_id, "done")
        assert done.attempt == 2 and len(outputs(root)) == 1
    finally:
        reopened.close()


def test_corrupt_done_output_blocks_reopen_and_preserves_bytes(tmp_path):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "corrupt")
        done = wait(owner, job.job_id, "done")
    finally:
        owner.close()
    damaged = root / "exports" / Path(done.result).parent / "novel.txt"
    damaged.write_bytes(b"synthetic damaged data")
    with pytest.raises(ValueError):
        ManagedNovelJobs(root, source())
    assert damaged.read_bytes() == b"synthetic damaged data"


def test_second_owner_is_refused_without_touching_first_journal(tmp_path):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "owned")
        wait(owner, job.job_id, "done")
        before = (root / "novel-journal.json").read_bytes()
        with pytest.raises(JobServiceError):
            ManagedNovelJobs(root, source())
        assert (root / "novel-journal.json").read_bytes() == before
    finally:
        owner.close()


def test_unowned_root_and_live_mode_and_arbitrary_transport_are_refused(tmp_path):
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    sentinel = unowned / "preserve.txt"
    sentinel.write_text("synthetic unrelated bytes", encoding="utf-8")
    with pytest.raises(ManagedNovelError):
        ManagedNovelJobs(unowned, source())
    assert sentinel.read_text(encoding="utf-8") == "synthetic unrelated bytes"
    with pytest.raises(ManagedNovelError):
        ManagedNovelJobs(tmp_path / "transport", object())
    assert not (tmp_path / "transport").exists()
    with pytest.raises(ValueError):
        ApplicationServices(novel_fixture_root=tmp_path / "mixed", novel_fixture_source=source(), live_client=object())
    assert not (tmp_path / "mixed").exists()


def test_get_services_keeps_application_ownership_and_rejects_replacement(tmp_path):
    from types import SimpleNamespace
    app = SimpleNamespace(state=SimpleNamespace())
    fixture = source()
    services = get_services(app, novel_fixture_root=tmp_path / "novels", novel_fixture_source=fixture)
    try:
        assert get_services(app) is services
        assert get_services(app, novel_fixture_source=fixture) is services
        with pytest.raises(RuntimeError):
            get_services(app, novel_fixture_source=source())
        with pytest.raises(RuntimeError):
            get_services(app, novel_fixture_root=tmp_path / "other")
    finally:
        services.close()


def test_stale_attempt_context_cannot_publish_callback(tmp_path, monkeypatch):
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    original = owner.store.save
    monkeypatch.setattr(owner.store, "save", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture")))
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "stale-attempt")
        error = wait(owner, job.job_id, "error")
        stale = JobContext(owner.jobs, job.job_id, error.attempt)
        monkeypatch.setattr(owner.store, "save", original)
        assert owner.jobs.control(job.job_id, "retry")
        called = []
        with pytest.raises(JobCancelled):
            stale.publish(lambda: called.append(True))
        assert called == []
        wait(owner, job.job_id, "done")
    finally:
        owner.close()


def test_current_reader_during_storage_hook_cannot_deadlock_publication(tmp_path, monkeypatch):
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    staged, release, reader_waiting = Event(), Event(), Event()
    reader = None
    result = []
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "initial")
        wait(owner, job.job_id, "done")
        old = owner.current("123")
        original_verify = owner.store._verify
        original_reconcile = owner.store.reconcile

        def verify(folder, generation, receipt, completion):
            value = original_verify(folder, generation, receipt, completion)
            if folder.parent.name == ".staging":
                staged.set()
                assert release.wait(10)
            return value

        def reconcile(*args, **kwargs):
            if current_thread().name == "novel-reader":
                reader_waiting.set()
            return original_reconcile(*args, **kwargs)

        monkeypatch.setattr(owner.store, "_verify", verify)
        monkeypatch.setattr(owner.store, "reconcile", reconcile)
        update = NovelJobIntent("123", "91", expected_relative_receipt=old.relative_receipt,
                                expected_receipt_sha256=old.receipt_sha256)
        newer = owner.enqueue(update, "update")
        assert staged.wait(10)

        def read():
            try:
                result.append(owner.current("123"))
            except ManagedNovelError:
                result.append("generation-changed")
        reader = Thread(target=read, name="novel-reader", daemon=True)
        reader.start()
        assert reader_waiting.wait(10)
        release.set()
        wait(owner, newer.job_id, "done")
        reader.join(timeout=10)
        assert not reader.is_alive() and len(result) == 1
        assert result[0] in (old, "generation-changed")
    finally:
        release.set()
        owner.close()
        if reader is not None:
            reader.join(timeout=10)


def test_graceful_close_after_rename_recovers_without_refetch(tmp_path, monkeypatch):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    published, release = Event(), Event()
    original = owner.store.save
    failures = []

    def save(*args, **kwargs):
        saved = original(*args, **kwargs)
        published.set()
        assert release.wait(10)
        return saved

    def close():
        try:
            owner.close()
        except Exception as error:
            failures.append(type(error).__name__)

    monkeypatch.setattr(owner.store, "save", save)
    closer = None
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "close-published")
        assert published.wait(10)
        closer = Thread(target=close, daemon=True)
        closer.start()
        wait(owner, job.job_id, "stopping")
        release.set()
        closer.join(timeout=10)
        assert not closer.is_alive() and failures == []
    finally:
        release.set()
        owner.close()
        if closer is not None:
            closer.join(timeout=10)

    def no_fetch(*args):
        raise AssertionError("receipt recovery must not refetch")
    monkeypatch.setattr(FixtureNovelSource, "fetch", no_fetch)
    reopened = ManagedNovelJobs(root, source())
    try:
        done = find(reopened, job.job_id)
        assert done.state == "done" and done.result == reopened.current("123").relative_receipt
        assert len(outputs(root)) == 1
    finally:
        reopened.close()


def test_missing_predecessor_is_error_and_cannot_be_silently_replaced(tmp_path):
    owner = ManagedNovelJobs(tmp_path / "novels", source())
    try:
        job = owner.enqueue(NovelJobIntent("123", "91"), "original")
        wait(owner, job.job_id, "done")
        old = owner.current("123")
        damaged = owner.store.root / Path(old.relative_receipt).parent / "novel.txt"
        damaged.write_bytes(b"synthetic corruption")
        intent = NovelJobIntent("123", "91", expected_relative_receipt=old.relative_receipt,
                                expected_receipt_sha256=old.receipt_sha256)
        update = owner.enqueue(intent, "update-corrupt")
        error = wait(owner, update.job_id, "error")
        assert error.result is None and len(outputs(owner.root)) == 1
        assert damaged.read_bytes() == b"synthetic corruption"
        with pytest.raises(ValueError):
            owner.current("123")
    finally:
        owner.close()


def test_delete_after_rename_does_not_revive_job_and_operation_tombstone_survives(tmp_path, monkeypatch):
    root = tmp_path / "novels"
    owner = ManagedNovelJobs(root, source())
    published, release, exited = Event(), Event(), Event()
    original_save = owner.store.save
    original_runner = owner.jobs._runner

    def save(*args, **kwargs):
        saved = original_save(*args, **kwargs)
        published.set()
        assert release.wait(10)
        return saved

    def runner(*args):
        try:
            return original_runner(*args)
        finally:
            exited.set()

    monkeypatch.setattr(owner.store, "save", save)
    monkeypatch.setattr(owner.jobs, "_runner", runner)
    intent = NovelJobIntent("123", "91")
    try:
        job = owner.enqueue(intent, "delete-published")
        assert published.wait(10)
        assert owner.jobs.control(job.job_id, "delete")
        release.set()
        assert exited.wait(10)
        assert owner.jobs.snapshot() == () and owner.current("123") is None
    finally:
        release.set()
        owner.close()
    reopened = ManagedNovelJobs(root, source())
    try:
        assert reopened.jobs.snapshot() == () and reopened.current("123") is None
        with pytest.raises(JobServiceError):
            reopened.enqueue(intent, "delete-published")
        assert len(outputs(root)) == 1
    finally:
        reopened.close()
