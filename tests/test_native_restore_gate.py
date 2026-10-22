"""Synthetic restoration gates: no processes, Win32 calls, or desktop captures."""
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from tools import native_restore_gate as gate


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


def pattern():
    image = Image.new("RGB", (32, 24))
    image.putdata([
        ((x * 7) % 256, (y * 11) % 256, ((x + y) * 5) % 256)
        for y in range(24) for x in range(32)
    ])
    return image


def baseline_geometry():
    return {
        "top_client": (32, 24),
        "top_origin": (100, 100),
        "top_dpi": 168,
        "target_offset": (0, 0),
        "target_client": (34, 26),
        "target_dpi": 168,
        "target_visible": True,
        "target_intersects_client": True,
    }


class Window:
    def __init__(self, clock, fault=None, topmost=False):
        self.clock, self.fault = clock, fault
        self.is_topmost = topmost
        self.topmost_calls = []
        self.commands = []
        self.current_state = "normal"
        self.restoring = False
        self.top_identity = (11, 1100)
        self.before_captures = self.after_captures = 0
        self.restore_geometry_reads = 0
        self.first = pattern()
        self.other = ImageChops.invert(self.first)

    def topmost(self):
        return self.is_topmost

    def set_topmost(self, value):
        self.topmost_calls.append(value)
        if len(self.topmost_calls) > 1 and self.fault == "cleanup":
            raise gate.GateFailure("topmost_verification_failed")
        if self.restoring and self.fault == "root_identity":
            raise gate.GateFailure("root_identity_changed")
        self.is_topmost = value
        if len(self.topmost_calls) == 1 and self.fault == "enter_topmost":
            raise RuntimeError("failure after changing our own topmost flag")

    def prepare(self):
        if self.fault == "prepare":
            raise RuntimeError("OwnedWindow constructor failed")

    def activate_for_capture(self):
        if self.fault == "foreground_failure":
            raise gate.GateFailure("owned_top_raise_failed")
        return self.fault != "foreground_denied"

    def state(self):
        if self.restoring and self.fault == "root_identity":
            raise gate.GateFailure("root_identity_changed")
        return self.current_state

    def show(self, command):
        self.commands.append(command)
        if command == 6 and self.fault != "minimize_timeout":
            self.current_state = "minimized"
        elif command == 9:
            self.restoring = True
            if self.fault != "restore_timeout":
                self.current_state = "normal"

    def geometry(self):
        geometry = baseline_geometry()
        if self.restoring:
            self.restore_geometry_reads += 1
            if (self.fault == "persistent_reparent"
                    or self.fault == "transient_reparent" and self.restore_geometry_reads <= 2
                    or self.fault == "reparent_after_first_frame" and self.restore_geometry_reads == 3
                    or self.fault == "reparent_after_capture" and self.restore_geometry_reads == 2
                    or self.fault == "identity_after_reparent" and self.restore_geometry_reads == 1):
                raise gate.GateFailure("target_reparented")
            if self.fault == "identity_after_reparent":
                raise gate.GateFailure("target_identity_changed")
            if self.fault == "target_identity":
                raise gate.GateFailure("target_identity_changed")
            if self.fault == "after_capture_dpi" and self.after_captures:
                geometry["target_dpi"] = 144
            changes = {
                "offscreen": {
                    "target_offset": (32816, 32896),
                    "target_intersects_client": False,
                },
                "child_size": {"target_client": (35, 26)},
                "dpi": {"target_dpi": 144},
                "top_dpi": {"top_dpi": 144},
                "moved": {"top_origin": (101, 100)},
            }
            geometry.update(changes.get(self.fault, {}))
        return geometry

    def capture(self):
        if self.restoring:
            self.after_captures += 1
            if self.fault == "late_capture":
                self.clock.now += 20
            if self.fault == "capture_error":
                raise OSError("synthetic GDI failure")
            if self.fault == "restore_flat":
                return Image.new("RGB", self.first.size, "white")
            if self.fault == "restore_scene":
                return self.other.copy()
            if self.fault == "reset_stability":
                frames = [self.first, self.other, self.first, self.first]
                return frames[min(self.after_captures - 1, 3)].copy()
        else:
            self.before_captures += 1
            if self.fault == "baseline_flat":
                return Image.new("RGB", self.first.size, "white")
            if self.fault == "baseline_unstable" and self.before_captures % 2 == 0:
                return self.other.copy()
        return self.first.copy()

    def record(self):
        return {
            "root_pid": 10, "root_birth": 1000, "top_hwnd": 100,
            "target_hwnd": 101,
        }


@pytest.fixture
def exercise(monkeypatch, tmp_path):
    def run(fault=None, topmost=False, collision=False, expected_owner=None):
        clock = Clock()
        window = Window(clock, fault, topmost)
        events = []
        output = tmp_path / "native.json"
        before = tmp_path / "native-restore-before.png"
        after = tmp_path / "native-restore-after.png"
        if collision:
            before.write_bytes(b"existing evidence")
        monkeypatch.setattr(gate, "time", clock)
        monkeypatch.setattr(gate, "ensure_physical_dpi", lambda: events.append("dpi"))

        def factory(*_args, **_kwargs):
            assert events == ["dpi"]
            events.append("window")
            return window

        monkeypatch.setattr(gate, "Win32Window", factory)
        result = gate.run_restore_gate(
            10, 1000, 100, output, clock.now + 30,
            {"restore_baseline": 5.0, "minimize": 5.0, "restore": 5.0},
            expected_top_identity=expected_owner,
        )
        return SimpleNamespace(
            result=result, window=window, clock=clock,
            events=events, before=before, after=after,
        )
    return run


@pytest.mark.parametrize("topmost", [False, True])
def test_full_client_template_and_original_topmost_are_preserved(exercise, topmost):
    run = exercise(topmost=topmost)
    assert run.result["passed"]
    assert run.result["minimized"] and run.result["restored_os_state"]
    assert run.result["baseline_stable_samples"] == 2
    assert run.result["restore_stable_samples"] == 2
    assert run.window.commands == [6, 9]
    assert run.window.topmost_calls == [True, topmost]
    assert run.window.is_topmost is topmost
    assert run.result["topmost_restored"]
    with Image.open(run.before) as before, Image.open(run.after) as after:
        assert before.size == after.size == (32, 24)
        assert gate.pixels.mean_diff(before, after) == 0
    assert run.result["baseline_last_geometry"]["target_client"] == (34, 26)
    assert run.result["restore_last_geometry"]["target_client"] == (34, 26)


@pytest.mark.parametrize("fault", ["offscreen", "child_size", "dpi", "top_dpi", "moved"])
def test_restored_flag_with_wrong_geometry_is_failure_before_gdi(exercise, fault):
    run = exercise(fault)
    assert not run.result["passed"]
    assert run.result["restored_os_state"]
    assert "restore_geometry_mismatch" in run.result["errors"]
    assert run.window.after_captures == 0
    assert run.before.exists() and not run.after.exists()
    assert run.result["topmost_restored"]


@pytest.mark.parametrize("fault,reason", [
    ("baseline_unstable", "baseline_unstable"),
    ("restore_scene", "restore_scene_mismatch"),
])
def test_unstable_baseline_and_wrong_restore_scene_are_distinct(exercise, fault, reason):
    run = exercise(fault)
    assert not run.result["passed"]
    assert reason in run.result["errors"]
    assert run.result["topmost_restored"]
    if fault == "baseline_unstable":
        assert run.window.commands == []
        assert not run.before.exists()
    else:
        assert run.window.commands == [6, 9]
        assert run.before.exists()
    assert not run.after.exists()


@pytest.mark.parametrize("fault", [
    "baseline_flat", "restore_flat", "capture_error", "target_identity",
    "minimize_timeout", "restore_timeout", "late_capture", "after_capture_dpi",
])
def test_unknown_blank_timeout_and_identity_change_never_pass(exercise, fault):
    run = exercise(fault)
    assert not run.result["passed"]
    assert run.result["errors"]
    assert run.result["topmost_restored"]
    assert not run.after.exists()
    if fault == "late_capture":
        assert "deadline_expired" in run.result["errors"]
        assert run.window.after_captures == 1


@pytest.mark.parametrize("fault", ["prepare", "enter_topmost"])
def test_constructor_and_partial_enter_failures_restore_topmost(exercise, fault):
    run = exercise(fault)
    assert not run.result["passed"]
    assert run.window.topmost_calls == [True, False]
    assert not run.window.is_topmost
    assert run.result["topmost_restored"]
    assert run.window.commands == []


@pytest.mark.parametrize("fault", ["cleanup", "root_identity"])
def test_failed_topmost_restore_is_not_success(exercise, fault):
    run = exercise(fault)
    assert not run.result["passed"]
    assert not run.result["topmost_restored"]
    assert "topmost_restore_failed" in run.result["errors"]


def test_matching_samples_must_be_consecutive(exercise):
    run = exercise("reset_stability")
    assert run.result["passed"]
    assert run.window.after_captures == 4
    assert run.result["restore_stable_samples"] == 2


def test_temporary_reparent_waits_without_capturing_or_sending_rescue_input(exercise):
    run = exercise("transient_reparent")
    assert run.result["passed"]
    assert run.result["restore_reparent_observations"] == 2
    assert run.window.after_captures == 2
    assert run.window.commands == [6, 9]


@pytest.mark.parametrize("fault", ["reparent_after_first_frame", "reparent_after_capture"])
def test_reparent_resets_consecutive_sample_count(exercise, fault):
    run = exercise(fault)
    assert run.result["passed"]
    assert run.result["restore_reparent_observations"] == 1
    assert run.window.after_captures == 3


@pytest.mark.parametrize("fault,reason", [
    ("persistent_reparent", "restore_target_reparented"),
    ("identity_after_reparent", "target_identity_changed"),
])
def test_detached_or_replaced_target_never_passes_or_captures(exercise, fault, reason):
    run = exercise(fault)
    assert not run.result["passed"]
    assert reason in run.result["errors"]
    assert run.window.after_captures == 0
    assert run.result["topmost_restored"]
    assert not run.after.exists()


def test_existing_evidence_is_never_overwritten(exercise):
    run = exercise(collision=True)
    assert not run.result["passed"]
    assert "restore_artifact_exists" in run.result["errors"]
    assert run.before.read_bytes() == b"existing evidence"
    assert not run.after.exists()
    assert run.events == []
    assert run.window.topmost_calls == []


def test_gate_rejects_a_changed_top_owner_before_mutation(exercise):
    run = exercise(expected_owner=(12, 1200))
    assert not run.result["passed"]
    assert run.result["errors"] == ["top_identity_changed"]
    assert run.window.topmost_calls == []
    assert run.window.commands == []


def test_foreground_denial_does_not_replace_real_pixel_validation(exercise):
    run = exercise("foreground_denied")
    assert run.result["owned_top_foreground"] is False
    assert run.result["passed"]
    assert run.result["baseline_stable_samples"] == 2
    assert run.result["restore_stable_samples"] == 2


def test_owned_foreground_failure_restores_original_topmost_and_fails(exercise):
    run = exercise("foreground_failure")
    assert not run.result["passed"]
    assert run.result["errors"] == ["owned_top_raise_failed"]
    assert run.result["topmost_restored"]
    assert run.window.commands == []


def test_foreground_request_only_targets_the_verified_top_window():
    window = object.__new__(gate.Win32Window)
    window.top = 101
    events = []
    window.verify_top = lambda: events.append(("verify", 101))
    window.topmost = lambda: events.append(("topmost", 101)) or True
    window.user = SimpleNamespace(
        SetWindowPos=lambda *args: events.append(("position", args)) or True,
        GetForegroundWindow=lambda: 101,
    )
    assert window.activate_for_capture()
    assert events == [("verify", 101), ("position", (101, -1, 0, 0, 0, 0, 0x0003)),
                      ("verify", 101), ("topmost", 101)]


def test_late_png_save_cannot_change_failure_to_success(monkeypatch, exercise):
    original_save = gate._save_png

    def save(image, path):
        original_save(image, path)
        if path.name.endswith("-after.png"):
            gate.time.now += 20

    monkeypatch.setattr(gate, "_save_png", save)
    run = exercise()
    assert not run.result["passed"]
    assert "deadline_expired" in run.result["errors"]
    assert run.result["topmost_restored"]
    assert run.after.exists()


def test_expired_deadline_cannot_accept_a_frame(monkeypatch):
    clock = Clock()
    window = Window(clock)
    monkeypatch.setattr(gate, "time", clock)
    with pytest.raises(gate.GateFailure):
        gate._stable(window, clock.now, "baseline", {})
    assert window.before_captures == 0


@pytest.mark.parametrize("set_result,is_v2", [(False, True), (True, False)])
def test_dpi_is_verified_instead_of_trusting_set_return(monkeypatch, set_result, is_v2):
    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)

    calls = []
    user = SimpleNamespace(
        SetProcessDpiAwarenessContext=Function(
            lambda *_: calls.append("set") or set_result),
        GetThreadDpiAwarenessContext=Function(
            lambda: calls.append("get") or -4),
        AreDpiAwarenessContextsEqual=Function(
            lambda *_: calls.append("equal") or is_v2),
    )
    monkeypatch.setattr(gate.ctypes, "WinDLL", lambda *a, **k: user, raising=False)
    if is_v2:
        gate.ensure_physical_dpi()
    else:
        with pytest.raises(gate.GateFailure):
            gate.ensure_physical_dpi()
    assert calls == ["set", "get", "equal"]
