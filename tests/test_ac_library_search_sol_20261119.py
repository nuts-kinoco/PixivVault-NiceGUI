import asyncio
from urllib.parse import urlsplit

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.composite_library import CompositeLocalCatalog, compose_library
from pixivvault_nicegui.external_vault_transport import VaultResponse, SEARCH
from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.premium_gate import PremiumAuthorization
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_ac_library_search_20261119 import FakeVault, until
from test_managed_download_services import FakeSource
from test_oauth_send_boundary import make_vault
from test_premium_authorization import CHECK, Protection, TEST_PASSWORD
from test_premium_borrow_routes import row


@pytest.fixture
def app_view(monkeypatch):
    views = []
    async def make(source=None, vault=None):
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        reader = source or FakeSource()
        svc = ApplicationServices(live_client=reader)
        svc.external_vault = vault or FakeVault()
        client = Client(ui.page(f'/_sol_k02_{len(views)}'))
        views.append((svc,client,None,reader))
        return svc, client, reader
    yield make, views
    for svc,client,view,reader in views:
        if isinstance(svc.external_vault, FakeVault):
            svc.external_vault.release.set()
            svc.external_vault = None
        if view is not None:
            view.close()
        client.delete()
        svc.close()


@pytest.mark.asyncio
async def test_production_premium_authorization_and_shared_stop_wiring(app_view,tmp_path):
    make, views = app_view
    vault, wire, scope = make_vault(tmp_path, [
        VaultResponse(200,dict(uid=1,account=dict(access_token='generated-only',
          user=dict(id=99,is_premium=True)),expiresAt=4600000)),
        VaultResponse(200,{'illusts':[dict(row(204),title='alpha remote')]}),
        VaultResponse(429,{}),
    ])
    gate=PremiumAuthorization(vault,protect=Protection(),verifier=CHECK,clock=lambda:1000)
    vault.premium_authorization=gate
    svc, client, reader = await make(vault=vault)
    svc.managed_network_stop=scope
    svc.premium_authorization=gate
    svc.library=LibrarySnapshot((LibraryWork(Artwork('203','alpha local','Local',author_id='7'),'7'),))
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.search('alpha')
        view.set_search_scope('pixiv')
        assert view.search_scope=='library' and wire.calls==[]
        gate.authenticate(TEST_PASSWORD)
        gate.confirm(False)
        await asyncio.to_thread(vault.refresh,1)
        view.refresh_search_scope()
        view.set_search_scope('pixiv')
        await until(lambda:view.pixiv_query=='alpha')
        assert [w.work_id for w in view.state.visible_authors[0].works]==['203','204']
        view.open_author('7')
        view.open_page('204',0)
        assert view._viewer_state.viewer_open and view._viewer_state.selected_artwork.work_id=='204'
        from io import BytesIO
        from PIL import Image
        thumb = await asyncio.to_thread(view._image_provider.get,'204',0,'thumb')
        with Image.open(BytesIO(thumb.data)) as decoded:
            assert len(decoded.getpixel((0,0))) in (3,4)
        assert 'image' in reader.calls
        view.close_viewer()
        view.search('alpha stopped')
        await until(lambda:view.pixiv_error)
        assert scope.gate.snapshot().blocked
        before=len(wire.calls)
        assert sum(urlsplit(u).path==urlsplit(SEARCH).path for u in wire.calls)==2
        view.retry_pixiv_search()
        await until(lambda:view.pixiv_error)
        assert len(wire.calls)==before and view.pixiv_retry.visible
        gate.disable()
        view.refresh_search_scope()
        assert view.search_scope=='library' and not view.scope_pixiv.enabled


@pytest.mark.parametrize('subview', ['downloaded','bookmarks'])
@pytest.mark.asyncio
async def test_non_network_filters_do_not_start_remote_search(app_view,subview):
    make,views=app_view
    svc,client,reader=await make()
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(lambda:view.pixiv_query=='alpha')
        view.show_subview(subview)
        view.apply_filters(LibraryFilters(include=frozenset({'alpha'}),authors=frozenset({'23'})))
        await asyncio.sleep(.45)
        print(subview, 'remote calls',len(svc.external_vault.calls))
        assert len(svc.external_vault.calls)==1


@pytest.mark.asyncio
async def test_scope_selection_has_visible_state(app_view):
    make,views=app_view
    svc,client,reader=await make()
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        assert 'pv-library-subview-selected' in view.scope_library.classes
        assert 'pv-library-subview-selected' not in view.scope_pixiv.classes
        view.set_search_scope('pixiv')
        assert 'pv-library-subview-selected' not in view.scope_library.classes
        assert 'pv-library-subview-selected' in view.scope_pixiv.classes


@pytest.mark.asyncio
async def test_ai_filter_changed_on_bookmarks_waits_for_library(app_view):
    make,views=app_view
    svc,client,reader=await make()
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(lambda:view.pixiv_query=='alpha')
        view.show_subview('bookmarks')
        view.apply_filters(LibraryFilters(exclude_ai=True))
        await asyncio.sleep(.3)
        assert len(svc.external_vault.calls)==1
        view.show_subview('downloaded')
        await until(lambda:len(svc.external_vault.calls)==2 and view.pixiv_query=='alpha')
        assert svc.external_vault.calls[-1][2]['exclude_ai'] is True


@pytest.mark.asyncio
async def test_local_tombstone_cannot_be_revived_as_remote_preview(app_view):
    make,views=app_view
    svc,client,reader=await make()
    empty=LibrarySnapshot(())
    comp=compose_library(empty,empty,(),(),managed_source_pages={},
        managed_original_page_counts={},managed_excluded_work_ids={'204'})
    svc.library=comp.snapshot
    svc.images.publish(CompositeLocalCatalog(comp),None)
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(lambda:view.pixiv_query=='alpha')
        revived=tuple(w.work_id for w in view.state.snapshot.works)
        print('tombstone resurface',revived,view._image_provider.can_open('204'))
        assert '204' not in revived
        assert not view._image_provider.can_open('204')


@pytest.mark.asyncio
async def test_existing_remote_pin_respects_new_tombstone(app_view):
    make,views=app_view
    svc,client,reader=await make()
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(lambda:view.pixiv_query=='alpha')
        assert view._image_provider.can_open('204')
        empty=LibrarySnapshot(())
        comp=compose_library(empty,empty,(),(),managed_source_pages={},
            managed_original_page_counts={},managed_excluded_work_ids={'204'})
        svc.images.publish(CompositeLocalCatalog(comp),None)
        assert not view._image_provider.can_open('204')
        with pytest.raises(ValueError):
            view._image_provider.get('204',0,'thumb')


@pytest.mark.asyncio
async def test_hidden_view_clears_results_on_revocation(app_view):
    make,views=app_view
    svc,client,reader=await make()
    vault=svc.external_vault
    class Authorization:
        enabled=True
        def status(self):return {'enabled':self.enabled}
    vault.premium_authorization=Authorization()
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(lambda:view.pixiv_query=='alpha')
        view.set_active(False)
        vault.premium_authorization.enabled=False
        view.refresh_search_scope()
        view.set_active(True)
        assert view.search_scope=='library'
        assert all(w.work_id!='204' for w in view.state.snapshot.works)


@pytest.mark.asyncio
async def test_pending_empty_uses_search_status_before_remote_result(app_view):
    make,views=app_view
    svc,client,reader=await make()
    vault=svc.external_vault
    vault.block=True
    with client:
        view=LibraryView(svc)
        views[-1]=(svc,client,view,reader)
        view.set_search_scope('pixiv')
        view.search('alpha')
        await until(vault.entered.is_set)
        try:
            assert view.pixiv_loading and not view.state.visible_authors
            print('pending empty',view.empty.text)
            assert view.empty.text=='pixiv本体を検索中…'
        finally:
            vault.release.set()
            await until(lambda:not view.pixiv_loading)


@pytest.mark.asyncio
async def test_separate_views_preserve_independent_results(app_view):
    make,views=app_view
    svc,client,reader=await make()
    other=Client(ui.page('/_sol_k02_other'))
    second=None
    try:
        with client:
            first=LibraryView(svc)
            views[-1]=(svc,client,first,reader)
            first.set_search_scope('pixiv')
            first.search('one')
        with other:
            second=LibraryView(svc)
            second.set_search_scope('pixiv')
            second.search('two')
        await until(lambda: first.pixiv_query=='one' and second.pixiv_query=='two')
        with client:
            first.set_search_scope('library')
        assert not first.state.snapshot.works
        assert [w.artwork.title for w in second.state.snapshot.works]==['two']
        assert second._image_provider.can_open('204')
    finally:
        if second is not None:second.close()
        other.delete()
