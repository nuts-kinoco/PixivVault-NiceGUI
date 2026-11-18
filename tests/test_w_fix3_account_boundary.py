import asyncio
from threading import Event

import pytest

from pixivvault_nicegui.unified_download_state import status_text
from test_v_dl_priority_20261117 import ACCOUNT, sync
from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs

@pytest.mark.asyncio
@pytest.mark.parametrize('synced', [False, True])
async def test_new_bookmark_run_records_verified_bookmark_account(tmp_path, monkeypatch, synced):
    class Reader(GeneratedReader):
        def verify_identity(self):
            return '77'

    kwargs = {'following_sync_root': tmp_path / 'follow'} if synced else {}
    owner = services_with_runs(tmp_path, Reader(), **kwargs)
    ready, finish = Event(), Event()

    def hold(_):
        ready.set()
        assert finish.wait(8)
        return ()

    try:
        if synced:
            sync(owner.following_sync, 1)
            assert owner.following_sync.last_synced_account() == ACCOUNT != '77'
        monkeypatch.setattr(owner.batch_runs.port, 'list_bookmarks', hold)
        run_id = await owner.start_bookmark_run(('show',))
        assert await asyncio.to_thread(ready.wait, 8)
        owner.batch_runs.stop()
        finish.set()
        assert await asyncio.to_thread(owner.batch_runs.join, 8)
        run = owner.batch_run_ledger.run(run_id)
        assert run.state == 'stopped'
        assert run.account_id == '77'
    finally:
        finish.set()
        await asyncio.to_thread(owner.close)


def test_bookmark_mismatch_is_visible_in_progress_band():
    message = status_text('interrupted', outcome_code='bookmark_account_mismatch')
    assert 'アカウント' in message
