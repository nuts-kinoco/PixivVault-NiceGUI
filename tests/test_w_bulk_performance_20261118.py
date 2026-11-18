import time
from types import SimpleNamespace

import pytest

from test_w_dl_screen_20261118 import author_rows, finish, make
from pixivvault_nicegui import unified_download_state as U


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [5000, 20000])
async def test_select_many_targets_has_bounded_rendering(tmp_path, count):
    services, client, view = make(tmp_path, author_rows(count))
    try:
        await view.reload()
        started = time.perf_counter()
        view.select_all_shown()
        elapsed = time.perf_counter() - started
        print(f'count={count} select={elapsed:.3f}s chips={len(view._chips)}')
        assert len(view.targets) == count
        assert len(view._chips) <= 400
        assert any(f'ほか {count - len(view._chips)} 人' in str(getattr(e, 'text', ''))
                   for e in client.elements.values())
        assert elapsed < 1.0
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_tick_refreshes_only_changed_chips(tmp_path, monkeypatch):
    services, client, view = make(tmp_path, author_rows(100))
    try:
        await view.reload()
        view.select_all_shown()
        view.progress.target_ids = view.target_ids()
        calls = []
        original = view._chip_status
        monkeypatch.setattr(view, '_chip_status', lambda uid: (calls.append(uid), original(uid))[1])
        view._events.put({'event': 'author_start', 'author_id': '1001'})
        view.tick()
        assert calls == ['1001']
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_resuming_reconciles_chips_outside_new_run(tmp_path, monkeypatch):
    services, client, view = make(tmp_path, author_rows(3))
    try:
        await view.reload()
        view.select_all_shown()
        view.progress = U.RunProgress(['1001', '1002', '1003'])
        view.progress.apply({'event': 'author_done', 'author_id': '1001'})
        view.progress.apply({'event': 'author_failed', 'author_id': '1002'})
        view.refresh_chip_status()
        assert view._chips['1001']['status'].text == '完了'
        assert view._chips['1002']['status'].text == '失敗'
        monkeypatch.setattr(services, 'resume_batch_run', lambda: None)
        monkeypatch.setattr(services.batch_run_ledger, 'authors', lambda _: [
            SimpleNamespace(author_id='1002'), SimpleNamespace(author_id='1003')])
        assert view.resume_interrupted()
        assert view._chips['1001']['status'].text == '待機'
        assert view._chips['1002']['status'].text == '待機'
    finally:
        finish(services, client)
