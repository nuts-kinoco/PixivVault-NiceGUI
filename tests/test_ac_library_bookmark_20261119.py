from datetime import datetime, timedelta
import asyncio
from threading import Event

import pytest
from nicegui import Client, core, ui
from test_managed_download_services import FakeSource

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.preferences import SPEC_BY_KEY
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
import sqlite3


def test_auto_refresh_age_and_default_setting():
    now = datetime(2026, 11, 19, 12, 0)
    assert SPEC_BY_KEY['bookmark_auto_refresh'].default == '1'
    assert LibraryView._bookmark_refresh_due(None, now)
    assert not LibraryView._bookmark_refresh_due((now - timedelta(seconds=119)).isoformat(), now)
    assert LibraryView._bookmark_refresh_due((now - timedelta(seconds=120)).isoformat(), now)
    assert LibraryView._bookmark_refresh_due('bad', now)


@pytest.mark.asyncio
async def test_bookmark_text_and_paging():
    client = Client(ui.page('/_ac_bookmark'))
    state = BookmarkState(cached_remote=[Artwork(str(i), f'title {i}', 'author') for i in range(60)])
    state.loaded_at = '2026-11-19T12:34:56'
    try:
        with client:
            view = BookmarkView(state, on_enqueue=lambda *a, **k: None)
            view.render()
            assert view.freshness.text == '最終更新: 2026-11-19 12:34'
            assert view.counter.text == '60 件中 50 件を表示 / 全 60 件'
            assert view.download_button.text == '選択をダウンロード'
            view.more()
            assert view.counter.text == '60 件 / 全 60 件'
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_revisit_refresh_respects_setting():
    services = ApplicationServices()
    client = Client(ui.page('/_ac_bookmark_revisit'))
    try:
        with client:
            view = LibraryView(services)
            calls = []
            view.refresh_bookmarks = lambda **kwargs: calls.append(True)
            services.bookmark_auto_refresh_enabled = lambda: True
            view.bookmark_state.loaded_at = None
            view.show_subview('bookmarks')
            assert calls == [True]
            view.show_subview('downloaded')
            services.bookmark_auto_refresh_enabled = lambda: False
            view.show_subview('bookmarks')
            assert calls == [True]
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_stop_gate_prevents_automatic_fetch_and_keeps_cache():
    services = ApplicationServices()
    before = services.bookmark_remote_snapshot()
    class Reader:
        def verify_identity(self):
            raise AssertionError('network must not be attempted')
    class Gate:
        def require_allowed(self):
            raise RuntimeError('stopped')
    services.live_client = Reader()
    services.managed_network_stop = Gate()
    try:
        with pytest.raises(RuntimeError, match='stopped'):
            await services.refresh_bookmark_cache()
        assert services.bookmark_remote_snapshot() == before
    finally:
        services.live_client = None
        services.close()


@pytest.mark.asyncio
async def test_automatic_refresh_rechecks_setting_under_service_lock():
    source = FakeSource()
    services = ApplicationServices(live_client=source)
    assert not services.bookmark_auto_refresh_enabled()
    enabled = [True]
    services.bookmark_auto_refresh_enabled = lambda: enabled[0]
    try:
        await services._library_refresh_lock.acquire()
        task = asyncio.create_task(services.refresh_bookmark_cache(automatic=True))
        await asyncio.sleep(0)
        enabled[0] = False
        services._library_refresh_lock.release()
        remote, _ = await task
        assert remote is None
        assert source.calls == []
    finally:
        services.close()


def test_legacy_timestamp_is_read_only(tmp_path):
    path = tmp_path / 'library.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,user_id TEXT,tags TEXT,is_deleted INTEGER);'
                         'CREATE TABLE following_users(user_id TEXT,name TEXT);'
                         'CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);')
        db.execute('INSERT INTO settings VALUES (?,?)', ('bookmark_cache_fetched_at', '2026-11-19T12:34:56'))
    before = path.read_bytes()
    repository = ReadonlyLibraryRepository(path)
    try:
        assert repository.bookmark_cache_fetched_at() == '2026-11-19T12:34:56'
    finally:
        repository.close()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_silent_auto_refresh_revisit_and_shared_coalescing(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    class DelayedSource(FakeSource):
        def __init__(self):
            super().__init__()
            self.entered, self.release = Event(), Event()
            self.wait = False
        def verify_identity(self):
            if self.wait:
                self.entered.set()
                assert self.release.wait(5)
            return super().verify_identity()
    async def wait_for(condition):
        async def poll():
            while not condition():
                await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 5)
    source = DelayedSource()
    services = ApplicationServices(live_client=source)
    services.bookmark_auto_refresh_enabled = lambda: True
    clients = [Client(ui.page(f'/_ac_shared_{i}')) for i in range(2)]
    views = []
    try:
        await services.refresh_bookmark_cache()
        services.bookmark_cache_fetched_at = '2020-01-01T00:00:00'
        source.wait = True
        for client in clients:
            with client:
                views.append(LibraryView(services))
                views[-1].show_subview('bookmarks')
        await wait_for(source.entered.is_set)
        assert views[0].bookmark_state.status == 'ready'
        assert views[0].bookmarks.retry.visible
        with clients[0]:
            views[0].show_subview('downloaded')
        source.release.set()
        await wait_for(lambda: all(not view._bookmark_refreshing for view in views))
        assert source.calls.count('identity') == 2
        with clients[0]:
            views[0].show_subview('bookmarks')
        await asyncio.sleep(.05)
        assert source.calls.count('identity') == 2
        assert views[0].bookmark_state.remote_cache == services.bookmark_remote_snapshot()
        assert views[0].bookmark_state.loaded_at == services.bookmark_cache_fetched_at
        assert views[0].bookmarks.freshness.text.startswith('最終更新: ')
    finally:
        source.release.set()
        for view in views:
            view.close()
        for client in clients:
            client.delete()
        services.close()
