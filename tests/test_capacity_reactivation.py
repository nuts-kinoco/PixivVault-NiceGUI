"""L12 automatic cache restore through real view activation, synthetic SQLite only."""
import asyncio
import sqlite3
from threading import Event, Lock
from types import SimpleNamespace

import pytest
import pytest_asyncio
from nicegui import Client, ui
from pixivvault_nicegui.capacity_cache import CapacityCache
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView, format_capacity
from pixivvault_nicegui.ui.media_views import MediaLibraryView


class ScheduledReads:
    def __init__(self):
        self.tasks = []

    def create(self, coro, **kwargs):
        # The actual product coroutine executes on pytest's running loop.
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def drain(self):
        index = 0
        while index < len(self.tasks):
            batch = self.tasks[index:]
            index = len(self.tasks)
            await asyncio.gather(*batch)


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    schedule = ScheduledReads()
    monkeypatch.setattr('nicegui.background_tasks.create', schedule.create)
    path = tmp_path / 'capacity.sqlite3'
    root = tmp_path / 'synthetic-library'
    reader = SimpleNamespace(root=root)
    scans=[]
    def measure_missing(author):
        scans.append(author)
        # Cached authors are forbidden to rescan; unknown authors now have an
        # automatic attempt, whose synthetic failure must remain uncached.
        if author in values:raise AssertionError('cached author must not rescan media')
        raise OSError('synthetic unmeasured author unavailable')
    reader.measure = measure_missing
    services = ApplicationServices(120)
    db = sqlite3.connect(path, check_same_thread=False)
    cache = CapacityCache(db)
    cache.initialize()
    services.configure_legacy_capacity(reader, cache)
    values = {'demo-author-01': 1024, 'demo-author-02': 3072, 'demo-author-03': 0}
    for aid, value in values.items():
        assert cache.commit_success(aid, 0, value)
    cache.close()
    # Reopen persisted SQLite before constructing any UI: the reported restart boundary.
    cache = CapacityCache(sqlite3.connect(path, check_same_thread=False))
    services.configure_legacy_capacity(reader, cache)
    client = Client(ui.page('/_capacity_reactivation'))
    state = SimpleNamespace(services=services, cache=cache, reader=reader, values=values,
                            client=client, schedule=schedule, releases=[], scans=scans)
    try:
        yield state
    finally:
        for release in state.releases:
            release.set()
        await schedule.drain()
        client.delete()
        services.managed_downloads = None
        services.close()
        cache.close()


def assert_cache_unchanged(env):
    for aid, value in env.values.items():
        assert env.cache.snapshot(aid) == (1, value)
    assert env.cache.snapshot('demo-author-04') == (0, None)
    assert not set(env.scans).intersection(env.values)


@pytest.mark.asyncio
async def test_real_media_library_restores_on_first_activation_after_restart(env):
    # Sentinel selects the production MediaLibraryView's illustration branch;
    # no managed measurement/save function is invoked by this cache-only test.
    env.services.managed_downloads = SimpleNamespace()
    refreshes = []
    original_refresh = env.services.refresh_library_snapshot
    async def record_refresh():
        refreshes.append(True)
        return await original_refresh()
    env.services.refresh_library_snapshot = record_refresh
    with env.client:
        media = MediaLibraryView(env.services)
        view = media.views['illustration']
        assert not view.active
        initial = view.state.snapshot
        unknown_before = next(a.capacity_bytes for a in view.state.visible_authors if a.author_id == 'demo-author-04')
        assert len(env.schedule.tasks) == 1
        media.set_active(True)
        media.set_active(True)
        assert len(env.schedule.tasks) == 2
        await env.schedule.drain()
        assert view.state.snapshot is not initial
        for aid, value in env.values.items():
            assert view.state.snapshot.author_capacities[aid] == value
            texts = [getattr(e, 'text', '') for e in view.author_cards[aid].descendants()]
            assert any(format_capacity(value) in text for text in texts)
        assert next(a.capacity_bytes for a in view.state.visible_authors if a.author_id == 'demo-author-04') == unknown_before
        assert not view._legacy_restore_needed
        count = len(env.schedule.tasks)
        media.set_active(True)
        assert len(env.schedule.tasks) == count
        view.sort('capacity')
        await env.schedule.drain()
        order = [a.author_id for a in view.state.visible_authors]
        assert order.index('demo-author-02') < order.index('demo-author-01') < order.index('demo-author-03')
        assert_cache_unchanged(env)
        assert refreshes == []
        media.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['inactive', 'subview', 'author', 'search', 'reload', 'close'])
@pytest.mark.parametrize('old_read_fails', [False, True])
async def test_late_old_cache_read_cannot_win_after_lifetime_change(env, action, old_read_fails):
    entered, release, lock = Event(), Event(), Lock()
    env.releases.append(release)
    original = env.services.legacy_capacity.cached
    calls = []
    def delayed(aid):
        with lock:
            first = not calls
            calls.append(aid)
        result = original(aid)
        if first:
            entered.set()
            assert release.wait(5)
            if old_read_fails:
                raise OSError('synthetic old read failure')
            return result[0], 777
        return result
    env.services.legacy_capacity.cached = delayed
    with env.client:
        view = LibraryView(env.services)
        assert await asyncio.to_thread(entered.wait, 5)
        original_snapshot = view.state.snapshot
        if action == 'inactive':
            view.set_active(False)
            view.set_active(True)
        elif action == 'subview':
            view.show_subview('bookmarks')
            view.show_subview('downloaded')
        elif action == 'author':
            view.open_author('demo-author-01')
            view.close_author()
        elif action == 'search':
            view.search('0003')
        elif action == 'reload':
            view.reload()
        else:
            view.close()
        release.set()
        await env.schedule.drain()
        if action == 'close':
            assert view.state.snapshot is original_snapshot
        else:
            assert view.state.snapshot.author_capacities['demo-author-01'] == 1024
            assert 777 not in view.state.snapshot.author_capacities.values()
            assert view.legacy_capacity_status.text == ''
            assert not view._legacy_restore_needed
        assert_cache_unchanged(env)
        view.close()


@pytest.mark.asyncio
async def test_current_cache_failure_retains_snapshot_and_reentry_can_retry(env):
    original = env.services.legacy_capacity.cached
    calls = []
    def fail(aid):
        calls.append(aid)
        raise OSError('synthetic cache read failure')
    env.services.legacy_capacity.cached = fail
    with env.client:
        view = LibraryView(env.services)
        previous = view.state.snapshot
        await env.schedule.drain()
        assert calls and view.state.snapshot is previous
        assert '読み込めません' in view.legacy_capacity_status.text
        assert_cache_unchanged(env)
        env.services.legacy_capacity.cached = original
        view.set_active(False)
        view.set_active(True)
        await env.schedule.drain()
        assert view.state.snapshot.author_capacities['demo-author-03'] == 0
        assert view.state.snapshot.author_capacities['demo-author-01'] == 1024
        assert view.legacy_capacity_status.text == ''
        view.close()


@pytest.mark.asyncio
async def test_reentry_during_cancelled_measurement_restores_after_worker_exits(env):
    entered, release = Event(), Event()
    env.releases.append(release)
    def measure(aid):
        if aid not in env.values:raise OSError('synthetic unmeasured author unavailable')
        entered.set()
        assert release.wait(5)
        return 999
    task = None
    try:
        with env.client:
            view = LibraryView(env.services)
            await env.schedule.drain()
            count = len(env.schedule.tasks)
            env.reader.measure = measure
            task = asyncio.create_task(view.measure_legacy_capacity())
            assert await asyncio.to_thread(entered.wait, 5)
            view.set_active(False)
            view.set_active(True)
            assert view._legacy_capacity_busy
            assert len(env.schedule.tasks) == count
            release.set()
            await task
            assert len(env.schedule.tasks) == count + 1
            await env.schedule.drain()
            assert not view._legacy_capacity_busy and not view._legacy_restore_needed
            assert view.state.snapshot.author_capacities['demo-author-01'] == 1024
            assert view.legacy_capacity_status.text == ''
            assert_cache_unchanged(env)
            view.close()
    finally:
        release.set()
        if task is not None:
            await task
