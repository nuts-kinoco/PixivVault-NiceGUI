import asyncio

import pytest

from pixivvault_nicegui.batch_engine import RunNeedsConfirmation
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


@pytest.mark.asyncio
@pytest.mark.parametrize('old_account', [None, '77'])
async def test_unverified_legacy_bookmark_run_does_not_resume(tmp_path, old_account):
    reader = GeneratedReader()
    services = services_with_runs(tmp_path, reader)
    try:
        ledger = services.batch_run_ledger
        old_run = ledger.begin_run('bookmarks', 'manual', 'both', (VISIBILITY_ROWS['show'],),
                                   account_id=old_account)
        ledger.update_author(old_run, VISIBILITY_ROWS['show'], state='downloading')
        ledger.recover_interrupted()
        services.batch_runs.account_id = '77'
        count = len(ledger.runs())
        with pytest.raises(RunNeedsConfirmation):
            services.resume_batch_run(old_run)
        assert len(ledger.runs()) == count
        assert reader.calls == []
        assert ledger.run(old_run).state == 'interrupted'
    finally:
        await asyncio.to_thread(services.close)


@pytest.mark.asyncio
async def test_new_bookmark_start_records_verified_account(tmp_path):
    services = services_with_runs(tmp_path)
    try:
        run_id = await services.start_bookmark_run(('show',))
        assert await asyncio.to_thread(services.batch_runs.join, 12)
        assert services.batch_run_ledger.run(run_id).account_id == '55'
        assert services.batch_run_ledger.has_verified_bookmark_account(run_id)
    finally:
        await asyncio.to_thread(services.close)


def test_unknown_account_notice_hides_resume():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView
    from pixivvault_nicegui.unified_download_state import RunProgress
    view = UnifiedDownloadView.__new__(UnifiedDownloadView)
    view.closed = False
    view.progress = RunProgress(())
    view.run_state = view.band_state = 'idle'
    view.resumable = SimpleNamespace(kind='bookmarks', account_id=None, run_id='a' * 32, error_code='')
    view.services = SimpleNamespace(batch_runs=SimpleNamespace(ledger=SimpleNamespace(has_verified_bookmark_account=lambda _: False)))
    view.band_error = ''
    view.band_status, view.resume_button, view.discard_button = Mock(), Mock(), Mock()
    view.mode, view.bm_hierarchical = 'bookmark', False
    view._save_label = lambda: 'isolated'
    view.notice_text, view.band_summary = '', Mock()
    view.bar, view.bar_fill = Mock(props={}), Mock()
    view.render_band_boxes = lambda: None
    view._render_band()
    assert '\u30a2\u30ab\u30a6\u30f3\u30c8\u4e0d\u660e' in view.band_status.set_text.call_args.args[0]
    view.resume_button.set_visibility.assert_called_once_with(False)
    view.discard_button.set_visibility.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_version2_ledger_migrates_existing_account_as_unknown(tmp_path):
    import sqlite3
    services = services_with_runs(tmp_path)
    ledger = services.batch_run_ledger
    run_id = ledger.begin_run('bookmarks', 'manual', 'both', (VISIBILITY_ROWS['show'],), account_id='77')
    ledger.recover_interrupted()
    path = ledger.path
    await asyncio.to_thread(services.close)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE run_flags DROP COLUMN verified_bookmark_account')
        db.execute('PRAGMA user_version=2')
    reopened = services_with_runs(tmp_path)
    try:
        assert not reopened.batch_run_ledger.has_verified_bookmark_account(run_id)
        with pytest.raises(RunNeedsConfirmation):
            reopened.resume_batch_run(run_id)
    finally:
        await asyncio.to_thread(reopened.close)
