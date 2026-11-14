"""Original review queue semantics, using actual NiceGUI and local learning DB."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


async def settled(view):
    async with asyncio.timeout(10):
        while view._storage_busy or not view._storage_ready or view.state.saving:
            await asyncio.sleep(.01)


@pytest.fixture
async def learning_view(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=4, learning_sandbox=tmp_path/'learning')
    client = Client(ui.page('/_original_review_queue'))
    before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(services)
    view.set_active(True)
    await settled(view)
    try:
        yield view, services
    finally:
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks)-before
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)


async def judge(view, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge('like', dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)
    return work_id


@pytest.mark.parametrize('target', [0, 1])
async def test_review_cancel_keeps_current_and_inserts_cancelled_after_it(learning_view, target):
    view, _ = learning_view
    judged = [await judge(view, 'first'), await judge(view, 'second')]
    current = view.state.current_artwork.work_id
    tail = [art.work_id for art in view.state.artworks[view.state.index+1:]]
    await view.open_review()
    row = next(row for row in view._review_rows if row['work_id'] == judged[target])
    await view._review_change(row)
    await settled(view)
    remaining = [art.work_id for art in view.state.artworks[view.state.index:]]
    assert remaining == [current, judged[target], *tail]
    assert view.state.current_artwork.work_id == current
    assert judged[target] not in {row['work_id'] for row in view._review_rows}
    _, operation = view._review_undone[judged[target]]
    await view._review_change(row, operation)
    await settled(view)
    assert [art.work_id for art in view.state.artworks[view.state.index:]] == [current, *tail]
    assert view.state.current_artwork.work_id == current
    restored = next(row for row in view._review_rows if row['work_id'] == judged[target])
    assert restored['verdict'] == row['verdict']
    assert restored['revision'] > row['revision']


async def test_review_cancel_from_exhausted_queue_can_be_judged_after_close(learning_view):
    view, _ = learning_view
    judged = [await judge(view, str(i)) for i in range(4)]
    assert view.state.current_artwork is None
    await view.open_review()
    row = next(row for row in view._review_rows if row['work_id'] == judged[1])
    await view._review_change(row)
    await settled(view)
    assert view.state.current_artwork.work_id == judged[1]
    view.review_dialog.close()
    view._close_review()
    assert await judge(view, 'judge-again') == judged[1]
    assert view.state.current_artwork is None


async def test_review_cancel_of_current_card_changed_by_another_client_keeps_it(learning_view):
    view, services = learning_view
    from pixivvault_nicegui.learning import Judgment
    current = view.state.current_artwork.work_id
    before = [a.work_id for a in view.state.artworks]
    other = await services.learning.open_session()
    await services.learning.record(other.session_id, Judgment(current, 'like', 'other-client'))
    await view.open_review()
    row = next(row for row in view._review_rows if row['work_id'] == current)
    await view._review_change(row)
    await settled(view)
    assert [a.work_id for a in view.state.artworks] == before
    assert view.state.current_artwork.work_id == current
    services.learning.release_session(other.session_id)
