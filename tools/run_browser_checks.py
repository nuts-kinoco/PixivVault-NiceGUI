"""Spawn an isolated synthetic-data app and run Playwright browser regressions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("check_gallery_browser.py", "check_learning_browser.py", "check_image_races_browser.py")


def source_hashes() -> dict[str, str]:
    """Bind the browser evidence to the exact runtime files it exercised."""
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "src").rglob("*"))
            if path.suffix in {".py", ".js", ".css"}}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def child_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def wait_for_health(url: str, app: subprocess.Popen, timeout: float = 35.0) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "HTTP endpoint not ready"
    while time.monotonic() < deadline:
        if app.poll() is not None:
            raise RuntimeError(f"app exited before healthy: exit={app.returncode}")
        try:
            with opener.open(f"{url}/healthz", timeout=1) as response:
                payload = json.loads(response.read())
                if response.status == 200 and payload == {
                    "status": "ok", "mode": "demo", "data": "synthetic"
                }:
                    return
                last_error = f"unexpected health response: {response.status} {payload!r}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.15)
    raise TimeoutError(f"app health timeout: {last_error}")


def stop_child(app: subprocess.Popen | None) -> None:
    if app is None or app.poll() is not None:
        return
    app.terminate()
    try:
        app.wait(timeout=8)
    except subprocess.TimeoutExpired:
        app.kill()
        app.wait(timeout=5)


def port_released(port: int, timeout: float = 5.0) -> bool:
    """Require the bound app endpoint to stop accepting TCP connections."""
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((HOST, port)) != 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def run(*, channel: str = "chromium", output: Path = Path("artifacts/browser-ci")) -> int:
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    port = free_port()
    url = f"http://{HOST}:{port}"
    app = None
    app_pid: int | None = None
    app_returncode: int | None = None
    released = False
    results: dict[str, int | None] = {name: None for name in CHECKS}
    failure: str | None = None
    # Process diagnostics remain local; CI uploads only browser screenshots and JSON.
    with (output / "app-stderr.log").open("wb") as app_err:
        try:
            app = subprocess.Popen(
                [sys.executable, "-m", "pixivvault_nicegui", "--host", HOST,
                 "--port", url.rsplit(":", 1)[-1]],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=app_err, env=child_environment(),
            )
            app_pid = app.pid
            wait_for_health(url, app)
            for name in CHECKS:
                destination = output / name.removeprefix("check_").removesuffix("_browser.py")
                command = [sys.executable, str(Path(__file__).with_name(name)),
                           "--channel", channel, "--url", url, "--output", str(destination)]
                with (output / f"{name}.stderr.log").open("wb") as err:
                    try:
                        completed = subprocess.run(command, stdin=subprocess.DEVNULL,
                                                   stdout=subprocess.DEVNULL, stderr=err,
                                                   check=False, timeout=240)
                        results[name] = completed.returncode
                    except subprocess.TimeoutExpired:
                        results[name] = -1
                # Every check must pass; still run the next check for diagnostic coverage.
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                stop_child(app)
            except Exception as exc:
                failure = (failure + "; " if failure else "") + f"cleanup: {exc}"
            if app is not None:
                app_returncode = app.poll()
                if app_returncode is None:
                    failure = (failure + "; " if failure else "") + "app root still alive"
            try:
                released = port_released(port)
                if not released:
                    failure = (failure + "; " if failure else "") + "app port still accepting connections"
            except Exception as exc:
                failure = (failure + "; " if failure else "") + f"port cleanup: {exc}"
    after = source_hashes()
    if before != after:
        failure = (failure + "; " if failure else "") + "runtime sources changed during browser checks"
    success = failure is None and all(code == 0 for code in results.values())
    (output / "runner-result.json").write_text(json.dumps({
        "success": success, "channel": channel, "checks": results,
        "source_before": before, "source_after": after, "source_changed": before != after,
        "failure": failure, "app": {"pid": app_pid, "returncode": app_returncode,
                                    "port_released": released},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="chromium")
    parser.add_argument("--output", type=Path, default=Path("artifacts/browser-ci"))
    args = parser.parse_args()
    return run(channel=args.channel, output=args.output)


if __name__ == "__main__":
    sys.exit(main())
