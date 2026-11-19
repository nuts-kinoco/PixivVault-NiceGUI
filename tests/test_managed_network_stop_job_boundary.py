"""Offline integration of the opt-in guarded reader with real managed job workers.

Generated records, Cookie and PNG only. This is NOT bootstrap/product wiring or
proof that every scheduler/GET path has adopted the shared network gate.
"""

from io import BytesIO
from threading import Event
import time

from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_reader import ManagedLivePixivReadClient
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


_DATE = "2026-10-31"


def _synthetic_cookie(path):
    cookie = path / "generated-cookie.txt"
    cookie.write_text(
        "# Netscape HTTP Cookie File\n"
        f".pixiv.net\tTRUE\t/\tTRUE\t{int(time.time()) + 86400}\tPHPSESSID\t55_generatedonly\n",
        encoding="utf-8",
    )
    return cookie


class _GeneratedMetadataReader(ManagedLivePixivReadClient):
    """Override metadata with fictional results, retain the real guarded image GET."""

    def verify_identity(self):
        self._verified_identity = '999'
        return self._verified_identity

    def get_artwork_update_revision(self, work_id, author_id):
        assert author_id == "24" and work_id in ("123", "124")
        return {
            "work_id": work_id,
            "author_id": author_id,
            "page_count": 1,
            "illustration_type": 0,
            "update_date": _DATE,
        }

    def get_artwork_original_pages(self, work_id):
        assert work_id in ("123", "124")
        return (f"https://i.pximg.net/generated/{work_id}_p0.png",)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_guarded_reader_fences_queued_managed_image_get_after_fatal_status(tmp_path, status):
    root = tmp_path / "owned-managed"
    # A fresh ManagedPixivDownloads root rejects arbitrary files until its
    # ownership marker exists. Keep the gate's durable state in an adjacent
    # explicitly owned namespace, never pre-seed the writer's storage root.
    gate_path = tmp_path / "owned-network-stop" / "network-stop.sqlite"
    gate = ManagedNetworkStopGate(gate_path)
    data = BytesIO()
    Image.new("RGB", (16, 16), (12, 24, 36)).save(data, "PNG")
    image_bytes = data.getvalue()
    remote_calls = []

    def transport(request, *_args):
        assert request.get_method() == "GET" and request.data is None
        assert request.get_header("Cookie") is None
        work_id = request.full_url.rsplit("/", 1)[-1].split("_", 1)[0]
        assert work_id in ("123", "124")
        remote_calls.append(work_id)
        if work_id == "123":
            raise HTTPStatusRejected(status)
        return image_bytes

    reader = _GeneratedMetadataReader(_synthetic_cookie(tmp_path), gate, transport=transport)
    records = {
        wid: RemoteBookmark(wid, "illustration", "show", "Generated " + wid,
                            "24", "Generated Author", ("offline",), "", _DATE, 1, 0, 0)
        for wid in ("123", "124")
    }
    intents = {wid: ManagedAuthorIntent(record, "follow", _DATE, None, "new")
               for wid, record in records.items()}
    writer = ManagedPixivDownloads(root, reader, resolve_record=records.get)
    started, release = Event(), Event()

    def runner(job, context):
        if job.target_id == "123":
            started.set()
            if not release.wait(5):
                raise RuntimeError("synthetic test worker handshake failed")
        return writer.run(job, context, intent=intents[job.target_id])

    queue = JobService(runner)
    try:
        first = queue.enqueue("pixiv-illust", "123", "Generated first",
                              operation_id="generated-first")
        assert started.wait(5), "first generated worker did not start"
        second = queue.enqueue("pixiv-illust", "124", "Generated second",
                               operation_id="generated-second")
        release.set()
        with queue._condition:
            completed = queue._condition.wait_for(
                lambda: all(any(j.job_id == jid and j.state in ("done", "error", "stopped")
                                for j in queue.snapshot())
                            for jid in (first.job_id, second.job_id)),
                timeout=10,
            )
        assert completed, "generated job queue did not settle"
        jobs = {job.target_id: job for job in queue.snapshot()}
        assert jobs["123"].state == "error"
        assert jobs["124"].state != "done"
        # Even if the worker proceeds to the second durable job, no second
        # Pixiv GET can cross the shared reader transport after the first fatal.
        assert remote_calls == ["123"]
        assert writer.saved_receipts() == ()
        assert gate.snapshot().blocked and gate.snapshot().status_code == status
        assert ManagedNetworkStopGate(gate_path).snapshot().status_code == status
    finally:
        release.set()
        queue.close()
        writer.close()
        reader.close()
