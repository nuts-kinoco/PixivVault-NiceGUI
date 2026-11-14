"""Bound the first Windows native window to the primary monitor's work area.

pywebview 6.2.1 WinForms takes logical size/origin and scales both once. Query
physical work-area pixels and effective DPI, then convert inward to its units.
No existing application window is moved, resized, activated or inspected.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


class NativeLayoutError(RuntimeError):
    """Startup geometry could not be established safely."""


@dataclass(frozen=True, slots=True)
class StartupLayout:
    width: int
    height: int
    x: int
    y: int
    dpi: int
    work_area: tuple[int, int, int, int]

    @property
    def physical_bounds(self) -> tuple[int, int, int, int]:
        scale = self.dpi / 96
        left, top = int(self.x * scale), int(self.y * scale)
        return left, top, left + int(self.width * scale), top + int(self.height * scale)


def fit_startup_window(work_area: tuple[int, int, int, int], dpi: int,
                       desired: tuple[int, int] = (1920, 1080),
                       inset: int = 16) -> StartupLayout:
    """Pure calculation; preserve the requested logical size whenever it fits."""
    if (not isinstance(work_area, tuple) or len(work_area) != 4 or
            any(type(value) is not int for value in work_area)):
        raise ValueError("work area must be four integer physical coordinates")
    if type(dpi) is not int or not 48 <= dpi <= 960:
        raise ValueError("invalid display DPI")
    if (not isinstance(desired, tuple) or len(desired) != 2 or
            any(type(value) is not int or value <= 0 for value in desired)):
        raise ValueError("desired size must be two positive integers")
    if type(inset) is not int or inset < 0:
        raise ValueError("invalid work area inset")
    left, top, right, bottom = work_area
    if left >= right or top >= bottom:
        raise ValueError("empty work area")
    scale = dpi / 96
    # Round every edge inward. Extra margins also keep non-client borders
    # comfortably away from taskbars and monitor edges.
    x0, y0 = math.ceil((left + inset) / scale), math.ceil((top + inset) / scale)
    x1, y1 = math.floor((right - inset) / scale), math.floor((bottom - inset) / scale)
    width, height = min(desired[0], x1 - x0), min(desired[1], y1 - y0)
    # pywebview's default MinimumSize is 200x100 logical pixels. Never ask
    # WinForms for less and let it silently enlarge beyond the monitor.
    if width < 200 or height < 100:
        raise NativeLayoutError("display working area is too small")
    layout = StartupLayout(width, height, x0 + (x1 - x0 - width) // 2,
                           y0 + (y1 - y0 - height) // 2, dpi, work_area)
    actual_left, actual_top, actual_right, actual_bottom = layout.physical_bounds
    if not (left <= actual_left < actual_right <= right and
            top <= actual_top < actual_bottom <= bottom):
        raise NativeLayoutError("display rounding exceeded working area")
    return layout


def _windows_api():
    """Load Win32 lazily: importing or running browser/check mode has no effect."""
    import ctypes
    from ctypes import wintypes as w

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.MonitorFromPoint.argtypes = [w.POINT, w.DWORD]
    user.MonitorFromPoint.restype = w.HANDLE
    user.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.c_void_p]
    user.GetMonitorInfoW.restype = w.BOOL
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
    user.CreateWindowExW.restype = w.HWND
    user.GetDpiForWindow.argtypes = [w.HWND]
    user.GetDpiForWindow.restype = w.UINT
    user.DestroyWindow.argtypes = [w.HWND]
    user.DestroyWindow.restype = w.BOOL
    return user


def primary_work_area(*, api=None) -> tuple[tuple[int, int, int, int], int]:
    """Measure our own hidden 1px window; restore this thread's DPI context.

    GetDpiForMonitor is not used on a per-monitor-aware thread. A non-visible
    standard STATIC window provides GetDpiForWindow on the chosen monitor.
    It is destroyed before this function returns, including failure paths.
    """
    import ctypes
    from ctypes import wintypes as w

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                    ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

    user = _windows_api() if api is None else api
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if not previous:
        raise NativeLayoutError("native DPI measurement unavailable")
    probe = None
    try:
        monitor = user.MonitorFromPoint(w.POINT(0, 0), 1)  # primary fallback
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise NativeLayoutError("native working area unavailable")
        if not info.dwFlags & 1:  # MONITORINFOF_PRIMARY
            raise NativeLayoutError("native primary monitor unavailable")
        area = info.rcWork
        bounds = (int(area.left), int(area.top), int(area.right), int(area.bottom))
        if not (info.rcMonitor.left <= area.left < area.right <= info.rcMonitor.right and
                info.rcMonitor.top <= area.top < area.bottom <= info.rcMonitor.bottom):
            raise NativeLayoutError("native working area invalid")
        probe = user.CreateWindowExW(0, "STATIC", "PixivVault DPI measurement", 0x80000000,
                                     area.left, area.top, 1, 1, None, None, None, None)
        if not probe:
            raise NativeLayoutError("native DPI measurement unavailable")
        dpi = int(user.GetDpiForWindow(probe))
        if not 48 <= dpi <= 960:
            raise NativeLayoutError("native DPI measurement invalid")
        return bounds, dpi
    finally:
        destroyed = True
        try:
            if probe:
                destroyed = bool(user.DestroyWindow(probe))
        finally:
            restored = user.SetThreadDpiAwarenessContext(previous)
            if not restored:
                raise NativeLayoutError("native DPI context restore failed")
        if not destroyed:
            raise NativeLayoutError("native DPI measurement cleanup failed")


def current_startup_layout() -> StartupLayout:
    area, dpi = primary_work_area()
    return fit_startup_window(area, dpi)
