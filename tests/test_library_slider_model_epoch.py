"""L07: queued slider preview/release cannot undo a later accepted threshold."""
from types import SimpleNamespace

import pytest

from test_library_calendar_slider import bookmark_preset, event_handler, library


@pytest.mark.parametrize("replacement", (("number", 137), ("number", 1500), ("preset", 100)))
@pytest.mark.asyncio
async def test_old_slider_packets_cannot_replace_a_new_threshold_in_same_panel_epoch(
        library, replacement):
    view, _ = library
    panel = view.filter_panel
    slider = panel.bookmark_slider
    update = event_handler(slider, "update:model-value")
    release = event_handler(slider, "change", browser=True)
    old_epoch = panel._epoch
    old_packet = SimpleNamespace(args={
        "epoch": old_epoch,
        "revision": slider.props.get("data-bookmark-revision"),
        "value": 500,
    })
    kind, threshold = replacement
    if kind == "number":
        panel.change_setting("bookmark_min", str(threshold))
    else:
        bookmark_preset(panel, threshold)
    assert panel._epoch == old_epoch
    expected_thumb = min(1000, threshold)
    expected_generation = view.state.generation
    assert slider.value == expected_thumb
    assert view.filters.bookmark_min == panel.bookmark_min.value == threshold

    # Reproduce the old built-in model packet, as well as a packet already
    # carrying the prior revision. Neither may make the thumb contradict data.
    update(SimpleNamespace(args=500))
    assert slider.value == expected_thumb
    update(old_packet)
    release(old_packet)
    assert slider.value == expected_thumb
    assert view.filters.bookmark_min == panel.bookmark_min.value == threshold
    assert view.state.generation == expected_generation
    assert view.state.filters == view.bookmark_state.filters == view.filters


@pytest.mark.asyncio
async def test_stale_model_does_not_cancel_a_new_valid_preview_after_clear(library):
    view, _ = library
    panel = view.filter_panel
    slider = panel.bookmark_slider
    update = event_handler(slider, "update:model-value")
    release = event_handler(slider, "change", browser=True)
    old_packet = SimpleNamespace(args={
        "epoch": panel._epoch,
        "revision": slider.props.get("data-bookmark-revision"),
        "value": 500,
    })
    panel.clear()
    original, changes = panel.on_change, []

    def apply(filters):
        changes.append(filters)
        return original(filters)

    panel.on_change = apply
    current_packet = SimpleNamespace(args={
        "epoch": panel._epoch,
        "revision": slider.props.get("data-bookmark-revision"),
        "value": 600,
    })
    update(current_packet)
    assert slider.value == 600 and view.filters.bookmark_min == 0
    update(old_packet)
    update(SimpleNamespace(args=500))
    release(old_packet)
    assert slider.value == 600 and view.filters.bookmark_min == panel.bookmark_min.value == 0
    assert changes == []
    release(current_packet)
    assert len(changes) == 1
    assert view.filters.bookmark_min == panel.bookmark_min.value == slider.value == 600
    assert view.state.filters == view.bookmark_state.filters == view.filters
