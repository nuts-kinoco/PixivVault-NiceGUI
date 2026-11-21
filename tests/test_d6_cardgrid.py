"""Ranking refresh and card event contracts with a fake reader."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.ranking_view import RankingView
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
