"""A retired recommendation request cannot clear a newer request's guard.

Uses the real read client and the existing all-synthetic HTTP lifetime fixture.
Two independent barriers establish the interleaving; no external I/O is used.
"""
from __future__ import annotations

import asyncio
from threading import Event

import pytest

from test_recommendation_viewer_lifetime import (
    item, mount, product_client, remote_application,
)


@pytest.mark.asyncio
async def test_retired_same_work_get_cannot_clear_new_pending_request_guard(
        remote_application, product_client):
    env = remote_application
    shell, view = await mount(env, product_client, (item('11', unknown=True), item('12')))
    first_started, first_release = Event(), Event()
    second_started, second_release = Event(), Event()
    tasks = []
    try:
        env.http.block_detail['11'] = (first_started, first_release)
        first = asyncio.create_task(view.open_work('11', generation=view._render_generation))
        tasks.append(first)
        assert await asyncio.to_thread(first_started.wait, 2)
        await view.open_work('12', generation=view._render_generation)
        assert view._recommend_viewer._viewer_state.selected_artwork.work_id == '12'

        # The first transport already owns its local release event. Replacing
        # this entry gives the second GET an independent, still-closed barrier.
        env.http.block_detail['11'] = (second_started, second_release)
        second = asyncio.create_task(view.open_work('11', generation=view._render_generation))
        tasks.append(second)
        assert await asyncio.to_thread(second_started.wait, 2)
        pending = view._opening
        request_generation = view._open_generation
        assert pending is not None and not second.done()

        first_release.set()
        await asyncio.wait_for(first, 3)
        assert not second.done() and view._open_generation == request_generation
        # A third attempt is immediate HTTP if dispatched, so the RED observes
        # an actual redundant detail GET without depending on a timing guess.
        # The second transport retains second_release in its already-running call.
        env.http.block_detail.pop('11')
        await asyncio.wait_for(
            view.open_work('11', generation=view._render_generation), 3)
        detail_count = sum(path == '/ajax/illust/11' for path, _ in env.http.reads)
        assert detail_count == 2, 'old completion removed the new pending GET guard'
        assert view._opening == pending and view._open_generation == request_generation
        assert not second.done()

        second_release.set()
        await asyncio.wait_for(second, 3)
        assert view._opening is None
        assert view._recommend_viewer._viewer_state.selected_artwork.work_id == '11'
        assert sum(path == '/ajax/illust/11' for path, _ in env.http.reads) == 2
    finally:
        first_release.set()
        second_release.set()
        for task in tasks:
            await asyncio.wait_for(task, 3)
        shell.close()
    assert len(env.services.images._versions) == 1
    assert not env.reader._closed and env.http.posts == []

