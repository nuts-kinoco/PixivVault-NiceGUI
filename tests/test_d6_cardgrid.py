"""Ranking refresh and card event contracts with a fake reader."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import row
from test_ranking_ui import setup


@pytest.mark.asyncio
async def test_identical_ranking_refresh_reuses_owned_catalog_and_card():
    services, reader, pins = setup()
    client = Client(ui.page('/_d6_cardgrid_refresh'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            previous = view.gallery.cards['123']
            pin = view.pin
            url = pin.url('123', 0, 'thumb')
            reader.rows[0]['view_count'] += 7
            await view.load()
            assert view.gallery.cards['123'] is previous
            assert pins == [pin] and not pin.closed
            assert view.pin.url('123', 0, 'thumb') == url
            assert view.page.items[0].view_count == 17
            view.close()
            assert pin.closed
    finally:
        client.delete()
        services.close()

@pytest.mark.asyncio
async def test_gallery_grid_is_single_element_and_rejects_older_render_epoch():
    services, reader, _ = setup()
    client = Client(ui.page('/_d6_cardgrid_epoch'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            gallery = view.gallery
            assert not list(gallery.grid.descendants())
            assert gallery.grid.props['cards'][0]['work_id'] == '123'
            old_epoch = gallery.grid.props['render_epoch']
            await view.load()
            assert gallery.grid.render_epoch == old_epoch
            reader.rows = [row(124), row(123)]
            await view.load()
            assert gallery.grid.render_epoch > old_epoch
            opened = []
            gallery.open = opened.append
            await gallery.dispatch_card_event(dict(work_id='123', action='open', epoch=old_epoch))
            assert opened == []
            await gallery.dispatch_card_event(dict(
                work_id='123', action='open', epoch=gallery.grid.props['render_epoch']))
            assert opened == ['123']
            view.set_active(False)
            view.set_active(True)
            current = gallery.grid.props['render_epoch']
            await gallery.dispatch_card_event(dict(work_id='123', action='open', epoch=current - 1))
            assert opened == ['123']
            view.close()
    finally:
        client.delete()
        services.close()
