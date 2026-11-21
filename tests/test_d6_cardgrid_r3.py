"""Verify unchanged CardGrid projections preserve live viewer and action state."""
import asyncio
from types import SimpleNamespace

from nicegui import Client, core, ui
import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import row
from test_ranking_ui import setup
from test_y_ranking_personal_20261118 import make, open_view


@pytest.fixture
def client():
    client = Client(ui.page('/_d6_cardgrid_r3'))
    try:
        yield client
    finally:
        client.delete()


async def test_personal_timer_preserves_viewer_and_actions_until_results_change(client, monkeypatch):
    with client:
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        hidden = set()
        context = lambda: dict(hidden_work_ids=set(hidden), hidden_author_ids=set(),
                               downloaded_ids=set(), hated_ids=set())
        services, vault = make()
        vault.saved = {'tags': ['fixture']}
        revision = 0
        services.local_tag_view = SimpleNamespace(revision=lambda: revision)
        services.personal_ranking_context = context
        services.personal_ranking_context_base = context
        try:
            view = await open_view(services, vault)
            await view.load()
            gallery = view.gallery
            state, epoch = gallery.state, gallery.grid.render_epoch
            assert state.open_artwork('123')
            generation = view._open_generation
            revision += 1
            view._sync_local_context()
            await view._context_task
            assert gallery.state is state and state.selected_id == '123'
            assert gallery.grid.render_epoch == epoch and view._open_generation == generation
            opened = []
            monkeypatch.setattr(gallery, 'open', opened.append)
            packet = dict(work_id='123', action='open', epoch=epoch)
            await gallery.dispatch_card_event(packet)
            assert opened == ['123']

            hidden.add('124')
            revision += 1
            view._sync_local_context()
            await view._context_task
            assert '124' not in gallery.cards
            assert gallery.grid.render_epoch == epoch + 1
            assert state.closed
            await gallery.dispatch_card_event(packet)
            assert opened == ['123']
        finally:
            services.close()


async def test_retry_failed_thumbnails_matches_each_original_ranking(client, monkeypatch):
    with client:
        monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
        public, _, _ = setup()
        personal, vault = make()
        vault.saved = {'tags': ['fixture']}
        try:
            view = RankingView(public)
            view.set_active(True)
            await view.load()
            before = view.gallery.grid.props.get('retry_epoch', 0)
            await view.refresh()
            assert view.gallery.grid.props.get('retry_epoch', 0) == before

            view = await open_view(personal, vault)
            await view.load()
            before = view.gallery.grid.props.get('retry_epoch', 0)
            await view.load()
            assert view.gallery.grid.props.get('retry_epoch', 0) == before
            await view.refresh()
            assert view.gallery.grid.props.get('retry_epoch', 0) == before + 1
        finally:
            personal.close()
            public.close()


async def test_identical_public_refresh_keeps_cards_and_open_viewer(client):
    with client:
        services, reader, _ = setup()
        reader.rows = [row(123), row(124)]
        try:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            gallery = view.gallery
            state, epoch = gallery.state, gallery.grid.render_epoch
            assert state.open_artwork('123')
            await view.load()
            assert gallery.state is state and state.selected_id == '123'
            assert gallery.grid.render_epoch == epoch
        finally:
            services.close()


async def test_bookmark_source_and_disabled_changes_retire_but_selection_does_not(client):
    with client:
        source = '/media/fixture/thumb.png'
        enabled = True
        state = BookmarkState(cached_remote=(Artwork('fixture', 'Work', 'Author'),))
        view = BookmarkView(state, can_open=lambda _: enabled, image_url=lambda _: source)
        view.render()
        epoch = view.grid.render_epoch
        await view._dispatch_grid_event(dict(work_id='fixture', action='select', epoch=epoch, value=True))
        assert state.selected_ids == {'fixture'} and view.grid.render_epoch == epoch
        enabled = False
        view.refresh()
        assert view.grid.render_epoch == epoch + 1 and view.cards['fixture']['disabled']
        source += '?catalog=fixture'
        view.refresh()
        assert view.grid.render_epoch == epoch + 2
        view.refresh()
        assert view.grid.render_epoch == epoch + 2


async def test_identical_background_library_reload_keeps_navigation_and_actions(client):
    with client:
        services = ApplicationServices(120)
        try:
            view = LibraryView(services)
            author = next(iter(view.author_cards))
            author_epoch = view.author_grid.render_epoch
            view.reload(preserve=True, after_viewer=True)
            assert view.author_grid.render_epoch == author_epoch
            view.open_author(author)
            page_epoch, page_session = view.page_grid.render_epoch, view._page_session
            view.reload(preserve=True, after_viewer=True)
            assert view.page_grid.render_epoch == page_epoch and view._page_session is page_session
        finally:
            services.close()
