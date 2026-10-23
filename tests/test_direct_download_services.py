"""Non-bookmarked artwork ID lookup, durable managed queue and library display."""

import asyncio
from io import BytesIO

from PIL import Image
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView


def png(color):
    out = BytesIO()
    Image.new("RGB", (120, 84), color).save(out, format="PNG")
    return out.getvalue()


def record(title="任意ID作品"):
    return RemoteBookmark("123", "illustration", "show", title, "23", "架空作者",
        ("手動取得",), "", "2026-10-23T00:00:00+09:00", 2, 0, 0)


class Source:
    def __init__(self):
        self.calls = []
        self.title = "任意ID作品"
        self.fail = False

    def verify_identity(self):
        self.calls.append("identity")
        return "55"

    def get_artwork_download_record(self, wid):
        self.calls.append(("detail", wid))
        if self.fail:
            raise RuntimeError("private response not for UI")
        return record(self.title)

    def get_bookmarked_works(self, identity, rest_type):
        self.calls.append(("bookmarks", rest_type))
        return ()

    def get_bookmarked_novels(self, identity, rest_type):
        self.calls.append(("novels", rest_type))
        return ()

    def get_artwork_original_pages(self, wid):
        assert wid == "123"
        self.calls.append("originals")
        return tuple(f"https://i.pximg.net/img-original/img/2026/10/23/01/02/03/123_p{i}.png"
                     for i in range(2))

    def get_image_bytes(self, url):
        self.calls.append("image")
        return png("blue" if "_p1." in url else "red")

    def close(self):
        self.calls.append("close")


def finished(service, job_id):
    with service._condition:
        assert service._condition.wait_for(
            lambda: any(j.job_id == job_id and j.state in {"done", "error", "stopped"}
                        for j in service.snapshot()), timeout=8)
    return next(j for j in service.snapshot() if j.job_id == job_id)


@pytest.mark.asyncio
async def test_direct_id_queues_non_bookmarked_original_and_reopens_library(tmp_path):
    source = Source()
    root = tmp_path / "managed"
    services = ApplicationServices(live_client=source, pixiv_download_root=root)
    owner = "a" * 32
    try:
        assert not source.calls
        await services.refresh_bookmark_cache()
        assert services.download_candidates() == () and services.bookmark_remote_snapshot() == ()
        found = await services.lookup_managed_work_id("123", owner=owner)
        assert found == record() and services.download_candidates(owner=owner) == (found,)
        assert services.download_candidates() == (), "manual candidates require their view owner"
        assert services.bookmark_remote_snapshot() == (), "lookup must never bookmark a work"
        job = await asyncio.to_thread(services.enqueue_managed_download,
                                      found, "direct-first", owner=owner)
        result = await asyncio.to_thread(finished, services.jobs, job.job_id)
        assert result.state == "done" and result.done == result.total == 2
        assert source.calls.count("image") == 2
        snapshot = await services.refresh_library_snapshot()
        assert [work.work_id for work in snapshot.works] == ["123"]
        provider = services.acquire_library_images()
        try:
            blob = await asyncio.to_thread(services.images.get_versioned,
                provider.token, "123", 1, "view")
            with Image.open(BytesIO(blob.data)) as image:
                assert image.getpixel((0, 0))[:3] == (0, 0, 255)
        finally:
            provider.close()
        second = await asyncio.to_thread(services.enqueue_managed_download,
                                         found, "direct-second", owner=owner)
        assert (await asyncio.to_thread(finished, services.jobs, second.job_id)).result == result.result
        assert source.calls.count("image") == 2, "verified saved pages must not be redownloaded"
    finally:
        await asyncio.to_thread(services.close)
    reopened = ApplicationServices(live_client=Source(), pixiv_download_root=root)
    try:
        assert [item.work_id for item in reopened.library_snapshot().works] == ["123"]
        assert reopened.jobs._thread is None, "restart must not run an old job"
    finally:
        await asyncio.to_thread(reopened.close)


@pytest.mark.asyncio
async def test_direct_lookup_rejects_invalid_input_and_keeps_previous_candidate(tmp_path):
    source = Source()
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / "managed")
    owner = "b" * 32
    try:
        for invalid in ("abc", "0", "../123", "123?token=x"):
            with pytest.raises(ValueError, match="invalid work ID"):
                await services.lookup_managed_work_id(invalid, owner=owner)
        assert not source.calls
        original = await services.lookup_managed_work_id("123", owner=owner)
        source.fail = True
        with pytest.raises(RuntimeError, match="private response"):
            await services.lookup_managed_work_id("123", owner=owner)
        assert services.download_candidates(owner=owner) == (original,), "failed fetch must keep last verified record"
        source.fail = False
        source.title = "更新後タイトル"
        updated = await services.lookup_managed_work_id("123", owner=owner)
        assert updated.title == "更新後タイトル"
        with pytest.raises(ValueError, match="selection changed"):
            services.enqueue_managed_download(original, "stale-title", owner=owner)
        assert not services.jobs.snapshot()
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_manual_candidate_ownership_is_enforced_for_independent_clients(tmp_path):
    """A manual lookup authorizes its own view, not an unrelated client's queue."""
    source = Source()
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / "managed")
    client_a = Client(ui.page("/_owned_direct_a"))
    client_b = Client(ui.page("/_owned_direct_b"))
    try:
        with client_a:
            a = ManagedDownloadView(services)
        with client_b:
            b = ManagedDownloadView(services)
        with client_a:
            a.direct_input.set_value("123")
            assert await a.lookup_work_id()
            assert tuple(a.records) == ("123",)
        with client_b:
            assert b._load_cached() and not b.records
            assert b.refreshing is False
            b.select("123", True)
            assert not b.selected
        assert services.download_candidates(owner=b._owner) == ()
        found = a.records["123"]
        with pytest.raises(ValueError, match="selection changed"):
            services.enqueue_managed_download(found, "foreign-owner", owner=b._owner)
        assert not services.jobs.snapshot() and source.calls.count("image") == 0
        with client_a:
            a.select("123", True)
            assert await a.submit()
        result = await asyncio.to_thread(finished, services.jobs, services.jobs.snapshot()[0].job_id)
        assert result.state == "done" and result.total == result.done == 2
        with client_b:
            b.direct_input.set_value("123")
            assert await b.lookup_work_id()
            assert tuple(b.records) == ("123",)
    finally:
        client_a.delete()
        client_b.delete()
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_unpublished_late_lookup_does_not_authorize_a_client(tmp_path):
    source = Source()
    services = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / "managed")
    owner = "a" * 32
    try:
        record_looked_up = await services.lookup_managed_work_id("123", owner=owner, publish=False)
        assert not services.download_candidates(owner=owner)
        with pytest.raises(ValueError, match="selection changed"):
            services.enqueue_managed_download(record_looked_up, "unaccepted", owner=owner)
        services.remember_managed_work_id(owner, record_looked_up)
        assert services.download_candidates(owner=owner) == (record_looked_up,)
        services.release_managed_owner(owner)
        assert services.download_candidates(owner=owner) == ()
        with pytest.raises(ValueError, match="selection changed"):
            services.enqueue_managed_download(record_looked_up, "after-close", owner=owner)
    finally:
        await asyncio.to_thread(services.close)
