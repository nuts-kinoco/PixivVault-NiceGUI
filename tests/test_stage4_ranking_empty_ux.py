"""Unfetched, empty-response and failure must not show the same UX message."""
from nicegui import Client, ui
import pytest
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup


@pytest.mark.asyncio
async def test_first_ranking_visit_distinguishes_not_loaded_from_zero_results():
    services, reader, _pins = setup()
    client = Client(ui.page('/_stage4_ranking_empty'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            assert reader.calls == []
            assert view.page is None and view.gallery.empty.visible
            assert '未取得' in view.gallery.empty.text
            assert '該当する作品がありません' not in view.gallery.empty.text
            reader.rows = []
            await view.load()
            assert view.page is not None
            assert '該当する作品がありません' in view.gallery.empty.text
            assert '未取得' not in view.gallery.empty.text
            view.close()
    finally:
        client.delete()
        services.close()
