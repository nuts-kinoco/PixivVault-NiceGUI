"""Explicit pre-acceptance placement of an identity-pinned test window only.

This is not application recovery: it runs before UI/capture acceptance, never
changes the client size or moves a WebView child, and never changes DPI/monitor.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w

if __package__:
    from .native_restore_gate import Win32Window, ensure_physical_dpi
    from .benchmark_native_pixels import checked_screen_bbox
else:
    from native_restore_gate import Win32Window, ensure_physical_dpi
    from benchmark_native_pixels import checked_screen_bbox


def position_client(window, target: tuple[int, int], *, expected_identity) -> dict:
    window.verify_top()
    if window.top_identity != expected_identity:
        raise RuntimeError("placement owner mismatch")
    if len(target) != 2 or any(type(value) is not int for value in target):
        raise ValueError("client origin must contain two integers")
    user, top = window.user, window.top
    if user.IsIconic(top) or not user.IsWindowVisible(top):
        raise RuntimeError("placement requires a visible normal test window")
    before_origin = window.origin(top)
    before_size = window.client(top)
    before_dpi = int(user.GetDpiForWindow(top))
    if before_dpi <= 0:
        raise RuntimeError("placement DPI unavailable")
    virtual = (user.GetSystemMetrics(76), user.GetSystemMetrics(77),
               user.GetSystemMetrics(76) + user.GetSystemMetrics(78),
               user.GetSystemMetrics(77) + user.GetSystemMetrics(79))
    bbox = (*target, target[0] + before_size[0], target[1] + before_size[1])
    checked_screen_bbox(bbox, virtual)
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.GetWindowRect.restype = w.BOOL
    user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
    user.MonitorFromWindow.restype = w.HANDLE
    user.MonitorFromRect.argtypes = [ctypes.POINTER(w.RECT), w.DWORD]
    user.MonitorFromRect.restype = w.HANDLE
    outer = w.RECT()
    if not user.GetWindowRect(top, ctypes.byref(outer)):
        raise RuntimeError("placement outer geometry unavailable")
    original_monitor = user.MonitorFromWindow(top, 2)
    desired = w.RECT(*bbox)
    if not original_monitor or user.MonitorFromRect(ctypes.byref(desired), 2) != original_monitor:
        raise RuntimeError("placement cannot change monitor")
    offset = before_origin[0] - outer.left, before_origin[1] - outer.top
    window.verify_top()
    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE. Only our pinned top HWND.
    if not user.SetWindowPos(top, 0, target[0] - offset[0], target[1] - offset[1], 0, 0, 0x0015):
        raise RuntimeError("owned placement failed")
    window.verify_top()
    after_origin, after_size = window.origin(top), window.client(top)
    after_dpi = int(user.GetDpiForWindow(top))
    if (after_origin != tuple(target) or after_size != before_size or after_dpi != before_dpi or
            user.MonitorFromWindow(top, 2) != original_monitor):
        raise RuntimeError("placement changed size, DPI, monitor or missed requested origin")
    return {"explicit_setup": True, "before_origin": list(before_origin),
            "client_origin": list(after_origin), "client_size": list(after_size),
            "dpi": after_dpi, "same_monitor": True, "resized": False,
            "set_window_pos_flags": 0x0015}


def place_owned_client(root: int, birth: int, hwnd: int, origin, expected_identity) -> dict:
    ensure_physical_dpi()
    window = Win32Window(root, birth, hwnd)
    return position_client(window, tuple(origin), expected_identity=expected_identity)
