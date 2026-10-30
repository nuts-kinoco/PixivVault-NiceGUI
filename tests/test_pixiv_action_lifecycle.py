"""Retired recommendation cards must not accumulate in client lifecycle hooks."""
import asyncio
import gc
from types import SimpleNamespace
import weakref

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationSnapshot, RecommendationWork,
)
from pixivvault_nicegui.ui.pixiv_action_buttons import _HUBS
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_following_sync_ui import runtime
from test_pixiv_actions import stack


@pytest.mark.asyncio
@pytest.mark.parametrize('write_capability', [False, True])
async def test_rerender_and_client_delete_release_retired_action_bindings(
        stack, monkeypatch, write_capability):
    runtime(monkeypatch)
    snapshot = RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('superlike', 'Generated', (
            RecommendationWork('123', 'Synthetic', '91', 'Author'),)),)),),
        unavailable=(),
    )
    controller = RecommendationController(SimpleNamespace(load=lambda: snapshot))
    services = SimpleNamespace(recommendations=controller, closing=False,
        pixiv_actions=stack.actions if write_capability else None)
    client = Client(ui.page('/_action_lifecycle_' + str(write_capability)))
    retired = []
    active_ref = None
    try:
        with client:
            view = RecommendView(services)
        view.active = True
        await view.refresh()
        handlers = len(client.delete_handlers)
        for _ in range(8):
            retired.append(weakref.ref(view._action_bindings[0]))
            view.render()
            assert len(view._action_bindings) == 1
            assert len(client.delete_handlers) == handlers, 'one cleanup owner per client, not per retired card'
        gc.collect()
        assert all(reference() is None for reference in retired)
        hub = view._action_bindings[0].hub
        if not write_capability:
            assert hub is not None and hub.timer is None
        active_ref = weakref.ref(view._action_bindings[0])
        view.close()
        assert hub is not None and not hub.bindings
        assert stack.http.reads == stack.http.posts == []
    finally:
        client.delete()
        controller.close()
    assert client not in _HUBS
    assert not stack.actions._closing and not stack.reader._closed
    del view, hub, client, controller
    # Permit cancelled NiceGUI timer/outbox callbacks to release their frames.
    for _ in range(20):
        await asyncio.sleep(.05)
        gc.collect()
        if active_ref() is None:
            break
    assert active_ref() is None
