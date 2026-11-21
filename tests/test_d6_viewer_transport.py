import asyncio
import time
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from test_library_view import event_for, library  # noqa: F401
from pixivvault_nicegui.ui.artwork_viewer import ArtworkViewer


@pytest.mark.asyncio
async def test_initial_wiring_uses_initial_elements(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    client = Client(ui.page('/_d6_viewer_initial'))
    try:
        with client:
            viewer = ArtworkViewer(*([lambda: None] * 5))
            viewer.root.on('keydown', lambda _: None)
        assert viewer.root.id in client.outbox.updates
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not client.outbox.messages
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_disconnected_viewer_changes_share_the_replayable_update_stream(library, monkeypatch):
    view, _, client = library
    # A response already exists but there is no active socket or NiceGUI core
    # loop. The asyncio loop is enough to submit work to the client's outbox.
    monkeypatch.setattr(core, 'loop', None)
    client._response_built = True
    view.open_author('demo-author-01')
    client.outbox.updates.clear()
    view.open_page('demo-0060', 0)
    view._image_loaded(event_for(view))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    messages = list(client.outbox.messages)
    opening = next((i for i, (_, kind, patch) in enumerate(messages)
                    if kind == 'update' and str(view.viewer.root.id) in patch), None)
    zoom = next((i for i, (_, kind, payload) in enumerate(messages)
                 if kind == 'run_javascript' and '__pvZoomController' in payload['code']), None)
    assert opening is not None and zoom is not None and opening < zoom

    client.outbox.messages.clear()
    client.outbox.updates.clear()
    view.move_work(1)
    view.viewer.set_detail_values('new description', ('new-tag', '<literal>'))
    await asyncio.sleep(0)
    assert not client.outbox.updates
    [(_, kind, patch)] = client.outbox.messages
    assert kind == 'update'
    viewer = view.viewer
    assert {str(control.id) for control in (viewer.root, viewer.title, viewer.counter,
                                           viewer.image, viewer.tags)} <= patch.keys()
    assert patch[str(viewer.tags.id)]['props']['tags'] == ['new-tag', '<literal>']
    assert patch[str(viewer.root.id)]['props']['data-quality-work'] == view._viewer_state.selected_artwork.work_id
    assert patch[str(viewer.image.id)]['props']['src'] == view._image_url

    view._image_loaded(event_for(view))
    await asyncio.sleep(0)
    assert not client.outbox.updates
    assert client.outbox.messages[-1][2][str(viewer.image.id)]['props']['aria-busy'] == 'false'
    assert patch[str(viewer.image.id)]['props']['aria-busy'] == 'true'


@pytest.mark.asyncio
async def test_delete_cancels_pending_viewer_update_and_dom_actions(library, monkeypatch):
    view, _, client = library
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    client._response_built = True
    page = view.viewer.page
    view.viewer.counter.set_text('2/3')
    page.after_state('console.log("viewer action");')
    view.close()
    page.delete()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not client.outbox.messages


@pytest.mark.asyncio
async def test_deleting_a_viewer_retires_published_ids_after_rewind(library):
    view, _, client = library
    client._response_built = True
    page = view.viewer.page
    counter = str(view.viewer.counter.id)
    view.viewer.counter.set_text('2/3')
    await asyncio.sleep(0)
    transmitted = client.outbox.messages.popleft()
    client.outbox.message_history.append((0, time.monotonic(), transmitted))
    client.outbox.next_message_id = 1
    view.close()
    page.delete()
    client.outbox.try_rewind(0)
    assert [message[2][counter] is None for message in client.outbox.messages] == [False, True]


@pytest.mark.asyncio
async def test_stale_hide_event_does_not_write_a_new_work(library, monkeypatch):
    view, _, _ = library
    from pixivvault_nicegui import hidden_works

    hidden = []
    monkeypatch.setattr(hidden_works, 'set_hidden', lambda _, work_id, value: hidden.append((work_id, value)))
    view.open_author('demo-author-01')
    view.open_page('demo-0060', 0)
    prior = str(view._request_serial)
    view.move_work(1)
    assert prior != str(view._request_serial)
    await view._viewer_hide(SimpleNamespace(args=prior))
    assert hidden == []
    await view._viewer_hide(SimpleNamespace(args=str(view._request_serial)))
    assert hidden == [(view._viewer_state.selected_artwork.work_id, True)]


@pytest.mark.asyncio
async def test_viewer_page_and_decode_only_update_the_isolated_component(library):
    view, _, client = library
    client._response_built = True
    view.open_author('demo-author-01')
    view.open_page('demo-0060', 0)
    view._image_loaded(event_for(view))
    await asyncio.sleep(0)
    client.outbox.updates.clear()
    client.outbox.messages.clear()

    view.move_page(1)
    await asyncio.sleep(0)
    assert view.viewer_counter.text == '2/3'
    assert view.viewer_image.props['aria-busy'] == 'true'
    assert not client.outbox.updates
    [(_, kind, patch)] = client.outbox.messages
    assert kind == 'update'
    assert patch[str(view.viewer_counter.id)]['text'] == '2/3'
    assert patch[str(view.viewer_image.id)]['props']['aria-busy'] == 'true'

    view._image_loaded(event_for(view))
    await asyncio.sleep(0)
    assert view.viewer_image.props['aria-busy'] == 'false'
    assert not client.outbox.updates
    assert len(client.outbox.messages) == 2
    _, kind, patch = client.outbox.messages[-1]
    assert kind == 'update'
    assert patch[str(view.viewer_image.id)]['props']['aria-busy'] == 'false'
