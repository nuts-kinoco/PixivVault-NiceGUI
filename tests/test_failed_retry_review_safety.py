"""Q17 review regressions: shared API failures stop, row failures continue."""
import asyncio

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.failed_records_state import FailedRecordsState
from pixivvault_nicegui.legacy_retry_errors import (
    DownloadQueueCapacityReached, LegacyRetryRowUnavailable,
)
from pixivvault_nicegui.pixiv_read_client import (
    AuthenticationMissing, HTTPStatusRejected, TransportFailed, ClientClosed,
)
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_failed_retry_all_matching import OwnedSynthetic, records


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [AuthenticationMissing(), HTTPStatusRejected(429),
                                  TransportFailed(), ClientClosed()])
async def test_api_wide_failure_never_contacts_remaining_rows(error):
    class Source(OwnedSynthetic):
        async def lookup_legacy_failed_original(self, row, *, owner):
            result = await super().lookup_legacy_failed_original(row, owner=owner)
            if row.work_id == '1002':
                raise error
            return result
    source = Source()
    client = Client(ui.page('/_retry_api_wide'))
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records(53))
            assert not await view._retry_records(view.state.records)
            assert source.lookups == ['1001', '1002']
            assert source.accepted == ['1001']
            assert '中断' in view.retry_notice.text
    finally:
        client.delete()


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [LegacyRetryRowUnavailable('artwork cannot be saved'),
                                  HTTPStatusRejected(404), HTTPStatusRejected(410)])
async def test_known_row_failure_continues_matching_set(error):
    class Source(OwnedSynthetic):
        async def lookup_legacy_failed_original(self, row, *, owner):
            result = await super().lookup_legacy_failed_original(row, owner=owner)
            if row.work_id == '1002':
                raise error
            return result
    source = Source()
    client = Client(ui.page('/_retry_row_specific'))
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records(3))
            assert not await view._retry_records(view.state.records)
            assert source.lookups == ['1001', '1002', '1003']
            assert source.accepted == ['1001', '1003']
            assert '未追加 1 件' in view.retry_notice.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_capacity_race_waits_and_reuses_candidate_and_operation():
    class Source(OwnedSynthetic):
        def __init__(self):
            super().__init__()
            self.waits = 0
            self.enqueues = []
        async def wait_legacy_retry_capacity(self, row, *, is_current, deadline=None):
            assert is_current()
            self.waits += 1
            await asyncio.sleep(0)
        def enqueue_legacy_failed_original(self, row, record, operation, *, owner):
            self.enqueues.append((row.work_id, operation))
            if len(self.enqueues) == 1:
                raise DownloadQueueCapacityReached('download queue capacity reached')
            return super().enqueue_legacy_failed_original(row, record, operation, owner=owner)
    source = Source()
    client = Client(ui.page('/_retry_capacity_race'))
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records(1))
            assert await view._retry_records(view.state.records)
            assert source.lookups == ['1001']
            assert len(source.enqueues) == 2 and source.enqueues[0] == source.enqueues[1]
            assert source.waits >= 2
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_validation_does_not_scan_full_tuple_for_every_row(monkeypatch):
    from pixivvault_nicegui.ui import failed_records_view
    class Counted(tuple):
        contains_calls = 0
        def __contains__(self, item):
            self.contains_calls += 1
            return super().__contains__(item)
    client = Client(ui.page('/_retry_validation_linear'))
    captured = []
    monkeypatch.setattr(failed_records_view.background_tasks, 'create', captured.append)
    try:
        with client:
            view = FailedRecordsView(OwnedSynthetic())
            view.state = FailedRecordsState(records(100))
            counted = Counted(view.state.records)
            view.state.records = counted
            view.request_retry()
            assert len(captured) == 1
            assert counted.contains_calls == 0
    finally:
        for coroutine in captured:
            coroutine.close()
        client.delete()
