"""Original X09 trash panel: unknown, empty, failure and late-client outcomes."""
import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_managed_quarantine import owned, plan_for, proof_bytes


@pytest.mark.asyncio
async def test_trash_panel_separates_uncreated_ready_zero_and_read_error(owned, monkeypatch):
    writer, source, remote = owned
    opened = []

    async def open_location():
        opened.append(True)

    client = Client(ui.page("/_x09_trash_states"))
    with client:
        view = DuplicateView(source, open_trash=open_location)
        view.set_active(True)
    try:
        before, calls = proof_bytes(writer), tuple(remote.images)
        with client:
            await view.open_trash()
            assert view.trash_dialog.value
            assert "まだ作成" in view.trash_status.text
            assert "0件" in view.trash_copy_count.text
            assert not view.trash_open_button.enabled
            await view.open_trash_location()
            assert opened == []

            result = source.execute(plan_for(source, "123:2"), operation_id="2" * 32)
            writer.quarantine.restore(result.operation_id,
                                      expected_revision=result.state_revision)
            await view.refresh_trash()
            assert "1件" in view.trash_copy_count.text
            assert "0ページ" in view.trash_excluded.text
            assert view.trash_open_button.enabled
            await view.open_trash_location()
            assert opened == [True]

            for path in writer.quarantine.operations.rglob("*.png"):
                path.unlink()
            await view.refresh_trash()
            assert "0件" in view.trash_copy_count.text
            assert "0 B" in view.trash_copy_bytes.text
            assert "まだ作成" not in view.trash_status.text
            assert view.trash_open_button.enabled

            def failure():
                raise OSError("synthetic unreadable manifest")

            monkeypatch.setattr(source, "quarantine_snapshot", failure)
            await view.refresh_trash()
            assert "読み取れません" in view.trash_status.text
            assert "未確認" in view.trash_copy_count.text
            assert "未確認" in view.trash_copy_bytes.text
            assert not view.trash_open_button.enabled
            await view.open_trash_location()
            assert opened == [True]
        assert proof_bytes(writer) == before and tuple(remote.images) == calls
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_trash_panel_late_read_cannot_reopen_deleted_client_or_change_selection(
    owned, monkeypatch
):
    writer, source, remote = owned
    source.execute(plan_for(source, "123:2"), operation_id="3" * 32)
    actual = source.quarantine_snapshot
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(5), "test did not release its own read"
        return actual()

    monkeypatch.setattr(source, "quarantine_snapshot", delayed)
    client = Client(ui.page("/_x09_trash_late_read"))
    with client:
        view = DuplicateView(source)
        view.set_active(True)
        await view.scan()
        view.set_mode("near")
        group = next(g for g in view.scan_result.groups if len(g.items) > 1)
        view.toggle(group.group_id, group.items[0].key, True, view.selection_revision)
        selected = {key: set(value) for key, value in view.selections.items()}
    task = None
    try:
        with client:
            task = asyncio.create_task(view.open_trash())
            assert await asyncio.to_thread(entered.wait, 3)
            assert not task.done(), "read must run off the UI thread"
            view.close()
            visible = (view.trash_status.text, view.trash_copy_count.text)
            release.set()
            await asyncio.wait_for(task, 3)
            assert not view.trash_dialog.value
            assert (view.trash_status.text, view.trash_copy_count.text) == visible
            assert view.selections == selected
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_trash_os_open_error_is_visible_and_inactive_view_cannot_launch(owned):
    writer, source, _ = owned
    source.execute(plan_for(source, "123:2"), operation_id="4" * 32)
    calls = []

    async def fail_open():
        calls.append(True)
        raise OSError("synthetic default viewer failure")

    client = Client(ui.page("/_x09_trash_open_failure"))
    with client:
        view = DuplicateView(source, open_trash=fail_open)
        view.set_active(True)
    try:
        with client:
            await view.open_trash()
            await view.open_trash_location()
            assert "開けません" in view.trash_status.text
            assert "1件" in view.trash_copy_count.text
            assert calls == [True]
            view.set_active(False)
            await view.open_trash_location()
            assert calls == [True]
            assert not view.trash_dialog.value
    finally:
        client.delete()
