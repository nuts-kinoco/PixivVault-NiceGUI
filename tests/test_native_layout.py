"""Pure startup geometry and an injected Win32 boundary; no desktop in pytest."""
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.native_layout import (
    NativeLayoutError, fit_startup_window, primary_work_area,
)


@pytest.mark.parametrize("dpi", [96, 120, 144, 168, 192, 240, 288, 384])
@pytest.mark.parametrize("area", [
    (0, 0, 1920, 1040), (0, 0, 3840, 2104),
    (-2560, 80, 0, 1440), (3840, -1080, 5760, 0),
])
def test_layout_stays_inside_physical_work_area_at_each_scale(area, dpi):
    layout = fit_startup_window(area, dpi)
    left, top, right, bottom = layout.physical_bounds
    assert area[0] <= left < right <= area[2]
    assert area[1] <= top < bottom <= area[3]
    assert 200 <= layout.width <= 1920
    assert 100 <= layout.height <= 1080
    assert layout.work_area == area and layout.dpi == dpi


def test_large_primary_screen_preserves_existing_image_area():
    layout = fit_startup_window((0, 0, 3840, 2104), 168)
    assert (layout.width, layout.height) == (1920, 1080)
    left, top, right, bottom = layout.physical_bounds
    assert (right - left, bottom - top) == (3360, 1890)
    assert 0 < left < 300 and 0 < top < 200


def test_small_screen_shrinks_outer_window_instead_of_spilling_below_taskbar():
    layout = fit_startup_window((48, 32, 1920, 1040), 168)
    assert layout.width < 1920 and layout.height < 1080
    left, top, right, bottom = layout.physical_bounds
    assert left >= 48 and top >= 32 and right <= 1920 and bottom <= 1040


@pytest.mark.parametrize("area,dpi,desired,inset", [
    ((0, 0, 0, 1080), 168, (1920, 1080), 16),
    ((0, 0, 1920, 1080), 0, (1920, 1080), 16),
    ((0, 0, 1920, 1080), True, (1920, 1080), 16),
    ((0, 0, 1920, 1080), 168, (0, 1080), 16),
    ((0, 0, 1920, 1080), 168, (1920, 1080), -1),
])
def test_invalid_geometry_rejected(area, dpi, desired, inset):
    with pytest.raises(ValueError):
        fit_startup_window(area, dpi, desired, inset)


def test_tiny_display_does_not_let_backend_minimum_size_expand_offscreen():
    with pytest.raises(NativeLayoutError, match="too small"):
        fit_startup_window((0, 0, 300, 180), 192)


def fake_api(fault=None):
    calls = []

    def context(value):
        raw = getattr(value, "value", value)
        calls.append(("context", raw))
        if len([item for item in calls if item[0] == "context"]) == 1:
            return 0 if fault == "enter" else 99
        return 0 if fault == "restore" else 88

    def monitor(point, flags):
        calls.append(("monitor", point.x, point.y, flags))
        return 0 if fault == "monitor" else 123

    def info(handle, pointer):
        calls.append(("info", handle))
        value = pointer._obj
        assert value.cbSize > 0
        value.rcMonitor.left, value.rcMonitor.top = 0, 0
        value.rcMonitor.right, value.rcMonitor.bottom = 3840, 2160
        value.rcWork.left, value.rcWork.top = 0, 0
        value.rcWork.right, value.rcWork.bottom = 3840, (2200 if fault == "bounds" else 2104)
        value.dwFlags = 0 if fault == "secondary" else 1
        return fault != "info"

    def create(*args):
        calls.append(("create", args))
        assert args[1] == "STATIC" and not args[3] & 0x10000000  # WS_VISIBLE absent
        assert args[6:8] == (1, 1)
        assert args[8] is None  # no foreign parent
        return 0 if fault == "create" else 456

    def dpi(hwnd):
        calls.append(("dpi", hwnd))
        assert hwnd == 456
        return 0 if fault == "dpi" else 168

    def destroy(hwnd):
        calls.append(("destroy", hwnd))
        return fault != "destroy"

    return SimpleNamespace(SetThreadDpiAwarenessContext=context, MonitorFromPoint=monitor,
                           GetMonitorInfoW=info, CreateWindowExW=create,
                           GetDpiForWindow=dpi, DestroyWindow=destroy), calls


def test_probe_uses_only_hidden_owned_window_and_restores_context():
    api, calls = fake_api()
    assert primary_work_area(api=api) == ((0, 0, 3840, 2104), 168)
    assert [item[0] for item in calls] == ["context", "monitor", "info", "create", "dpi", "destroy", "context"]
    assert calls[-1] == ("context", 99)


@pytest.mark.parametrize("fault", ["monitor", "info", "secondary", "bounds", "create", "dpi", "destroy", "restore"])
def test_probe_failure_still_restores_thread_and_destroys_own_handle(fault):
    api, calls = fake_api(fault)
    with pytest.raises(NativeLayoutError):
        primary_work_area(api=api)
    assert calls[-1] == ("context", 99)
    created = any(item[0] == "create" for item in calls) and fault != "create"
    assert ("destroy", 456) in calls if created else not any(item[0] == "destroy" for item in calls)


def test_context_failure_does_not_create_probe_or_call_other_display_apis():
    api, calls = fake_api("enter")
    with pytest.raises(NativeLayoutError):
        primary_work_area(api=api)
    assert len(calls) == 1


def test_native_start_applies_layout_before_registering_resources(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import bootstrap, native_layout

    calls = []
    layout = fit_startup_window((0, 0, 1920, 1040), 168)
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(app.native, "window_args", {})
    monkeypatch.setattr(app.native, "settings", {"ALLOW_DOWNLOADS": False})
    monkeypatch.setattr(native_layout, "current_startup_layout", lambda: (calls.append("layout"), layout)[1])
    monkeypatch.setattr(bootstrap, "register_pages", lambda **kwargs: calls.append("register"))
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(kwargs))
    assert bootstrap.main(["--native"]) == 0
    assert calls[:2] == ["layout", "register"]
    assert calls[-1]["window_size"] == (layout.width, layout.height)
    assert app.native.window_args == {"x": layout.x, "y": layout.y, "confirm_close": True}
    assert app.native.settings["ALLOW_DOWNLOADS"] is True


def test_browser_mode_does_not_measure_or_change_native_window_settings(monkeypatch):
    from nicegui import app, ui
    from pixivvault_nicegui import bootstrap, native_layout

    calls = []
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(native_layout, "current_startup_layout", lambda: pytest.fail("browser must not touch DPI"))
    monkeypatch.setattr(app.native, "window_args", {"existing": 123})
    monkeypatch.setattr(app.native, "settings", {"ALLOW_DOWNLOADS": False})
    monkeypatch.setattr(bootstrap, "register_pages", lambda **kwargs: None)
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(kwargs))
    assert bootstrap.main([]) == 0
    assert calls[0]["window_size"] is None and not calls[0]["native"]
    assert app.native.window_args == {"existing": 123}
    assert app.native.settings == {"ALLOW_DOWNLOADS": False}


def test_layout_failure_does_not_register_resources_or_start_ui(monkeypatch):
    from nicegui import ui
    from pixivvault_nicegui import bootstrap, native_layout

    def fail():
        raise NativeLayoutError("synthetic display failure")
    monkeypatch.setattr(bootstrap, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(bootstrap.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(native_layout, "current_startup_layout", fail)
    monkeypatch.setattr(bootstrap, "register_pages", lambda **kwargs: pytest.fail("resource leak"))
    monkeypatch.setattr(ui, "run", lambda **kwargs: pytest.fail("UI must not start"))
    with pytest.raises(NativeLayoutError):
        bootstrap.main(["--native"])


def test_native_check_does_not_probe_the_desktop(monkeypatch, capsys):
    import json
    from pixivvault_nicegui import bootstrap, native_layout

    monkeypatch.setattr(native_layout, "current_startup_layout", lambda: pytest.fail("check mode DPI probe"))
    assert bootstrap.main(["--native", "--check"]) == 0
    assert json.loads(capsys.readouterr().out)["native_requested"] is True
