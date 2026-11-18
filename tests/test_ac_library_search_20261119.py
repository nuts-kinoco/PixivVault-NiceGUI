import asyncio
from threading import Event

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.models import Artwork
from test_managed_download_services import FakeSource


class FakeVault:
    def __init__(self):
        self.calls = []
        self.entered, self.release = Event(), Event()
        self.block = False
        self.fail = False
    def status(self):
        return {'open': True, 'mode': 'nana7mi', 'transport_available': True}
    def search(self, word, bookmark_min=0, **kwargs):
        self.calls.append((word, bookmark_min, kwargs))
        if self.block:
            self.entered.set()
            assert self.release.wait(5)
        if self.fail:
            raise RuntimeError('synthetic failure')
        return {'illusts': [{'id': '204', 'title': word, 'user': {'id': '23', 'name': 'author'},
            'tags': [{'name': word}], 'type': 'illust', 'x_restrict': 0, 'page_count': 1,
            'total_bookmarks': 50, 'illust_ai_type': 1,
            'image_urls': {'medium': 'https://i.pximg.net/synthetic/204.png'}}]}


async def until(condition):
    async def poll():
        while not condition():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), 5)


@pytest.mark.asyncio
async def test_scope_search_retry_and_stale_result(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source, vault = FakeSource(), FakeVault()
    services = ApplicationServices(live_client=source)
    services.external_vault = vault
    client = Client(ui.page('/_ac_search'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            assert view.search_input._props['placeholder'] == 'タイトル・作者・タグを検索'
            view.set_search_scope('pixiv')
            vault.block = True
            view.search('old')
            await until(vault.entered.is_set)
            view.search('new')
            vault.block = False
            vault.release.set()
            await until(lambda: view.pixiv_query == 'new' and not view.pixiv_loading)
            assert all(work.artwork.title != 'old' for work in view.state.snapshot.works)
            assert any(work.artwork.title == 'new' for work in view.state.snapshot.works)
            assert view._image_provider.can_open('204')
            assert '204' in view._image_provider.url('204', 0, 'thumb')
            vault.fail = True
            view.search('failure')
            await until(lambda: bool(view.pixiv_error))
            assert view.pixiv_retry.visible
            vault.fail = False
            view.retry_pixiv_search()
            await until(lambda: view.pixiv_query == 'failure' and not view.pixiv_loading and not view.pixiv_error)
            view.set_search_scope('library')
            assert all(work.work_id != '204' for work in view.state.snapshot.works)
    finally:
        vault.release.set()
        if view: view.close()
        client.delete()
        services.external_vault = None
        services.close()


@pytest.mark.asyncio
async def test_search_merges_authors_filters_and_gate(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    source, vault = FakeSource(), FakeVault()
    services = ApplicationServices(live_client=source)
    services.external_vault = vault
    local = LibraryWork(Artwork('203', 'alpha local', 'author', author_id='23'), '23')
    services.library = LibrarySnapshot((local,))
    client = Client(ui.page('/_ac_search_merge'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            view.set_search_scope('pixiv')
            view.search('alpha')
            await until(lambda: view.pixiv_query == 'alpha' and not view.pixiv_loading)
            author = view.state.visible_authors[0]
            assert author.author_id == '23'
            assert {work.work_id for work in author.works} == {'203', '204'}
            view.open_author('23')
            assert {page.work.work_id for page in view.state.visible_pages} == {'203', '204'}
            view.open_page('204', 0)
            assert view._viewer_state.viewer_open
            assert view._viewer_state.selected_artwork.work_id == '204'
            view.close_viewer()
            view.close_author()
            view.apply_filters(LibraryFilters(bookmark_min=10, exclude_ai=True))
            await until(lambda: len(vault.calls) == 2 and view.pixiv_query == 'alpha')
            assert vault.calls[-1][1] == 10 and vault.calls[-1][2]['exclude_ai'] is True
            assert view.state.visible_authors[0].author_id == '23'
            class Stopped:
                def require_allowed(self): raise RuntimeError('stopped')
            services.managed_network_stop = Stopped()
            view.search('blocked')
            await until(lambda: view.pixiv_error)
            assert len(vault.calls) == 2
            assert view.pixiv_retry.visible
    finally:
        if view: view.close()
        client.delete()
        services.external_vault = None
        services.close()


@pytest.mark.asyncio
async def test_scope_tracks_borrow_permission(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    vault = FakeVault()
    class Gate:
        enabled = False
        def status(self): return {'enabled': self.enabled}
    vault.premium_authorization = Gate()
    services = ApplicationServices(live_client=FakeSource())
    services.external_vault = vault
    client = Client(ui.page('/_ac_search_permission'))
    view = None
    try:
        with client:
            view = LibraryView(services)
            view.set_search_scope('pixiv')
            assert view.search_scope == 'library' and not vault.calls
            vault.premium_authorization.enabled = True
            view.refresh_search_scope()
            assert view.scope_pixiv.enabled
            view.set_search_scope('pixiv')
            view.search('alpha')
            await until(lambda: view.pixiv_query == 'alpha')
            vault.premium_authorization.enabled = False
            view.refresh_search_scope()
            assert view.search_scope == 'library' and not view.scope_pixiv.enabled
            assert all(work.work_id != '204' for work in view.state.snapshot.works)
    finally:
        if view: view.close()
        client.delete()
        services.external_vault = None
        services.close()
