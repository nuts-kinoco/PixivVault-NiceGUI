"""学習画面のイベント契約と世代整合性（実ブラウザ試験は統合側）。"""

import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


@pytest.fixture
async def view(monkeypatch):
    # Exercise the real NiceGUI client/slot and timer lifecycle on the test loop.
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(count=2)
    client = Client(ui.page('/_learning_view_contract'))
    before = set(background_tasks.running_tasks)
    with client, ui.card():
        result = LearningView(services)
    owned = set(background_tasks.running_tasks) - before
    try:
        result.set_active(True)
        yield result
    finally:
        result.close()
        client.delete()
        for task in owned:
            if not task.done(): task.cancel()
        if owned: await asyncio.gather(*owned, return_exceptions=True)
        services.close()


def payload(view, operation_id="judgment-1"):
    return {"work_id": view.state.current_artwork.work_id,
            "generation": view.state.generation, "operation_id": operation_id}


async def test_judgment_and_undo_colors_are_controlled_by_existing_theme(view):
    assert all(button.props.get('color') is None for button in view.buttons.values())
    assert view.undo_button.props.get('color') is None


async def test_image_load_is_required_and_stale_events_cannot_enable_buttons(view):
    first_token, first_url = view._image_token, view._image_url
    assert not view.state.ready
    assert not view.judge("like", payload(view))
    view.request_image()
    assert first_token != view._image_token
    view._on_loaded(SimpleNamespace(args=first_url))
    assert not view.state.ready
    view._on_loaded(SimpleNamespace(args=view._image_url + "-wrong"))
    assert not view.state.ready
    view._on_failed(SimpleNamespace(args=view._image_url))
    assert not view.state.ready
    view.request_image()
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert view.state.ready


async def test_browser_absolute_load_source_and_stale_generation(view):
    old_url = view._image_url
    view.request_image()
    old_absolute = "http://127.0.0.1:8089" + old_url
    current_absolute = "http://127.0.0.1:8089" + view._image_url
    view._on_loaded(SimpleNamespace(args=old_absolute))
    assert not view.state.ready
    view._on_loaded(SimpleNamespace(args=current_absolute))
    assert view.state.ready


async def test_image_event_after_close_cannot_reactivate(view):
    captured_url = view._image_url
    view.close()
    view._on_loaded(SimpleNamespace(args=captured_url))
    assert view.state.closed and not view.state.ready


@pytest.mark.parametrize("choice", ["skip", "hate", "like", "superLike"])
async def test_capture_at_click_prevents_late_judgment_on_next_item(view, choice):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    old = payload(view)
    assert view.judge(choice, old)
    assert view.state.index == 1 and view.ledger.entries[0].choice == choice
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert not view.judge("hate", old)
    assert view.state.index == 1 and len(view.ledger.entries) == 1
    assert view.judge("like", payload(view, "judgment-2"))
    assert view.state.current_artwork is None


async def test_undo_is_generation_scoped_and_reloads(view):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    stale = payload(view)
    assert view.judge("like", stale)
    current = payload(view, "undo-1")
    view._on_undo(SimpleNamespace(args=stale))
    assert view.state.index == 1
    view._on_undo(SimpleNamespace(args=current))
    assert view.state.index == 0
    assert not view.state.ready
    assert not view.ledger.entries


async def test_inactive_and_closed_view_reject_server_events(view):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    captured = payload(view)
    view.set_active(False)
    assert not view.judge("like", captured)
    assert view.state.index == 0
    view.set_active(True)
    assert view.judge("like", captured)
    view.close()
    assert not view.judge("like", captured)


async def test_failed_save_does_not_advance_or_change_image(view):
    view._on_loaded(SimpleNamespace(args=view._image_url))
    captured = payload(view)
    original_url = view._image_url
    def failed_record(_):
        raise OSError("demo storage unavailable")
    original_record = view.ledger.record
    view.ledger.record = failed_record
    assert not view.judge("like", captured)
    assert view.state.index == 0 and view.state.ready
    assert view._image_url == original_url
    assert not view.ledger.entries
    view.ledger.record = original_record
    assert view.judge("like", captured)
    assert view.state.index == 1


def test_browser_handlers_send_captured_identity_and_filter_keys():
    js = LearningView._click_js()
    assert "getAttribute('data-work-id')" in js
    assert "getAttribute('data-generation')" in js
    assert "crypto.randomUUID()" in js
    keys = LearningView._key_js()
    for token in ("event.repeat", "event.isComposing", "isContentEditable",
                  "role=dialog", "arrowleft", "arrowright", "arrowup",
                  "arrowdown", "'1'", "'4'", "data-active"):
        assert token in keys
