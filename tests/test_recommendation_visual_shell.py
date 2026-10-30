"""The actual shell must expose recommendation pictures and an unsaved-work viewer.

The service owns an actual reader backed only by SyntheticHTTP. No request is
sent to Pixiv, and these tests never execute a write intent.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from nicegui import Client, core, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationSnapshot,
    RecommendationWork,
)
from pixivvault_nicegui.ui.shell import DemoShell
from test_pixiv_actions_services import environment
from test_recommendation_visual_metadata import visual_row


def install_recommendations(services, *, unknown=False):
    rows = [visual_row('11'), visual_row('12')]
    if unknown:
        for row in rows:
            row.pop('illust_type')
            row.pop('page_count')
    works = tuple(RecommendationWork.from_record(row) for row in rows)
    snapshot = RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('superlike', 'Synthetic shelf', works),)),),
        unavailable=('certain', 'boosted', 'trend', 'trend_added'))
    old = services.recommendations
    services.recommendations = RecommendationController(
        SimpleNamespace(load=lambda: snapshot))
    if old is not None:
        old.close()
    return works


@pytest_asyncio.fixture
async def product_client(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    # Component checks model a running, connected client; the separate Edge
    # acceptance supplies a real server, socket, decoding and browser input.
    from nicegui.app.app import State
    monkeypatch.setattr(core.app, '_state', State.STARTED)
    client = Client(ui.page('/_recommendation_visual_product'))
    async def connected(*_args, **_kwargs):
        return client
    monkeypatch.setattr(client, 'connected', connected)
    try:
        yield client
    finally:
        client.delete()


async def product_shell(environment, product_client, *, unknown=False):
    services = environment.build(with_following=False)
    install_recommendations(services, unknown=unknown)
    with product_client, ui.column():
        shell = DemoShell(services)
        shell.show('recommend')
    await shell.recommend_view.refresh()
    assert shell.active_tab == 'recommend' and shell.recommend_view.data is not None
    return services, shell, shell.recommend_view


@pytest.mark.asyncio
async def test_shell_mount_renders_only_visible_preview_urls_without_any_metadata_get(environment, product_client):
    services, shell, view = await product_shell(environment, product_client)
    try:
        previews = [node for node in view.body.descendants() if isinstance(node, ui.image)]
        assert len(previews) == 2, 'recommended works must have actual image elements'
        sources = [node.source for node in previews]
        assert all(source.startswith('/media/') and '/thumb.png?catalog=' in source
                   for source in sources)
        assert environment.http.reads == environment.http.posts == []
        assert view._recommend_viewer._viewer_state.selected_artwork is None
        view.apply_card_size('small')
        grids = [node for node in view.body.descendants() if 'pv-grid' in node.classes]
        assert grids and all(node.style['--pv-max-cols'] == '12' for node in grids)
        assert len([node for node in view.body.descendants() if isinstance(node, ui.image)]) == 2
        assert environment.http.reads == environment.http.posts == []
    finally:
        shell.close()


@pytest.mark.asyncio
async def test_unsaved_recommendation_opens_common_page_viewer_and_actions_through_product_shell(environment, product_client):
    services, shell, view = await product_shell(environment, product_client)
    try:
        # No library entry exists for either recommendation.
        assert not getattr(services.library, 'works', ())
        targets = [node for node in view.body.descendants()
                   if node.props.get('data-testid') == 'recommend-open-superlike-11']
        assert len(targets) == 1, 'the recommendation thumbnail must have an explicit open target'
        assert targets[0].props.get('role') == 'button'
        assert any(listener.type == 'click' for listener in targets[0]._event_listeners.values())
        await view.open_work('11', generation=view._render_generation)
        viewer = view._recommend_viewer
        selected = viewer._viewer_state.selected_artwork
        assert selected is not None and selected.work_id == '11'
        assert selected.page_count == 3 and selected.author_id == '91'
        assert viewer._quality == 'view'
        assert '/media/11/0/view.png?catalog=' in viewer._image_url
        viewer.move_page(1)
        assert viewer._viewer_state.selected_page == 1
        assert '/media/11/1/view.png?catalog=' in viewer._image_url
        assert viewer.viewer.bookmark_button.enabled
        assert viewer.viewer.follow_button.enabled
        assert viewer.viewer.bookmark_button.props.get('data-action-token')
        assert environment.http.reads == environment.http.posts == []
        # The shell's real tab lifecycle closes the viewer and its action target.
        shell.show('ranking')
        assert not viewer._viewer_state.viewer_open
        assert not viewer.viewer.bookmark_button.props.get('data-action-token')
    finally:
        shell.close()


@pytest.mark.asyncio
async def test_old_recommendation_render_event_cannot_open_a_new_shelf(environment, product_client):
    _, shell, view = await product_shell(environment, product_client)
    try:
        old_generation = view._render_generation
        view.render()
        await view.open_work('11', generation=old_generation)
        assert view._recommend_viewer._viewer_state.selected_artwork is None
        assert environment.http.reads == environment.http.posts == []
    finally:
        shell.close()
