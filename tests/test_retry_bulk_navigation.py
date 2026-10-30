"""Long explicit batches survive view navigation; explicit cancellation stops."""
import asyncio

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.failed_records_state import FailedRecordsState
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_failed_retry_all_matching import OwnedSynthetic, records


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['page', 'search', 'category', 'tab'])
async def test_captured_bulk_is_not_cancelled_by_navigation(operation):
    entered, release = asyncio.Event(), asyncio.Event()
    class Source(OwnedSynthetic):
        async def lookup_legacy_failed_original(self, row, *, owner):
            value = await super().lookup_legacy_failed_original(row, owner=owner)
            if row.work_id == '1001':
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return value
    source = Source()
    client = Client(ui.page('/_bulk_navigation'))
    task = None
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records(20))
            task = asyncio.create_task(view._retry_records(view.state.records))
            await asyncio.wait_for(entered.wait(), 5)
            if operation == 'page':
                view.change_page(1)
            elif operation == 'search':
                view.search('not in captured set')
            elif operation == 'category':
                view.select_category('format')
            else:
                view.set_active(False)
            release.set()
            assert await asyncio.wait_for(task, 5)
            assert len(source.accepted) == 20
            assert '20 件を管理キューへ追加' in view.retry_notice.text
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.wait_for(task, 5)
        client.delete()


@pytest.mark.asyncio
async def test_explicit_cancel_stops_before_any_unverified_acceptance():
    entered, release = asyncio.Event(), asyncio.Event()
    class Source(OwnedSynthetic):
        async def lookup_legacy_failed_original(self, row, *, owner):
            value = await super().lookup_legacy_failed_original(row, owner=owner)
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
            return value
    source = Source()
    client = Client(ui.page('/_bulk_explicit_cancel'))
    task = None
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records(20))
            task = asyncio.create_task(view._retry_records(view.state.records))
            await asyncio.wait_for(entered.wait(), 5)
            assert view.cancel_retry_button.visible and view.cancel_retry_button.enabled
            view.cancel_retry()
            release.set()
            assert not await asyncio.wait_for(task, 5)
            assert source.accepted == [] and source.lookups == ['1001']
            assert '中断' in view.retry_notice.text
            assert not view._retry_busy and not view.cancel_retry_button.visible
    finally:
        release.set()
        if task is not None and not task.done():
            await asyncio.wait_for(task, 5)
        client.delete()
