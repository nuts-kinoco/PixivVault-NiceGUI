"""L04/L07 missing controls and stale-event boundaries; no browser/API execution."""
from datetime import date
from types import SimpleNamespace

from nicegui import Client, helpers, ui
import pytest
import pytest_asyncio

from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui import library_filter_panel as panel_module
from pixivvault_nicegui.ui.library_view import LibraryView


@pytest_asyncio.fixture
async def library():
    services = ApplicationServices(120)
    client = Client(ui.page("/_library_calendar_slider"))
    with client:
        view = LibraryView(services)
        view.filter_panel.toggle()
    try:
        yield view, client
    finally:
        client.delete()
        services.close()


@pytest.fixture
def leap_day(monkeypatch):
    class LeapDay(date):
        @classmethod
        def today(cls):
            return cls(2024, 2, 29)
    monkeypatch.setattr(panel_module, "date", LeapDay)


def event_handler(element, kind, *, browser=False):
    values = [item.handler for item in element._event_listeners.values()
              if item.type == helpers.event_type_to_camel_case(kind) and
              (not browser or item.js_handler)]
    assert len(values) == 1
    return values[0]


def click(panel, element):
    event_handler(element, "click", browser=True)(
        SimpleNamespace(args={"epoch": panel._epoch}))


def open_calendar(panel, field):
    click(panel, getattr(panel, field + "_button"))
    assert panel.calendar_dialog.value is True
    token = panel._calendar_token
    assert token[0] == field and token[1] == panel._epoch
    return (token, event_handler(panel.calendar_picker, "update:model-value", browser=True),
            panel.calendar_dialog)


def selected(token, value):
    field, epoch, nonce = token
    return SimpleNamespace(args={"field": field, "epoch": epoch, "nonce": nonce, "value": value})


def date_preset(panel, value):
    click(panel, next(button for button, key in panel.date_buttons if key == value))


def bookmark_preset(panel, value):
    click(panel, next(button for button, key in panel.bookmark_buttons if key == value))


def slider_event(panel, value):
    return SimpleNamespace(args={
        "epoch": panel._epoch,
        "revision": panel.bookmark_slider.props["data-bookmark-revision"],
        "value": value,
    })


@pytest.mark.asyncio
async def test_calendar_selects_inclusive_leap_day_into_shared_subviews(library, leap_day):
    view, _ = library
    panel = view.filter_panel
    panel.change_setting("date_to", "2024-02-29")
    token, handler, dialog = open_calendar(panel, "date_from")
    handler(selected(token, "2024-02-29"))
    assert view.filters.date_from == view.filters.date_to == "2024-02-29"
    assert panel.date_from.value == panel.date_to.value == "2024-02-29"
    assert view.state.filters == view.bookmark_state.filters == view.filters
    assert panel._calendar_token is None and dialog.is_deleted
    view.show_subview("bookmarks")
    assert view.bookmark_state.filters.date_from == "2024-02-29"
    view.show_subview("downloaded")
    assert view.state.filters.date_to == "2024-02-29"
    # A duplicate delivery after the picker closed cannot select another day.
    handler(selected(token, "2024-02-28"))
    assert view.filters.date_from == "2024-02-29"


@pytest.mark.asyncio
async def test_calendar_rejects_invalid_outside_and_non_scalar_values(library, leap_day):
    view, _ = library
    panel = view.filter_panel
    token, handler, dialog = open_calendar(panel, "date_from")
    before = view.filters
    for value in ("2023-02-29", "2024-02-30", "2006-12-31", "2024-03-01",
                  "2024-2-29", "20240229", "", None, True,
                  {"from": "2024-01-01", "to": "2024-02-29"}, ["2024-02-29"]):
        handler(selected(token, value))
        assert view.filters == before
        assert panel._calendar_token == token and not dialog.is_deleted
    handler(selected(token, "2007-01-01"))
    token, handler, _ = open_calendar(panel, "date_to")
    handler(selected(token, "2024-02-29"))
    assert (view.filters.date_from, view.filters.date_to) == ("2007-01-01", "2024-02-29")


@pytest.mark.asyncio
async def test_cancel_and_old_hide_pick_cannot_close_or_write_a_new_picker(library):
    view, _ = library
    panel = view.filter_panel
    old_token, old_pick, old_dialog = open_calendar(panel, "date_from")
    old_hide = event_handler(old_dialog, "hide")
    old_cancel = event_handler(panel.calendar_cancel, "click")
    old_cancel(None)
    assert view.filters == LibraryFilters() and old_dialog.is_deleted
    new_token, new_pick, new_dialog = open_calendar(panel, "date_to")
    assert old_token[1] == new_token[1] and old_token[2] != new_token[2]
    old_hide(None)
    old_cancel(None)
    old_pick(selected(old_token, "2020-01-01"))
    # The server callback also pins the opening token, not just received fields.
    old_pick(selected(new_token, "2020-01-01"))
    assert view.filters == LibraryFilters()
    assert panel._calendar_token == new_token and not new_dialog.is_deleted
    new_pick(selected(new_token, "2020-01-02"))
    assert view.filters.date_from == "" and view.filters.date_to == "2020-01-02"


@pytest.mark.parametrize("boundary", ("tab", "subview", "panel_close", "clear", "retire"))
@pytest.mark.asyncio
async def test_picker_and_old_open_are_retired_at_real_view_boundaries(library, boundary):
    view, _ = library
    panel = view.filter_panel
    old_open = event_handler(panel.date_from_button, "click", browser=True)
    token, handler, dialog = open_calendar(panel, "date_from")
    if boundary == "tab":
        view.set_active(False)
        view.set_active(True)
    elif boundary == "subview":
        view.show_subview("bookmarks")
    elif boundary == "panel_close":
        panel.toggle()
    elif boundary == "clear":
        panel.clear()
    else:
        view.close()
    expected = view.filters
    assert panel._calendar_token is None and dialog.is_deleted
    handler(selected(token, "2020-01-01"))
    old_open(SimpleNamespace(args={"epoch": token[1]}))
    assert view.filters == expected and panel._calendar_token is None


@pytest.mark.asyncio
async def test_presets_clear_and_manual_text_keep_original_contract(library, leap_day):
    view, _ = library
    panel = view.filter_panel
    date_preset(panel, 1)
    assert (view.filters.date_from, view.filters.date_to) == ("2023-02-28", "")
    date_preset(panel, 3)
    assert (view.filters.date_from, view.filters.date_to) == ("2021-02-28", "")
    panel.change_setting("date_to", "2024-02-29")
    before = view.filters
    date_preset(panel, "custom")
    assert view.filters == before
    assert next(button for button, key in panel.date_buttons if key == "custom").props["aria-pressed"] == "true"
    token, handler, dialog = open_calendar(panel, "date_from")
    date_preset(panel, None)
    handler(selected(token, "2020-01-01"))
    assert view.filters.date_from == view.filters.date_to == "" and dialog.is_deleted
    # Do not replace the original text comparison behavior with date parsing.
    panel.change_setting("date_from", "2024-0")
    token, handler, dialog = open_calendar(panel, "date_from")
    assert panel.calendar_picker.value is None
    panel.change_setting("date_from", "2024-01-15")
    handler(selected(token, "2020-01-01"))
    assert view.filters.date_from == "2024-01-15" and dialog.is_deleted
    panel.clear()
    assert view.filters == LibraryFilters()
    assert next(button for button, key in panel.date_buttons if key is None).props["aria-pressed"] == "true"


@pytest.mark.asyncio
async def test_calendar_pending_and_selection_are_client_local():
    services = ApplicationServices(120)
    clients = [Client(ui.page("/_calendar_client_a")), Client(ui.page("/_calendar_client_b"))]
    views = []
    try:
        for client in clients:
            with client:
                view = LibraryView(services)
                view.filter_panel.toggle()
                views.append(view)
        a, b = (view.filter_panel for view in views)
        ta, ha, _ = open_calendar(a, "date_from")
        tb, hb, db = open_calendar(b, "date_to")
        hb(selected(ta, "2020-02-29"))
        ha(selected(tb, "2020-02-29"))
        assert views[0].filters == views[1].filters == LibraryFilters()
        ha(selected(ta, "2020-02-29"))
        assert views[0].filters.date_from == "2020-02-29"
        assert views[1].filters == LibraryFilters()
        assert b._calendar_token == tb and not db.is_deleted
        hb(selected(tb, "2020-03-01"))
        assert views[1].filters.date_to == "2020-03-01"
        assert views[0].filters.date_to == ""
    finally:
        for client in clients:
            client.delete()
        services.close()


@pytest.mark.asyncio
async def test_slider_drag_does_not_refilter_until_release(library):
    view, _ = library
    panel = view.filter_panel
    slider = panel.bookmark_slider
    assert slider.value == 0
    assert (slider.props["min"], slider.props["max"], slider.props["step"]) == (0, 1000, 50)
    original = panel.on_change
    changes = []

    def apply(filters):
        changes.append(filters)
        return original(filters)

    panel.on_change = apply
    update = event_handler(slider, "update:model-value")
    release = event_handler(slider, "change", browser=True)
    before = view.filters
    generation = view.state.generation
    for value in (50, 200, 350, 500):
        update(slider_event(panel, value))
        assert slider.value == value
    assert changes == [] and view.filters == before
    assert view.state.generation == generation
    release(slider_event(panel, 500))
    assert len(changes) == 1 and view.filters.bookmark_min == 500
    assert view.state.filters == view.bookmark_state.filters == view.filters
    assert slider.value == panel.bookmark_min.value == 500
    assert view.state.generation != generation


@pytest.mark.asyncio
async def test_slider_presets_number_and_shared_subviews_stay_synchronized(library):
    view, _ = library
    panel = view.filter_panel
    for value in (100, 500, 1000, 0):
        bookmark_preset(panel, value)
        assert panel.bookmark_slider.value == panel.bookmark_min.value == value
        assert view.filters.bookmark_min == value
    # Existing numeric entry remains an arbitrary nonnegative integer.
    panel.change_setting("bookmark_min", "137")
    assert panel.bookmark_slider.value == view.filters.bookmark_min == 137
    panel.change_setting("bookmark_min", 1500)
    assert panel.bookmark_slider.value == 1000
    assert panel.bookmark_min.value == view.filters.bookmark_min == 1500
    assert "1,500" in panel.bookmark_value.text
    view.show_subview("bookmarks")
    assert view.bookmark_state.filters.bookmark_min == 1500
    bookmark_preset(panel, 0)
    assert panel.bookmark_slider.value == panel.bookmark_min.value == 0
    assert view.state.filters == view.bookmark_state.filters == LibraryFilters()


@pytest.mark.asyncio
async def test_slider_rejects_queued_epoch_invalid_steps_and_closed_panel(library):
    view, _ = library
    panel = view.filter_panel
    release = event_handler(panel.bookmark_slider, "change", browser=True)
    stale = slider_event(panel, 1000)
    view.set_active(False)
    view.set_active(True)
    release(stale)
    assert view.filters.bookmark_min == 0
    for value in (True, -50, 1050, 125, float("inf"), float("nan"), "500", None):
        release(slider_event(panel, value))
        assert view.filters.bookmark_min == 0
    panel.toggle()
    release(slider_event(panel, 500))
    assert view.filters.bookmark_min == 0


@pytest.mark.asyncio
async def test_queued_builtin_slider_model_does_not_restore_a_cleared_thumb(library):
    view, _ = library
    panel = view.filter_panel
    slider = panel.bookmark_slider
    update = event_handler(slider, "update:model-value")
    release = event_handler(slider, "change", browser=True)
    old_release = slider_event(panel, 500)
    old_epoch = panel._epoch
    panel.clear()
    assert panel._epoch != old_epoch
    # This is the built-in throttled model handler, not the guarded release.
    # Its delayed delivery must not show a filter the library did not accept.
    update(SimpleNamespace(args=500))
    release(old_release)
    assert view.filters.bookmark_min == panel.bookmark_min.value == 0
    assert slider.value == 0

