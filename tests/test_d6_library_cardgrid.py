"""Library author grid payload, selection and stale event contracts."""
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


async def test_library_author_grid_reuses_one_element_and_scopes_open_events():
    services = ApplicationServices(120)
    client = Client(ui.page('/_d6_library_cardgrid'))
    try:
        with client:
            view = LibraryView(services)
            assert not list(view.author_grid.descendants())
            first = 'demo-author-01'
            epoch = view.author_grid.render_epoch
            assert first in {item['work_id'] for item in view.author_grid.props['cards']}
            view.search('0003')
            await view._author_grid_event(dict(work_id=first, action='open', epoch=epoch))
            assert view.state.selected_author_id is None
            await view._author_grid_event(dict(
                work_id=first, action='open', epoch=view.author_grid.render_epoch))
            assert view.state.selected_author_id == first
            assert not list(view.page_grid.descendants())
            assert len(view.page_grid.props['cards']) == 50
    finally:
        client.delete()
        services.close()
