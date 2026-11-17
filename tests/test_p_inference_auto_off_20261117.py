"""checker 指摘 1〜3: 推論の自動起動の設定尊重、待ち時間の保持、壊れた保存済みページの局所化。

偽の転送層と生成した画像だけを使う。Pixiv・Cookie・実データには触れない。
"""
import asyncio
import time

import pytest

import test_i_metadata_auto_20261117 as base
from pixivvault_nicegui.metadata_auto import next_delay
from pixivvault_nicegui.metadata_runner import MetadataRunSummary
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from test_i_metadata_auto_20261117 import Transport, make_services


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# ---- 1: 推論の自動実行が OFF のとき、ダウンロード完了で推論を起こさない ----------------

async def test_download_completion_does_not_wake_inference_when_auto_is_off(tmp_path):
    from test_i_inference_auto_20261117 import make_similarity_services
    services, seen = await make_similarity_services(tmp_path, pref="0")
    try:
        await asyncio.to_thread(services.start_similarity_analysis)  # 手動起動
        assert await asyncio.to_thread(wait_for, lambda: len({w for w, _ in seen}) == 3)
        await asyncio.sleep(0.3)
        n = len(seen)
        base.WORKS["206"] = "23"
        try:
            await services.refresh_bookmark_cache()
            recs = [r for r in services.download_candidates() if r.work_id == "206"]
            job = await asyncio.to_thread(services.enqueue_managed_download, recs[0], "t206")
            await asyncio.to_thread(base.wait_terminal, services.jobs, job.job_id)
        finally:
            del base.WORKS["206"]
        await asyncio.sleep(1.0)
        assert len(seen) == n
    finally:
        await asyncio.to_thread(services.close)


async def test_download_completion_still_wakes_inference_when_auto_is_on(tmp_path):
    from test_i_inference_auto_20261117 import make_similarity_services
    services, seen = await make_similarity_services(tmp_path, pref="1")
    try:
        services.start_similarity_auto()
        assert await asyncio.to_thread(wait_for, lambda: len({w for w, _ in seen}) == 3)
        await asyncio.sleep(0.3)
        base.WORKS["206"] = "23"
        try:
            await services.refresh_bookmark_cache()
            recs = [r for r in services.download_candidates() if r.work_id == "206"]
            job = await asyncio.to_thread(services.enqueue_managed_download, recs[0], "t206")
            await asyncio.to_thread(base.wait_terminal, services.jobs, job.job_id)
        finally:
            del base.WORKS["206"]
        assert await asyncio.to_thread(wait_for, lambda: "206" in {w for w, _ in seen})
    finally:
        await asyncio.to_thread(services.close)
