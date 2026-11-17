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


# ---- 2: 関係のない設定の保存で、失敗後の待ち時間を飛ばさない -------------------------------

async def test_unrelated_preference_save_does_not_cut_the_backoff(tmp_path):
    services = await make_services(tmp_path)
    try:
        t = Transport()
        for w in base.WORKS:
            t.fail[w] = HTTPStatusRejected(503)
        services.start_metadata_auto(transport=t, initial_delay=0.01,
                                     intervals=dict(backoff_start=900.0, backoff_max=21600.0))
        assert await asyncio.to_thread(wait_for, lambda: t.calls.count("identity") == 1)
        await asyncio.sleep(0.3)
        for i in range(3):
            snap = services.preferences.snapshot()
            services.preferences.update({"download_interval": "1.0" if i % 2 == 0 else "0.6"},
                                        expected_revision=snap.revision)
            services.apply_preferences()
            await asyncio.sleep(0.3)
        assert t.calls.count("identity") == 1
    finally:
        await asyncio.to_thread(services.close)


async def test_turning_the_switch_off_then_on_still_wakes_the_runner(tmp_path):
    services = await make_services(tmp_path)
    try:
        t = Transport()
        for w in base.WORKS:
            t.fail[w] = HTTPStatusRejected(503)
        services.start_metadata_auto(transport=t, initial_delay=0.01,
                                     intervals=dict(backoff_start=900.0, backoff_max=21600.0))
        assert await asyncio.to_thread(wait_for, lambda: t.calls.count("identity") == 1)
        for value in ("0", "1"):
            snap = services.preferences.snapshot()
            services.preferences.update({"meta_backfill_enabled": value},
                                        expected_revision=snap.revision)
            services.apply_preferences()
            await asyncio.sleep(0.3)
        assert await asyncio.to_thread(wait_for, lambda: t.calls.count("identity") == 2)
    finally:
        await asyncio.to_thread(services.close)
