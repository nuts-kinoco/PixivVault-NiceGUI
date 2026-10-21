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
