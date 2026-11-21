"""Neon review regressions at the production Python view and media boundaries."""
import asyncio
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from nicegui import Client, core, ui
from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
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
    client = Client(ui.page('/_cardgrid_review'))
    return client


async def test_ranking_reopens_closed_catalog(client):
    services, _, pins = setup()
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            view.pin.close()
            await view.load()
            assert len(pins) == 2 and view.pin is pins[-1] and not view.pin.closed
    finally:
        client.delete()
        services.close()


async def test_local_tag_monitor_updates_hearts_without_retiring_actions(client):
    services, _, _ = setup()
    services.local_tag_view = SimpleNamespace(revision=lambda: 2)
    services.preferred_ranking_tags_base = lambda: ()
    services.preferred_ranking_tags = lambda: ('猫',)
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            gallery = view.gallery
            epoch = gallery.grid.render_epoch
            state = gallery.state
            view.local_tags_changed()
            await view._local_revision_task
            assert gallery.grid.render_epoch == epoch and gallery.state is state
            assert gallery.cards['123'].data['heart']
            opened = []
            gallery.open = opened.append
            await gallery.dispatch_card_event(dict(work_id='123', action='open', epoch=epoch))
            assert opened == ['123']
    finally:
        client.delete()
        services.close()


async def test_author_capacity_keeps_epoch_and_disabled_page_more_is_ignored(client):
    services = ApplicationServices(120)
    try:
        with client:
            library = LibraryView(services)
            epoch = library.author_grid.render_epoch
            aid = next(iter(library.author_cards))
            library._apply_legacy_capacities(library.state, {aid: 1024})
            assert library.author_grid.render_epoch == epoch
            assert library.author_cards[aid]['capacity'] == '1.0 KiB'
            await library._author_grid_event(dict(work_id=aid, action='open', epoch=epoch))
            assert library.state.selected_author_id == aid
            library.more_button.disable()
            before = library.state.shown_page_count
            await library._page_grid_event(dict(work_id=None, action='more', epoch=library.page_grid.render_epoch))
            assert library.state.shown_page_count == before
    finally:
        client.delete()
        services.close()


async def test_public_catalog_keeps_overlap_urls_and_retires_removed_thumbs(client):
    services, reader, _ = setup()
    try:
        # Activate first so the fixture does not schedule a separate startup fetch.
        del services.acquire_ranking_images
        with client:
            view = RankingView(services)
            view.set_active(True)
            services._vault_images = reader
            thumbnail = BytesIO()
            Image.new('RGB', (4, 4), 'red').save(thumbnail, format='PNG')
            reader.get_image_bytes = lambda _: thumbnail.getvalue()
            reader.rows = [row(123), row(124)]
            await view.load()
            previous = {key: card.data['src'] for key, card in view.gallery.cards.items()}
            epoch = view.gallery.grid.render_epoch
            old_pin = view.pin
            reader.rows = [row(125)]
            await view.load(append=True)
            assert view.gallery.grid.render_epoch == epoch
            assert old_pin.closed
            assert all(view.gallery.cards[key].data['src'] == url for key, url in previous.items())
            reader.rows = [row(124), row(125)]
            await view.load()
            assert view.gallery.cards['124'].data['src'] == previous['124']
            retired = parse_qs(urlsplit(previous['123']).query)['catalog'][0]
            with pytest.raises(ValueError):
                services.images.metadata_versioned(retired, '123', 0, 'thumb')
            alias = parse_qs(urlsplit(previous['124']).query)['catalog'][0]
            assert services.images.get_versioned(alias, '124', 0, 'thumb').data
            for work_id, page, variant in (('125', 0, 'thumb'), ('124', 1, 'thumb'), ('124', 0, 'view')):
                with pytest.raises(ValueError):
                    services.images.get_versioned(alias, work_id, page, variant)
            reader.rows[0]['url'] = 'https://i.pximg.net/fixture-updated.png'
            await view.load()
            assert view.gallery.cards['124'].data['src'] != previous['124']
            with pytest.raises(ValueError):
                services.images.get_versioned(alias, '124', 0, 'thumb')
            assert services.images.cache_control('123') == 'no-store'
            view.close()
            assert not services.images._thumbnail_owners
    finally:
        client.delete()
        services.close()



async def test_personal_refresh_reuses_pin_without_replaying_failed_images(client, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault = make()
    vault.saved = {'tags': ['fixture']}
    try:
        with client:
            view = await open_view(services, vault)
            await view.load()
            pin = view.pin
            assert pin is not None
            grid = view.gallery.grid
            retry = grid.props.get('retry_epoch', 0)
            await view.load()
            assert view.pin is pin and not pin.closed
            await view.refresh()
            assert view.pin is pin and not pin.closed
            assert grid.props.get('retry_epoch', 0) == retry
            view.set_active(False)
            view.set_active(True)
            assert grid.props['retry_epoch'] == retry + 1
            view.close()
            assert pin.closed
    finally:
        client.delete()
        services.close()


async def test_author_and_bookmark_tail_append_keep_existing_action_epochs(client):
    services = ApplicationServices(60)
    try:
        with client:
            services.library = LibrarySnapshot(tuple(
                LibraryWork(art, f'fixture-{n:02}', author_name=f'Fixture {n:02}')
                for n, art in enumerate(services.artworks)))
            library = LibraryView(services)
            assert len(library.author_cards) == 50
            epoch = library.author_grid.render_epoch
            library.more_authors()
            assert len(library.author_cards) == 60 and library.author_grid.render_epoch == epoch

            bookmarks = BookmarkView(BookmarkState(cached_remote=tuple(
                Artwork(f'demo-{n}', f'Work {n}', 'Author') for n in range(60))))
            bookmarks.render()
            epoch = bookmarks.grid.render_epoch
            event = dict(work_id=None, action='more', epoch=epoch)
            bookmarks.more_button.disable()
            await bookmarks._dispatch_grid_event(event)
            assert len(bookmarks.cards) == 50
            bookmarks.more_button.enable()
            await bookmarks._dispatch_grid_event(event)
            assert len(bookmarks.cards) == 60 and bookmarks.grid.render_epoch == epoch
    finally:
        client.delete()
        services.close()



def test_preferred_base_and_effective_sets_share_one_discovery_read(client, tmp_path, monkeypatch):
    from pixivvault_nicegui.discovery_learning import DiscoveryLearningService

    services = ApplicationServices(1)
    discovery = DiscoveryLearningService(tmp_path / 'discovery', object())
    services.discovery = discovery
    services.local_tag_view = SimpleNamespace(revision=lambda: 1)
    reads = []
    try:
        def instrument():
            db = discovery._database()
            read = db.get_discovery_preferences

            def measured():
                reads.append(1)
                return read()

            monkeypatch.setattr(db, 'get_discovery_preferences', measured)

        discovery._submit(instrument).result()
        with client:
            view = RankingView(services)
            view._rebuild_preferred()
            assert reads == [1]
            assert view._preferred == view._preferred_base == frozenset()
    finally:
        client.delete()
        services.local_tag_view = None
        services.close()
