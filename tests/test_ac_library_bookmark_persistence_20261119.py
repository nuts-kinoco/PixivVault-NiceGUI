import pytest
import asyncio
from threading import Event
from nicegui import Client, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_managed_download_services import FakeSource


@pytest.mark.asyncio
async def test_same_verified_account_restores_snapshot_and_timestamp(tmp_path):
    root = tmp_path / 'preferences'
    first = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        await first.refresh_bookmark_cache()
        expected = first.bookmark_remote_snapshot()
        timestamp = first.bookmark_cache_fetched_at
    finally:
        first.close()
    source = FakeSource()
    second = ApplicationServices(live_client=source, preferences_root=root)
    try:
        assert second.bookmark_remote_snapshot() is None
        assert await second.restore_bookmark_cache()
        assert second.bookmark_remote_snapshot() == expected
        assert second.bookmark_cache_fetched_at == timestamp
        assert source.calls == ['identity']
        await second.refresh_bookmark_cache(automatic=True)
        assert source.calls == ['identity']
    finally:
        second.close()


@pytest.mark.asyncio
async def test_other_account_and_stopped_gate_never_publish_old_cache(tmp_path):
    root = tmp_path / 'preferences'
    first = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        await first.refresh_bookmark_cache()
    finally:
        first.close()
    class Other(FakeSource):
        def verify_identity(self):
            self.calls.append('identity')
            self._verified_identity = '13'
            return self._verified_identity
    source = Other()
    other = ApplicationServices(live_client=source, preferences_root=root)
    try:
        assert not await other.restore_bookmark_cache()
        assert other.bookmark_remote_snapshot() is None
        assert other.bookmark_cache_fetched_at is None
    finally:
        other.close()
    source = FakeSource()
    stopped = ApplicationServices(live_client=source, preferences_root=root)
    class Gate:
        def require_allowed(self): raise RuntimeError('stopped')
    stopped.managed_network_stop = Gate()
    try:
        with pytest.raises(RuntimeError, match='stopped'):
            await stopped.restore_bookmark_cache()
        assert source.calls == []
        assert stopped.bookmark_remote_snapshot() is None
    finally:
        stopped.close()


@pytest.mark.asyncio
async def test_failed_refresh_preserves_durable_snapshot_and_empty_success_replaces(tmp_path):
    root = tmp_path / 'preferences'
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=root)
    try:
        await services.refresh_bookmark_cache()
        expected, stamp = services.bookmark_remote_snapshot(), services.bookmark_cache_fetched_at
        source.fail = True
        with pytest.raises(RuntimeError): await services.refresh_bookmark_cache()
    finally:
        services.close()
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=root)
    try:
        assert await services.restore_bookmark_cache()
        assert services.bookmark_remote_snapshot() == expected
        assert services.bookmark_cache_fetched_at == stamp
        source.get_bookmarked_works = lambda *a, **k: iter(())
        await services.refresh_bookmark_cache()
        assert services.bookmark_remote_snapshot() == ()
    finally:
        services.close()
    services = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        assert await services.restore_bookmark_cache()
        assert services.bookmark_remote_snapshot() == ()
    finally:
        services.close()


@pytest.mark.asyncio
async def test_ui_restores_verified_stale_cache_before_network_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    root = tmp_path / 'preferences'
    first = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        await first.refresh_bookmark_cache()
        store = first._bookmark_cache_store
        assert store.write('12', '2020-01-01T00:00:00', first._remote_records,
                           expected_revision=store.revision())
    finally:
        first.close()
    class Slow(FakeSource):
        def __init__(self):
            super().__init__()
            self.entered, self.release = Event(), Event()
        def get_bookmarked_works(self, identity, rest_type):
            self.entered.set()
            assert self.release.wait(5)
            yield from super().get_bookmarked_works(identity, rest_type)
    source = Slow()
    services = ApplicationServices(live_client=source, preferences_root=root)
    client = Client(ui.page('/_ac_persist_restore'))
    view = None
    async def wait_for(predicate):
        async def poll():
            while not predicate(): await asyncio.sleep(.01)
        await asyncio.wait_for(poll(), 5)
    try:
        with client:
            view = LibraryView(services)
            view.show_subview('bookmarks')
            await wait_for(source.entered.is_set)
            assert {c.work_id for c in view.bookmark_state.items} == {'202', '203'}
            assert view.bookmarks.freshness.text == '最終更新: 2020-01-01 00:00'
            source.release.set()
            await wait_for(lambda: not view._bookmark_refreshing)
    finally:
        source.release.set()
        if view: view.close()
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_disabled_setting_does_not_verify_or_expose_cache(tmp_path):
    root = tmp_path / 'preferences'
    services = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        await services.refresh_bookmark_cache()
        services.preferences.set_setting('bookmark_auto_refresh', '0')
    finally:
        services.close()
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=root)
    try:
        assert not await services.restore_bookmark_cache()
        assert source.calls == [] and services.bookmark_remote_snapshot() is None
    finally:
        services.close()


@pytest.mark.asyncio
async def test_cache_compare_and_swap_rejects_stale_writer(tmp_path):
    root = tmp_path / 'preferences'
    services = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        await services.refresh_bookmark_cache()
        store = services._bookmark_cache_store
        previous = store.revision()
        assert store.write('12', '2020-01-01T00:00:00', (), expected_revision=previous)
        assert not store.write('12', '2021-01-01T00:00:00', services._remote_records,
                               expected_revision=previous)
        assert store.read('12') == ((), '2020-01-01T00:00:00')
    finally:
        services.close()


@pytest.mark.asyncio
async def test_unowned_cache_is_not_modified_and_does_not_block_refresh(tmp_path):
    root = tmp_path / 'preferences'
    root.mkdir()
    path = root / 'bookmark-cache.sqlite3'
    original = b'unowned data'
    path.write_bytes(original)
    services = ApplicationServices(live_client=FakeSource(), preferences_root=root)
    try:
        assert services.bookmark_cache_persist_failed
        await services.refresh_bookmark_cache()
        assert services.bookmark_remote_snapshot()
        assert path.read_bytes() == original
    finally:
        services.close()
