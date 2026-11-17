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


# ---- 3: 保存済みページが 1 件壊れていても、後埋めは他の作品へ進む ---------------------------

def test_managed_state_changed_extends_the_wait():
    first, backoff = next_delay(MetadataRunSummary(stopped='managed_state_changed'), 0)
    assert first == 900.0 and backoff == 900.0
    second, backoff = next_delay(MetadataRunSummary(stopped='managed_state_changed'), backoff)
    assert second == 1800.0
    top = 0.0
    for _ in range(20):
        top, backoff = next_delay(MetadataRunSummary(stopped='managed_state_changed'), backoff)
    assert top == 21600.0


async def test_one_corrupted_page_is_skipped_and_the_rest_are_filled(tmp_path):
    services = await make_services(tmp_path)
    try:
        root = services.managed_downloads.root
        victim = [p for p in root.rglob("page-1.png") if "work-202-" in str(p)][0]
        data = bytearray(victim.read_bytes())
        data[-1] ^= 0xFF
        victim.write_bytes(bytes(data))
        t = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=t)
        assert summary.stopped == ""
        assert summary.filled == 2 and summary.skipped == 1
        assert "202" not in t.calls and "204" in t.calls and "205" in t.calls
    finally:
        await asyncio.to_thread(services.close)


async def test_one_deleted_page_file_is_skipped_and_the_rest_are_filled(tmp_path):
    services = await make_services(tmp_path)
    try:
        root = services.managed_downloads.root
        for p in [p for p in root.rglob("page-1.png") if "work-202-" in str(p)]:
            p.unlink()
        t = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=t)
        assert summary.stopped == ""
        assert summary.filled == 2
        assert "202" not in t.calls and "204" in t.calls and "205" in t.calls
    finally:
        await asyncio.to_thread(services.close)
