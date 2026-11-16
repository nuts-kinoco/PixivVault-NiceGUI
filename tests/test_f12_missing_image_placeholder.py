"""Missing-file presentation is wired for both library thumbnail surfaces.

The actual image failures and viewer fallback are also exercised in GUI evidence.
"""
import pytest
from nicegui import Client, ui
from nicegui.elements.image import Image
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
            cards = view.author_cards.values() if surface == 'authors' else view.page_cards.values()
            checked = 0
            for card in cards:
                for image in card.descendants():
                    if not isinstance(image, Image):
                        continue
                    slot = image.slots.get('error')
                    assert slot is not None, 'failed thumbnail must not become a blank card'
                    assert len(slot.children) == 1
                    icon = slot.children[0]
                    assert icon.props['role'] == 'img'
                    assert icon.props['aria-label'] == '画像未表示'
                    assert icon.props['name'].startswith('M21 ')
                    checked += 1
            assert checked > 0
    finally:
        client.delete()
        services.close()
