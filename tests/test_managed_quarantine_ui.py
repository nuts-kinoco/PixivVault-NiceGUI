"""X07/X08 confirmation and accepted-task lifetime using owned synthetic bytes."""
import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from test_managed_quarantine import owned, proof_bytes


async def opened(source, path):
    client = Client(ui.page(path))
    with client:
        view = DuplicateView(source)
        view.set_active(True)
        await view.scan()
        view.set_mode('near')
    return client, view


def choose(view, *keys):
    wanted = set(keys)
    for group in view.scan_result.groups:
        for item in group.items:
            if item.key in wanted:
                view.toggle(group.group_id, item.key, True, view.selection_revision)
                wanted.remove(item.key)
    assert not wanted


@pytest.mark.asyncio
async def test_single_and_batch_share_confirmation_and_cancel_has_no_effect(owned):
    writer, source, remote = owned
    before = proof_bytes(writer)
    calls = tuple(remote.images)
    client, view = await opened(source, '/_x07_single_batch')
    try:
        with client:
            choose(view, '123:2', '223:1')
            group_id = next(g.group_id for g in view.scan_result.groups
                            if any(p.key == '123:2' for p in g.items))
            dialog_id = view.confirm_dialog.id
            await view.confirm(group_id, view.selection_revision)
            assert view.confirm_dialog.value
            assert [item.key for item in view._confirmation[0].items] == ['123:2']
            assert '1枚' in view.confirm_title.text
            view.cancel_confirmation()
            assert not view.confirm_dialog.value and view._confirmation is None
            assert writer.visible_source_pages('123') == (0, 1, 2)
            assert not (writer.root / 'quarantine').exists()
            await view.confirm()
            assert view.confirm_dialog.id == dialog_id
            assert {p.key for p in view._confirmation[0].items} == {'123:2', '223:1'}
            await view.execute_confirmation()
            assert {p.key for p in view._last_result.committed} == {'123:2', '223:1'}
            assert '2枚を隔離しました・失敗0枚' in view.notice.text
            assert writer.visible_source_pages('123') == (0, 2)
            assert writer.visible_source_pages('223') == ()
            assert not view.busy and not view._pending_quarantines
        assert proof_bytes(writer) == before and tuple(remote.images) == calls
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_each_group_keeps_one_and_changed_selection_invalidates_confirmation(owned):
    writer, source, _ = owned
    client, view = await opened(source, '/_x07_selection_binding')
    try:
        with client:
            group = next(g for g in view.scan_result.groups if len(g.items) == 4)
            for item in group.items:
                view.toggle(group.group_id, item.key, True, view.selection_revision)
            assert len(view.selections[group.group_id]) == len(group.items) - 1
            assert '1枚以上残します' in view.notice.text
            view.selections = {}
            view.render()
            choose(view, '123:2')
            await view.confirm()
            choose(view, '123:3')
            await view.execute_confirmation()
            assert view._last_result is None and view._confirmation is None
            assert '確認を開き直してください' in view.notice.text
            assert writer.visible_source_pages('123') == (0, 1, 2)
            assert not (writer.root / 'quarantine').exists()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_partial_failure_counts_only_committed_images_without_rescanning(owned, monkeypatch):
    writer, source, _ = owned
    client, view = await opened(source, '/_x08_partial_copy')
    original = writer.quarantine._copy_page
    def copy(item, destination):
        if item.work_id == '223':
            raise OSError('synthetic copy refusal')
        return original(item, destination)
    monkeypatch.setattr(writer.quarantine, '_copy_page', copy)
    monkeypatch.setattr(source, 'scan', lambda: pytest.fail('successful items must prune the current scan'))
    try:
        with client:
            choose(view, '123:2', '223:1')
            await view.confirm()
            await view.execute_confirmation()
            assert [p.key for p in view._last_result.committed] == ['123:2']
            assert [p.key for p in view._last_result.failed] == ['223:1']
            assert '1枚を隔離しました・失敗1枚' in view.notice.text
            keys = {p.key for g in view.scan_result.groups for p in g.items}
            assert '123:2' not in keys and '223:1' in keys
            assert writer.visible_source_pages('223') == (0,)
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_repeated_cancellation_and_client_close_drain_copy_and_refresh(owned, monkeypatch):
    writer, source, remote = owned
    client, view = await opened(source, '/_x08_cancel_drain')
    started, release = Event(), Event()
    refresh_started, refresh_release = asyncio.Event(), asyncio.Event()
    refreshed = []
    original = source.execute
    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(10), 'test did not release synthetic copy'
        return original(*args, **kwargs)
    async def refresh():
        refresh_started.set()
        await refresh_release.wait()
        refreshed.append(writer.visible_source_pages('123'))
    monkeypatch.setattr(source, 'execute', delayed)
    source.on_changed = refresh
    before = proof_bytes(writer)
    calls = tuple(remote.images)
    task = None
    try:
        with client:
            choose(view, '123:2')
            await view.confirm()
            old_notice = view.notice.text
            task = asyncio.create_task(view.execute_confirmation())
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and view.busy and view._pending_quarantines
            assert all(not pending.cancelled() for pending in view._pending_quarantines)
            view.close()
            assert view.closed and view.busy and view._pending_quarantines
            release.set()
            await asyncio.wait_for(refresh_started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and view.busy and view._pending_quarantines
            assert view._last_result is not None
            refresh_release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert not view.busy and not view._pending_quarantines
            assert refreshed == [(0, 2)]
            assert view.notice.text == old_notice, 'deleted client must not receive a late completion repaint'
        assert writer.visible_source_pages('123') == (0, 2)
        assert proof_bytes(writer) == before and tuple(remote.images) == calls
    finally:
        release.set()
        refresh_release.set()
        if task is not None and not task.done():
            await asyncio.gather(task, return_exceptions=True)
        client.delete()


@pytest.mark.asyncio
async def test_refresh_failure_does_not_relabel_committed_quarantine_as_failure(owned):
    writer, source, _ = owned
    client, view = await opened(source, '/_x07_refresh_warning')
    async def failed_refresh():
        raise OSError('synthetic UI refresh failure')
    source.on_changed = failed_refresh
    try:
        with client:
            choose(view, '123:2')
            await view.confirm()
            await view.execute_confirmation()
            assert '1枚を隔離しました・失敗0枚' in view.notice.text
            assert 'ライブラリ表示を再読込してください' in view.notice.text
            assert writer.visible_source_pages('123') == (0, 2)
            assert len(view._last_result.committed) == 1
    finally:
        client.delete()
