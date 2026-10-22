"""No window or process is started by these helper tests."""

import ctypes
from types import SimpleNamespace

import pytest

from tools.check_native_window import (
    PHASE_LIMITS, adopt_descendants, descendants, log_failures, matching_app_page,
    phase_deadline, valid_owned, wait_until,
)


def test_descendants_never_includes_unrelated_processes():
    pairs = [(11, 10), (12, 11), (13, 12), (80, 81), (81, 80), (21, 22)]
    assert descendants(10, pairs) == {10, 11, 12, 13}


def test_descendants_handles_unsorted_pairs_and_self_parent():
    assert descendants(42, [(45, 44), (44, 42), (42, 42)]) == {42, 44, 45}


def test_wait_until_immediate_success():
    assert wait_until(lambda: True, 0.0)


def test_wait_until_expired_failure():
    assert not wait_until(lambda: False, 0.0)


def test_phase_deadlines_obey_total_cap(monkeypatch):
    import tools.check_native_window as checker

    monkeypatch.setattr(checker.time, "monotonic", lambda: 100.0)
    assert phase_deadline(102.0, "health") == 102.0
    assert phase_deadline(200.0, "minimize") == 105.0
    assert PHASE_LIMITS["restore"] == 5.0


def test_child_adoption_checks_parent_and_creation_identity(monkeypatch):
    import tools.check_native_window as checker

    times = {10: 1000, 11: 1010, 12: 1020, 99: 500}
    monkeypatch.setattr(checker, "process_identity", lambda pid: times.get(pid))
    found = {10: 1000}
    pairs = [(12, 11), (11, 10), (99, 10)]
    adopt_descendants(10, found, pairs, False)
    assert found == {10: 1000}
    adopt_descendants(10, found, pairs, True)
    assert found == {10: 1000, 11: 1010, 12: 1020}
    times[11] = 9000  # PID reused: never counted as our live child.
    assert valid_owned(found) == {10, 12}


def test_cdp_page_must_match_local_app_port():
    assert matching_app_page("http://127.0.0.1:8089/", 8089)
    assert not matching_app_page("http://127.0.0.1:8090/", 8089)
    assert not matching_app_page("https://127.0.0.1:8089/", 8089)
    assert not matching_app_page("http://remote.test:8089/", 8089)


def test_process_log_reports_fixed_error_labels_only():
    result = log_failures("private:path Traceback\nVue failed to load\nObjectDisposedException")
    assert result == ["Traceback", "Vue failed to load", "ObjectDisposedException"]
    assert log_failures("ordinary startup and normal shutdown") == []


@pytest.fixture
def win32_failure_mock(monkeypatch):
    import tools.check_native_window as checker

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, *args):
            return self.callback(*args)

    state = {"open": 0, "wait": 0x102, "pids": [123]}
    kernel = SimpleNamespace(
        OpenProcess=FakeFunction(lambda *_: state["open"]),
        WaitForSingleObject=FakeFunction(lambda *_: state["wait"]),
        CloseHandle=FakeFunction(lambda *_: 1),
    )
    monkeypatch.setattr(checker.ctypes, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(checker.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(checker, "process_pairs", lambda: [(pid, 1) for pid in state["pids"]])
    return checker, state, kernel


def test_process_identity_denied_is_monitor_error(win32_failure_mock):
    checker, state, kernel = win32_failure_mock
    with pytest.raises(OSError, match="OpenProcess identity failed"):
        checker.process_identity(123)
    assert kernel.OpenProcess.argtypes == [checker.wintypes.DWORD, checker.wintypes.BOOL, checker.wintypes.DWORD]
    assert kernel.OpenProcess.restype == checker.wintypes.HANDLE
    state["pids"] = []
    assert checker.process_identity(123) is None


def test_living_pids_denied_vs_absent(win32_failure_mock, monkeypatch):
    checker, state, kernel = win32_failure_mock
    monkeypatch.setattr(checker, "valid_owned", lambda _: {123})
    with pytest.raises(OSError, match="OpenProcess wait failed"):
        checker.living_pids({123: 10})
    state["pids"] = []
    assert checker.living_pids({123: 10}) == set()


def test_living_pids_wait_failed_is_monitor_error(win32_failure_mock, monkeypatch):
    checker, state, kernel = win32_failure_mock
    monkeypatch.setattr(checker, "valid_owned", lambda _: {123})
    state["open"] = 100
    state["wait"] = 0xFFFFFFFF
    with pytest.raises(OSError, match="WaitForSingleObject failed"):
        checker.living_pids({123: 10})
    assert kernel.WaitForSingleObject.restype == checker.wintypes.DWORD
    state["wait"] = 0x102
    assert checker.living_pids({123: 10}) == {123}
    state["wait"] = 0
    assert checker.living_pids({123: 10}) == set()


@pytest.mark.parametrize('first_ok,last_error,expected_ok', [
    (False, 5, False),
    (True, 5, False),
    (True, 18, True),
])
def test_process_snapshot_error_is_not_an_empty_success(monkeypatch, first_ok, last_error, expected_ok):
    from unittest.mock import Mock
    import tools.check_native_window as checker

    def first(_handle, entry):
        entry._obj.th32ProcessID = 123
        entry._obj.th32ParentProcessID = 1
        return first_ok

    kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=42),
        Process32FirstW=Mock(side_effect=first),
        Process32NextW=Mock(return_value=False),
        CloseHandle=Mock(return_value=True),
    )
    monkeypatch.setattr(checker.ctypes, 'WinDLL', lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(checker.ctypes, 'get_last_error', lambda: last_error, raising=False)
    if expected_ok:
        assert checker.process_pairs() == [(123, 1)]
    else:
        with pytest.raises(OSError, match='Process32'):
            checker.process_pairs()
    kernel.CloseHandle.assert_called_once_with(42)


@pytest.fixture
def close_window_api(monkeypatch):
    from unittest.mock import Mock
    import tools.check_native_window as checker

    state = {
        "root_birth": 1000, "owner_birth": 1100,
        "owner_pid": 11, "window": True, "owner_api": 1,
    }

    def owner(_hwnd, result):
        result._obj.value = state["owner_pid"]
        return state["owner_api"]

    user = SimpleNamespace(
        IsWindow=Mock(side_effect=lambda *_: state["window"]),
        GetWindowThreadProcessId=Mock(side_effect=owner),
        PostMessageW=Mock(return_value=True),
    )
    monkeypatch.setattr(checker.ctypes, "WinDLL", lambda *a, **k: user, raising=False)
    monkeypatch.setattr(
        checker, "process_identity",
        lambda pid: state["root_birth"] if pid == 10 else state["owner_birth"],
    )
    return checker, state, user


def test_owned_close_posts_only_to_matching_window(close_window_api):
    checker, state, user = close_window_api
    assert checker.close_owned_window(10, 1000, 100, {10: 1000, 11: 1100}, (11, 1100))
    user.PostMessageW.assert_called_once_with(100, 0x0010, 0, 0)


@pytest.mark.parametrize("change", [
    {"root_birth": 9000}, {"root_birth": None},
    {"owner_birth": 9000}, {"owner_birth": None},
    {"owner_pid": 12}, {"window": False}, {"owner_api": 0},
])
def test_owned_close_rejects_changed_or_unknown_identity(close_window_api, change):
    checker, state, user = close_window_api
    state.update(change)
    with pytest.raises(RuntimeError, match="owned close"):
        checker.close_owned_window(10, 1000, 100, {10: 1000, 11: 1100}, (11, 1100))
    user.PostMessageW.assert_not_called()


def test_owned_close_rechecks_root_before_post(close_window_api, monkeypatch):
    checker, state, user = close_window_api
    root_times = iter([1000, 9000])
    monkeypatch.setattr(
        checker, "process_identity",
        lambda pid: next(root_times) if pid == 10 else 1100,
    )
    with pytest.raises(RuntimeError, match="owned close"):
        checker.close_owned_window(10, 1000, 100, {10: 1000, 11: 1100}, (11, 1100))
    user.PostMessageW.assert_not_called()


def test_owned_close_rejects_replacement_by_another_known_owned_pid(close_window_api):
    checker, state, user = close_window_api
    identities = {10: 1000, 11: 1100, 12: 1200}
    pinned = checker.owned_window_identity(10, 1000, 100, identities)
    state.update(owner_pid=12, owner_birth=1200)
    with pytest.raises(RuntimeError, match="owned close"):
        checker.close_owned_window(10, 1000, 100, identities, pinned)
    user.PostMessageW.assert_not_called()


@pytest.mark.parametrize("inspect_ui", [False, True])
@pytest.mark.parametrize("gate_passed,minimized,exhausts_budget", [
    (False, False, False), (False, True, False), (True, True, False), (False, True, True),
])
def test_main_requires_gate_and_closes_normally_even_after_gate_failure(
    monkeypatch, tmp_path, inspect_ui, gate_passed, minimized, exhausts_budget,
):
    import json
    import tools.check_native_window as checker

    events = []
    target = tmp_path / "synthetic.exe"
    target.write_bytes(b"not an executable; Popen is replaced")
    output = tmp_path / "native.json"
    now = [100.0]
    monkeypatch.setattr(checker, "time", SimpleNamespace(
        monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)))

    class Process:
        pid = 10
        close_at = None
        @property
        def closed(self):
            return self.close_at is not None and now[0] >= self.close_at

        def poll(self):
            return 0 if self.closed else None

    root = Process()

    def spawn(*args, **kwargs):
        events.append("spawn")
        return root

    # Replace checker.os only, not the process-wide os.name used by pathlib.
    monkeypatch.setattr(checker, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(checker, "subprocess", SimpleNamespace(
        Popen=spawn, DEVNULL=-3, STDOUT=-2,
    ))
    ports = iter([32000, 32001])
    monkeypatch.setattr(checker, "available_port", lambda: next(ports))
    monkeypatch.setattr(checker, "process_identity", lambda pid: None if root.closed else 1000)
    monkeypatch.setattr(checker, "process_pairs", lambda: [(10, 1)])
    monkeypatch.setattr(checker, "owned_windows", lambda pids: [100] if pids == {10} else [])
    monkeypatch.setattr(checker, "health_ready", lambda port: True)
    monkeypatch.setattr(checker, "ui_ready", lambda port: True)
    monkeypatch.setattr(checker, "living_pids", lambda identities: set() if root.closed else set(identities))
    monkeypatch.setattr(checker, "port_released", lambda port: root.closed)

    def inspect(*args, **kwargs):
        events.append("inspect")
        return {"passed": True}

    def pin(pid, birth, hwnd, identities):
        events.append("pin")
        return (10, 1000)

    def restore(pid, birth, hwnd, path, deadline, limits, *, expected_top_identity):
        assert (pid, birth, hwnd) == (10, 1000, 100)
        assert path == output.resolve()
        assert expected_top_identity == (10, 1000)
        events.append("gate")
        if exhausts_budget:
            now[0] = deadline
        return {"passed": gate_passed, "minimized": minimized,
                "errors": [] if gate_passed else ["synthetic_gate_failure"]}

    def close(pid, birth, hwnd, identities, expected_owner):
        assert (pid, birth, hwnd, identities) == (10, 1000, 100, {10: 1000})
        assert expected_owner == (10, 1000)
        events.append("close")
        root.close_at = now[0] + 2.0
        return True

    def terminate(_identities):
        pytest.fail("Gate failure must still attempt normal WM_CLOSE first")

    monkeypatch.setattr(checker, "inspect_ui", inspect)
    monkeypatch.setattr(checker, "run_restore_gate", restore)
    monkeypatch.setattr(checker, "owned_window_identity", pin)
    monkeypatch.setattr(checker, "close_owned_window", close)
    monkeypatch.setattr(checker, "terminate_owned", terminate)
    args = ["--exe", str(target), "--output", str(output)]
    if inspect_ui:
        args.append("--inspect-ui")
    result = checker.main(args)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == (0 if gate_passed else 1)
    assert report["status"] == ("passed" if gate_passed else "failed")
    assert report["restored"] is gate_passed
    assert report["wm_close_sent"] and report["normal_exit"] and report["port_released"]
    assert report["forced_cleanup"] == []
    assert report["close_reserve_seconds"] == 5.0
    assert now[0] < 190.0
    assert events == ["spawn"] + (["inspect"] if inspect_ui else []) + ["pin", "gate", "close"]
    if not gate_passed:
        assert "restore_gate_failed" in report["errors"]
