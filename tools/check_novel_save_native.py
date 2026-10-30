"""M4 source-native WebView2 fixture acceptance, with owned HWND restore/close.

A source-native result is not a packaged-build or independent-review result.
Uses only a freshly spawned app's local CDP and identity-pinned descendants.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import time
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_novel_save_browser import ROOT, inspect
from tools.check_native_window import (
    adopt_descendants, close_owned_window, living_pids, owned_window_identity,
    owned_windows, process_identity, process_pairs, terminate_owned, valid_owned,
)
from tools.native_restore_gate import run_restore_gate
from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, wait_for_health


def run(output):
    from playwright.sync_api import sync_playwright
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "temp"
    temporary.mkdir()
    os.environ.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE="1")
    sandbox = output / "data"
    previous = None
    results = []
    before = source_hashes()
    for restart in (False, True):
        evidence = output / ("restart" if restart else "initial")
        evidence.mkdir()
        port, debug_port, nonce = free_port(), free_port(), secrets.token_hex(24)
        url = f"http://127.0.0.1:{port}"
        headers = {"X-Acceptance": nonce}
        result = {"success": False, "restart": restart, "console_errors": [], "page_errors": [],
                  "external_requests": [], "forced_cleanup": []}
        env = child_environment()
        env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1")
        # WebView2 profile, crash data and Python temporary files all stay in S:.
        env["WEBVIEW2_USER_DATA_FOLDER"] = str(output / "webview-profile")
        identities = {}
        process = hwnd = owner = page = None
        with (evidence / "server.log").open("wb") as log, sync_playwright() as playwright:
            try:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "tools/check_novel_save_browser.py"), "--serve", str(port),
                     "--sandbox", str(sandbox), "--nonce", nonce, "--native-window"],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                birth = process_identity(process.pid)
                if birth is None:
                    raise RuntimeError("native root exited before identity capture")
                identities[process.pid] = birth
                result["pid"] = process.pid
                wait_for_health(url, process)
                deadline = time.monotonic() + 35
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                while time.monotonic() < deadline:
                    adopt_descendants(process.pid, identities, process_pairs(), process.poll() is None)
                    windows = owned_windows(valid_owned(identities))
                    if len(windows) == 1:
                        hwnd = windows[0]
                        owner = owned_window_identity(process.pid, birth, hwnd, identities)
                        try:
                            with opener.open(f"http://127.0.0.1:{debug_port}/json/version", timeout=1) as response:
                                if response.status == 200:
                                    break
                        except OSError:
                            pass
                    time.sleep(0.15)
                else:
                    raise TimeoutError("owned WebView2 window/CDP did not become ready")
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
                if len(browser.contexts) != 1:
                    raise RuntimeError("unique owned WebView2 context not found")
                context = browser.contexts[0]
                pages = context.pages
                if not pages:
                    pages = [context.wait_for_event("page", timeout=15000)]
                if len(pages) != 1:
                    raise RuntimeError("unique owned WebView2 page not found")
                page = pages[0]
                # CDP can be ready while the owned window is still at about:blank.
                # Wait for the app's initial navigation; never navigate an unknown page.
                page.wait_for_url(re.compile(r"^http://(?:127\.0\.0\.1|localhost):" + str(port) + r"(?:/|$)"), timeout=15000)
                def route(request_route):
                    if not request_route.request.url.startswith(url + "/"):
                        result["external_requests"].append("blocked non-loopback page request")
                        request_route.abort()
                    else:
                        request_route.continue_()
                page.route("**/*", route)
                page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                if owned_window_identity(process.pid, birth, hwnd, identities) != owner:
                    raise RuntimeError("owned window changed before inspection")
                previous = inspect(page, url, headers, evidence, restart=restart, previous=previous)
                result["inspection"] = previous
                result["viewport"] = page.evaluate("({width: innerWidth, height: innerHeight})")
                assert not result["console_errors"] and not result["page_errors"] and not result["external_requests"]
                result["restore"] = run_restore_gate(
                    process.pid, birth, hwnd, evidence / "native.json", time.monotonic() + 25,
                    {"restore_baseline": 8, "minimize": 5, "restore": 10}, expected_top_identity=owner)
                assert result["restore"]["passed"], "native minimize/restore acceptance failed"
                result["success"] = True
            except Exception as error:
                result["failure"] = f"{type(error).__name__}: {error}"
                if page is not None:
                    try:
                        page.screenshot(path=str(evidence / "failure.png"))
                    except Exception:
                        pass
            finally:
                if process is not None:
                    adopt_descendants(process.pid, identities, process_pairs(), process.poll() is None)
                    try:
                        result["wm_close"] = bool(hwnd and owner and close_owned_window(
                            process.pid, identities[process.pid], hwnd, identities, owner))
                        process.wait(timeout=20)
                        result["normal_exit"] = process.returncode == 0 and result["wm_close"]
                    except Exception:
                        result["normal_exit"] = False
                    deadline = time.monotonic() + 5
                    while living_pids(identities) and time.monotonic() < deadline:
                        time.sleep(0.15)
                    if living_pids(identities):
                        result["forced_cleanup"] = terminate_owned(identities)
                        process.wait(timeout=10)
                    result["owned_remaining"] = sorted(living_pids(identities))
                result["port_released"] = port_released(port)
                result["debug_port_released"] = port_released(debug_port)
                result["success"] &= bool(result.get("normal_exit") and result["port_released"]
                                          and result["debug_port_released"] and not result["forced_cleanup"]
                                          and not result.get("owned_remaining"))
        (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
        if not result["success"]:
            break
    after = source_hashes()
    success = len(results) == 2 and all(r["success"] for r in results) and before == after
    (output / "result.json").write_text(json.dumps({"success": success, "kind": "source-native",
        "checker_sha256": {name: hashlib.sha256((ROOT / "tools" / name).read_bytes()).hexdigest()
                           for name in ("check_novel_save_native.py", "check_novel_save_browser.py")},
        "runs": results, "source_before": before, "source_after": after, "source_changed": before != after},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": success, "runs": len(results), "output": str(output)}))
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/novel-native")
    sys.exit(run(parser.parse_args().output))