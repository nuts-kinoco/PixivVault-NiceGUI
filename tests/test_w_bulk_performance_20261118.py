import time
from html.parser import HTMLParser
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
async def test_many_targets_keep_chip_element_count_bounded(tmp_path):
    services, client, view = make(tmp_path, author_rows(5000))
    try:
        await view.reload()
        before = len(client.elements)
        view.select_all_shown()
        assert len(view._chips) == 400
        # Static author text does not need thousands of individually reactive elements.
        assert len(client.elements) - before <= 2100
    finally:
        finish(services, client)


@pytest.mark.asyncio
async def test_target_chip_treats_author_name_as_text(tmp_path):
    rows = author_rows(1)
    rows[0]['name'] = '<img src=x onerror=alert(1)> & 作者'
    services, client, view = make(tmp_path, rows)
    try:
        await view.reload()
        view.select_all_shown()
        chip = view._chips['1001']['row']
        content = chip.default_slot.children[0].content

        class Reader(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags, self.text = [], []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)

            def handle_data(self, data):
                self.text.append(data)

        reader = Reader()
        reader.feed(content)
        assert reader.tags == ['div', 'div']
        assert reader.text == [rows[0]['name'], '1001']
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
        view.resumable = SimpleNamespace(run_id='1' * 32)
        monkeypatch.setattr(services, 'resume_batch_run', lambda *args, **kwargs: None)
        monkeypatch.setattr(services.batch_run_ledger, 'authors', lambda _: [
            SimpleNamespace(author_id='1002'), SimpleNamespace(author_id='1003')])
        assert view.resume_interrupted()
        assert view._chips['1001']['status'].text == '待機'
        assert view._chips['1002']['status'].text == '待機'
    finally:
        finish(services, client)


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [5000, 20000])
async def test_large_run_start_and_active_tick(tmp_path, count):
    services, client, view = make(tmp_path, author_rows(count), hold=True)
    try:
        await view.reload()
        view.select_all_shown()
        started = time.perf_counter()
        assert view.run()
        elapsed = time.perf_counter() - started
        view._events.put({'event': 'author_start', 'author_id': '1001'})
        view._events.put({'event': 'work_progress', 'author_id': '1001', 'done': 0, 'queued': 2})
        started = time.perf_counter()
        view.tick()
        tick = time.perf_counter() - started
        assert view.progress.current == '1001'
        print(f'count={count} run={elapsed:.3f}s tick={tick:.3f}s')
        assert elapsed < 1.0
        assert tick < 1.0
    finally:
        finish(services, client)
