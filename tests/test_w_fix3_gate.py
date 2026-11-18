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
@pytest.mark.parametrize('when', ['before_identity', 'during_first_route'])
async def test_bookmarks_resume_pauses_when_gate_closes(tmp_path, when):
    owner = services_with_runs(tmp_path)
    row_id = VISIBILITY_ROWS['show']
    run_id = owner.batch_run_ledger.begin_run('bookmarks', 'manual', 'both', (row_id,))
    owner.batch_run_ledger.update_author(run_id, row_id, state='downloading')
    await asyncio.to_thread(owner.close)

    integration = ManagedNetworkStopIntegration(tmp_path / 'managed')
    issued = []
    if when == 'before_identity':
        with pytest.raises(HTTPStatusRejected), integration.gate.request():
            raise HTTPStatusRejected(429)

    class GatedReader(GeneratedReader):
        def verify_identity(self):
            with integration.gate.request():
                issued.append('identity')
                return super().verify_identity()

        def get_bookmarked_works(self, uid, *, rest_type):
            with integration.gate.request():
                issued.append(f'illustration:{rest_type}')
                if when == 'during_first_route':
                    raise HTTPStatusRejected(429)
                return super().get_bookmarked_works(uid, rest_type=rest_type)

        def get_bookmarked_novels(self, uid, *, rest_type):
            with integration.gate.request():
                issued.append(f'novel:{rest_type}')
                return super().get_bookmarked_novels(uid, rest_type=rest_type)

    reopened = services_with_runs(tmp_path, GatedReader())
    reopened.managed_network_stop = integration
    try:
        new_id = reopened.resume_batch_run(run_id)
        for _ in range(500):
            state = reopened.batch_run_ledger.run(new_id)
            if state.state in ('paused', 'failed', 'done'):
                break
            await asyncio.sleep(0.01)
        assert integration.gate.snapshot().blocked
        assert issued == ([] if when == 'before_identity' else ['identity', 'illustration:show'])
        print(f'gate {when}: issued={issued}, state={state.state}, error={state.error_code}')
        assert (state.state, state.error_code) == ('paused', 'network_stopped')
    finally:
        await asyncio.to_thread(reopened.close)
