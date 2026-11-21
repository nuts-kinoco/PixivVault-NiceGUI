import asyncio
from datetime import datetime
from threading import Event

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_managed_download_services import FakeSource


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


async def wait_for(ready):
    async def observe():
        while not ready():
            await asyncio.sleep(.01)
    await asyncio.wait_for(observe(), 5)


@pytest.mark.asyncio
async def test_actual_settings_and_two_views_coalesce_silent_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = DelayedSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path/'preferences')
    clients = [Client(ui.page(f'/_sol_k06_{i}')) for i in range(2)]
    views = []
    try:
        assert services.bookmark_auto_refresh_enabled()
        assert 'bookmark_auto_refresh' in services.preference_consumers()
        await services.refresh_bookmark_cache()
        services.bookmark_cache_fetched_at = '2020-01-01T00:00:00'
        source.wait = True
        for client in clients:
            with client:
                view = LibraryView(services)
                views.append(view)
                view.show_subview('bookmarks')
        await wait_for(source.entered.is_set)
        assert all(v.bookmark_state.status == 'ready' and v.bookmarks.retry.visible for v in views)
        with clients[0]:
            views[0].show_subview('downloaded')
        source.release.set()
        await wait_for(lambda: all(not v._bookmark_refreshing for v in views))
        with clients[0]:
            views[0].show_subview('bookmarks')
        await asyncio.sleep(.05)
        assert source.calls.count('identity') == 2
        assert all(v.bookmark_state.loaded_at == services.bookmark_cache_fetched_at for v in views)
        assert views[0].bookmarks.freshness.text.startswith('最終更新: '+datetime.now().strftime('%Y-%m-%d'))
    finally:
        source.release.set()
        for v, c in zip(views, clients):
            v.close()
            c.delete()
        services.close()


@pytest.mark.asyncio
async def test_actual_settings_off_while_waiting_prevents_network(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path/'preferences')
    client = Client(ui.page('/_sol_k06_off'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            async with services._library_refresh_lock:
                view.show_subview('bookmarks')
                assert view._bookmark_refreshing
                await asyncio.sleep(0)
                services.preferences.set_setting('bookmark_auto_refresh','0')
            await wait_for(lambda:not view._bookmark_refreshing)
            assert not services.bookmark_auto_refresh_enabled()
            assert source.calls == []
            assert view.bookmark_state.status == 'unloaded'
            view.refresh_bookmarks()
            await wait_for(lambda:not view._bookmark_refreshing)
            assert source.calls.count('identity') == 1
            assert view.bookmark_state.status == 'ready'
    finally:
        if view is not None:
            view.close()
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_real_stop_gate_preserves_cache_on_auto(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = FakeSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path/'preferences')
    client = Client(ui.page('/_sol_k06_gate'))
    view = None
    try:
        remote, _ = await services.refresh_bookmark_cache()
        services.bookmark_cache_fetched_at = '2020-01-01T00:00:00'
        root = tmp_path/'owned-pixiv'
        writer = ManagedPixivDownloads(root, object(), resolve_record=lambda _id: None)
        writer.close()
        services.managed_network_stop = ManagedNetworkStopIntegration(root)
        services.managed_network_stop.gate.trip(429)
        with client:
            view = LibraryView(services)
            view.show_subview('bookmarks')
            await wait_for(lambda:not view._bookmark_refreshing)
            assert view.bookmark_state.fetch_failed
            assert view.bookmark_state.remote_cache == remote
            assert view.bookmark_state.loaded_at == '2020-01-01T00:00:00'
            assert source.calls.count('identity') == 1
    finally:
        if view is not None:
            view.close()
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_silent_completion_keeps_the_open_bookmark_viewer(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = DelayedSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path/'preferences')
    client = Client(ui.page('/_sol_k06_reader'))
    view = None
    try:
        await services.refresh_bookmark_cache()
        services.bookmark_cache_fetched_at = '2020-01-01T00:00:00'
        source.wait = True
        with client:
            view = LibraryView(services)
            view.show_subview('bookmarks')
            await wait_for(source.entered.is_set)
            view.open_bookmark('202')
            assert view._viewer_state.viewer_open
            source.release.set()
            await wait_for(lambda:not view._bookmark_refreshing)
            assert view._viewer_state.viewer_open
    finally:
        source.release.set()
        if view is not None:
            view.close()
        client.delete()
        services.close()


class AddedBookmarkSource(DelayedSource):
    def get_bookmarked_works(self, identity, rest_type):
        yield from super().get_bookmarked_works(identity, rest_type)
        if rest_type == 'show' and self.wait:
            yield {'id':'204','title':'new work','user_id':'23','user_name':'author',
                   'tags':[], 'type':0, 'thumb_url':'https://i.pximg.net/synthetic/204.png',
                   'page_count':1}


@pytest.mark.parametrize('dismiss', ['close', 'navigation'])
@pytest.mark.asyncio
async def test_new_bookmark_card_has_current_source_after_dismissal(tmp_path, monkeypatch, dismiss):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source = AddedBookmarkSource()
    services = ApplicationServices(live_client=source, preferences_root=tmp_path/'preferences')
    client = Client(ui.page('/_sol_k06_image_'+dismiss))
    view = None
    try:
        await services.refresh_bookmark_cache()
        services.bookmark_cache_fetched_at = '2020-01-01T00:00:00'
        source.wait = True
        with client:
            view = LibraryView(services)
            view.show_subview('bookmarks')
            await wait_for(source.entered.is_set)
            view.open_bookmark('202')
            assert view._viewer_state.viewer_open
            old_provider = view._image_provider
            source.release.set()
            await wait_for(lambda:not view._bookmark_refreshing)
            assert view._viewer_state.viewer_open
            assert view._image_provider is old_provider
            if dismiss == 'close':
                view.close_viewer()
            else:
                view.set_active(False)
                view.set_active(True)
            card = view.bookmarks.cards['204']
            assert view._image_provider is not old_provider
            assert card['src'] == view._image_provider.url('204', 0, 'thumb')
            assert not card['disabled']
            view.open_bookmark('204')
            assert view._viewer_state.viewer_open
    finally:
        source.release.set()
        if view is not None:
            view.close()
        client.delete()
        services.close()
