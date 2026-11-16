"""Q17: the failed-records retry batch always ends with the original-style aggregate notice."""
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from test_q07_novel_retry import create


@pytest.mark.asyncio
async def test_aborted_batch_reports_failed_over_total_and_unprocessed(tmp_path, monkeypatch):
    service, reader, db = create(tmp_path, count=3)
    client = Client(ui.page('/q17-aggregate-abort'))
    try:
        async def fail(*a, **kw): raise ValueError('synthetic metadata error')
        monkeypatch.setattr(service, 'lookup_legacy_failed_original', fail)
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert not await view._retry_records(view.state.records)
            text = view.retry_notice.text
            assert text.startswith('再試行: 1/3件が失敗しました')
            assert '未処理 2 件' in text
            view.close()
    finally:
        service.close(); client.delete()


@pytest.mark.asyncio
async def test_complete_batch_has_no_unprocessed_suffix(tmp_path):
    service, reader, _ = create(tmp_path, count=2)
    client = Client(ui.page('/q17-aggregate-clean'))
    try:
        with client:
            view = FailedRecordsView(service)
            view._retry_batch_summary = dict(total=3, failed=0, jobs=('a', 'b', 'c'), interrupted=False)
            view._retry_results = {k: type('J', (), {'state': 'done'})() for k in 'abc'}
            view._update_retry_summary()
            assert view.retry_notice.text.startswith('再試行: 0/3件が失敗しました')
            assert '未処理 0' not in view.retry_notice.text.split('。')[0]
            view.close()
    finally:
        service.close(); client.delete()
