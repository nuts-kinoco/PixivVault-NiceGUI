import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

from z_learning_support_20261118 import open_discovery_view, settled


async def test_fast_judgment_advances_without_publishing_saving_status(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        pending = Future()
        monkeypatch.setattr(service, 'submit_record', lambda *_: pending)
        seen = []
        set_text = view.status.set_text

        def status(value):
            seen.append(value)
            set_text(value)

        monkeypatch.setattr(view.status, 'set_text', status)
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            payload = dict(work_id=view.state.current_artwork.work_id,
                           generation=view.state.generation, operation_id='save')
            assert view.judge('like', payload)
            assert view.state.saving and view.state.index == 0
            assert not view.judge('skip', {**payload, 'operation_id': 'repeat'})
            pending.set_result(None)
        await settled(view)
        assert view.state.index == 1
        assert '判定を保存しています…' not in seen
    finally:
        await cleanup()


async def test_slow_judgment_publishes_busy_state_before_completion(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        pending = Future()
        monkeypatch.setattr(service, 'submit_record', lambda *_: pending)
        with view.client:
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge('like', dict(work_id=view.state.current_artwork.work_id,
                                          generation=view.state.generation, operation_id='slow-save'))
        async with asyncio.timeout(2):
            while view.status.text != '判定を保存しています…':
                await asyncio.sleep(.01)
        assert view.state.index == 0 and not view.buttons['like'].enabled
        pending.set_exception(OSError('fixture write failed'))
        await settled(view)
        assert view.state.index == 0 and view.save_retry_button.visible
    finally:
        await cleanup()


async def test_toast_reuses_undo_and_leaves_meter_updates_to_browser(tmp_path, monkeypatch):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        toast = view.toast
        with view.client:
            toast.show_undo('', lambda event: None, 'data-generation=1')
            first = toast.undo_button
            start = toast.meter.value
            view.clock.now += 1
            toast.tick()
            assert toast.meter.value == start
            toast.hide()
            toast.show_undo('', lambda event: None, 'data-generation=2')
        assert toast.undo_button is first
        assert first.props['data-generation'] == '2'
    finally:
        await cleanup()


async def test_toast_expiry_rearms_when_the_loop_wakes_early(tmp_path, monkeypatch):
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    try:
        toast = view.toast
        with view.client:
            toast.show_undo('', lambda event: None, '')
        toast._expiry.cancel()
        view.clock.now = toast._deadline - 0.001
        toast._expire()
        assert toast.band.visible and toast._expiry is not None
        view.clock.now += 1
        toast.tick()
        assert not toast.band.visible
    finally:
        await cleanup()
