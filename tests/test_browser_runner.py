"""Browser runner lifecycle tests without starting an app or browser."""

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "run_browser_checks.py"
spec = importlib.util.spec_from_file_location("run_browser_checks", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
ACTUAL_RUN_OWNED_CHECKER = runner.run_owned_checker


class FakeOwnedTracker:
    errors = []

    def observed_endpoint(self, pid):
        return type(pid) is int and pid > 0

    def finish(self):
        return {"observed_count": 2, "remaining_count": 0, "errors": []}


@pytest.fixture(autouse=True)
def mock_process_tree_for_isolated_runner_unit_tests(monkeypatch):
    # Separate tests/test_owned_process_tree.py tests the real identity tracker.
    # These existing tests isolate the runner flow without launching an OS PID.
    monkeypatch.setattr(runner, "make_process_tracker", lambda process: FakeOwnedTracker())

    def fake_owned_checker(command, stderr_log, timeout=240):
        try:
            result = runner.subprocess.run(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=stderr_log,
                                           check=False, timeout=timeout)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = -1
        return code, {"observed_count": 2, "remaining_count": 0, "errors": [],
                      "timeout": code == -1, "checker_returncode": code}

    monkeypatch.setattr(runner, "run_owned_checker", fake_owned_checker)


def test_environment_removes_pytest_session_without_losing_other_values(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_identifier")
    monkeypatch.setenv("RUNNER_TEST_KEEP", "keep")
    monkeypatch.setenv("PYTHONPATH", "unexpected-checkout/src")
    monkeypatch.setenv("PYTHONSAFEPATH", "")
    result = runner.child_environment()
    assert "PYTEST_CURRENT_TEST" not in result
    assert result["RUNNER_TEST_KEEP"] == "keep"
    assert result["PYTHONPATH"] == str(runner.ROOT / "src")
    assert result["PYTHONSAFEPATH"] == "1"
    assert result["PYTHONUNBUFFERED"] == "1"
    assert result["PYTHONUTF8"] == "1"


def test_health_rejects_non_synthetic_response(monkeypatch):
    app = Mock()
    app.poll.return_value = None
    response = Mock()
    response.status = 200
    response.read.return_value = b'{"status":"ok","mode":"production"}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    build = Mock(return_value=opener)
    monkeypatch.setattr(runner.urllib.request, "build_opener", build)
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="unexpected health response"):
        runner.wait_for_health("http://127.0.0.1:9000", app, timeout=1)
    build.assert_called_once()
    assert isinstance(build.call_args.args[0], runner.urllib.request.ProxyHandler)
    assert build.call_args.args[0].proxies == {}
    assert opener.open.call_count == 1


def test_app_early_exit_is_reported(monkeypatch):
    app = Mock(returncode=37)
    app.poll.return_value = 37
    with pytest.raises(RuntimeError, match="exit=37"):
        runner.wait_for_health("http://127.0.0.1:9000", app)


def test_termination_escalates_when_child_ignores_term():
    app = Mock()
    app.poll.return_value = None
    app.wait.side_effect = [subprocess.TimeoutExpired("app", 8), 0]
    runner.stop_child(app)
    app.terminate.assert_called_once()
    app.kill.assert_called_once()
    assert app.wait.call_count == 2


@pytest.mark.parametrize("codes,expected", [
    ([0, 0, 0, 0], 0),
    ([1, 0, 0, 0], 1),
    ([0, -1, 0, 0], 1),
    ([0, 0, 1, 0], 1),
    ([0, 0, 0, 1], 1),
    ([0, 0, 0, -1], 1),
])
def test_runner_requires_all_checks_and_always_cleans_up(tmp_path, monkeypatch, codes, expected):
    process = Mock(pid=1234, returncode=0)
    process.poll.return_value = 0
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, app: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, app, nonce:
                        {"listener_pid": 1234, "listener_parent_pid": 999})
    monkeypatch.setattr(runner, "request_owned_shutdown", lambda url, app, nonce: True)
    monkeypatch.setattr(runner, "port_released", lambda port: True)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        code = codes[len(calls) - 1]
        if code == -1:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        return Mock(returncode=code)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    rc = runner.run(output=tmp_path)
    assert rc == expected and len(calls) == len(runner.CHECKS)
    assert all("--channel" in cmd and "--url" in cmd and "--output" in cmd for cmd in calls)
    assert calls[0][0] == calls[1][0] == runner.sys.executable
    popen.assert_called_once()
    assert "PYTEST_CURRENT_TEST" not in popen.call_args.kwargs["env"]
    process.terminate.assert_not_called()
    data = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert data["success"] is (expected == 0)
    assert data["app"]["port_released"] is True


def test_runner_sources_match_child_when_caller_uses_foreign_repo(tmp_path, monkeypatch):
    trusted = tmp_path / "trusted"
    foreign = tmp_path / "foreign"
    local_source = trusted / "src" / "pixivvault_nicegui" / "bootstrap.py"
    foreign_source = foreign / "src" / "pixivvault_nicegui" / "bootstrap.py"
    local_source.parent.mkdir(parents=True)
    foreign_source.parent.mkdir(parents=True)
    local_source.write_bytes(b"# trusted source\n")
    foreign_source.write_bytes(b"# foreign source\n")
    expected_hashes = {"src/pixivvault_nicegui/bootstrap.py":
                       hashlib.sha256(local_source.read_bytes()).hexdigest()}
    monkeypatch.setattr(runner, "ROOT", trusted)
    monkeypatch.chdir(foreign)
    monkeypatch.setenv("PYTHONPATH", str(foreign / "src"))

    app = Mock(pid=1234, returncode=0)
    app.poll.return_value = 0
    popen = Mock(return_value=app)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: Mock(returncode=0))
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, app, nonce:
                        {"listener_pid": 1234, "listener_parent_pid": 999})
    monkeypatch.setattr(runner, "request_owned_shutdown", lambda url, app, nonce: True)
    monkeypatch.setattr(runner, "port_released", lambda port: True)

    assert runner.run(output=tmp_path / "results") == 0
    kwargs = popen.call_args.kwargs
    assert popen.call_args.args[0][:3] == [runner.sys.executable, "-m", "pixivvault_nicegui"]
    assert kwargs["cwd"] == str(trusted)
    assert kwargs["env"]["PYTHONPATH"] == str(trusted / "src")
    assert kwargs["env"]["PYTHONSAFEPATH"] == "1"
    assert kwargs["env"]["PYTHONPATH"] != str(foreign / "src")
    data = json.loads((tmp_path / "results" / "runner-result.json").read_text(encoding="utf-8"))
    assert data["source_before"] == data["source_after"] == expected_hashes
    assert data["checks"] == dict.fromkeys(runner.CHECKS, 0)
    app.terminate.assert_not_called()


def test_failed_health_still_terminates_child(tmp_path, monkeypatch):
    process = Mock(pid=1234, returncode=0)
    process.poll.side_effect = [None, None, 0]
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(runner, "wait_for_health",
                        lambda url, app: (_ for _ in ()).throw(TimeoutError("not ready")))
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    assert runner.run(output=tmp_path) == 1
    process.terminate.assert_called_once()
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert "not ready" in result["failure"]


def test_port_still_open_fails_even_if_both_checks_pass(tmp_path, monkeypatch):
    app = Mock(pid=1234, returncode=0)
    app.poll.return_value = 0
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: app)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, app, nonce:
                        {"listener_pid": 1234, "listener_parent_pid": 999})
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: False)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["checks"] == dict.fromkeys(runner.CHECKS, 0)
    assert result["app"] == {"pid": 1234, "returncode": 0, "port_released": False,
                             "listener_owner": {"listener_pid": 1234, "listener_parent_pid": 999},
                             "graceful_shutdown": True, "forced_cleanup": False,
                             "owned_tree": {"observed_count": 2, "remaining_count": 0,
                                            "errors": []}}
    assert "port still accepting" in result["failure"]


def test_port_released_detects_connectable_socket(monkeypatch):
    class FakeSocket:
        def __init__(self, code):
            self.code = code
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def settimeout(self, timeout):
            pass
        def connect_ex(self, address):
            assert address == ("127.0.0.1", 49123)
            return self.code

    codes = iter([0, 0, 1])
    monkeypatch.setattr(runner.socket, "socket", lambda *args: FakeSocket(next(codes)))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    assert runner.port_released(49123)


def test_source_change_invalidates_otherwise_successful_evidence(tmp_path, monkeypatch):
    app = Mock(pid=1234, returncode=0)
    app.poll.return_value = 0
    hashes = iter([{"src/app.py": "before"}, {"src/app.py": "after"}])
    monkeypatch.setattr(runner, "source_hashes", lambda: next(hashes))
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: app)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, app, nonce:
                        {"listener_pid": 1234, "listener_parent_pid": 999})
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["source_changed"] is True
    assert "sources changed" in result["failure"]


def test_harness_change_invalidates_success_even_when_product_source_is_unchanged(tmp_path, monkeypatch):
    app = Mock(pid=1234, returncode=0)
    app.poll.return_value = 0
    monkeypatch.setattr(runner, "source_hashes", lambda: {"src/app.py": "same"})
    hashes = iter([{"tools/check_gallery_browser.py": "old"},
                   {"tools/check_gallery_browser.py": "new"}])
    monkeypatch.setattr(runner, "harness_hashes", lambda: next(hashes))
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: app)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, app, nonce:
                        {"listener_pid": 1234, "listener_parent_pid": 999})
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["source_changed"] is False
    assert result["harness_changed"] is True
    assert "checker or runner changed" in result["failure"]


def test_runner_never_overwrites_existing_receipt(tmp_path):
    receipt = tmp_path / "runner-result.json"
    receipt.write_text("frozen", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be overwritten"):
        runner.run(output=tmp_path)
    assert receipt.read_text(encoding="utf-8") == "frozen"


def test_surviving_owned_child_rejects_egress_clean_claim(tmp_path, monkeypatch):
    class LeakedTracker(FakeOwnedTracker):
        def finish(self):
            return {"observed_count": 3, "remaining_count": 1, "errors": []}

    process = Mock(pid=1234, returncode=0)
    process.poll.return_value = 0
    monkeypatch.setattr(runner, "make_process_tracker", lambda _: LeakedTracker())
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "wait_for_health", lambda *a: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda *a: {"listener_pid": 1234})
    monkeypatch.setattr(runner, "request_owned_shutdown", lambda *a: True)
    monkeypatch.setattr(runner, "port_released", lambda *a: True)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: Mock(returncode=0))
    assert runner.run(output=tmp_path) == 1
    receipt = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert receipt["app"]["owned_tree"]["remaining_count"] == 1
    assert "descendant remains" in receipt["failure"]


def test_real_checker_wrapper_requires_zero_remaining_child_tree(monkeypatch):
    checker = Mock(pid=5102)
    checker.wait.return_value = 0
    checker.poll.return_value = 0
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: checker)

    class Surviving(FakeOwnedTracker):
        def finish(self):
            return {"observed_count": 3, "remaining_count": 1, "errors": []}

    monkeypatch.setattr(runner, "make_process_tracker", lambda _: Surviving())
    code, receipt = ACTUAL_RUN_OWNED_CHECKER(["fake-checker"], Mock(), timeout=1)
    assert code == -1
    assert receipt["remaining_count"] == 1
    checker.terminate.assert_not_called()


def test_real_checker_wrapper_reports_clean_natural_exit(monkeypatch):
    checker = Mock(pid=5102)
    checker.wait.return_value = 0
    checker.poll.return_value = 0
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: checker)
    code, receipt = ACTUAL_RUN_OWNED_CHECKER(["fake-checker"], Mock(), timeout=1)
    assert code == 0
    assert receipt["remaining_count"] == 0 and receipt["timeout"] is False
    checker.terminate.assert_not_called()


def test_checker_tracker_failure_still_closes_exact_popen_handle(monkeypatch):
    checker = Mock(pid=5102)
    checker.poll.return_value = None
    checker.wait.return_value = 0
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: checker)

    class BrokenTracker(FakeOwnedTracker):
        errors = ["root_identity_unknown"]

    monkeypatch.setattr(runner, "make_process_tracker", lambda _: BrokenTracker())
    with pytest.raises(RuntimeError, match="identity unknown"):
        ACTUAL_RUN_OWNED_CHECKER(["fake-checker"], Mock(), timeout=1)
    checker.terminate.assert_called_once()
    checker.wait.assert_called_once_with(timeout=8)


def test_unverified_owner_is_never_a_green_browser_run(tmp_path, monkeypatch):
    app = Mock(pid=4321, returncode=0)
    app.poll.return_value = 0
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: app)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda url, process, nonce:
                        (_ for _ in ()).throw(RuntimeError("foreign owner")))
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["success"] is False
    assert result["app"]["listener_owner"] is None
    assert "foreign owner" in result["failure"]


def test_owner_handshake_rejects_foreign_pid_even_if_nonce_endpoint_responds(monkeypatch):
    app = Mock(pid=4321)
    app.poll.return_value = None
    response = Mock()
    response.read.return_value = b'{"synthetic":true,"pid":55,"parent_pid":66}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(RuntimeError, match="does not belong"):
        runner.verify_owned_listener("http://127.0.0.1:49123", app, "a" * 48)
    assert opener.open.call_args.args[0].headers["X-acceptance"] == "a" * 48


def test_owned_shutdown_rejects_unacknowledged_response(monkeypatch):
    app = Mock(pid=4321)
    app.poll.return_value = None
    monkeypatch.setattr(runner, "verify_owned_listener", lambda *args: {"listener_pid": 4321})
    response = Mock()
    response.read.return_value = b'{"stopping":false}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(RuntimeError, match="not acknowledged"):
        runner.request_owned_shutdown("http://127.0.0.1:49123", app, "a" * 48)
    app.terminate.assert_not_called()


@pytest.mark.parametrize("identity", [
    {"synthetic": True, "pid": 902, "parent_pid": 901},
    {"synthetic": True, "pid": 901, "parent_pid": 800},
])
def test_listener_owner_challenge_accepts_exact_owned_process_or_child(monkeypatch, identity):
    process = Mock(pid=901)
    process.poll.return_value = None
    response = Mock()
    response.read.return_value = json.dumps(identity).encode("utf-8")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *_: opener)
    assert runner.verify_owned_listener("http://127.0.0.1:49000", process, "nonce") == {
        "listener_pid": identity["pid"], "listener_parent_pid": identity["parent_pid"]}
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:49000/_browser_acceptance/owner"
    assert request.get_header("X-acceptance") == "nonce"


def test_listener_owner_challenge_rejects_unrelated_server(monkeypatch):
    process = Mock(pid=901)
    process.poll.return_value = None
    response = Mock()
    response.read.return_value = b'{"synthetic":true,"pid":455,"parent_pid":456}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *_: opener)
    with pytest.raises(RuntimeError, match="does not belong"):
        runner.verify_owned_listener("http://127.0.0.1:49000", process, "nonce")


def test_owner_shutdown_acknowledged_only_after_zero_exit(monkeypatch):
    process = Mock(pid=901)
    process.poll.return_value = None
    process.wait.return_value = 0
    authorized = Mock(return_value={"listener_pid": 902, "listener_parent_pid": 901})
    monkeypatch.setattr(runner, "verify_owned_listener", authorized)
    response = Mock()
    response.read.return_value = b'{"stopping":true}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *_: opener)
    assert runner.request_owned_shutdown("http://127.0.0.1:49000", process, "nonce") is True
    authorized.assert_called_once_with("http://127.0.0.1:49000", process, "nonce")
    request = opener.open.call_args.args[0]
    assert request.get_method() == "POST"
    assert request.get_header("X-acceptance") == "nonce"
    process.wait.assert_called_once_with(timeout=20)


def test_owner_shutdown_timeout_fails_without_silent_success(monkeypatch):
    process = Mock(pid=901)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("app", 20)
    monkeypatch.setattr(runner, "verify_owned_listener", lambda *a: {"listener_pid": 902})
    response = Mock()
    response.read.return_value = b'{"stopping":true}'
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(runner.urllib.request, "build_opener", lambda *_: opener)
    assert runner.request_owned_shutdown("http://127.0.0.1:49000", process, "nonce") is False
