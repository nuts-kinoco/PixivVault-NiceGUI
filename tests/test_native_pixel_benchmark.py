import pytest
from PIL import Image

from tools.benchmark_native_pixels import (
    THRESHOLD, is_descendant, meaningful_templates, mean_diff, percentile, point, rect, sha,
)
from tools import benchmark_native_pixels as benchmark


def test_percentile_interpolated():
    assert percentile([10, 20, 30, 40], 50) == 25
    assert percentile([10, 20, 30, 40], 95) == pytest.approx(38.5)
    with pytest.raises(ValueError):
        percentile([], 50)


def test_roi_and_point_bounds():
    assert rect([10, 20, 30, 40], 100, 100) == (10, 20, 40, 60)
    assert point([99, 99], 100, 100) == (99, 99)
    with pytest.raises(ValueError):
        rect([90, 10, 20, 10], 100, 100)
    with pytest.raises(ValueError):
        point([-1, 5], 100, 100)


def test_templates_reject_identical_black_and_minor_differences():
    black = Image.new("RGB", (32, 32), "black")
    assert not meaningful_templates([black] * 3)
    low = Image.new("RGB", (32, 32), (5, 5, 5))
    assert not meaningful_templates([black, low, black])
    images = []
    for offset in (0, 1, 2):
        img = Image.new("RGB", (32, 32))
        for x in range(32):
            for y in range(32):
                img.putpixel((x, y), ((x + offset * 11) * 6 % 256,
                                       (y + offset * 13) * 7 % 256, offset * 90))
        images.append(img)
    assert meaningful_templates(images)
    assert mean_diff(images[0], images[1]) > THRESHOLD
    assert sha(images[0]) != sha(images[1])


def test_process_ancestry_mocked():
    parents = {10: 1, 11: 10, 12: 11, 20: 21, 21: 20}
    assert is_descendant(12, 10, parents)
    assert not is_descendant(20, 10, parents)
    assert is_descendant(10, 10, parents)
    assert not is_descendant(10, 10, {})


def test_measure_rejects_wrong_start_before_input(monkeypatch):
    monkeypatch.setattr(benchmark, "TIMEOUT", 0.001)
    templates = [Image.new("RGB", (8, 8), color) for color in ("red", "green", "blue")]

    class Window:
        previous = (1, 1)
        clicks = []

        def crop(self):
            return templates[0]

        def click(self, point_xy, **kwargs):
            self.clicks.append(point_xy)

    window = Window()
    with pytest.raises(RuntimeError, match="start at calibrated page2"):
        benchmark.measure(window, templates, 1)
    assert window.clicks == []


@pytest.mark.parametrize("current_pid,birth", [(99, None), (99, 199), (11, None), (11, 201)])
def test_window_identity_changes_are_rejected(monkeypatch, current_pid, birth):
    window = benchmark.OwnedWindow.__new__(benchmark.OwnedWindow)
    window.kernel = object()
    window.root, window.root_birth = 10, 100
    window.top, window.input = 1000, 1001
    window.expected_title = "dedicated test"
    window.identities = {11: 200}
    window.window_pids = {1000: 11, 1001: 11}
    window.size = window.input_size = (100, 100)
    window._title = lambda: "dedicated test"
    window._pid = lambda hwnd: current_pid
    window.client_size = lambda hwnd: (100, 100)
    monkeypatch.setattr(benchmark, "process_birth", lambda kernel, pid: 100 if pid == 10 else birth)
    with pytest.raises(RuntimeError, match="window PID identity changed"):
        window.verify()


def test_failed_series_has_no_success_percentiles(monkeypatch):
    monkeypatch.setattr(benchmark, "transition_observed", lambda *args: True)
    monkeypatch.setattr(benchmark, "capture_overhead", lambda *args: [10.0])
    outcomes = iter([{"ok": True, "ms": 20.0}, {"ok": False, "reason": "pixel_transition_timeout", "ms": None}])
    monkeypatch.setattr(benchmark, "transition", lambda *args: next(outcomes))

    class Window:
        previous, next = (1, 1), (2, 2)
        def click(self, *args):
            pass

    result = benchmark.measure(Window(), [None, None, None], 30)
    assert result["status"] == "failed"
    assert result["p50_ms"] is None and result["p95_ms"] is None
    assert len(result["samples"]) == 2
    assert result["samples"][0]["ms"] == 20.0


@pytest.mark.parametrize("bbox", [(0, 0, 0, 5), (-101, 0, 5, 5),
                                     (0, -51, 5, 5), (0, 0, 101, 5)])
def test_screen_bbox_rejects_invalid_or_offscreen(bbox):
    with pytest.raises(RuntimeError, match="bbox"):
        benchmark.checked_screen_bbox(bbox, (-100, -50, 100, 100))


def test_screen_sampling_has_nine_physical_positions():
    points = benchmark.sample_points((-100, -50, 200, 250))
    assert len(points) == 9 and len(set(points)) == 9
    assert (-100, -50) in points and (199, 249) in points


def test_screen_checks_reject_occlusion_pid_failure_and_move(monkeypatch):
    class User:
        def WindowFromPoint(self, xy):
            return 50 if (xy.x, xy.y) != (5, 5) else 60
    window = benchmark.OwnedWindow.__new__(benchmark.OwnedWindow)
    window.user, window.kernel = User(), object()
    window.root, window.root_birth = 10, 100
    window.screen_bbox = (0, 0, 11, 11)
    window.identities = {10: 100, 11: 200}
    window.verify = lambda: None
    window._screen_geometry = lambda: window.screen_bbox
    window._pid = lambda hwnd: 11 if hwnd == 50 else 99
    monkeypatch.setattr(benchmark, "process_parents", lambda: {10: 1, 11: 10, 99: 1})
    monkeypatch.setattr(benchmark, "process_birth", lambda kernel, pid: {10: 100, 11: 200, 99: 300}.get(pid))
    with pytest.raises(RuntimeError, match="occluded"):
        window._check_screen()
    window._pid = lambda hwnd: 11
    window._check_screen()
    monkeypatch.setattr(benchmark, "process_birth", lambda kernel, pid: None if pid == 11 else 100)
    with pytest.raises(RuntimeError, match="occluded"):
        window._check_screen()
    window._screen_geometry = lambda: (1, 0, 12, 11)
    with pytest.raises(RuntimeError, match="moved"):
        window._check_screen()


@pytest.mark.parametrize("mode", ["screen", "screen-gdi"])
def test_screen_capture_checks_both_sides_and_rejects_blank(monkeypatch, mode):
    window = benchmark.OwnedWindow.__new__(benchmark.OwnedWindow)
    window.capture_mode = mode
    window.size, window.screen_bbox = (11, 11), (4, 5, 15, 16)
    count = []
    window._check_screen = lambda: count.append(True)
    monkeypatch.setattr(benchmark.ImageGrab, "grab", lambda **kw: Image.new("RGB", (11, 11), "white"))
    monkeypatch.setattr(benchmark, "capture_screen_box", lambda box: Image.new("RGB", (11, 11), "white"))
    with pytest.raises(RuntimeError, match="flat"):
        window.capture()
    assert len(count) == 2
    def grab(**kwargs):
        assert kwargs == {"bbox": (4, 5, 15, 16), "all_screens": True}
        return Image.new("RGB", (10, 11), "red")
    monkeypatch.setattr(benchmark.ImageGrab, "grab", grab)
    def gdi_grab(box):
        assert box == (4, 5, 15, 16)
        return Image.new("RGB", (10, 11), "red")
    monkeypatch.setattr(benchmark, "capture_screen_box", gdi_grab)
    with pytest.raises(RuntimeError, match="capture differs"):
        window.capture()


@pytest.mark.parametrize("mode", ["screen", "screen-gdi"])
@pytest.mark.parametrize("changed_at", [1, 2])
def test_screen_capture_rejects_ownership_change_before_or_after_copy(monkeypatch, mode, changed_at):
    window = benchmark.OwnedWindow.__new__(benchmark.OwnedWindow)
    window.capture_mode = mode
    window.size, window.screen_bbox = (11, 11), (4, 5, 15, 16)
    checks, copies = [], []
    def check():
        checks.append(True)
        if len(checks) == changed_at:
            raise RuntimeError("screen client occluded or PID identity unknown")
    def copy(*args, **kwargs):
        copies.append(True)
        return Image.new("RGB", (11, 11), "white")
    window._check_screen = check
    monkeypatch.setattr(benchmark.ImageGrab, "grab", copy)
    monkeypatch.setattr(benchmark, "capture_screen_box", copy)
    with pytest.raises(RuntimeError, match="occluded"):
        window.capture()
    assert len(checks) == changed_at
    assert len(copies) == changed_at - 1
