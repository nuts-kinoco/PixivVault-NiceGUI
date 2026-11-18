from datetime import datetime, timezone
import asyncio
import json
import sqlite3

import pytest

from pixivvault_nicegui import managed_generations as mg
from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.managed_downloads import ManagedDownloadError
from pixivvault_nicegui.managed_root_writer import writer_fence
from test_v_dl_cap_20261117 import make_root, mark_of, publish, seed_index
from test_w_review_favourites_20261118 import create_stars
from test_w_review_bookmark_resume_20261118 import services_with_runs


@pytest.mark.asyncio
async def test_bookmark_resume_without_source_keeps_offer(tmp_path):
    first = services_with_runs(tmp_path)
    row_id = VISIBILITY_ROWS['show']
    try:
        await first.refresh_bookmark_cache()
        old_run = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row_id,), account_id='55', verified_bookmark_account=True)
        first.batch_run_ledger.update_author(old_run, row_id, state='downloading')
    finally:
        await asyncio.to_thread(first.close)
    second = services_with_runs(tmp_path)
    try:
        second.live_client = None
        assert not second.bookmark_download_candidates()
        assert second.library_repository is None
        new_run = second.resume_batch_run(old_run)
        assert await asyncio.to_thread(second.batch_runs.join, 10)
        assert second.batch_run_ledger.run(new_run).state != 'done'
        assert second.batch_runs.resumable_run() is not None
    finally:
        await asyncio.to_thread(second.close)


