"""L04/L07 real model routes: primitive values survive render/tab and stale events."""
from types import SimpleNamespace

from nicegui import Client, helpers, ui
import pytest

from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView
from test_library_calendar_slider import event_handler, library


def model_routes(element):
    native = helpers.event_type_to_camel_case(f"update:{element.VALUE_PROP}")
    # Input has a component-level update:value; the old extra model-value
    # listener leaks through QInput's $attrs as a second model route.
    return [event for event in element._event_listeners.values()
            if event.type in {native, "update:modelValue"}]


def browser_change(panel, name, value, *, epoch=None):
    """Deliver the actual registered model paths, with their advertised payload.

    The real Edge failure already demonstrates the client assignment. Here we
    exercise real ValueElement handlers and its preserved_props serialization,
    without replacing NiceGUI or inventing a parallel model implementation.
    """
    element = getattr(panel, name)
    token = panel._epoch if epoch is None else epoch
    routes = list(model_routes(element))
    assert routes
    for event in routes:
        if event.js_handler:
            assert "filterEpoch" in event.js_handler
            args = {"epoch": token, "value": value}
        else:
            args = value
        event.handler(SimpleNamespace(args=args))


def primitive_wire(element, expected):
    value = element._to_dict()
    model = value["props"][element.VALUE_PROP]
    assert type(model) in (str, int, float, bool), value
    assert str(model) == str(expected)
    # A previous loopback=False raw handler must not keep a client-side
    # envelope when the server resends the accepted primitive after revisit.
    assert element.VALUE_PROP not in value.get("preserved_props", []), value
    routes = model_routes(element)
    assert len(routes) == 1
    assert routes[0].type == helpers.event_type_to_camel_case(f"update:{element.VALUE_PROP}")
    assert routes[0].js_handler and "filterEpoch" in routes[0].js_handler
    assert element.props["loopback"] is True


@pytest.mark.asyncio
async def test_number_typing_and_tab_revisit_keep_primitive_1500_and_slider_revision(library):
    view, _ = library
    panel = view.filter_panel
    token = panel._epoch
    old_slider = SimpleNamespace(args={
        "epoch": token,
        "revision": panel.bookmark_slider.props["data-bookmark-revision"],
        "value": 500,
    })
    for raw, expected in (("1", 1), ("13", 13), ("137", 137), ("1500", 1500)):
        browser_change(panel, "bookmark_min", raw, epoch=token)
        assert panel._epoch == token  # queued typing stays in this input epoch
        assert view.filters.bookmark_min == panel.bookmark_min.value == expected
        assert panel.bookmark_slider.value == min(1000, expected)
        assert view.state.filters == view.bookmark_state.filters == view.filters
    event_handler(panel.bookmark_slider, "update:model-value")(old_slider)
    event_handler(panel.bookmark_slider, "change", browser=True)(old_slider)
    assert view.filters.bookmark_min == 1500 and panel.bookmark_slider.value == 1000

    view.set_active(False)
    view.set_active(True)
    assert view.filters.bookmark_min == panel.bookmark_min.value == 1500
    primitive_wire(panel.bookmark_min, 1500)


@pytest.mark.parametrize("field", ("date_from", "date_to"))
@pytest.mark.asyncio
async def test_manual_date_component_model_keeps_partial_typing_and_revisit_value(library, field):
    view, _ = library
    panel = view.filter_panel
    token = panel._epoch
    for value in ("2", "20", "2024", "2024-", "2024-02", "2024-02-29"):
        browser_change(panel, field, value, epoch=token)
        assert panel._epoch == token
        assert getattr(view.filters, field) == getattr(panel, field).value == value
        assert view.state.filters == view.bookmark_state.filters == view.filters
    view.show_subview("bookmarks")
    view.set_active(False)
    view.set_active(True)
    view.show_subview("downloaded")
    assert getattr(view.filters, field) == getattr(panel, field).value == "2024-02-29"
    primitive_wire(getattr(panel, field), "2024-02-29")


@pytest.mark.parametrize("field,accepted,old", (
    ("bookmark_min", "137", "500"),
    ("date_from", "2024-02-29", "2020-01-01"),
    ("date_to", "2024-03-01", "2020-02-01"),
))
@pytest.mark.asyncio
async def test_old_and_raw_model_paths_cannot_change_clear_or_tab_returned_field(
        library, field, accepted, old):
    view, _ = library
    panel = view.filter_panel
    element = getattr(panel, field)
    for boundary in ("clear", "tab"):
        browser_change(panel, field, accepted)
        old_epoch = panel._epoch
        if boundary == "clear":
            panel.clear()
        else:
            view.set_active(False)
            view.set_active(True)
        assert panel._epoch != old_epoch
        filters, generation = view.filters, view.state.generation
        expected = getattr(filters, field)

        browser_change(panel, field, old, epoch=old_epoch)
        assert view.filters == filters
        assert element.value == expected, "stale model changed the field while its filter was rejected"
        for event in model_routes(element):
            event.handler(SimpleNamespace(args=old))
        assert view.filters == filters and element.value == expected
        assert view.state.generation == generation
        assert view.state.filters == view.bookmark_state.filters == filters
        primitive_wire(element, expected)


@pytest.mark.asyncio
async def test_checkbox_has_one_guarded_route_and_stale_toggle_cannot_change_its_model(library):
    view, _ = library
    panel = view.filter_panel
    browser_change(panel, "exclude_ai", True)
    assert view.filters.exclude_ai is panel.exclude_ai.value is True
    old_epoch = panel._epoch
    panel.clear()
    browser_change(panel, "exclude_ai", True, epoch=old_epoch)
    assert view.filters == LibraryFilters()
    assert panel.exclude_ai.value is False
    primitive_wire(panel.exclude_ai, False)
    browser_change(panel, "exclude_ai", True)
    assert view.filters.exclude_ai is panel.exclude_ai.value is True
    primitive_wire(panel.exclude_ai, True)


@pytest.mark.asyncio
async def test_model_envelope_from_another_client_cannot_change_number_or_dates():
    services = ApplicationServices(120)
    clients = [Client(ui.page("/_model_client_a")), Client(ui.page("/_model_client_b"))]
    views = []
    try:
        for client in clients:
            with client:
                view = LibraryView(services)
                view.filter_panel.toggle()
                views.append(view)
        a, b = (view.filter_panel for view in views)
        browser_change(a, "bookmark_min", "1500")
        browser_change(a, "date_from", "2024-02-29")
        browser_change(b, "bookmark_min", "500", epoch=a._epoch)
        browser_change(b, "date_from", "2020-01-01", epoch=a._epoch)
        assert views[1].filters == LibraryFilters()
        assert b.bookmark_min.value == 0 and b.date_from.value == ""
        assert views[0].filters.bookmark_min == 1500 and a.date_from.value == "2024-02-29"
        primitive_wire(b.bookmark_min, 0)
        primitive_wire(b.date_from, "")
    finally:
        for client in clients:
            client.delete()
        services.close()
