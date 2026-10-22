"""Contract tests with fake Win32 calls: never capture the real desktop."""
import ctypes
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "native_screen_capture", Path(__file__).resolve().parents[1] / "tools" / "native_screen_capture.py"
)
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class FakeGDI:
    def __init__(self, fail=None):
        self.fail = fail
        self.log = []
        self.buffer = None
        self.bitmap_selected = False
        self.dc_alive = False

    def CreateCompatibleDC(self, dc):
        self.log.append("create_dc")
        self.dc_alive = self.fail != "create_dc"
        return 0 if self.fail == "create_dc" else 0x100000001

    def CreateDIBSection(self, dc, info_ptr, usage, bits_ptr, section, offset):
        self.log.append("create_dib")
        if self.fail == "create_dib":
            return 0
        info = ctypes.cast(info_ptr, ctypes.POINTER(capture.BITMAPINFO)).contents
        assert (info.bmiHeader.biWidth, info.bmiHeader.biHeight) == (2, -2)
        self.buffer = ctypes.create_string_buffer(bytes([
            3, 2, 1, 0, 6, 5, 4, 0,
            9, 8, 7, 0, 12, 11, 10, 0,
        ]))
        ctypes.cast(bits_ptr, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(self.buffer)
        return 0x100000002

    def SelectObject(self, dc, obj):
        if obj == 0x100000002:
            self.log.append("select")
            if self.fail == "select":
                return 0
            self.bitmap_selected = True
            return 0x100000003
        self.log.append("restore")
        if self.fail == "restore_none":
            return None
        if self.fail in ("restore_zero", "restore_error", "blit_restore_zero"):
            return -1 if self.fail == "restore_error" else 0
        self.bitmap_selected = False
        return 0x100000002

    def BitBlt(self, dest, x, y, width, height, source, left, top, rop):
        self.log.append("blit")
        assert (left, top, width, height) == (-2, 0, 2, 2)
        assert rop == 0x40CC0020
        return self.fail not in ("blit", "blit_restore_zero")

    def GdiFlush(self):
        self.log.append("flush")
        return self.fail != "flush"

    def DeleteObject(self, obj):
        assert not self.bitmap_selected or not self.dc_alive
        self.log.append("delete_bitmap")
        return self.fail != "delete_bitmap"

    def DeleteDC(self, dc):
        self.log.append("delete_dc")
        if self.fail == "delete_dc":
            return False
        self.dc_alive = False
        self.bitmap_selected = False
        return True


class FakeUser:
    def __init__(self, log, fail):
        self.log, self.fail = log, fail

    def GetSystemMetrics(self, index):
        return {76: -2, 77: 0, 78: 4, 79: 4}[index]

    def GetDC(self, hwnd):
        self.log.append("get_dc")
        return 0 if self.fail == "get_dc" else 0x100000004

    def ReleaseDC(self, hwnd, dc):
        self.log.append("release_dc")
        return 0 if self.fail == "release_dc" else 1


@pytest.mark.parametrize("failure", [None, "get_dc", "create_dc", "create_dib",
                                    "select", "blit", "flush"])
def test_capture_and_cleanup(monkeypatch, failure):
    fake = FakeGDI(failure)
    user = FakeUser(fake.log, failure)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "_api", lambda: (user, fake))
    if failure:
        with pytest.raises(OSError, match="failed"):
            capture.capture_screen_box((-2, 0, 0, 2))
    else:
        image = capture.capture_screen_box((-2, 0, 0, 2))
        assert image.mode == "RGB"
        assert image.size == (2, 2)
        assert [image.getpixel((x, y)) for y in range(2) for x in range(2)] == [
            (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
        fake.buffer[0] = 99
        assert image.getpixel((0, 0)) == (1, 2, 3)
    assert fake.log[-1] == ("release_dc" if failure != "get_dc" else "get_dc")
    if failure not in ("get_dc", "create_dc"):
        assert "delete_dc" in fake.log
    if failure not in ("get_dc", "create_dc", "create_dib"):
        assert "delete_bitmap" in fake.log
    if failure in (None, "blit", "flush"):
        assert fake.log.index("restore") < fake.log.index("delete_bitmap")


@pytest.mark.parametrize("box", [
    (0, 0, 0, 2), (0, 0, 2, 0), (-3, 0, 0, 2), (-2, 0, 3, 2),
    (-2, -1, 0, 2), (-2, 0, 0, 5), (-2, 0, True, 2),
    (-2, 0, 2.0, 2), (0, 0, 2), (-(2**31)-1, 0, 0, 2),
])
def test_invalid_bounds(box):
    with pytest.raises(ValueError):
        capture._validate(box, lambda index: {76: -2, 77: 0, 78: 4, 79: 4}[index])


def test_linux_import_and_windows_only(monkeypatch):
    monkeypatch.setattr(capture.sys, "platform", "linux")
    with pytest.raises(OSError, match="Windows"):
        capture.capture_screen_box((-2, 0, 0, 2))


@pytest.mark.parametrize("failure", [
    "restore_zero", "restore_error", "restore_none", "delete_bitmap", "delete_dc", "release_dc"
])
def test_cleanup_failures_are_reported_after_all_releases(monkeypatch, failure):
    fake = FakeGDI(failure)
    user = FakeUser(fake.log, failure)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "_api", lambda: (user, fake))
    with pytest.raises(OSError) as caught:
        capture.capture_screen_box((-2, 0, 0, 2))
    assert failure.split("_")[0].lower() in str(caught.value).lower() or (
        failure.startswith("restore") and "SelectObject restore" in str(caught.value))
    assert fake.log.count("delete_bitmap") == 1
    assert fake.log.count("delete_dc") == 1
    assert fake.log.count("release_dc") == 1
    if failure.startswith("restore"):
        assert fake.log.index("delete_dc") < fake.log.index("delete_bitmap")
    else:
        assert fake.log.index("restore") < fake.log.index("delete_bitmap") < fake.log.index("delete_dc")


def test_primary_error_kept_when_restore_fails(monkeypatch):
    fake = FakeGDI("blit_restore_zero")
    user = FakeUser(fake.log, fake.fail)
    monkeypatch.setattr(capture.sys, "platform", "win32")
    monkeypatch.setattr(capture, "_api", lambda: (user, fake))
    with pytest.raises(OSError, match="BitBlt failed") as caught:
        capture.capture_screen_box((-2, 0, 0, 2))
    assert any("SelectObject restore failed" in note for note in caught.value.__notes__)
    assert fake.log.index("delete_dc") < fake.log.index("delete_bitmap")
