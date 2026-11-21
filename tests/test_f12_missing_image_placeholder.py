"""Missing-file presentation is wired for both library thumbnail surfaces.

The actual image failures and viewer fallback are also exercised in GUI evidence.
"""
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


@pytest.mark.asyncio
@pytest.mark.parametrize('surface', ['authors', 'pages'])
async def test_library_thumbnail_errors_have_accessible_image_outline(surface):
    services = ApplicationServices(120)
    client = Client(ui.page('/_f12_placeholder_' + surface))
    try:
        with client:
            view = LibraryView(services)
            if surface == 'pages':
                view.open_author('demo-author-01')
            grid = view.author_grid if surface == 'authors' else view.page_grid
            assert grid.props['library_placeholder'].startswith('M21 ')
            assert grid.props['layout'] == ('library-author' if surface == 'authors' else 'library-page')
            assert grid.props['cards'] and all(card['src'] for card in grid.props['cards'])
    finally:
        client.delete()
        services.close()
