"""Launch the product on a non-primary landscape 1920x1080 monitor."""
import collections
import contextlib
import ctypes as c
from ctypes import wintypes as w
import runpy

PER_MONITOR_AWARE_V2 = -4
SYSTEM_AWARE = -2
TARGET_SIZE = (1920, 1080)
Monitor = collections.namedtuple('Monitor', 'handle rect work primary')


def select_target(monitors):
    """Non-primary, landscape 1920x1080 monitor; None when there is none."""
    found = [m for m in monitors
             if not m.primary and (m.rect[2] - m.rect[0], m.rect[3] - m.rect[1]) == TARGET_SIZE]
    return min(found, key=lambda m: (m.rect[0], m.rect[1])) if found else None


def _user32():
    user = c.WinDLL('user32', use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [c.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = c.c_void_p
    user.GetDpiForSystem.restype = w.UINT
    return user


@contextlib.contextmanager
def dpi_context(user, context):
    previous = user.SetThreadDpiAwarenessContext(c.c_void_p(context))
    if not previous:
        raise OSError('dpi_context_unavailable')
    try:
        yield
    finally:
        if not user.SetThreadDpiAwarenessContext(previous):
            raise OSError('dpi_context_restore_failed')


class _MonitorInfo(c.Structure):
    _fields_ = [('size', w.DWORD), ('monitor', w.RECT), ('work', w.RECT), ('flags', w.DWORD)]


def enumerate_monitors(user):
    """Monitors in the calling thread's current DPI awareness context."""
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, c.POINTER(w.RECT), w.LPARAM)
    user.EnumDisplayMonitors.argtypes = [w.HDC, c.c_void_p, callback_type, w.LPARAM]
    user.GetMonitorInfoW.argtypes = [w.HANDLE, c.c_void_p]
    found = []

    @callback_type
    def visit(handle, _dc, _rect, _param):
        info = _MonitorInfo()
        info.size = c.sizeof(info)
        if user.GetMonitorInfoW(handle, c.byref(info)):
            found.append(Monitor(int(handle or 0),
                                 (info.monitor.left, info.monitor.top, info.monitor.right, info.monitor.bottom),
                                 (info.work.left, info.work.top, info.work.right, info.work.bottom),
                                 bool(info.flags & 1)))
        return True

    if not user.EnumDisplayMonitors(None, None, visit, 0):
        raise OSError('monitor_enumeration_failed')
    return found


def discover(user=None):
    """Select by physical size and return system-aware work area and DPI."""
    user = user or _user32()
    with dpi_context(user, PER_MONITOR_AWARE_V2):
        physical = enumerate_monitors(user)
    primary = next((m for m in physical if m.primary), None)
    chosen = select_target(physical)
    if primary is None or chosen is None:
        return None
    with dpi_context(user, SYSTEM_AWARE):
        virtual = {m.handle: m for m in enumerate_monitors(user)}
        dpi = int(user.GetDpiForSystem())
    if chosen.handle not in virtual:
        return None
    return virtual[chosen.handle].work, dpi


def main(*, discover_target=discover):
    target = discover_target()
    if target is None:
        raise SystemExit('target_monitor_missing: no non-primary landscape 1920x1080 monitor')
    from pixivvault_nicegui import native_layout
    native_layout.primary_work_area = lambda *, api=None: target
    runpy.run_module('pixivvault_nicegui', run_name='__main__')


if __name__ == '__main__':
    main()
