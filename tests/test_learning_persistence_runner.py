"""Owned persistence-runner lifecycle and DB isolation; no app/browser launch."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import Mock, patch

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
helper_spec = importlib.util.spec_from_file_location("persistence_runner_helpers", TOOLS / "run_browser_checks.py")
helpers = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helpers)
spec = importlib.util.spec_from_file_location("check_learning_persistence_browser", TOOLS / "check_learning_persistence_browser.py")
runner = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"run_browser_checks": helpers}):
    spec.loader.exec_module(runner)


def test_owned_sandbox_is_new_and_never_opens_existing_output_db(tmp_path):
    existing = tmp_path / runner.DB_NAME
    existing.write_bytes(b"unrelated existing file")
    first, second = runner.OwnedSandbox(tmp_path), runner.OwnedSandbox(tmp_path)
    assert first.path != second.path
    assert first.path.parent == second.path.parent == tmp_path.resolve()
    assert first.path.is_absolute() and second.path.is_absolute()
    for sandbox in (first, second):
        with pytest.raises(FileNotFoundError):
            sandbox.snapshot()
        with pytest.raises(FileNotFoundError):
            with sandbox.write_lock():
                pytest.fail("absent DB must not be created")
        assert not sandbox.db_path.exists()
    assert existing.read_bytes() == b"unrelated existing file"


def test_db_guard_rejects_outside_path_before_open(tmp_path):
    sandbox = runner.OwnedSandbox(tmp_path)
    outside = tmp_path / runner.DB_NAME
    outside.write_bytes(b"keep")
    sandbox.db_path = outside
    with pytest.raises(ValueError, match="outside"):
        sandbox.snapshot()
    with pytest.raises(ValueError, match="outside"):
        with sandbox.write_lock():
            pytest.fail("outside DB must not be opened")
    assert outside.read_bytes() == b"keep"


def test_external_lock_is_real_and_released_after_exception(tmp_path):
    sandbox = runner.OwnedSandbox(tmp_path)
    connection = sqlite3.connect(sandbox.db_path, isolation_level=None, timeout=0)
    try:
        connection.execute("CREATE TABLE synthetic_marker (value INTEGER)")
        with pytest.raises(RuntimeError, match="test interruption"):
            with sandbox.write_lock():
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    connection.execute("BEGIN IMMEDIATE")
                raise RuntimeError("test interruption")
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
    finally:
        connection.close()


def test_evidence_validator_detects_duplicate_tag_and_author_counts():
    snapshot = {
        "history": [{"work_id": "demo-0001", "verdict": "hate", "reason": None,
                     "opened": 0, "bookmarked": 0, "tags": '["tag"]', "author_id": "demo-author"}],
        "tags": [{"tag": "tag", "superlike_count": 0, "like_count": 0, "skip_count": 0,
                  "hate_count": 1, "shown_count": 1}],
        "authors": [{"author_id": "demo-author", "superlike_count": 0, "like_count": 0,
                     "skip_count": 0, "hate_count": 0, "shown_count": 1}],
    }
    runner.verify_aggregates(snapshot)
    snapshot["tags"][0]["hate_count"] = 2
    with pytest.raises(AssertionError, match="tag counters"):
        runner.verify_aggregates(snapshot)
    snapshot["tags"][0]["hate_count"] = 1
    snapshot["authors"][0]["hate_count"] = 1
    with pytest.raises(AssertionError, match="author counters"):
        runner.verify_aggregates(snapshot)


@pytest.fixture
def fake_runtime(monkeypatch):
    processes = []
    calls = []

    def spawn(command, **kwargs):
        assert all(process.poll() is not None for process in processes)
        process = Mock(pid=43000 + len(processes))
        process.poll.return_value = None
        processes.append(process)
        calls.append((command, kwargs))
        return process

    def stop(process):
        if process is not None and process.poll() is None:
            process.terminate()
            process.poll.return_value = 0

    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(runner, "stop_child", stop)
    monkeypatch.setattr(runner, "free_port", lambda: 49123)
    monkeypatch.setattr(runner, "port_released", lambda port: True)
    monkeypatch.setattr(runner, "wait_for_health", lambda url, process: None)
    monkeypatch.setattr(runner, "source_hashes", lambda: {"src/app.py": "unchanged"})
    return processes, calls


def test_restart_uses_same_owned_sandbox_and_cleans_both_children(tmp_path, monkeypatch, fake_runtime):
    processes, calls = fake_runtime
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "runner-test")

    def exercise(app, sandbox, output, result, channel):
        assert sandbox.path.parent == output and sandbox.path.is_absolute()
        app.restart()
        result["browser_launches"] = 1
        result["passed"].append("synthetic fixture")

    monkeypatch.setattr(runner, "exercise_browser", exercise)
    assert runner.run(output=tmp_path) == 0
    assert len(processes) == len(calls) == 2
    for process in processes:
        process.terminate.assert_called_once()
    commands = [call[0] for call in calls]
    assert commands[0] == commands[1]
    assert commands[0][:3] == [sys.executable, "-m", "pixivvault_nicegui"]
    assert commands[0][3:7] == ["--host", "127.0.0.1", "--port", "49123"]
    assert commands[0][-2] == "--learning-sandbox"
    assert Path(commands[0][-1]).parent == tmp_path.resolve()
    assert all("PYTEST_CURRENT_TEST" not in kwargs["env"] for _, kwargs in calls)
    evidence = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert evidence["success"] and evidence["app"]["port_released"]
    assert len(evidence["app"]["instances"]) == 2
    assert evidence["source_before"] == evidence["source_after"]


@pytest.mark.parametrize("failure_phase", ["health", "browser"])
def test_failure_still_cleans_owned_child_and_writes_result(tmp_path, monkeypatch, fake_runtime, failure_phase):
    processes, _ = fake_runtime
    def fail(*args):
        raise TimeoutError("deliberate test failure")
    browser = Mock(side_effect=fail if failure_phase == "browser" else None)
    monkeypatch.setattr(runner, "exercise_browser", browser)
    if failure_phase == "health":
        monkeypatch.setattr(runner, "wait_for_health", fail)
    assert runner.run(output=tmp_path) == 1
    assert len(processes) == 1
    processes[0].terminate.assert_called_once()
    if failure_phase == "health":
        browser.assert_not_called()
    evidence = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert not evidence["success"]
    assert "deliberate test failure" in evidence["failure"]
    assert evidence["app"]["port_released"]


@pytest.mark.parametrize("fault", ["source", "console", "page", "port"])
def test_evidence_faults_fail_otherwise_successful_run(tmp_path, monkeypatch, fake_runtime, fault):
    def exercise(app, sandbox, output, result, channel):
        if fault == "console":
            result["console_errors"].append("unexpected console error")
        if fault == "page":
            result["page_errors"].append("unexpected page error")
    monkeypatch.setattr(runner, "exercise_browser", exercise)
    if fault == "source":
        hashes = iter([{"src/app.py": "before"}, {"src/app.py": "after"}])
        monkeypatch.setattr(runner, "source_hashes", lambda: next(hashes))
    if fault == "port":
        monkeypatch.setattr(runner, "port_released", lambda port: False)
    assert runner.run(output=tmp_path) == 1
    evidence = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert not evidence["success"]
    if fault == "source":
        assert evidence["source_changed"] and "sources changed" in evidence["failure"]
    if fault == "port":
        assert not evidence["app"]["port_released"] and "port still accepting" in evidence["failure"]
