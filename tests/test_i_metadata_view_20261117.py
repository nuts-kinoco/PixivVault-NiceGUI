"""FL-C20: メタデータ画面の「いま埋める」「取れなかったものをもう一度」と状態行。"""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.metadata_auto import MetadataStatus
from pixivvault_nicegui.metadata_runner import MetadataRunSummary
from pixivvault_nicegui.ui.metadata_view import MetadataView


def fake_services(status, summary=None):
    calls = []
    state = {"status": status}

    def run_all(**kwargs):
        calls.append("run")
        return summary or MetadataRunSummary(target=3, filled=3)

    def reset():
        calls.append("reset")
        state["status"] = MetadataStatus(2, 3, 1, 0, 0)
        return 1

    services = SimpleNamespace(
        managed_metadata=None, closing=False,
        managed_metadata_status=lambda: state["status"],
        run_managed_metadata_all=run_all,
        reset_managed_metadata_gave_up=reset,
        metadata_auto_enabled=lambda: True,
        stop_managed_metadata=lambda: calls.append("stop"))
    return services, calls


def texts(view):
    return [e.text for e in view.root.descendants() if hasattr(e, "text") and isinstance(e.text, str)]


@pytest.mark.asyncio
async def test_status_line_runs_and_retry_follow_the_original():
    services, calls = fake_services(MetadataStatus(2, 3, 0, 1, 0))
    client = Client(ui.page("/_i_meta"))
    try:
        with client:
            view = MetadataView(services)
            await view.refresh_status()
            assert view.meta_status.text == "メタデータ: 2 / 3 件（残り 0 ／ 3回試しても取れなかった 1）"
            assert view.retry_button.visible and view.run_all_button.visible
            assert not any("自動通信はしません" in t for t in texts(view))
            await view.retry_gave_up()
            assert calls == ["reset"]
            assert view.meta_status.text.startswith("メタデータ: 2 / 3 件（残り 1")
            assert not view.retry_button.visible
            await view.run_all()
            assert calls == ["reset", "run"]
            assert "3" in view.run_status.text
    finally:
        view.close()
        client.delete()


@pytest.mark.asyncio
async def test_run_all_is_a_single_flight_and_reports_stop_reason():
    services, calls = fake_services(
        MetadataStatus(0, 3, 3, 0, 0), MetadataRunSummary(target=3, stopped="network_stopped"))
    client = Client(ui.page("/_i_meta2"))
    try:
        with client:
            view = MetadataView(services)
            await asyncio.gather(view.run_all(), view.run_all())
            assert calls.count("run") == 1
            assert "取得停止" in view.run_status.text
    finally:
        view.close()
        client.delete()
