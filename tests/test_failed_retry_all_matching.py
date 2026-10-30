"""Q17: a filtered retry is all matching rows, not first 50 or first success."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.failed_records_state import FailureRecord, FailedRecordsState
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui.legacy_retry_errors import LegacyRetryRowUnavailable


def records(count=53):
    return tuple(FailureRecord(str(1001 + number), '23',
                               f'Synthetic retry target {number:03}',
                               'illust', 'Timeout', '2026-10-23', 0)
                 for number in range(count))


class OwnedSynthetic:
    """Only the view's explicit acceptance boundary, no HTTP or real DB."""

    data_mode = 'synthetic'
    failed_records_available = True
    legacy_failed_retry_available = True

    def __init__(self, *, failing=(), changed=()):
        self.failing = set(failing)
        self.changed = set(changed)
        self.lookups = []
        self.accepted = []
        self.released = []
        self.jobs = SimpleNamespace(snapshot=lambda: ())

    async def lookup_legacy_failed_original(self, row, *, owner):
        self.lookups.append(row.work_id)
        if row.work_id in self.changed:
            raise ValueError('synthetic frozen source changed')
        return SimpleNamespace(remote_id=row.work_id)

    def enqueue_legacy_failed_original(self, row, record, operation, *, owner):
        assert record.remote_id == row.work_id
        if row.work_id in self.failing:
            # Only an explicitly page-local rejection permits the next row.
            # Unknown RuntimeError must now abort (review regression coverage).
            raise LegacyRetryRowUnavailable('synthetic acceptance failed; must not leak')
        self.accepted.append(row.work_id)
        return SimpleNamespace(job_id='job-' + row.work_id)

    def release_managed_owner(self, owner):
        self.released.append(owner)


@pytest.mark.asyncio
async def test_q17_filter_all_matching_53_across_pages_and_single_failed_row(monkeypatch):
    from pixivvault_nicegui.ui import failed_records_view
    source = OwnedSynthetic(failing={'1002'})
    client = Client(ui.page('/_q17_all_matching'))
    captured = []
    monkeypatch.setattr(failed_records_view.background_tasks, 'create',
                        lambda coroutine: captured.append(coroutine))
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(records())
            view._loaded = True
            view.render()
            assert view.state.set_page(4)
            assert view.state.page_index == 4
            view.render()
            view.request_retry()
            assert len(captured) == 1
            assert not await captured.pop()
            assert source.lookups == [row.work_id for row in records()]
            assert source.accepted == [row.work_id for row in records()
                                       if row.work_id != '1002']
            assert '52 件を管理キューへ追加' in view.retry_notice.text
            assert '未追加 1 件' in view.retry_notice.text
            assert 'synthetic acceptance' not in view.retry_notice.text
    finally:
        for task in captured:
            task.close()
        client.delete()


@pytest.mark.asyncio
async def test_q17_every_supported_row_over_256_keeps_correct_ownership():
    source = OwnedSynthetic()
    client = Client(ui.page('/_q17_over_256'))
    rows = records(260)
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(rows)
            view._loaded = True
            view.render()
            assert view.retry_all.enabled
            assert '260 件' in view.retry_all.text
            assert await view._retry_records(rows)
            assert source.accepted == [row.work_id for row in rows]
            assert len(view._retry_ops) == 260
            assert '260 件を管理キューへ追加' in view.retry_notice.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_q17_frozen_generation_change_stops_entire_remaining_batch():
    source = OwnedSynthetic(changed={'1003'})
    client = Client(ui.page('/_q17_stale_copy'))
    rows = records()
    try:
        with client:
            view = FailedRecordsView(source)
            view.state = FailedRecordsState(rows)
            view._loaded = True
            assert not await view._retry_records(rows)
            assert source.lookups == ['1001', '1002', '1003']
            assert source.accepted == ['1001', '1002']
            assert '未追加 51 件' in view.retry_notice.text
    finally:
        client.delete()
