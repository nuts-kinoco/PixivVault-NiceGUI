"""Capture a physical virtual-desktop rectangle with GDI (no ownership checks).

The caller must establish DPI awareness and screen/window ownership separately.
This is an experimental capture backend; latency is not assumed to be lower.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PIL import Image


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _api():
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    H = wintypes.HANDLE
    def signature(dll, name, result, *args):
        fn = getattr(dll, name)
        fn.argtypes = list(args)
        fn.restype = result
    signature(user, "GetDC", H, wintypes.HWND)
    signature(user, "ReleaseDC", ctypes.c_int, wintypes.HWND, H)
    signature(user, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
    signature(gdi, "CreateCompatibleDC", H, H)
    signature(gdi, "DeleteDC", wintypes.BOOL, H)
    signature(gdi, "CreateDIBSection", H, H, ctypes.POINTER(BITMAPINFO),
              wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD)
    signature(gdi, "SelectObject", H, H, H)
    signature(gdi, "DeleteObject", wintypes.BOOL, H)
    signature(gdi, "BitBlt", wintypes.BOOL, H, ctypes.c_int, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, H, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    signature(gdi, "GdiFlush", wintypes.BOOL)
    return user, gdi


def _validate(box, metrics):
    if not isinstance(box, (tuple, list)) or len(box) != 4:
        raise ValueError("box must have four integer coordinates")
    if any(type(v) is not int or not -(2**31) <= v < 2**31 for v in box):
        raise ValueError("coordinates must be signed 32-bit integers")
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if not (0 < width <= 2**31 - 1 and 0 < height <= 2**31 - 1):
        raise ValueError("empty or overflowing capture rectangle")
    if width * height * 4 > 2**31 - 1:
        raise ValueError("capture allocation exceeds supported DIB size")
    x, y, w, h = (metrics(i) for i in (76, 77, 78, 79))
    if w <= 0 or h <= 0 or not (x <= left < right <= x + w and y <= top < bottom <= y + h):
        raise ValueError("capture rectangle outside virtual desktop")
    return width, height


def _rgb_from_bgra(raw: bytes, size: tuple[int, int]) -> Image.Image:
    # 'raw' decoder maps B,G,R,X into RGB; negative biHeight gave top-first rows.
    return Image.frombytes("RGB", size, raw, "raw", "BGRX", 0, 1)


def capture_screen_box(box: tuple[int, int, int, int]) -> Image.Image:
    """Copy a screen rectangle to an independent RGB Pillow image.

    Requires Windows and caller-selected DPI awareness. Raises on every GDI failure.
    """
    if sys.platform != "win32":
        raise OSError("Windows GDI capture requires Windows")
    user, gdi = _api()
    width, height = _validate(box, user.GetSystemMetrics)
    left, top, _, _ = box
    desktop = memory = bitmap = previous = None
    selected = False
    try:
        desktop = user.GetDC(None)
        if not desktop:
            raise OSError("GetDC failed")
        memory = gdi.CreateCompatibleDC(desktop)
        if not memory:
            raise OSError("CreateCompatibleDC failed")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(desktop, ctypes.byref(info), 0,
                                      ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise OSError("CreateDIBSection failed")
        previous = gdi.SelectObject(memory, bitmap)
        if not previous or ctypes.c_void_p(previous).value == ctypes.c_void_p(-1).value:
            raise OSError("SelectObject failed")
        selected = True
        if not gdi.BitBlt(memory, 0, 0, width, height, desktop, left, top,
                          0x00CC0020 | 0x40000000):
            raise OSError("BitBlt failed")
        if not gdi.GdiFlush():
            raise OSError("GdiFlush failed")
        raw = ctypes.string_at(bits.value, width * height * 4)
        return _rgb_from_bgra(raw, (width, height))
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_errors = []
        def attempt(label, action):
            try:
                if not action():
                    cleanup_errors.append(label)
                    return False
                return True
            except Exception as exc:
                cleanup_errors.append(f"{label}: {exc}")
                return False

        restored = not selected
        if selected:
            result = None
            restore_raised = False
            try:
                result = gdi.SelectObject(memory, previous)
            except Exception as exc:
                restore_raised = True
                cleanup_errors.append(f"SelectObject restore: {exc}")
            restored = result is not None and ctypes.c_void_p(result).value not in (
                None, 0, ctypes.c_void_p(-1).value)
            if not restored and not restore_raised:
                cleanup_errors.append("SelectObject restore failed")

        # A failed restore may leave the bitmap selected. Destroy its DC first.
        # Even a failing DeleteDC is attempted; an unsuccessful release is reported.
        if memory and not restored:
            attempt("DeleteDC failed", lambda: gdi.DeleteDC(memory))
            memory = None
        if bitmap:
            attempt("DeleteObject failed", lambda: gdi.DeleteObject(bitmap))
        if memory:
            attempt("DeleteDC failed", lambda: gdi.DeleteDC(memory))
        if desktop:
            attempt("ReleaseDC failed", lambda: user.ReleaseDC(None, desktop))
        if cleanup_errors:
            problem = OSError("; ".join(cleanup_errors))
            if primary_error is None:
                raise problem
            primary_error.add_note(f"GDI cleanup errors: {problem}")
