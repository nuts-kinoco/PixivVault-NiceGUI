"""Owner-only initial placement; no Win32 calls or real windows in these tests."""
from types import SimpleNamespace

import pytest

from tools.native_window_position import position_client


class Function:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def make_window(*, fault=None):
    state = {"origin": (496, 536), "size": (3336, 1826), "dpi": 168,
             "calls": [], "checks": 0}

    def verify():
        state["checks"] += 1
        if fault == "owner" and state["checks"] >= 2:
            raise RuntimeError("owner changed")

    def outer(_hwnd, pointer):
        pointer._obj.left, pointer._obj.top = 490, 498
        pointer._obj.right, pointer._obj.bottom = 3838, 2368
        return fault != "geometry"

    def move(hwnd, zorder, x, y, width, height, flags):
        state["calls"].append((hwnd, zorder, x, y, width, height, flags))
        if fault == "move":
            return False
        state["origin"] = (x + 6, y + 38)
        if fault == "dpi":
            state["dpi"] = 144
        if fault == "size":
            state["size"] = (3000, 1800)
        return True

    user = SimpleNamespace(
        IsIconic=lambda hwnd: fault == "minimized", IsWindowVisible=lambda hwnd: True,
        GetDpiForWindow=lambda hwnd: state["dpi"],
        GetSystemMetrics=lambda n: {76: 0, 77: -1080, 78: 6000, 79: 3240}[n],
        GetWindowRect=Function(outer),
        MonitorFromWindow=Function(lambda hwnd, flags: 1),
        MonitorFromRect=Function(lambda rect, flags: 2 if fault == "monitor" else 1),
        SetWindowPos=move,
    )
    return SimpleNamespace(verify_top=verify, top_identity=(100, 123), top=999, user=user,
                           origin=lambda hwnd: state["origin"], client=lambda hwnd: state["size"]), state


def test_places_only_owned_top_without_resize_zorder_or_activation():
    window, state = make_window()
    report = position_client(window, (100, 140), expected_identity=(100, 123))
    assert state["calls"] == [(999, 0, 94, 102, 0, 0, 0x0015)]
    assert report["client_origin"] == [100, 140]
    assert report["client_size"] == [3336, 1826]
    assert report["dpi"] == 168 and report["same_monitor"] and not report["resized"]
    assert state["checks"] >= 3


@pytest.mark.parametrize("fault", ["owner", "geometry", "monitor", "minimized"])
def test_rejects_before_mutation(fault):
    window, state = make_window(fault=fault)
    with pytest.raises(RuntimeError):
        position_client(window, (100, 140), expected_identity=(100, 123))
    assert state["calls"] == []


@pytest.mark.parametrize("fault", ["move", "dpi", "size"])
def test_mutation_failure_or_changed_conditions_are_not_success(fault):
    window, _ = make_window(fault=fault)
    with pytest.raises(RuntimeError):
        position_client(window, (100, 140), expected_identity=(100, 123))


def test_offscreen_target_and_wrong_owner_rejected_without_mutation():
    window, state = make_window()
    with pytest.raises(RuntimeError):
        position_client(window, (100, 140), expected_identity=(200, 123))
    with pytest.raises(RuntimeError, match="outside virtual screen"):
        position_client(window, (100, 1000), expected_identity=(100, 123))
    assert state["calls"] == []
