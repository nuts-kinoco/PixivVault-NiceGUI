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


async def test_learning_reset_clears_the_count(tmp_path, monkeypatch):
    """元 discovery_tab.py:5669 §3-3-7: リセットで回数も0へ。"""
    from pixivvault_nicegui.ui.learning_reset import LearningResetView

    view, cleanup = await make_view(monkeypatch, learning_sandbox=tmp_path / 'learning')
    try:
        await judge(view, 'superLike', 'a')
        assert view.superlike_used_label.text == 'SuperLike使用: 1回'
        with view.client:
            reset = LearningResetView(view, view.storage)
        await reset.refresh()
        await reset.open()
        reset.confirm.set_value('1')
        await reset.execute()
        reset.close()
        await settled(view)
        assert view.superlike_used_label.visible is False and view._superlike_used == 0
    finally:
        await cleanup()


async def test_reset_during_slow_rerank_does_not_restore_old_count(tmp_path, monkeypatch):
    from pixivvault_nicegui.ui.learning_reset import LearningResetView
    from z_learning_support_20261118 import open_discovery_view, settled as discovery_settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=5)
    started, release = asyncio.Event(), asyncio.Event()
    original = service.rank_pending
    async def slow_rank(*args):
        started.set()
        await release.wait()
        return await original(*args)
    monkeypatch.setattr(service, 'rank_pending', slow_rank)
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        work_id = view.state.current_artwork.work_id
        assert view.judge('superLike', dict(work_id=work_id, generation=view.state.generation, operation_id='slow'))
        await asyncio.wait_for(started.wait(), 5)
        save = view._save_task
        with view.client:
            reset = LearningResetView(view, view.storage)
        await reset.refresh()
        await reset.open()
        reset.confirm.set_value('1')
        await reset.execute()
        reset.close()
        await discovery_settled(view)
        assert not view.state.can_undo and view.toast.kind is None
        release.set()
        await asyncio.wait_for(save, 5)
        await discovery_settled(view)
        assert view._superlike_used == 0
        assert not view.superlike_used_label.visible
        assert view.toast.kind is None and not view.state.can_undo
    finally:
        release.set()
        await cleanup()
