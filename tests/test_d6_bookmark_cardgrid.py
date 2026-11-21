"""Selections travel through the single bookmark grid listener."""
from nicegui import Client, ui

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.bookmark_view import BookmarkView


async def test_bookmark_selection_stays_separate_from_open_and_rejects_stale_packet():
    state = BookmarkState(cached_remote=(Artwork('demo-1', 'First', 'Author'),))
    client = Client(ui.page('/_d6_bookmark_grid'))
    opened = []
    try:
        with client:
            view = BookmarkView(state, on_open=opened.append)
            view.render()
            assert not list(view.grid.descendants())
            epoch = view.grid.render_epoch
            packet = dict(work_id='demo-1', action='select', epoch=epoch, value=True)
            await view._dispatch_grid_event(packet)
            assert state.selected_ids == {'demo-1'} and not opened
            assert view.grid.props['cards'][0]['checked']
            view.set_active(False)
            view.set_active(True)
            await view._dispatch_grid_event(dict(packet, value=False))
            assert state.selected_ids == {'demo-1'}
            await view._dispatch_grid_event(dict(packet, action='open', epoch=view.grid.render_epoch))
            assert opened == ['demo-1']
    finally:
        client.delete()
