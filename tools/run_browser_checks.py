"""Spawn an isolated synthetic-data app and run Playwright browser regressions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.owned_process_tree import OwnedProcessTree
CHECKS = ("check_gallery_browser.py", "check_learning_browser.py", "check_image_races_browser.py",
          "check_library_browser.py")


def harness_hashes() -> dict[str, str]:
    """Pin test implementation bytes independently of the product source tree."""
    scripts = ("run_browser_checks.py", *CHECKS, "browser_reporting.py",
               "owned_process_tree.py", "check_native_window.py")
    return {f"tools/{name}": hashlib.sha256((HARNESS_ROOT / "tools" / name).read_bytes()).hexdigest()
            for name in scripts}


def source_hashes() -> dict[str, str]:
    """Bind the browser evidence to the exact runtime files it exercised."""
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT / "src").rglob("*"))
            if path.suffix in {".py", ".js", ".css"}}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def child_environment(*, acceptance_nonce: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    # Exercise the same tree that source_hashes() records, even when the
    # caller has another checkout on PYTHONPATH or invokes us from elsewhere.
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env.pop("PV_SYNTHETIC_BROWSER_OWNER_NONCE", None)
    if acceptance_nonce is not None:
        env["PV_SYNTHETIC_BROWSER_OWNER_NONCE"] = acceptance_nonce
    return env


def verify_owned_listener(url: str, app: subprocess.Popen, nonce: str) -> dict[str, int]:
    """Reject a foreign endpoint even when it reports the expected healthz."""
    if app.poll() is not None:
        raise RuntimeError("test app exited before owner handshake")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url + "/_browser_acceptance/owner",
                                     headers={"X-Acceptance": nonce})
    with opener.open(request, timeout=5) as response:
        identity = json.loads(response.read())
    if (identity.get("synthetic") is not True or
            app.pid not in (identity.get("pid"), identity.get("parent_pid")) or
            app.poll() is not None):
        raise RuntimeError("synthetic browser listener does not belong to this run")
    return {"listener_pid": identity["pid"], "listener_parent_pid": identity["parent_pid"]}


def request_owned_shutdown(url: str, app: subprocess.Popen, nonce: str) -> bool:
    """Request a normal shutdown only from the authenticated owned endpoint."""
    if app.poll() is not None:
        return app.returncode == 0
    verify_owned_listener(url, app, nonce)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url + "/_browser_acceptance/shutdown",
                                     headers={"X-Acceptance": nonce}, data=b"", method="POST")
    with opener.open(request, timeout=5) as response:
        accepted = json.loads(response.read())
    if accepted != {"stopping": True}:
        raise RuntimeError("owned test shutdown not acknowledged")
    try:
        return app.wait(timeout=20) == 0
    except subprocess.TimeoutExpired:
        return False


def wait_for_health(url: str, app: subprocess.Popen, timeout: float = 35.0,
                    *, expected_data: str = "synthetic") -> None:
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
                    "status": "ok", "mode": "demo" if expected_data == "synthetic" else "readonly",
                    "data": expected_data
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


def make_process_tracker(process):
    return OwnedProcessTree(process)


def run_owned_checker(command: list[str], stderr_log, timeout: float = 240) -> tuple[int, dict]:
    """Wait for a checker's own Chrome child tree, fail closed on any survivor."""
    checker = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=stderr_log)
    tree = None
    returncode = -1
    timed_out = False
    forced_cleanup = False
    try:
        tree = make_process_tracker(checker)
        if tree.errors:
            raise RuntimeError("checker process identity unknown")
        try:
            returncode = checker.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        if checker.poll() is None:
            # Cleanup also applies when the tracker itself raised before wait.
            # This bound Popen handle is ours; never signal an enumerated PID.
            forced_cleanup = True
            stop_child(checker)
        receipt = (tree.finish() if tree is not None else
                   {"observed_count": 0, "remaining_count": None,
                    "errors": ["tracker_unavailable"]})
    receipt["timeout"] = timed_out
    receipt["forced_cleanup"] = forced_cleanup
    receipt["checker_returncode"] = checker.poll()
    if timed_out or forced_cleanup or receipt["errors"] or receipt["remaining_count"] != 0:
        return -1, receipt
    return returncode, receipt


def run(*, channel: str = "chromium", output: Path = Path("artifacts/browser-ci")) -> int:
    if (output / "runner-result.json").exists():
        raise ValueError("existing browser evidence cannot be overwritten")
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    harness_before = harness_hashes()
    nonce = secrets.token_hex(24)
    port = free_port()
    url = f"http://{HOST}:{port}"
    app = None
    app_tree = None
    app_tree_receipt = None
    app_pid: int | None = None
    app_returncode: int | None = None
    released = False
    listener_owner: dict[str, int] | None = None
    graceful_shutdown = False
    forced_cleanup = False
    results: dict[str, int | None] = {name: None for name in CHECKS}
    checker_trees: dict[str, dict] = {}
    failure: str | None = None
    # Process diagnostics remain local; CI uploads only browser screenshots and JSON.
    with (output / "app-stderr.log").open("wb") as app_err:
        try:
            app = subprocess.Popen(
                [sys.executable, "-m", "pixivvault_nicegui", "--host", HOST,
                 "--port", url.rsplit(":", 1)[-1]],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=app_err, cwd=str(ROOT), env=child_environment(acceptance_nonce=nonce),
            )
            app_pid = app.pid
            app_tree = make_process_tracker(app)
            if app_tree.errors:
                raise RuntimeError("owned app identity unavailable")
            wait_for_health(url, app)
            listener_owner = verify_owned_listener(url, app, nonce)
            if not app_tree.observed_endpoint(listener_owner["listener_pid"]):
                raise RuntimeError("listener PID/birth not observed in the owned app tree")
            for name in CHECKS:
                destination = output / name.removeprefix("check_").removesuffix("_browser.py")
                command = [sys.executable, str(Path(__file__).with_name(name)),
                           "--channel", channel, "--url", url, "--output", str(destination)]
                with (output / f"{name}.stderr.log").open("wb") as err:
                    try:
                        results[name], checker_trees[name] = run_owned_checker(command, err)
                    except Exception:
                        results[name] = -1
                        raise
                # Every check must pass; still run the next check for diagnostic coverage.
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if app is not None and listener_owner is not None:
                    graceful_shutdown = request_owned_shutdown(url, app, nonce)
                if app is not None and app.poll() is None:
                    forced_cleanup = True
                    stop_child(app)
            except Exception as exc:
                failure = (failure + "; " if failure else "") + f"cleanup: {exc}"
                if app is not None and app.poll() is None:
                    forced_cleanup = True
                    try:
                        stop_child(app)
                    except Exception as inner:
                        failure = (failure + "; ") + f"cleanup fallback: {inner}"
            if app is not None:
                app_returncode = app.poll()
                if app_returncode is None:
                    failure = (failure + "; " if failure else "") + "app root still alive"
                if listener_owner is not None and (not graceful_shutdown or forced_cleanup or app_returncode != 0):
                    failure = (failure + "; " if failure else "") + "owned app did not exit normally"
            if app_tree is not None:
                app_tree_receipt = app_tree.finish()
                if app_tree_receipt["errors"] or app_tree_receipt["remaining_count"] != 0:
                    failure = (failure + "; " if failure else "") + "owned app descendant remains or cannot be proven closed"
            try:
                released = port_released(port)
                if not released:
                    failure = (failure + "; " if failure else "") + "app port still accepting connections"
            except Exception as exc:
                failure = (failure + "; " if failure else "") + f"port cleanup: {exc}"
    after = source_hashes()
    harness_after = harness_hashes()
    if before != after:
        failure = (failure + "; " if failure else "") + "runtime sources changed during browser checks"
    if harness_before != harness_after:
        failure = (failure + "; " if failure else "") + "checker or runner changed during browser checks"
    success = (failure is None and listener_owner is not None and graceful_shutdown
               and not forced_cleanup and app_returncode == 0 and released
               and app_tree_receipt is not None
               and app_tree_receipt["remaining_count"] == 0 and not app_tree_receipt["errors"]
               and all(code == 0 for code in results.values())
               and len(checker_trees) == len(CHECKS)
               and all(record["remaining_count"] == 0 and not record["errors"]
                       for record in checker_trees.values()))
    (output / "runner-result.json").write_text(json.dumps({
        "success": success, "channel": channel, "checks": results,
        "source_before": before, "source_after": after, "source_changed": before != after,
        "harness_before": harness_before, "harness_after": harness_after,
        "harness_changed": harness_before != harness_after,
        "checker_trees": checker_trees,
        "failure": failure, "app": {"pid": app_pid, "returncode": app_returncode,
                                    "port_released": released, "listener_owner": listener_owner,
                                    "graceful_shutdown": graceful_shutdown,
                                    "forced_cleanup": forced_cleanup,
                                    "owned_tree": app_tree_receipt},
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
