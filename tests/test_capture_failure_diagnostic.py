"""Failure metadata does not relax screen ownership or capture foreign pixels."""
from types import SimpleNamespace
import pytest

from tools import benchmark_native_pixels as pixels


def test_unknown_screen_owner_is_rejected_with_identity_only_metadata(monkeypatch):
    window = object.__new__(pixels.OwnedWindow)
    window.root, window.top, window.input = 1, 100, 101
    window.identities = {1: 10, 2: 20}
    window.screen_bbox = (0, 0, 100, 80)
    window.verify = lambda: None
    window._screen_geometry = lambda: window.screen_bbox
    window.user = SimpleNamespace(WindowFromPoint=lambda point: 900)
    window._pid = lambda hwnd: 9
    window.kernel = object()
    monkeypatch.setattr(pixels, "process_parents", lambda: {1: 0, 2: 1, 9: 8})
    monkeypatch.setattr(pixels, "process_birth", lambda kernel, pid: 90)
    with pytest.raises(RuntimeError, match="occluded") as caught:
        window._check_screen()
    diagnostic = caught.value.capture_probe
    assert diagnostic["pid"] == 9
    assert diagnostic["known_pid"] is False
    assert diagnostic["descendant"] is False
    assert diagnostic["birth_matches"] is False
    assert set(diagnostic) == {"point", "hwnd", "pid", "known_pid", "birth_matches",
                               "descendant", "screen_bbox", "owned_top_hwnd", "owned_target_hwnd"}
