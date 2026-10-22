"""Failure-record UI events and lifecycle without touching the real database."""
import asyncio
import csv
from io import StringIO
from types import SimpleNamespace
import threading
import time

from nicegui import Client, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui.ui.shell import DemoShell


@pytest_asyncio.fixture
async def screen():
    service = ApplicationServices()
    client = Client(ui.page("/_failure_records_unit"))
    with client:
        view = FailedRecordsView(service)
    try:
        yield view, service, client
    finally:
        if client.id in Client.instances:
            client.delete()
        service.close()


@pytest.mark.asyncio
async def test_original_categories_pages_full_reason_and_readonly_actions(screen):
    view, _, _ = screen
    assert len(view.rows.default_slot.children) == 8
    assert view.page_label.text == "1 / 3"
    assert view.counter.text == "17 / 17 件"
    assert view.retry_all.props["disable"]
    assert "二行目" in view.state.visible_records[0].error_reason
    view.change_page(1)
    assert view.page_label.text == "2 / 3"
    view.select_category("format")
    assert view.page_label.text == "1 / 1"
    assert view.counter.text == "3 / 17 件"
    assert view.category_buttons["all"].text == "すべて 17"
    view.search(" ABSENT ")
    assert view.empty.text == "一致する失敗はありません"
    assert view.recovery.visible
    assert view.search_input.value == " ABSENT "
    assert view.category_buttons["format"].text == "形式 3"


@pytest.mark.asyncio
async def test_csv_explicit_export_uses_all_matches_not_only_page(screen, monkeypatch):
    view, _, _ = screen
    captured = []
    monkeypatch.setattr(ui.download, "content", lambda *args: captured.append(args))
    view.change_page(1)
    view.export()
    content, filename, media_type = captured[-1]
    assert filename == "failed_queue_export.csv" and content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(StringIO(content.decode("utf-8-sig"))))
    assert len(rows) == 18
    assert rows[-1][2] == "'=SUM(1,2)"
    assert view.state.records[-1].title == "=SUM(1,2)"
    view.select_category("format")
    view.export()
    assert len(list(csv.reader(StringIO(captured[-1][0].decode("utf-8-sig"))))) == 4
    view.set_active(False)
    view.export()
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_browser_epoch_blocks_queued_controls_after_revisit(screen, monkeypatch):
    view, _, _ = screen
    captured = []
    monkeypatch.setattr(ui.download, "content", lambda *args: captured.append(args))
    events = [(view.next, "click"), (view.export_item, "click"),
              (view.category_buttons["format"], "click"),
              (view.search_input, "update:model-value")]
    handlers = [next(listener.handler for listener in element._event_listeners.values()
                     if listener.type.replace("-", "").lower() == kind.replace("-", "").lower())
                for element, kind in events]
    stale = SimpleNamespace(args={"epoch": view._epoch, "value": "absent"})
    view.set_active(False)
    view.set_active(True)
    for callback in handlers:
        callback(stale)
    assert view.state.page_index == 0 and view.state.category == "all" and view.state.query == ""
    assert not captured
    handlers[0](SimpleNamespace(args={"epoch": view._epoch}))
    assert view.state.page_index == 1
    view.search("failure-0001")
    handlers[-1](SimpleNamespace(args={"epoch": view._search_epoch, "value": None}))
    assert view.state.query == ""
    view.search("failure-0001")
    handlers[-1](SimpleNamespace(args={"epoch": view._search_epoch}))
    assert view.state.query == "failure-0001"
    typing_epoch = view._search_epoch
    for text in ("f", "failure-", "failure-0009"):
        handlers[-1](SimpleNamespace(args={"epoch": typing_epoch, "value": text}))
    assert view.state.query == "failure-0009"
    view.set_active(False)
    view.set_active(True)
    handlers[-1](SimpleNamespace(args={"epoch": typing_epoch, "value": "stale"}))
    assert view.state.query == "failure-0009" and view.search_input.value == "failure-0009"


@pytest.mark.asyncio
async def test_slow_refresh_does_not_overwrite_after_tab_departure(screen, monkeypatch):
    view, service, _ = screen
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        started.set()
        await release.wait()
        return ()

    monkeypatch.setattr(service, "read_failed_records", read)
    token = view.state.begin_refresh()
    task = asyncio.create_task(view._refresh(token))
    await asyncio.wait_for(started.wait(), 2)
    view.search("001")
    view.set_active(False)
    view.set_active(True)
    release.set()
    await asyncio.wait_for(task, 2)
    assert len(view.state.records) == 17
    assert view.state.query == "001" and not view.state.loading


@pytest.mark.asyncio
async def test_failure_invalid_snapshot_empty_and_retry_are_distinct(screen, monkeypatch):
    view, service, _ = screen

    async def broken():
        raise RuntimeError("private path must not be displayed")

    monkeypatch.setattr(service, "read_failed_records", broken)
    await view._refresh(view.state.begin_refresh())
    assert view.alert.visible and len(view.state.records) == 17
    assert "private" not in view.alert.text

    async def malformed():
        return (object(),)

    monkeypatch.setattr(service, "read_failed_records", malformed)
    await view._refresh(view.state.begin_refresh())
    assert view.state.error and not view.state.loading and len(view.state.records) == 17

    async def empty():
        return ()

    monkeypatch.setattr(service, "read_failed_records", empty)
    await view._refresh(view.state.begin_refresh())
    assert not view.alert.visible and not view.state.records
    assert view.empty.text == "失敗はありません\n失敗した取得は0件です"


@pytest.mark.asyncio
async def test_deleted_client_never_rebuilds_ui_after_refresh(screen, monkeypatch):
    view, service, client = screen
    gate = asyncio.Event()

    async def delayed():
        await gate.wait()
        return ()

    monkeypatch.setattr(service, "read_failed_records", delayed)
    task = asyncio.create_task(view._refresh(view.state.begin_refresh()))
    await asyncio.sleep(0)
    client.delete()
    gate.set()
    await asyncio.wait_for(task, 2)
    assert view.closed and len(view.state.records) == 17


@pytest.mark.asyncio
async def test_read_service_runs_in_worker_thread(tmp_path, monkeypatch):
    from pixivvault_nicegui import failed_records_repository as repo
    service = ApplicationServices()
    service.library_readonly_db = tmp_path / "synthetic-only.sqlite"
    service.library_repository = SimpleNamespace(close=lambda: None)
    main_thread = threading.get_ident()
    visited = []

    def slow(path):
        visited.append(threading.get_ident())
        time.sleep(0.1)
        return ()

    monkeypatch.setattr(repo, "read_failed_records", slow)
    try:
        task = asyncio.create_task(service.read_failed_records())
        await asyncio.sleep(0.01)
        assert not task.done()
        assert await asyncio.wait_for(task, 2) == ()
        assert visited and visited[0] != main_thread
    finally:
        service.close()


@pytest.mark.asyncio
async def test_shell_two_clients_preserve_separate_filters_and_page():
    service = ApplicationServices()
    clients = [Client(ui.page("/_failure_a")), Client(ui.page("/_failure_b"))]
    try:
        shells = []
        for client in clients:
            with client:
                shells.append(DemoShell(service))
                shells[-1].show("failed")
        a, b = [shell.failed_records for shell in shells]
        a.change_page(1)
        b.select_category("format")
        old_rows = tuple(a.rows.default_slot.children)
        with clients[0]:
            shells[0].show("ranking")
            shells[0].show("failed")
        assert a.state.page_index == 1 and a.state.category == "all"
        assert tuple(a.rows.default_slot.children) == old_rows
        assert b.state.page_index == 0 and b.state.category == "format"
        assert not service.closed
    finally:
        for client in clients:
            client.delete()
        service.close()


@pytest.mark.asyncio
async def test_live_mode_without_failure_database_is_not_reported_as_zero_failures():
    client = Client(ui.page("/_failure_unavailable"))
    calls = []
    service = SimpleNamespace(data_mode="pixiv-readonly", failed_records_available=False,
                              read_failed_records=lambda: calls.append("unexpected read"))
    try:
        with client:
            view = FailedRecordsView(service)
        view.set_active(True)
        assert view.counter.text == "未取得"
        assert "0件であることを意味しません" in view.empty.text
        assert view.reload_button.props["disable"] and view.search_input.props["disable"]
        assert not calls
    finally:
        client.delete()
