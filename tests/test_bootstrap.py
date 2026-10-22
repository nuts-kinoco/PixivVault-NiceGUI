"""OS画面を操作せず、起動境界とオプションを確かめる。"""

import json
import runpy
import subprocess
import sys

import pytest

from pixivvault_nicegui.bootstrap import parse_args


def test_default_is_loopback_demo_without_native_window():
    config = parse_args([])
    assert config.host == "127.0.0.1"
    assert config.port == 8089
    assert not config.native
    assert config.learning_sandbox is None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.1", "example.com"])
def test_non_loopback_hosts_are_rejected(host):
    with pytest.raises(SystemExit):
        parse_args(["--host", host])


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc"])
def test_invalid_port_is_rejected(port):
    with pytest.raises(SystemExit):
        parse_args(["--port", port])


def test_alternate_loopback_and_port():
    config = parse_args(["--host", "::1", "--port", "12345"])
    assert (config.host, config.port) == ("::1", 12345)


def test_import_does_not_import_ui_database_or_legacy_app():
    code = (
        "import sys; import pixivvault_nicegui.bootstrap; "
        "assert not {'nicegui','sqlite3','gui','core','pixiv_client','scheduler'}"
        ".intersection(sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_spawn_import_does_not_start_a_second_application(monkeypatch):
    calls = []
    monkeypatch.setattr("pixivvault_nicegui.bootstrap.main", lambda: calls.append("run"))
    runpy.run_module("pixivvault_nicegui.__main__", run_name="__mp_main__")
    assert calls == []


def test_default_start_does_not_implicitly_enable_native(monkeypatch):
    from nicegui import ui
    from pixivvault_nicegui import bootstrap

    calls = []
    monkeypatch.setattr(bootstrap, "register_pages", lambda: None)
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(kwargs))
    assert bootstrap.main([]) == 0
    assert len(calls) == 1
    assert calls[0]["native"] is False
    assert calls[0]["window_size"] is None
    assert calls[0]["show"] is False
    assert calls[0]["reload"] is False
    assert calls[0]["host"] == "127.0.0.1"


def test_check_mode_prints_metadata_without_starting_server():
    result = subprocess.run([sys.executable, "-m", "pixivvault_nicegui", "--check"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["mode"] == "demo"
    assert data["data"] == "synthetic"
    assert data["host"] == "127.0.0.1"
    assert data["nicegui"] == "3.18.0"


def test_learning_sandbox_requires_explicit_absolute_path(tmp_path):
    with pytest.raises(SystemExit):
        parse_args(["--learning-sandbox", "relative-demo"])
    target = tmp_path / "learning-demo"
    config = parse_args(["--learning-sandbox", str(target)])
    assert config.learning_sandbox == target
    assert not target.exists()


def test_check_with_sandbox_does_not_create_or_connect_storage(tmp_path):
    target = tmp_path / "learning-demo"
    result = subprocess.run(
        [sys.executable, "-m", "pixivvault_nicegui", "--check", "--learning-sandbox", str(target)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["learning_storage"] == "sandbox"
    assert not target.exists()


def test_requested_storage_failure_does_not_fall_back_or_start_server(monkeypatch, tmp_path):
    from nicegui import ui
    from pixivvault_nicegui import bootstrap

    calls = []
    def fail_registration(*, learning_sandbox):
        assert learning_sandbox == tmp_path
        raise ValueError("unmanaged database")
    monkeypatch.setattr(bootstrap, "register_pages", fail_registration)
    monkeypatch.setattr(ui, "run", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="unmanaged"):
        bootstrap.main(["--learning-sandbox", str(tmp_path)])
    assert calls == []
