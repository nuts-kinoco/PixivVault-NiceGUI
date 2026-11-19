import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView
from pixivvault_nicegui.unified_download_state import INTERRUPTED_TEXT, RunProgress
from test_v_dl_priority_20261117 import Reader as FollowingReader
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs

@pytest.mark.asyncio
async def test_rejected_concurrent_start_cannot_rebind_active_resume(tmp_path):
    row = VISIBILITY_ROWS['show']
    first = services_with_runs(tmp_path)
    try:
        previous = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row,),
                                                    account_id='55', verified_bookmark_account=True)
        first.batch_run_ledger.update_author(previous, row, state='downloading', account_id=first.batch_run_ledger.run(previous).account_id)
    finally:
        await asyncio.to_thread(first.close)

    class OtherReader(GeneratedReader):
        def verify_identity(self):
            self._verified_identity = '77'
            return self._verified_identity

    class OtherFollowing(FollowingReader):
        def require_identity(self):
            self._verified_identity = '77'
            return self._verified_identity

    before_preflight, finish = Event(), Event()

    class Admission:
        def require_allowed(self):
            before_preflight.set()
            assert finish.wait(8)

    reader = OtherReader()
    owner = services_with_runs(tmp_path, reader, following_sync_root=tmp_path / 'following')
    await asyncio.to_thread(owner.following_sync.sync, OtherFollowing(1))
    assert owner.following_sync.last_synced_account() == '77'
    owner.managed_network_stop = Admission()
    try:
        current = owner.resume_batch_run(previous)
        assert await asyncio.to_thread(before_preflight.wait, 8)
        assert owner.batch_run_ledger.run(current).account_id == '55'
        reader.verify_identity()
        with pytest.raises(RuntimeError, match='already active'):
            owner.start_batch_run(('1000',), origin='manual', account_id='77')
        finish.set()
        assert await asyncio.to_thread(owner.batch_runs.join, 12)
        run = owner.batch_run_ledger.run(current)
        assert run.account_id == '55'
        saved = owner.managed_downloads.verified_saved_receipt('99') is not None
        print('resume state:', run.state, 'download committed:', saved,
              'data issued:', bool(reader.calls), 'error:', run.error_code)
        assert (run.state, run.error_code) == ('interrupted', 'bookmark_account_mismatch')
        assert not reader.calls and not saved
    finally:
        finish.set()
        await asyncio.to_thread(owner.close)


@pytest.mark.parametrize('state,code', [
    ('idle', 'bookmark_account_mismatch'),
    ('interrupted', 'bookmark_account_mismatch'),
    ('idle', ''),
])
def test_ui_progress_band_displays_action_and_retains_controls(state, code):
    view = UnifiedDownloadView.__new__(UnifiedDownloadView)
    view.closed = False
    view.progress = RunProgress(())
    view.run_state, view.band_state = 'idle', state
    view.resumable = SimpleNamespace(error_code=code)
    view.band_error = code
    view.band_status, view.resume_button, view.discard_button = Mock(), Mock(), Mock()
    view.mode, view.bm_hierarchical = 'bookmark', False
    view._save_label = lambda: 'isolated'
    view.notice_text, view.band_summary = '', Mock()
    view.bar, view.bar_fill = Mock(props={}), Mock()
    view.render_band_boxes = lambda: None
    view._render_band()
    label = view.band_status.set_text.call_args.args[0]
    if code:
        assert '\u30a2\u30ab\u30a6\u30f3\u30c8' in label
        assert '55' not in label and '77' not in label
    else:
        assert label == INTERRUPTED_TEXT
    view.resume_button.set_visibility.assert_called_once_with(True)
    view.discard_button.set_visibility.assert_called_once_with(True)


def test_author_account_mismatch_explains_why_run_stopped():
    from pixivvault_nicegui.unified_download_state import status_text

    message = status_text('failed', outcome_code='author_account_mismatch')
    assert 'アカウント' in message and '中止' in message
