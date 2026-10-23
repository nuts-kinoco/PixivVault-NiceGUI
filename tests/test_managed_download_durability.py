"""Deterministic filesystem-publication / SQLite-commit boundary regressions.

Complete outputs are never deleted to conceal a stopped or faulted queue row.
These tests cover process recovery, not power-loss durability of Windows rename.
"""

from io import BytesIO
import json
import sqlite3
from threading import Event

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.job_service import JobService, JobStorageError
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads


class Source:
    def __init__(self):
        self.images = 0
        stream = BytesIO()
        Image.new("RGB", (12, 8), (8, 34, 217)).save(stream, "PNG")
        self.payload = stream.getvalue()

    def verify_identity(self):
        return "456"

    def get_artwork_original_pages(self, work_id):
        return ("https://i.pximg.net/img-original/img/2026/10/01/12/00/00/123_p0.png",)

    def get_image_bytes(self, url):
        self.images += 1
        return self.payload


def writer(tmp_path):
    record = RemoteBookmark("123", "illustration", "show", "合成", "456", "合成作者",
                            (), "", "", 1, 0, 0)
    source = Source()
    return ManagedPixivDownloads(tmp_path / "managed", source, resolve_record=lambda _: record), source


def wait_terminal(service, job_id, expected):
    with service._condition:
        assert service._condition.wait_for(lambda: any(j.job_id == job_id and j.state == expected
            for j in service.snapshot()), timeout=8)
    return next(j for j in service.snapshot() if j.job_id == job_id)


@pytest.mark.parametrize("action", ["stop", "delete"])
def test_control_after_atomic_publish_retains_outputs_and_requeue_reuses(tmp_path, monkeypatch, action):
    save, source = writer(tmp_path)
    reached, release = Event(), Event()
    verify = save._receipt
    def after_publish(relative):
        result = verify(relative)
        reached.set()
        assert release.wait(8), "test did not release the published image"
        return result
    monkeypatch.setattr(save, "_receipt", after_publish)
    service = JobService(save.run, save.storage_path)
    try:
        job = service.enqueue("pixiv-illust", "123", "合成", operation_id="first")
        assert reached.wait(8)
        assert service.control(job.job_id, action)
        if action == "stop":
            assert service.snapshot()[0].state == "stopping"
        release.set()
        with service._condition:
            assert service._condition.wait_for(lambda: service._active is None, timeout=8)
        if action == "stop":
            assert service.snapshot()[0].state == "stopped"
            assert service.snapshot()[0].result is None
        else:
            assert service.snapshot() == ()
        monkeypatch.setattr(save, "_receipt", verify)
        before = save.saved_receipts()
        assert len(before) == 1
        assert save.read_saved_page(before[0][0], 0, before[0][2]) == source.payload
    finally:
        release.set()
        service.close()
    reopened = JobService(save.run, save.storage_path)
    try:
        assert reopened._thread is None
        count = source.images
        repeat = reopened.enqueue("pixiv-illust", "123", "合成", operation_id="second")
        done = wait_terminal(reopened, repeat.job_id, "done")
        assert done.result == before[0][0]
        assert done.done == done.total == 1
        assert source.images == count
        assert save.saved_receipts() == before
    finally:
        reopened.close()
        save.close()


def test_terminal_database_failure_never_publishes_done_or_deletes_originals(tmp_path, monkeypatch):
    save, source = writer(tmp_path)
    reached, release = Event(), Event()
    verify = save._receipt
    def after_publish(relative):
        result = verify(relative)
        reached.set()
        assert release.wait(8)
        return result
    monkeypatch.setattr(save, "_receipt", after_publish)
    service = JobService(save.run, save.storage_path)
    try:
        original_write = service._store.write
        def terminal_failure(jobs, operations):
            if any(job.state == "done" for job in jobs.values()):
                raise JobStorageError("injected terminal commit failure")
            original_write(jobs, operations)
        monkeypatch.setattr(service._store, "write", terminal_failure)
        service.enqueue("pixiv-illust", "123", "合成", operation_id="before-fault")
        assert reached.wait(8)
        release.set()
        with service._condition:
            assert service._condition.wait_for(lambda: service.faulted, timeout=8)
        assert all(job.state != "done" for job in service.snapshot())
        with sqlite3.connect(save.storage_path.as_uri() + "?mode=ro", uri=True) as connection:
            persisted = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM jobs")]
        assert persisted and all(job["state"] != "done" for job in persisted)
        monkeypatch.setattr(save, "_receipt", verify)
        receipt = save.saved_receipts()[0]
        assert save.read_saved_page(receipt[0], 0, receipt[2]) == source.payload
    finally:
        release.set()
        service.close()
    recovered = JobService(save.run, save.storage_path)
    try:
        assert recovered._thread is None
        assert all(job.state == "stopped" for job in recovered.snapshot())
        count = source.images
        again = recovered.enqueue("pixiv-illust", "123", "合成", operation_id="after-fault")
        result = wait_terminal(recovered, again.job_id, "done")
        assert result.result == receipt[0] and source.images == count
    finally:
        recovered.close()
        save.close()
