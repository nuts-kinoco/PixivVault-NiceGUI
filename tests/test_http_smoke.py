"""専用子プロセスのloopback HTTPだけを試験し、必ずその子を終了する。"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def test_demo_serves_health_and_initial_page_without_browser(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(os.environ)
    # 実起動のスモークでNiceGUIのscreen fixtureモードを誤選択しない。
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONUTF8"] = "1"
    log_path = tmp_path / "server.log"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "pixivvault_nicegui", "--port", str(port)],
            cwd=tmp_path, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, log_path.read_text(encoding="utf-8", errors="replace")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                        health = json.load(response)
                    break
                except (OSError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise AssertionError(log_path.read_text(encoding="utf-8", errors="replace"))
                    time.sleep(0.1)
            assert health == {"status": "ok", "mode": "demo", "data": "synthetic"}
            with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
                html = response.read().decode("utf-8")
                assert response.status == 200
            assert "PixivVault" in html
            assert "pv-grid" in html
            assert "pv-card" in html
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert process.poll() is not None
    assert not list(tmp_path.rglob("*.db"))
