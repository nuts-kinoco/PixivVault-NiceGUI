"""FL-C20/O05: 元の meta_backfill_enabled 相当の自動後埋めを移植版へ復元する。

生成した画像と偽の転送層だけを使い、Pixiv・Cookie・実データには触れない。
"""
import asyncio
from io import BytesIO
from threading import Event
from types import SimpleNamespace
import time

from PIL import Image
import pytest

from pixivvault_nicegui.metadata_transport import MetadataFetchResult
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.preferences import SPEC_BY_KEY
from pixivvault_nicegui.services import ApplicationServices


def png():
    out = BytesIO()
    Image.new("RGB", (40, 30), "red").save(out, "PNG")
    return out.getvalue()


WORKS = {"202": "23", "204": "23", "205": "24"}


class Source:
    def __init__(self):
        self.closed = False

    def verify_identity(self):
        self._verified_identity = "12"
        return self._verified_identity

    def get_bookmarked_works(self, identity, rest_type):
        if rest_type == "show":
            for wid, uid in WORKS.items():
                yield {"id": wid, "title": "合成" + wid, "user_id": uid,
                       "user_name": "合成作者", "tags": [], "type": 0,
                       "thumb_url": f"https://i.pximg.net/synthetic/{wid}.png", "page_count": 1}

    def get_bookmarked_novels(self, identity, rest_type):
        return iter(())

    def get_artwork_original_pages(self, work_id):
        return (f"https://i.pximg.net/img-original/img/2026/10/01/12/00/00/{work_id}_p0.png",)

    def get_artwork_pages(self, work_id):
        return (f"https://i.pximg.net/synthetic/{work_id}-p0.png",)

    def get_image_bytes(self, url):
        return png()

    def close(self):
        self.closed = True


class Transport:
    def __init__(self):
        self.calls = []
        self.fail = {}

    def verify_identity(self):
        self.calls.append("identity")
        return "12"

    def fetch_detail(self, work_id, author_id):
        self.calls.append(work_id)
        if work_id in self.fail:
            raise self.fail[work_id]
        return MetadataFetchResult(work_id, author_id,
                                   {"bookmark_count": 7, "meta_source": "illust_detail"}, False)


def wait_terminal(jobs, job_id):
    with jobs._condition:
        assert jobs._condition.wait_for(lambda: any(
            j.job_id == job_id and j.state in {"done", "error", "stopped"}
            for j in jobs.snapshot()), timeout=10)


async def make_services(tmp_path, *, preferences=True):
    services = ApplicationServices(
        live_client=Source(), pixiv_download_root=tmp_path / "managed",
        preferences_root=(tmp_path / "prefs") if preferences else None)
    await services.refresh_bookmark_cache()
    for record in services.download_candidates():
        job = await asyncio.to_thread(services.enqueue_managed_download, record, "t" + record.work_id)
        await asyncio.to_thread(wait_terminal, services.jobs, job.job_id)
    return services


def test_preference_specs_default_on_with_original_names():
    meta = SPEC_BY_KEY["meta_backfill_enabled"]
    assert meta.default == "1" and meta.choices == ("0", "1")
    assert meta.label == "メタデータ（ブックマーク数など）をバックグラウンドで自動的に埋める"
    assert meta.group == "作品メタデータ"


@pytest.mark.asyncio
async def test_auto_enabled_defaults_on_and_follows_preference(tmp_path):
    services = await make_services(tmp_path)
    try:
        assert services.metadata_auto_enabled() is True
        snap = services.preferences.snapshot()
        services.preferences.update({"meta_backfill_enabled": "0"}, expected_revision=snap.revision)
        assert services.metadata_auto_enabled() is False
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_no_preference_store_means_no_automatic_run(tmp_path):
    services = await make_services(tmp_path / "x", preferences=False)
    try:
        assert services.metadata_auto_enabled() is False
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_bulk_run_fills_every_saved_work_and_reports_n_of_m(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        before = services.managed_metadata_status()
        assert (before.filled, before.total, before.remaining) == (0, 3, 3)
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.filled == 3 and summary.stopped == ""
        assert transport.calls.count("identity") == 1
        assert sorted(c for c in transport.calls if c != "identity") == ["202", "204", "205"]
        after = services.managed_metadata_status()
        assert (after.filled, after.total, after.remaining, after.gave_up) == (3, 3, 0, 0)
        assert "3 / 3 件" in after.text and "残り 0" in after.text
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_nothing_left_means_zero_requests_even_identity(tmp_path):
    services = await make_services(tmp_path)
    try:
        await asyncio.to_thread(services.run_managed_metadata_all, transport=Transport())
        again = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=again)
        assert summary.target == 0 and again.calls == []
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_network_stop_gate_blocks_before_any_request_and_leaves_ledger(tmp_path):
    services = await make_services(tmp_path)
    try:
        def blocked():
            raise RuntimeError("stopped")
        services.managed_network_stop = SimpleNamespace(require_allowed=blocked)
        transport = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.stopped == "network_stopped" and transport.calls == []
        status = services.managed_metadata_status()
        assert status.filled == 0 and status.blocked == 0 and status.gave_up == 0
    finally:
        services.managed_network_stop = None
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_unfinished_download_wins_and_no_request_is_sent(tmp_path):
    services = await make_services(tmp_path)
    try:
        real_jobs = services.jobs
        services.jobs = SimpleNamespace(snapshot=lambda: (SimpleNamespace(state="running"),))
        transport = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.stopped == "download_busy" and transport.calls == []
        services.jobs = real_jobs
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_global_failure_stops_the_pass_at_the_first_work(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        for wid in WORKS:
            transport.fail[wid] = HTTPStatusRejected(429)
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.stopped == "global_failure"
        assert len([c for c in transport.calls if c != "identity"]) == 1
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_retry_moves_gave_up_back_without_any_request(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        transport.fail["202"] = HTTPStatusRejected(404)
        for _ in range(3):
            await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        status = services.managed_metadata_status()
        assert status.gave_up == 1 and status.filled == 2
        assert "3回試しても取れなかった 1" in status.text
        calls_before = list(transport.calls)
        assert await asyncio.to_thread(services.reset_managed_metadata_gave_up) == 1
        assert transport.calls == calls_before
        assert services.managed_metadata_status().gave_up == 0
        transport.fail.clear()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.filled == 1
        assert services.managed_metadata_status().remaining == 0
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_blocked_after_outage_is_retried_on_a_later_pass(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        transport.fail["202"] = HTTPStatusRejected(503)
        transport.fail["204"] = HTTPStatusRejected(503)
        transport.fail["205"] = HTTPStatusRejected(503)
        first = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert first.stopped == "global_failure"
        transport.fail.clear()
        second = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert second.filled == 3
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_pass_is_capped_so_one_pass_cannot_run_away(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all,
                                          transport=transport, limit=2)
        assert summary.filled == 2 and summary.target == 2
    finally:
        await asyncio.to_thread(services.close)


# --- 自動実行ループ（スケジューラ相当） ---

def test_auto_runner_backs_off_after_global_failure_and_resets_after_success():
    from pixivvault_nicegui.metadata_auto import next_delay
    from pixivvault_nicegui.metadata_runner import MetadataRunSummary as S
    assert next_delay(S(stopped="global_failure"), 0) == (900, 900)
    assert next_delay(S(stopped="global_failure"), 900) == (1800, 1800)
    assert next_delay(S(stopped="global_failure"), 21600)[0] == 21600
    assert next_delay(S(stopped="authentication_unverified"), 0)[0] == 900
    assert next_delay(S(stopped="five_consecutive_unknown_failures"), 0)[0] == 900
    assert next_delay(S(stopped="download_busy"), 1800) == (60, 1800)
    assert next_delay(S(stopped="network_stopped"), 0)[0] == 300
    assert next_delay(S(filled=3, target=3), 1800) == (300, 0)
    assert next_delay(S(), 900) == (1800, 0)


def test_auto_runner_runs_only_while_enabled_and_stops_promptly():
    from pixivvault_nicegui.metadata_auto import MetadataAutoRunner
    from pixivvault_nicegui.metadata_runner import MetadataRunSummary as S
    enabled = [False]
    passes = []
    ran = Event()

    def run_pass():
        passes.append(1)
        ran.set()
        return S()
    runner = MetadataAutoRunner(run_pass, lambda: enabled[0], initial_delay=0.01,
                                intervals=dict(idle=0.05, busy=0.05, stopped=0.05,
                                               backoff_start=0.05, backoff_max=0.2))
    runner.start()
    try:
        assert not ran.wait(0.4) and passes == []
        enabled[0] = True
        runner.wake()
        assert ran.wait(2)
    finally:
        started = time.monotonic()
        runner.stop(timeout=5)
        assert time.monotonic() - started < 4.5   # stop(timeout=5) must not run to its timeout; 2 s was tight under parallel load
    n = len(passes)
    time.sleep(0.2)
    assert len(passes) == n


@pytest.mark.asyncio
async def test_services_auto_thread_fills_when_on_and_not_when_off(tmp_path):
    services = await make_services(tmp_path)
    try:
        transport = Transport()
        snap = services.preferences.snapshot()
        services.preferences.update({"meta_backfill_enabled": "0"}, expected_revision=snap.revision)
        services.start_metadata_auto(transport=transport, initial_delay=0.01,
                                     intervals=dict(idle=0.05, busy=0.05, stopped=0.05,
                                                    backoff_start=0.05, backoff_max=0.2))
        await asyncio.sleep(0.5)
        assert transport.calls == [], "OFF のときは一切通信しない"
        snap = services.preferences.snapshot()
        services.preferences.update({"meta_backfill_enabled": "1"}, expected_revision=snap.revision)
        services.apply_preferences()
        for _ in range(100):
            if services.managed_metadata_status().remaining == 0:
                break
            await asyncio.sleep(0.1)
        assert services.managed_metadata_status().remaining == 0
        n = len(transport.calls)
        snap = services.preferences.snapshot()
        services.preferences.update({"meta_backfill_enabled": "0"}, expected_revision=snap.revision)
        services.apply_preferences()
        await asyncio.sleep(0.3)
        assert len(transport.calls) == n
    finally:
        started = time.monotonic()
        await asyncio.to_thread(services.close)
        assert time.monotonic() - started < 20
