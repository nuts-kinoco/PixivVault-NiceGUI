"""Actual synthetic novel saving and publication recovery, with no network."""
import socket
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import novel_storage as storage
from pixivvault_nicegui.job_service import JobCancelled
from pixivvault_nicegui.managed_novel_jobs import (
    FixtureNovelSource, ManagedNovelJobs, NovelJobIntent,
)


PRIVATE = "synthetic-cookie=private token=private S:/private/example.txt"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("no network permitted")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def source():
    return FixtureNovelSource({"1": {
        "id": "1", "userId": "2", "userName": "synthetic author",
        "title": "synthetic title", "content": "synthetic novel text",
    }})


def wait_terminal(owner, attempt=1):
    with owner.jobs._condition:
        assert owner.jobs._condition.wait_for(
            lambda: owner.jobs.snapshot()
            and owner.jobs.snapshot()[0].attempt >= attempt
            and owner.jobs.snapshot()[0].state in {"done", "error", "stopped"},
            timeout=5)
    return owner.jobs.snapshot()[0]


def check_tooltip(owner, phrase):
    from nicegui import ui
    from pixivvault_nicegui.ui.job_views import QueueView
    with ui.column() as holder:
        view = QueueView(SimpleNamespace(jobs=owner.jobs, artworks=()), failed_only=True)
    try:
        message = view.rows[owner.jobs.snapshot()[0].job_id]["tooltip"].text
        assert phrase in message and PRIVATE not in message
    finally:
        view.close()
        holder.delete()


@pytest.mark.parametrize("fault,code,phrase", [
    ("write", "novel_storage_io", "ファイル操作"),
    ("integrity", "novel_storage_integrity", "整合性"),
    ("uncertain", "novel_publication_unconfirmed", "保存結果"),
    ("known_success", "", ""),
])
def test_actual_novel_save_and_recovery(tmp_path, monkeypatch, fault, code, phrase):
    root = tmp_path / "generated-novels"
    calls = {"fetch": 0, "save": 0}
    original_fetch = FixtureNovelSource.fetch
    original_save = storage.NovelExportStore.save
    def fetch(self, work_id):
        calls["fetch"] += 1
        return original_fetch(self, work_id)
    def save(self, *args, **kwargs):
        calls["save"] += 1
        return original_save(self, *args, **kwargs)
    monkeypatch.setattr(FixtureNovelSource, "fetch", fetch)
    monkeypatch.setattr(storage.NovelExportStore, "save", save)
    owner = ManagedNovelJobs(root, source())
    outputs = owner.store.root / "outputs"
    original_write, original_sync, original_read = storage._write_new, storage._fsync_dir, storage._read_bounded
    barrier = {"published": False, "block_read": fault == "uncertain"}
    def write(path, data):
        if path.name == "novel.txt":
            if fault == "write":
                raise OSError(PRIVATE)
            if fault == "integrity":
                data += b"synthetic-corruption"
        return original_write(path, data)
    def sync(path):
        if path == outputs and fault in {"uncertain", "known_success"}:
            barrier["published"] = True
            raise OSError(PRIVATE)
        return original_sync(path)
    def read(path, maximum):
        if barrier["published"] and barrier["block_read"] and path.parent.parent == outputs:
            raise PermissionError(PRIVATE)
        return original_read(path, maximum)
    monkeypatch.setattr(storage, "_write_new", write)
    monkeypatch.setattr(storage, "_fsync_dir", sync)
    monkeypatch.setattr(storage, "_read_bounded", read)
    intent = NovelJobIntent("1", "2")
    try:
        owner.enqueue(intent, "synthetic-operation")
        first = wait_terminal(owner)
        assert first.state == ("done" if fault == "known_success" else "error")
        assert first.error_code == code
        assert calls == {"fetch": 1, "save": 1}
        assert len(tuple(outputs.iterdir())) == (1 if fault in {"uncertain", "known_success"} else 0)
        proof = owner._journal["jobs"][first.job_id]["pending"]
        assert (proof is not None) == (fault in {"uncertain", "known_success"})
        if code:
            assert first.result is None
            check_tooltip(owner, phrase)
        else:
            assert owner.current("1").relative_receipt == first.result
        assert PRIVATE.encode() not in (root / "jobs.sqlite3").read_bytes()
        assert PRIVATE not in (root / "novel-journal.json").read_text()
    finally:
        owner.close()

    reopened = ManagedNovelJobs(root, source())
    try:
        assert reopened.jobs.snapshot()[0] == first
        assert calls == {"fetch": 1, "save": 1}
        if fault == "uncertain":
            # A user retry while verification is still impossible must not
            # fetch the body or write another generation.
            assert reopened.jobs.control(first.job_id, "retry")
            blocked = wait_terminal(reopened, 2)
            assert blocked.state == "error" and blocked.error_code == code
            assert calls == {"fetch": 1, "save": 1}
            assert len(tuple(outputs.iterdir())) == 1
            assert reopened._journal["jobs"][first.job_id]["pending"] == proof
            # Once the saved bytes can actually be verified, the same proof
            # recovers success without fetching or saving again.
            barrier["block_read"] = False
            assert reopened.jobs.control(first.job_id, "retry")
            recovered = wait_terminal(reopened, 3)
            assert recovered.state == "done" and recovered.error_code == ""
            assert recovered.result == proof["relative_receipt"]
            assert calls == {"fetch": 1, "save": 1}
            assert len(tuple(outputs.iterdir())) == 1
            assert reopened.current("1").relative_receipt == recovered.result
    finally:
        reopened.close()


@pytest.mark.parametrize("error,state,code", [
    (ValueError(PRIVATE), "error", "runner_failed"),
    (TimeoutError(PRIVATE), "error", "operation_timeout"),
    (JobCancelled(), "stopped", ""),
])
def test_unknown_failure_and_cancellation_unchanged(tmp_path, monkeypatch, error, state, code):
    def fetch(*_):
        raise error
    monkeypatch.setattr(FixtureNovelSource, "fetch", fetch)
    owner = ManagedNovelJobs(tmp_path / "generated-novels", source())
    try:
        owner.enqueue(NovelJobIntent("1", "2"), "synthetic-operation")
        result = wait_terminal(owner)
        assert (result.state, result.error_code, result.result) == (state, code, None)
        assert not tuple((owner.store.root / "outputs").iterdir())
    finally:
        owner.close()


def test_novel_error_contract_rejects_private_code():
    from pixivvault_nicegui.job_failure import job_failure_code
    from pixivvault_nicegui.managed_novel_jobs import ManagedNovelError
    error = ManagedNovelError(reason_code=PRIVATE)
    assert isinstance(error, ValueError)
    assert str(error) == "managed novel operation failed"
    assert job_failure_code(error) == "runner_failed"
    error.reason_code = [PRIVATE]
    assert job_failure_code(error) == "runner_failed"
