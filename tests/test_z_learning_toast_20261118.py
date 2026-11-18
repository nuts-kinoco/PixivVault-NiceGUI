"""FL-I06: 判定直後の「判定しました／取り消す」帯（元 discovery_tab.py:1528-1700, 1674-1682）。

元の秒数は取り消し帯 4.0 秒・理由帯 6.0 秒（_TOAST_SECONDS_UNDO / _TOAST_SECONDS_REASON）。
"""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_toast import LearningToast, UNDO_SECONDS, REASON_SECONDS
from pixivvault_nicegui.ui.learning_view import LearningView


async def settled(view):
    async with asyncio.timeout(10):
        while view._storage_busy or not view._storage_ready or view.state.saving:
            await asyncio.sleep(.01)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
async def learning_view(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=6, learning_sandbox=tmp_path / 'learning')
    client = Client(ui.page('/_z_learning_toast'))
    before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(services)
    view.clock = Clock()
    view.toast._clock = view.clock
    view.set_active(True)
    await settled(view)
    try:
        yield view, services
    finally:
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - before
        for task in owned:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(services.close)


def texts(toast):
    return [str(getattr(e, 'text', '')) for e in toast.band.descendants() if getattr(e, 'text', None)]


def buttons(toast):
    return [e for e in toast.band.descendants() if isinstance(e, ui.button)]


async def judge(view, choice, operation):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    work_id = view.state.current_artwork.work_id
    assert view.judge(choice, dict(work_id=work_id, generation=view.state.generation, operation_id=operation))
    await settled(view)
    return work_id


def click(button, args=None):
    for listener in button._event_listeners.values():
        if listener.type == 'click':
            return listener.handler(SimpleNamespace(args=args or {}))
    raise AssertionError('no click handler')


def undo_payload(view, button):
    return dict(work_id=button.props['data-work-id'], generation=int(button.props['data-generation']),
                operation_id='undo-' + button.props['data-generation'])


def test_toast_seconds_follow_acceptance():
    assert UNDO_SECONDS == 4.0 and REASON_SECONDS == 6.0


async def test_band_hidden_before_any_judgment(learning_view):
    view, _ = learning_view
    assert view.toast.band.visible is False


async def test_like_shows_judged_band_with_undo_chip(learning_view):
    view, _ = learning_view
    await judge(view, 'like', 'op-1')
    assert view.toast.band.visible is True
    assert '判定しました' in texts(view.toast)
    undo = [b for b in buttons(view.toast) if b.props.get('data-testid') == 'learning-toast-undo']
    assert len(undo) == 1 and undo[0].text == '取り消す'


async def test_band_disappears_after_five_seconds_not_before(learning_view):
    view, _ = learning_view
    await judge(view, 'like', 'op-1')
    view.clock.now += UNDO_SECONDS - 0.5
    view.toast.tick()
    assert view.toast.band.visible is True
    view.clock.now += 0.6
    view.toast.tick()
    assert view.toast.band.visible is False


async def test_undo_chip_undoes_last_judgment_and_hides_band(learning_view):
    view, _ = learning_view
    first = await judge(view, 'like', 'op-1')
    button = view.toast.undo_button
    assert button.props['data-work-id'] == view.state.current_artwork.work_id
    assert view.state.can_undo
    click(button, undo_payload(view, button))
    await settled(view)
    assert view.state.current_artwork.work_id == first      # 直前の判定が戻った
    assert view.toast.band.visible is False


async def test_undo_chip_props_follow_current_card_after_next_judgment(learning_view):
    view, _ = learning_view
    await judge(view, 'skip', 'op-1')
    await judge(view, 'like', 'op-2')
    button = view.toast.undo_button
    assert button.props['data-work-id'] == view.state.current_artwork.work_id
    assert int(button.props['data-generation']) == view.state.generation


async def test_stale_undo_payload_is_rejected(learning_view):
    view, _ = learning_view
    await judge(view, 'like', 'op-1')
    button = view.toast.undo_button
    payload = undo_payload(view, button)
    payload['generation'] -= 1
    before = view.state.current_artwork.work_id
    click(button, payload)
    await settled(view)
    assert view.state.current_artwork.work_id == before


async def test_band_slot_is_inside_picture_wrapper_not_between_buttons(learning_view):
    view, _ = learning_view
    # 帯は画像の重ね置き（絶対配置）。判定ボタンの行は帯の有無で動かない。
    assert view.toast.band._style.get('position') == 'absolute'


# ---- LearningToast 単体（優先順位）-----------------------------------------
def make_toast():
    client = Client(ui.page('/_z_toast_unit'))
    clock = Clock()
    with client:
        with ui.column():
            toast = LearningToast(clock=clock)
    return client, toast, clock


def test_heavy_notice_is_not_replaced_by_light_and_survives_hide_and_timer():
    client, toast, clock = make_toast()
    try:
        assert toast.show_gave_up('判定を保存できませんでした', '再試行できます', heavy=True, key='save')
        assert toast.show_undo('', lambda e: None, '') is False
        assert toast.show_gave_up('軽い', '軽い', heavy=False) is False
        assert toast.hide() is False
        clock.now += 3600
        toast.tick()
        assert toast.band.visible is True
        assert toast.hide(force=True) is True
        assert toast.band.visible is False
    finally:
        client.delete()


def test_reason_band_blocks_undo_band_until_replaced_or_hidden():
    client, toast, clock = make_toast()
    try:
        assert toast.show_reason(lambda reason: None)
        assert toast.reason_active
        assert toast.show_undo('', lambda e: None, '') is False
        toast.hide()
        assert toast.reason_active is False
        assert toast.show_undo('', lambda e: None, '') is True
    finally:
        client.delete()


async def test_last_candidate_undo_buttons_target_empty_current_card(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view, settled as discovery_settled
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=1)
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        work_id = view.state.current_artwork.work_id
        assert view.judge('like', dict(work_id=work_id, generation=view.state.generation, operation_id='last'))
        await discovery_settled(view)
        if view._save_task:
            await view._save_task
        assert view.state.current_artwork is None and view.state.can_undo
        assert view.undo_button.props.get('data-work-id') == ''
        assert view.toast.undo_button.props.get('data-work-id') == ''
        click(view.toast.undo_button, dict(work_id='', generation=view.state.generation, operation_id='undo-last'))
        await discovery_settled(view)
        assert view.state.current_artwork.work_id == work_id
    finally:
        await cleanup()
