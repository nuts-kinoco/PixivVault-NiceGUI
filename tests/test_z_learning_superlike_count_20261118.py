"""FL-Y08: 「SuperLike使用: N回」を、1回以上使ったときだけ出す（元 discovery_tab.py:136-142, 1874-1877, 1996-1998, 2672）。"""
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


async def make_view(monkeypatch, **kwargs):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=6, **kwargs)
    client = Client(ui.page('/_z_superlike'))
    before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(services)
    view.set_active(True)
    await settled(view)

    async def cleanup():
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - before
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)
    return view, cleanup


@pytest.fixture(params=['stored', 'memory'])
async def view(request, tmp_path, monkeypatch):
    kwargs = dict(learning_sandbox=tmp_path / 'learning') if request.param == 'stored' else {}
    view, cleanup = await make_view(monkeypatch, **kwargs)
    try:
        yield view
    finally:
        await cleanup()


async def judge(view, choice, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge(choice, dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)


def undo(view, operation):
    view._on_undo(SimpleNamespace(args=dict(work_id=view.state.current_artwork.work_id if view.state.current_artwork else '',
                                            generation=view.state.generation, operation_id=operation)))


async def test_hidden_until_first_superlike(view):
    label = view.superlike_used_label
    assert label.visible is False and label.text == ''
    await judge(view, 'like', 'a')
    await judge(view, 'skip', 'b')
    await judge(view, 'hate', 'c')
    assert label.visible is False and label.text == ''     # SuperLike 以外では出ない（0回のときは何も出さない）


async def test_shows_count_after_each_superlike(view):
    label = view.superlike_used_label
    await judge(view, 'superLike', 'a')
    assert label.visible and label.text == 'SuperLike使用: 1回'
    await judge(view, 'like', 'b')
    await judge(view, 'superLike', 'c')
    assert label.text == 'SuperLike使用: 2回'


async def test_undoing_a_superlike_decrements_and_hides_at_zero(view):
    label = view.superlike_used_label
    await judge(view, 'superLike', 'a')
    undo(view, 'u1')
    await settled(view)
    assert label.visible is False and label.text == ''


async def test_undoing_a_non_superlike_keeps_the_count(view):
    label = view.superlike_used_label
    await judge(view, 'superLike', 'a')
    await judge(view, 'like', 'b')
    undo(view, 'u1')
    await settled(view)
    assert label.text == 'SuperLike使用: 1回'


async def test_label_sits_in_fixed_height_row_so_buttons_do_not_move(view):
    row = view.superlike_used_label.parent_slot.parent
    assert 'height:20px' in row._style.get('height', '') or row._style.get('height') == '20px'
