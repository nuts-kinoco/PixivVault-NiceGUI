"""Verify an owned native window's real pixels after minimize and restore.

The existing ShowWindow(6/9) path is intentional. This gate never performs a
rescue cycle, resizes the application, or moves a WebView child.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from pathlib import Path
import time

from PIL import ImageStat

if __package__:
    from . import benchmark_native_pixels as pixels
else:
    import benchmark_native_pixels as pixels


INTERVAL = 0.15
REQUIRED_STABLE_SAMPLES = 2
TARGET_CLASS = "Chrome_RenderWidgetHostHWND"


class GateFailure(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def ensure_physical_dpi() -> None:
    """Verify per-monitor v2 on this thread before reading physical coordinates."""
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user.SetProcessDpiAwarenessContext.restype = w.BOOL
        user.GetThreadDpiAwarenessContext.argtypes = []
        user.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user.AreDpiAwarenessContextsEqual.restype = w.BOOL
        expected = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        # False may mean awareness was already configured. Verify the effective context.
        user.SetProcessDpiAwarenessContext(expected)
        current = user.GetThreadDpiAwarenessContext()
        if not current or not user.AreDpiAwarenessContextsEqual(current, expected):
            raise GateFailure("dpi_v2_unavailable")
    except GateFailure:
        raise
    except Exception as exc:
        raise GateFailure("dpi_v2_unavailable") from exc


class Win32Window:
    """Pin the original root, top HWND, and one visible WebView render HWND."""

    def __init__(self, root: int, root_birth: int, top: int):
        self.root, self.root_birth, self.top = root, root_birth, top
        self.kernel, self.user = pixels.api()
        self.user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.restype = ctypes.c_int
        self.user.GetDpiForWindow.argtypes = [w.HWND]
        self.user.GetDpiForWindow.restype = w.UINT
        self.user.IsChild.argtypes = [w.HWND, w.HWND]
        self.user.IsChild.restype = w.BOOL
        self.user.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, w.UINT]
        self.user.SetWindowPos.restype = w.BOOL
        self.user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        self.user.ShowWindow.restype = w.BOOL
        self.top_identity = self.identity(top)
        if not pixels.is_descendant(self.top_identity[0], root, pixels.process_parents()):
            raise GateFailure("top_outside_owned_tree")
        self.title = self.title_now()
        self.target = None
        self.target_identity = None
        self.window = None
        self.verify_top()

    def identity(self, hwnd: int) -> tuple[int, int]:
        if not self.user.IsWindow(hwnd):
            raise GateFailure("window_missing")
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
            raise GateFailure("window_owner_unknown")
        birth = pixels.process_birth(self.kernel, int(pid.value))
        if birth is None:
            raise GateFailure("window_birth_unknown")
        return int(pid.value), birth

    def title_now(self) -> str:
        length = self.user.GetWindowTextLengthW(self.top)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user.GetWindowTextW(self.top, buffer, len(buffer))
        return buffer.value

    def verify_top(self) -> None:
        if pixels.process_birth(self.kernel, self.root) != self.root_birth:
            raise GateFailure("root_identity_changed")
        if self.identity(self.top) != self.top_identity:
            raise GateFailure("top_identity_changed")
        if self.title_now() != self.title:
            raise GateFailure("top_title_changed")

    def verify_all(self) -> None:
        self.verify_top()
        if self.target is None or self.target_identity is None:
            raise GateFailure("target_missing")
        if self.identity(self.target) != self.target_identity:
            raise GateFailure("target_identity_changed")
        if not self.user.IsChild(self.top, self.target):
            raise GateFailure("target_reparented")
        self.verify_top()

    def topmost(self) -> bool:
        self.verify_top()
        ctypes.set_last_error(0)
        style = self.user.GetWindowLongW(self.top, -20)
        if style == 0 and ctypes.get_last_error():
            raise GateFailure("topmost_state_unknown")
        self.verify_top()
        return bool(style & 0x8)

    def set_topmost(self, enabled: bool) -> None:
        # A broken child must not prevent restoring our original top's z-order flag.
        self.verify_top()
        insert_after = -1 if enabled else -2
        if not self.user.SetWindowPos(self.top, insert_after, 0, 0, 0, 0, 0x0013):
            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            raise GateFailure("topmost_set_failed")
        if self.topmost() != bool(enabled):
            raise GateFailure("topmost_verification_failed")

    def state(self) -> str:
        self.verify_top()
        if self.user.IsIconic(self.top):
            state = "minimized"
        elif self.user.IsWindowVisible(self.top):
            state = "normal"
        else:
            state = "hidden"
        self.verify_top()
        return state

    def show(self, command: int) -> None:
        self.verify_top()
        # The result describes previous visibility, not success.
        self.user.ShowWindow(self.top, command)
        self.verify_top()

    def prepare(self) -> None:
        self.verify_top()
        candidates = []
        errors = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

        def visit(hwnd, _parameter):
            try:
                if self.user.IsWindowVisible(hwnd):
                    name = ctypes.create_unicode_buffer(256)
                    if not self.user.GetClassNameW(hwnd, name, len(name)):
                        raise GateFailure("child_class_unknown")
                    if name.value == TARGET_CLASS:
                        candidates.append(int(hwnd))
                return True
            except Exception as exc:
                # ctypes callbacks otherwise print and swallow Python errors.
                errors.append(exc)
                return False

        callback = callback_type(visit)
        self.user.EnumChildWindows.argtypes = [w.HWND, callback_type, w.LPARAM]
        self.user.EnumChildWindows.restype = w.BOOL
        self.user.EnumChildWindows(self.top, callback, 0)
        if errors:
            raise errors[0]
        self.verify_top()
        if not candidates:
            raise GateFailure("target_missing")
        if len(candidates) != 1:
            raise GateFailure("target_ambiguous")
        self.target = candidates[0]
        self.target_identity = self.identity(self.target)
        if not pixels.is_descendant(self.target_identity[0], self.root, pixels.process_parents()):
            raise GateFailure("target_outside_owned_tree")
        self.verify_all()
        owner = self

        class PinnedWindow(pixels.OwnedWindow):
            def verify(self):
                # Include the first capture performed inside OwnedWindow's constructor.
                owner.verify_all()
                super().verify()

        size = self.client(self.top)
        self.window = PinnedWindow(self.root, self.top, self.target, self.title,
                                   [0, 0], [0, 0], [0, 0, *size], capture_mode="screen-gdi")

    def client(self, hwnd: int) -> tuple[int, int]:
        bounds = w.RECT()
        if not self.user.GetClientRect(hwnd, ctypes.byref(bounds)):
            raise GateFailure("client_geometry_unknown")
        size = bounds.right - bounds.left, bounds.bottom - bounds.top
        if size[0] <= 0 or size[1] <= 0:
            raise GateFailure("client_geometry_unknown")
        return size

    def origin(self, hwnd: int) -> tuple[int, int]:
        point = w.POINT(0, 0)
        if not self.user.ClientToScreen(hwnd, ctypes.byref(point)):
            raise GateFailure("client_origin_unknown")
        return point.x, point.y

    def geometry(self) -> dict:
        self.verify_all()
        top_client, target_client = self.client(self.top), self.client(self.target)
        top_origin, target_origin = self.origin(self.top), self.origin(self.target)
        top_dpi = int(self.user.GetDpiForWindow(self.top))
        target_dpi = int(self.user.GetDpiForWindow(self.target))
        if top_dpi <= 0 or target_dpi <= 0:
            raise GateFailure("window_dpi_unknown")
        offset = target_origin[0] - top_origin[0], target_origin[1] - top_origin[1]
        x, y = offset
        width, height = target_client
        geometry = {
            "top_client": top_client,
            "top_origin": top_origin,
            "top_dpi": top_dpi,
            "target_offset": offset,
            "target_client": target_client,
            "target_dpi": target_dpi,
            "target_visible": bool(self.user.IsWindowVisible(self.target)),
            "target_intersects_client": bool(x < top_client[0] and y < top_client[1]
                                              and x + width > 0 and y + height > 0),
        }
        self.verify_all()
        return geometry

    def capture(self):
        if self.window is None:
            raise GateFailure("capture_not_prepared")
        return self.window.capture()

    def record(self) -> dict:
        self.verify_all()
        return {
            "root_pid": self.root, "root_birth": self.root_birth,
            "top_hwnd": self.top, "top_pid": self.top_identity[0],
            "top_birth": self.top_identity[1], "target_hwnd": self.target,
            "target_pid": self.target_identity[0], "target_birth": self.target_identity[1],
            "target_class": TARGET_CLASS,
        }


def _before(deadline: float) -> None:
    if not time.monotonic() < deadline:
        raise GateFailure("deadline_expired")


def _pause(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(INTERVAL, remaining))


def _stable(window, deadline: float, phase: str, report: dict,
            expected_geometry: dict | None = None, template=None):
    """Require consecutive, nonflat full-client samples before the deadline."""
    last_image = last_geometry = None
    consecutive = 0
    reason = "baseline_unstable" if phase == "baseline" else "restore_scene_mismatch"
    while time.monotonic() < deadline:
        if window.state() != "normal":
            reason = phase + "_state_not_restored"
            consecutive = 0
            _pause(deadline)
            continue
        if phase == "restore":
            report["restored_os_state"] = True
        try:
            geometry = window.geometry()
            report[phase + "_last_geometry"] = geometry
            if (not geometry["target_visible"] or not geometry["target_intersects_client"]
                    or (expected_geometry is not None and geometry != expected_geometry)):
                reason = phase + "_geometry_mismatch"
                consecutive = 0
                _pause(deadline)
                continue
            image = window.capture()
            after_geometry = window.geometry()
        except GateFailure as exc:
            if phase != "restore" or exc.reason != "target_reparented":
                raise
            # WebView2 can temporarily detach this same HWND while restoring.
            # Every attempt still checks PID/birth first. No sample detected as
            # detached is accepted or saved, and the interrupted pair is discarded.
            reason = "restore_target_reparented"
            report["restore_reparent_observations"] += 1
            consecutive = 0
            last_image = last_geometry = None
            _pause(deadline)
            continue
        _before(deadline)
        if after_geometry != geometry:
            reason = phase + "_geometry_unstable"
            consecutive = 0
        elif image.size != geometry["top_client"]:
            raise GateFailure(phase + "_capture_size_mismatch")
        elif max(ImageStat.Stat(image.convert("RGB")).stddev) < 2:
            raise GateFailure(phase + "_flat_capture")
        elif template is not None and pixels.mean_diff(image, template) > pixels.STABLE_DIFF:
            reason = "restore_scene_mismatch"
            consecutive = 0
        else:
            reason = phase + "_unstable"
            if (last_geometry == geometry and last_image is not None
                    and pixels.mean_diff(image, last_image) <= pixels.STABLE_DIFF):
                consecutive += 1
            else:
                consecutive = 1
            if consecutive >= REQUIRED_STABLE_SAMPLES:
                _before(deadline)
                report[phase + "_stable_samples"] = consecutive
                return image, geometry
        last_image, last_geometry = image, geometry
        _pause(deadline)
    raise GateFailure(reason)


def _save_png(image, path: Path) -> None:
    with path.open("xb") as stream:
        image.save(stream, format="PNG")


def run_restore_gate(root: int, root_birth: int, top: int, output: Path,
                     deadline: float, limits: dict[str, float], *,
                     expected_top_identity: tuple[int, int] | None = None) -> dict:
    output = Path(output)
    before_path = output.with_name(output.stem + "-restore-before.png")
    after_path = output.with_name(output.stem + "-restore-after.png")
    report = {
        "passed": False, "minimized": False, "restored_os_state": False,
        "topmost_restored": False, "capture_mode": "screen-gdi",
        "pixel_mean_diff_limit": float(pixels.STABLE_DIFF),
        "required_stable_samples": REQUIRED_STABLE_SAMPLES,
        "sample_interval_seconds": INTERVAL,
        "baseline_stable_samples": 0, "restore_stable_samples": 0,
        "restore_reparent_observations": 0, "errors": [],
    }
    window = None
    original_topmost = None
    stage = "baseline"
    accepted = False
    try:
        _before(deadline)
        if before_path.exists() or after_path.exists():
            raise GateFailure("restore_artifact_exists")
        ensure_physical_dpi()
        window = Win32Window(root, root_birth, top)
        if expected_top_identity is not None and window.top_identity != expected_top_identity:
            raise GateFailure("top_identity_changed")
        original_topmost = window.topmost()
        report["original_topmost"] = original_topmost
        # Cleanup is armed before mutation, including partial SetWindowPos failures.
        window.set_topmost(True)
        baseline_deadline = min(deadline, time.monotonic() + limits["restore_baseline"])
        window.prepare()
        _before(baseline_deadline)
        report["window"] = window.record()
        template, baseline_geometry = _stable(window, baseline_deadline, "baseline", report)
        _save_png(template, before_path)
        report["baseline_geometry"] = baseline_geometry
        report["before_png"] = str(before_path)
        report["template_sha"] = pixels.sha(template)
        _before(baseline_deadline)

        stage = "minimize"
        _before(deadline)
        minimize_deadline = min(deadline, time.monotonic() + limits["minimize"])
        window.show(6)
        while time.monotonic() < minimize_deadline:
            state = window.state()
            _before(minimize_deadline)
            if state == "minimized":
                report["minimized"] = True
                break
            _pause(minimize_deadline)
        if not report["minimized"]:
            raise GateFailure("minimize_timeout")

        stage = "restore"
        restore_deadline = min(deadline, time.monotonic() + limits["restore"])
        _before(restore_deadline)
        window.show(9)
        restored, restored_geometry = _stable(window, restore_deadline, "restore", report,
                                              expected_geometry=baseline_geometry, template=template)
        _save_png(restored, after_path)
        report["restored_geometry"] = restored_geometry
        report["after_png"] = str(after_path)
        report["restored_sha"] = pixels.sha(restored)
        report["restored_mean_diff"] = pixels.mean_diff(restored, template)
        _before(restore_deadline)
        accepted = True
    except Exception as exc:
        report["errors"].append(exc.reason if isinstance(exc, GateFailure) else stage + "_error")
        report["error_type"] = type(exc).__name__
    finally:
        if window is not None and original_topmost is not None:
            try:
                window.set_topmost(original_topmost)
                report["topmost_restored"] = True
            except Exception as exc:
                report["errors"].append("topmost_restore_failed")
                report["topmost_restore_error_type"] = type(exc).__name__
    if accepted and not time.monotonic() < deadline:
        report["errors"].append("deadline_expired")
    report["passed"] = bool(accepted and report["topmost_restored"] and not report["errors"])
    return report
