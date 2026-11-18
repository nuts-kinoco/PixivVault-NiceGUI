from concurrent.futures import ThreadPoolExecutor


from datetime import datetime, timezone


from pathlib import Path


from threading import Event


import asyncio


import json


import subprocess


import types


import pytest


from pixivvault_nicegui import managed_generations as mg


from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS


from pixivvault_nicegui.managed_downloads import ManagedDownloadError


from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration


from pixivvault_nicegui.managed_scheduler import ScheduleError


from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected


from test_v_dl_cap_20261117 import make_root, journal_of, mark_of, publish, seed_index


from test_w_review_bookmark_resume_20261118 import GeneratedReader, services_with_runs


from test_w_review_favourites_20261118 import create_stars


@pytest.mark.asyncio
async def test_bookmark_resume_does_not_use_different_account(tmp_path, monkeypatch):
    from test_v_dl_priority_20261117 import sync

    following = tmp_path / 'follow'
    owner = services_with_runs(tmp_path, following_sync_root=following)
    prepared, finish = Event(), Event()

    def hold_before_listing(_):
        prepared.set()
        if not finish.wait(8):
            raise TimeoutError('review synchronization')
        return ()

    try:
        sync(owner.following_sync, 1)
        monkeypatch.setattr(owner.batch_runs.port, 'list_bookmarks', hold_before_listing)
        old_id = await owner.start_bookmark_run(('show',))
        assert await asyncio.to_thread(prepared.wait, 8)
        owner.batch_runs.stop()
        finish.set()
        assert await asyncio.to_thread(owner.batch_runs.join, 8)
        old = owner.batch_run_ledger.run(old_id)
        assert (old.state, old.account_id) == ('stopped', '55')
    finally:
        finish.set()
        await asyncio.to_thread(owner.close)

    class OtherReader(GeneratedReader):
        def verify_identity(self):
            return '77'

    reader = OtherReader()
    reopened = services_with_runs(tmp_path, reader, following_sync_root=following)
    try:
        offer = reopened.batch_runs.resumable_run()
        assert offer.run_id == old_id
        new_id = reopened.resume_batch_run(old_id)
        assert await asyncio.to_thread(reopened.batch_runs.join, 12)
        new = reopened.batch_run_ledger.run(new_id)
        items = reopened.batch_run_ledger.items(new_id, VISIBILITY_ROWS['show'])
        print(f'bookmark account: run account={new.account_id}, actual account=77, '
              f'state={new.state}, stored={reopened.managed_downloads.verified_saved_receipt("99") is not None}, '
              f'works={[row.work_key for row in items]}')
        assert not items and (new.state, new.error_code) == ('interrupted', 'bookmark_account_mismatch')
        assert reopened.managed_downloads.verified_saved_receipt('99') is None
    finally:
        await asyncio.to_thread(reopened.close)
