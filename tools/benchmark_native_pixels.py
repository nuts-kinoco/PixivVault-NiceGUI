"""Window-scoped, human-approved pixel transition benchmark for Flet and WebView2."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

from PIL import Image, ImageChops, ImageGrab, ImageStat

if __package__:
    from .native_screen_capture import capture_screen_box
else:
    from native_screen_capture import capture_screen_box

THRESHOLD = 12.0  # ROI mean absolute RGB difference; fixed for both backends
STABLE_DIFF = 1.0
TIMEOUT = 2.5


def percentile(values: list[float], percentage: float) -> float:
    if not values or not 0 <= percentage <= 100:
        raise ValueError("nonempty values and percentile 0..100 required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def rect(values: list[int], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, w, h = values
    if x < 0 or y < 0 or w < 1 or h < 1 or x + w > width or y + h > height:
        raise ValueError("ROI outside window capture bounds")
    return x, y, x + w, y + h


def point(values: list[int], width: int, height: int) -> tuple[int, int]:
    x, y = values
    if x < 0 or y < 0 or x >= width or y >= height:
        raise ValueError("click outside client bounds")
    return x, y


def mean_diff(left: Image.Image, right: Image.Image) -> float:
    if left.size != right.size:
        raise ValueError("image size mismatch")
    return statistics.mean(ImageStat.Stat(ImageChops.difference(left.convert("RGB"), right.convert("RGB"))).mean)


def meaningful_templates(images: list[Image.Image]) -> bool:
    if len(images) != 3 or any(image.size != images[0].size for image in images):
        return False
    if any(max(ImageStat.Stat(image.convert("RGB")).stddev) < 2 for image in images):
        return False
    return all(mean_diff(images[i], images[j]) >= THRESHOLD
               for i in range(3) for j in range(i + 1, 3))


def sha(image: Image.Image) -> str:
    return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def checked_screen_bbox(bbox, virtual):
    left, top, right, bottom = bbox
    vl, vt, vr, vb = virtual
    if left >= right or top >= bottom or left < vl or top < vt or right > vr or bottom > vb:
        error = RuntimeError("screen client bbox invalid or outside virtual screen")
        error.capture_probe = {"screen_bbox": list(bbox), "virtual_screen": list(virtual),
                               "reason": "outside_virtual_screen"}
        raise error
    return tuple(bbox)


def sample_points(bbox):
    l, t, r, b = bbox
    return [(l + (r-l-1)*x//2, t + (b-t-1)*y//2)
            for y in range(3) for x in range(3)]


def api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.restype = ctypes.c_int
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetClientRect.restype = wintypes.BOOL
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.ClientToScreen.restype = wintypes.BOOL
    user.GetSystemMetrics.argtypes = [ctypes.c_int]
    user.GetSystemMetrics.restype = ctypes.c_int
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.IsIconic.argtypes = [wintypes.HWND]
    user.IsIconic.restype = wintypes.BOOL
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    user.WindowFromPoint.argtypes = [wintypes.POINT]
    user.WindowFromPoint.restype = wintypes.HWND
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindow.restype = wintypes.BOOL
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    return kernel, user


def process_birth(kernel, pid: int) -> int | None:
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def process_parents() -> dict[int, int]:
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = kernel.CreateToolhelp32Snapshot(2, 0)
    if snap == ctypes.c_void_p(-1).value:
        raise OSError("Cannot enumerate process ancestry")
    result = {}
    try:
        item = Entry()
        item.size = ctypes.sizeof(item)
        if not kernel.Process32FirstW(snap, ctypes.byref(item)):
            raise OSError("Process32FirstW failed")
        while True:
            result[int(item.pid)] = int(item.parent)
            ctypes.set_last_error(0)
            if not kernel.Process32NextW(snap, ctypes.byref(item)):
                if ctypes.get_last_error() != 18:
                    raise OSError("Process32NextW failed")
                break
    finally:
        kernel.CloseHandle(snap)
    return result


def is_descendant(pid: int, root: int, parents: dict[int, int]) -> bool:
    if root not in parents or pid not in parents:
        return False
    visited = set()
    while pid not in visited and pid in parents:
        if pid == root:
            return True
        visited.add(pid)
        pid = parents[pid]
    return False


class OwnedWindow:
    def __init__(self, root: int, top: int, input_hwnd: int, title: str,
                 previous: list[int], next_: list[int], roi: list[int], input_interval_ms: float = 600,
                 capture_mode: str = "window"):
        if capture_mode not in ("window", "screen", "screen-gdi"):
            raise ValueError("invalid capture mode")
        self.capture_mode = capture_mode
        self.kernel, self.user = api()
        self.input_interval_ms = input_interval_ms
        self.last_input_at = None
        self.root, self.top, self.input = root, top, input_hwnd
        self.root_birth = process_birth(self.kernel, root)
        if self.root_birth is None:
            raise RuntimeError("root PID not accessible")
        self.expected_title = title
        self.identities = {}
        self.window_pids = {}
        self._verify_initial()
        self.size = self.client_size(self.top)
        self.input_size = self.client_size(self.input)
        self.screen_bbox = None
        if capture_mode in ("screen", "screen-gdi"):
            self._adopt_descendants()
            self.screen_bbox = self._screen_geometry()
        self.previous = point(previous, *self.input_size)
        self.next = point(next_, *self.input_size)
        self.roi = rect(roi, *self.size)
        self.capture()

    def _pid(self, hwnd: int) -> int:
        if not self.user.IsWindow(hwnd):
            raise RuntimeError("window handle no longer valid")
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _title(self) -> str:
        length = self.user.GetWindowTextLengthW(self.top)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user.GetWindowTextW(self.top, buffer, len(buffer))
        return buffer.value

    def _verify_initial(self):
        parents = process_parents()
        for hwnd in (self.top, self.input):
            pid = self._pid(hwnd)
            if not is_descendant(pid, self.root, parents):
                raise RuntimeError("window PID outside requested root process tree")
            birth = process_birth(self.kernel, pid)
            if birth is None or birth < self.root_birth:
                raise RuntimeError("window PID identity unavailable or older than root")
            self.identities[pid] = birth
            self.window_pids[hwnd] = pid
        if self._title() != self.expected_title:
            raise RuntimeError("top-level title mismatch")

    def verify(self):
        if process_birth(self.kernel, self.root) != self.root_birth:
            raise RuntimeError("root PID changed")
        if self._title() != self.expected_title:
            raise RuntimeError("window title changed")
        for hwnd in (self.top, self.input):
            pid = self._pid(hwnd)
            birth = process_birth(self.kernel, pid)
            if (pid != self.window_pids.get(hwnd) or pid not in self.identities
                    or birth is None or self.identities[pid] != birth):
                raise RuntimeError("window PID identity changed")
        if self.client_size(self.top) != self.size or self.client_size(self.input) != self.input_size:
            raise RuntimeError("viewport size changed")

    def client_size(self, hwnd):
        bounds = wintypes.RECT()
        if not self.user.GetClientRect(hwnd, ctypes.byref(bounds)):
            raise OSError("GetClientRect failed")
        return bounds.right - bounds.left, bounds.bottom - bounds.top

    def _adopt_descendants(self):
        parents = process_parents()
        if self.root not in parents:
            raise RuntimeError("root missing from process snapshot")
        for pid in parents:
            if is_descendant(pid, self.root, parents):
                birth = process_birth(self.kernel, pid)
                if birth is None or birth < self.root_birth:
                    raise RuntimeError("descendant PID birth unavailable")
                self.identities[pid] = birth

    def _screen_geometry(self):
        if not self.user.IsWindowVisible(self.top) or self.user.IsIconic(self.top):
            raise RuntimeError("screen window hidden or minimized")
        if not (self.user.GetWindowLongW(self.top, -20) & 0x8):
            raise RuntimeError("screen capture requires WS_EX_TOPMOST")
        origin = wintypes.POINT(0, 0)
        if not self.user.ClientToScreen(self.top, ctypes.byref(origin)):
            raise OSError("ClientToScreen failed")
        w, h = self.client_size(self.top)
        virtual = (self.user.GetSystemMetrics(76), self.user.GetSystemMetrics(77),
                   self.user.GetSystemMetrics(76) + self.user.GetSystemMetrics(78),
                   self.user.GetSystemMetrics(77) + self.user.GetSystemMetrics(79))
        return checked_screen_bbox((origin.x, origin.y, origin.x+w, origin.y+h), virtual)

    def _check_screen(self):
        self.verify()
        if self._screen_geometry() != self.screen_bbox:
            raise RuntimeError("screen client moved or resized")
        parents = process_parents()
        if self.root not in parents:
            raise RuntimeError("root missing from process snapshot")
        for x, y in sample_points(self.screen_bbox):
            hwnd = self.user.WindowFromPoint(wintypes.POINT(x, y))
            pid = self._pid(hwnd)
            birth = process_birth(self.kernel, pid)
            if (pid not in self.identities or birth is None or birth != self.identities[pid]
                    or not is_descendant(pid, self.root, parents)):
                error = RuntimeError("screen client occluded or PID identity unknown")
                # Record identity/geometry only, never a foreign title or image.
                error.capture_probe = {
                    "point": [x, y], "hwnd": int(hwnd or 0), "pid": pid,
                    "known_pid": pid in self.identities,
                    "birth_matches": birth is not None and birth == self.identities.get(pid),
                    "descendant": is_descendant(pid, self.root, parents),
                    "screen_bbox": list(self.screen_bbox),
                    "owned_top_hwnd": int(self.top), "owned_target_hwnd": int(self.input),
                }
                raise error

    def click(self, where: tuple[int, int], on_send=None):
        # Pace independent actions outside the timed interval. A zero interval
        # remains available to reproduce the unpaced input stress failure.
        if self.last_input_at is not None:
            remaining = self.input_interval_ms / 1000 - (time.monotonic() - self.last_input_at)
            if remaining > 0:
                time.sleep(remaining)
        self.verify()
        point(where, *self.input_size)
        x, y = where
        lparam = (y << 16) | (x & 0xffff)
        if on_send is not None:
            on_send()
        for message, flags in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
            if not self.user.PostMessageW(self.input, message, flags, lparam):
                raise OSError("PostMessage failed")
        self.last_input_at = time.monotonic()

    def capture(self) -> Image.Image:
        if self.capture_mode in ("screen", "screen-gdi"):
            self._check_screen()
            image = (capture_screen_box(self.screen_bbox) if self.capture_mode == "screen-gdi"
                     else ImageGrab.grab(bbox=self.screen_bbox, all_screens=True))
            self._check_screen()
        else:
            self.verify()
            image = ImageGrab.grab(window=self.top)
        if image is None or image.size != self.size:
            raise RuntimeError("window capture differs from GetClientRect; offset unknown")
        if self.capture_mode in ("screen", "screen-gdi") and max(ImageStat.Stat(image.convert("RGB")).stddev) < 2:
            raise RuntimeError("flat screen capture rejected")
        return image.convert("RGB")

    def crop(self) -> Image.Image:
        return self.capture().crop(self.roi)


def settled(window: OwnedWindow, timeout: float = TIMEOUT,
            previous: Image.Image | None = None) -> Image.Image:
    until = time.monotonic() + timeout
    last = None
    same = 0
    while time.monotonic() < until:
        full = window.capture()
        image = full.crop(window.roi)
        image._full_frame = full
        if previous is not None and mean_diff(previous, image) < THRESHOLD:
            same = 0
            last = image
            time.sleep(0.06)
            continue
        if last is not None and mean_diff(last, image) <= STABLE_DIFF:
            same += 1
            if same >= 2:
                return image
        else:
            same = 0
        last = image
        time.sleep(0.06)
    raise TimeoutError("two consecutive stable captures not observed")


def metadata(window, backend: str, extra: dict) -> dict:
    return {"backend": backend, "root_pid": window.root, "root_birth": window.root_birth,
            "top_hwnd": window.top, "input_hwnd": window.input,
            "identities": {str(k): v for k, v in window.identities.items()},
            "title": window.expected_title, "client_size": list(window.size),
            "capture_mode": window.capture_mode,
            "screen_bbox": list(window.screen_bbox) if window.screen_bbox else None,
            "input_client_size": list(window.input_size),
            "input_interval_ms": window.input_interval_ms,
            "roi": list(window.roi), "previous": list(window.previous), "next": list(window.next),
            "threshold": THRESHOLD, "extra": extra}


def calibrate(window, directory: Path, backend: str, extra: dict) -> dict:
    # Prime starts on page 2. Two previous clicks reach page 0.
    current = settled(window)
    for _ in range(2):
        window.click(window.previous)
        current = settled(window, previous=current)
    frames = [current]
    for _ in range(2):
        window.click(window.next)
        current = settled(window, previous=current)
        frames.append(current)
    if not meaningful_templates(frames):
        raise RuntimeError("black/indistinguishable calibration templates")
    # Capture full frames at each settled page, requiring pixel-identical ROI.
    # A fresh full capture may contain animation; do not silently accept it.
    full_frames = []
    for frame in frames:
        # frames are captured in order below by the settled() capture path.
        if not hasattr(frame, "_full_frame"):
            raise RuntimeError("settled frame does not retain full capture")
        full = frame._full_frame
        if sha(full.crop(window.roi)) != sha(frame):
            raise RuntimeError("full-frame ROI mismatch")
        full_frames.append(full)
    directory.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        frame.save(directory / f"template-{i}.png")
        full_frames[i].save(directory / f"frame-{i}.png")
    record = metadata(window, backend, extra)
    record.update({"status": "pending_human_review", "templates": [sha(f) for f in frames],
                   "full_frames": [sha(f) for f in full_frames],
                   "calibration_approved": False})
    (directory / "calibration.json").write_text(json.dumps(record, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    return record


def validate_calibration(window, directory: Path, backend: str, extra: dict) -> list[Image.Image]:
    record = json.loads((directory / "calibration.json").read_text(encoding="utf-8"))
    expected = metadata(window, backend, extra)
    for key, value in expected.items():
        if record.get(key) != value:
            raise RuntimeError(f"calibration metadata mismatch: {key}")
    templates = [Image.open(directory / f"template-{i}.png").convert("RGB") for i in range(3)]
    full_frames = [Image.open(directory / f"frame-{i}.png").convert("RGB") for i in range(3)]
    if ([sha(f) for f in full_frames] != record.get("full_frames")
            or any(f.size != window.size or sha(f.crop(window.roi)) != sha(t)
                   for f, t in zip(full_frames, templates))):
        raise RuntimeError("full-frame calibration mismatch")
    if [sha(image) for image in templates] != record["templates"] or not meaningful_templates(templates):
        raise RuntimeError("calibration image/hash invalid")
    return templates


def capture_overhead(window, count: int = 10) -> list[float]:
    values = []
    for _ in range(count):
        start = time.perf_counter_ns()
        window.crop()
        values.append((time.perf_counter_ns() - start) / 1_000_000)
    return values


def transition(window, point_xy, expected: Image.Image, other: Image.Image) -> dict:
    # Timer begins immediately before the first native message (WM_MOUSEMOVE).
    started = []
    window.click(point_xy, on_send=lambda: started.append(time.perf_counter_ns()))
    start = started[0]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        capture = window.crop()
        if mean_diff(capture, expected) <= STABLE_DIFF and mean_diff(capture, other) >= THRESHOLD:
            return {"ok": True, "ms": (time.perf_counter_ns() - start) / 1_000_000}
    return {"ok": False, "reason": "pixel_transition_timeout", "ms": None}


def measure(window, templates, samples: int) -> dict:
    # Reject an incorrect starting page before sending any native input.
    if not transition_observed(window, templates[2]):
        raise RuntimeError("measurement must start at calibrated page2")
    overhead = capture_overhead(window)
    # Calibration finishes at page 2, so navigate to page 0.
    for index in (1, 0):
        window.click(window.previous)
        if not transition_observed(window, templates[index]):
            raise RuntimeError("cannot return to page0")
    samples_out = []
    page = 0
    sequence = [1, 2, 1, 0]
    for n in range(samples):
        target = sequence[n % len(sequence)]
        direction = window.next if target > page else window.previous
        value = transition(window, direction, templates[target], templates[page])
        samples_out.append({"from": page, "to": target, **value})
        if not value["ok"]:
            break
        page = target
    valid = [s["ms"] for s in samples_out if s["ok"]]
    complete = len(valid) == samples
    return {"status": "passed" if complete else "failed",
            "metric": "native message to central image change observed (capture overhead included; pre-send verification excluded)",
            "samples": samples_out, "p50_ms": percentile(valid, 50) if complete else None,
            "p95_ms": percentile(valid, 95) if complete else None,
            "capture_overhead_ms": overhead,
            "capture_overhead_p50_ms": percentile(overhead, 50)}


def transition_observed(window, image):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if mean_diff(window.crop(), image) <= STABLE_DIFF:
            return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--measure", action="store_true")
    parser.add_argument("--root-pid", type=int, required=True)
    parser.add_argument("--top-hwnd", type=int, required=True)
    parser.add_argument("--input-hwnd", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--previous", type=int, nargs=2, required=True)
    parser.add_argument("--next", type=int, nargs=2, required=True)
    parser.add_argument("--roi", type=int, nargs=4, required=True)
    parser.add_argument("--backend", choices=("flet", "pywebview"), required=True)
    parser.add_argument("--capture-mode", choices=("window", "screen", "screen-gdi"), default="window")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--input-interval-ms", type=float, default=600,
                        help="Minimum gap between input batches, excluded from latency; 0 for unpaced stress")
    parser.add_argument("--output", type=Path, default=Path("artifacts/native-pixels"))
    parser.add_argument("--metadata", type=Path, required=True,
                        help="Required JSON: session_label, seed, fixture, source, dpi")
    parser.add_argument("--calibration-approved", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("Windows only")
    if args.samples <= 0:
        parser.error("--samples must be positive")
    if not math.isfinite(args.input_interval_ms) or args.input_interval_ms < 0:
        parser.error("--input-interval-ms must be finite and nonnegative")
    if args.measure and not args.calibration_approved:
        parser.error("--measure requires human-confirmed --calibration-approved")
    if args.calibrate and args.calibration_approved:
        parser.error("approval is only accepted for --measure")
    # DPI awareness must precede any Win32 size or pixel query.
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    if not user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise RuntimeError("per-monitor DPI v2 could not be set")
    extra = json.loads(args.metadata.read_text(encoding="utf-8"))
    if not isinstance(extra, dict) or any(not extra.get(k) for k in
            ("session_label", "seed", "fixture", "source", "dpi")):
        parser.error("metadata requires session_label, seed, fixture, source, dpi")
    window = OwnedWindow(args.root_pid, args.top_hwnd, args.input_hwnd,
                         args.title, args.previous, args.next, args.roi, args.input_interval_ms,
                         args.capture_mode)
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"status": "failed", "mode": "calibrate" if args.calibrate else "measure"}
    try:
        if args.calibrate:
            result = calibrate(window, args.output, args.backend, extra)
        else:
            templates = validate_calibration(window, args.output, args.backend, extra)
            result = {**metadata(window, args.backend, extra), **measure(window, templates, args.samples)}
            result["calibration_approved"] = True
    except Exception as exc:
        result["reason"] = type(exc).__name__ + ": " + str(exc)
    (args.output / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    return 0 if result["status"] in ("passed", "pending_human_review") else 1


if __name__ == "__main__":
    raise SystemExit(main())
