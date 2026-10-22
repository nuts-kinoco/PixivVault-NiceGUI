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
    process.poll.side_effect = [None, 0]
    process.wait.return_value = 0
    popen = Mock(return_value=process)
    calls = []
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, app: None)
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
    process.terminate.assert_called_once()
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
    app.poll.side_effect = [None, 0]
    popen = Mock(return_value=app)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: Mock(returncode=0))
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
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
    app.terminate.assert_called_once()


def test_failed_health_still_terminates_child(tmp_path, monkeypatch):
    process = Mock(pid=1234, returncode=0)
    process.poll.side_effect = [None, 0]
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
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: False)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["checks"] == dict.fromkeys(runner.CHECKS, 0)
    assert result["app"] == {"pid": 1234, "returncode": 0, "port_released": False}
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
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Mock(returncode=0))
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    assert runner.run(output=tmp_path) == 1
    result = json.loads((tmp_path / "runner-result.json").read_text(encoding="utf-8"))
    assert result["source_changed"] is True
    assert "sources changed" in result["failure"]
