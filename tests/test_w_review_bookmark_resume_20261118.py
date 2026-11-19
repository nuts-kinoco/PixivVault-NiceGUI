import asyncio
import json
from pathlib import Path

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.bookmark_run import VISIBILITY_ROWS
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from stage5_generated_shared_owner import generated_shared_scope
from test_managed_network_stop_integration import _cookie
from test_v_dl_cap_20261117 import png
from test_w_dl_bookmarks_20261118 import remote
from test_x_ctx_ranking_20261118 import MENU, items_of, label_of, make


class GeneratedReader:
    def __init__(self):
        self.calls = []

    def close(self): pass

    def verify_identity(self):
        self._verified_identity = '55'
        return self._verified_identity

    def get_bookmarked_works(self, _uid, *, rest_type):
        self.calls.append(('illustration', rest_type))
        if rest_type != 'show':
            return ()
        return (dict(id='99', title='Synthetic', user_id='25', user_name='Example',
                     tags=[], type=0, page_count=1, x_restrict=0,
                     thumb_url='https://i.pximg.net/synthetic/thumb.png',
                     create_date='2026-01-01T00:00:00+00:00'),)

    def get_bookmarked_novels(self, _uid, *, rest_type):
        self.calls.append(('novel', rest_type))
        return ()

    def get_artwork_original_pages(self, work_id):
        return (f'https://i.pximg.net/synthetic/{work_id}/1.png',)

    def get_image_bytes(self, _url):
        return png()


def services_with_runs(tmp_path, reader=None, **kwargs):
    services = ApplicationServices(live_client=reader if reader is not None else GeneratedReader(),
                                   pixiv_download_root=tmp_path / 'managed',
                                   preferences_root=tmp_path / 'prefs', **kwargs)
    services.configure_save_exports(tmp_path / 'exports')
    services.enable_batch_runs(tmp_path / 'runs')
    return services


@pytest.mark.asyncio
async def test_interrupted_bookmark_run_restores_list_before_finishing(tmp_path):
    first = services_with_runs(tmp_path)
    row_id = VISIBILITY_ROWS['show']
    try:
        await first.refresh_bookmark_cache()
        assert [r.work_id for r in first.bookmark_download_candidates()] == ['99']
        old_run = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row_id,), account_id='55', verified_bookmark_account=True)
        first.batch_run_ledger.update_author(old_run, row_id, state='downloading', account_id=first.batch_run_ledger.run(old_run).account_id)
    finally:
        await asyncio.to_thread(first.close)

    reader = GeneratedReader()
    reopened = services_with_runs(tmp_path, reader)
    try:
        assert reopened.batch_run_ledger.run(old_run).state == 'interrupted'
        assert not reopened.bookmark_download_candidates()
        new_run = reopened.resume_batch_run(old_run)
        assert await asyncio.to_thread(reopened.batch_runs.join, 12)
        outcome = reopened.batch_run_ledger.run(new_run).state
        items = reopened.batch_run_ledger.items(new_run, row_id)
        print(f'bookmark resume: state={outcome}, items={len(items)}, refresh_calls={len(reader.calls)}, offer={reopened.batch_runs.resumable_run() is not None}')
        assert outcome == 'done' and any(row.work_key == '99' for row in items)
    finally:
        await asyncio.to_thread(reopened.close)


@pytest.mark.asyncio
async def test_bookmark_resume_failed_refresh_remains_resumable(tmp_path):
    first = services_with_runs(tmp_path)
    row_id = VISIBILITY_ROWS['show']
    try:
        old_run = first.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row_id,), account_id='55', verified_bookmark_account=True)
        first.batch_run_ledger.update_author(old_run, row_id, state='downloading', account_id=first.batch_run_ledger.run(old_run).account_id)
    finally:
        await asyncio.to_thread(first.close)

    class BrokenReader(GeneratedReader):
        def verify_identity(self):
            raise RuntimeError('synthetic temporary failure')

    reopened = services_with_runs(tmp_path, BrokenReader())
    try:
        new_run = reopened.resume_batch_run(old_run)
        assert await asyncio.to_thread(reopened.batch_runs.join, 12)
        assert reopened.batch_run_ledger.run(new_run).state == 'failed'
        assert reopened.batch_runs.resumable_run().run_id == new_run
        assert reopened.batch_run_ledger.unfinished_authors(new_run) == (row_id,)
    finally:
        await asyncio.to_thread(reopened.close)


