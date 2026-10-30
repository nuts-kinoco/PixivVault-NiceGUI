"""Exercise real packaged MP4 selection, playback and restart on synthetic data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_native_window import (
    acknowledge_owned_close_confirmation, adopt_descendants, close_owned_window,
    living_pids, owned_window_identity, owned_windows, process_identity,
    process_pairs, terminate_owned, valid_owned,
)
from tools.native_restore_gate import run_restore_gate
from tools.native_ui_guard import NativeUiGuard
from tools.run_browser_checks import ROOT, child_environment, free_port, port_released


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def saved_mp4(root):
    """Read only our generated media after the native app releases its owner."""
    import cv2
    receipts = sorted((root / "mp4s").glob("*/receipt.json"))
    if len(receipts) != 1:
        raise AssertionError("expected one verified MP4 generation")
    packet = json.loads(receipts[0].read_text(encoding="utf-8"))
    folder = receipts[0].parent
    movie = folder / "animation.mp4"
    decoded = folder / "decoded_frames.zip"
    assert packet["mp4_sha256"] == sha(movie)
    assert packet["decoded_sha256"] == sha(decoded)
    assert re.fullmatch(r"frames/[a-f0-9]{32}/receipt\.json", packet["source_receipt"])
    assert packet["source_sha256"] == sha(root / "media" / packet["source_receipt"])
    report = packet["report"]
    assert report["codec"] == "mp4v"
    assert report["timing"] == "legacy-average-delay-cfr-clamped-1-60"
    assert report["source_duration_ms"] == 300
    capture = cv2.VideoCapture(str(movie))
    colors = []
    try:
        assert capture.isOpened()
        fps = capture.get(cv2.CAP_PROP_FPS)
        assert abs(fps - 10) < 0.001
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            assert frame.shape[:2] == (64, 96)
            colors.append(frame.mean(axis=(0, 1)).tolist())
    finally:
        capture.release()
    assert len(colors) == 3
    # Actual decoded BGR red, green, blue; green fixture uses Pillow's 128.
    for actual, expected in zip(colors, ((0, 0, 255), (0, 128, 0), (255, 0, 0)), strict=True):
        assert all(abs(a - b) < 12 for a, b in zip(actual, expected, strict=True))
    media = {}
    for name in ("mp4s", "media/frames"):
        assert (root / name).is_dir()
        for path in sorted((root / name).rglob("*")):
            if path.is_file():
                if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                    raise AssertionError("unexpected media reparse point")
                media[path.relative_to(root).as_posix()] = sha(path)
    return {"media": media, "receipt_sha256": sha(receipts[0]), "report": report,
            "decoded_frame_count": len(colors), "decoded_bgr_means": colors, "fps": fps}


def inspect(page, guard, evidence, restart):
    from playwright.sync_api import expect
    run = guard.run
    run(page.get_by_test_id("tab-download").click)
    run(page.get_by_test_id("ugoira-work-id").fill, "123")
    run(page.get_by_test_id("ugoira-author-id").fill, "91")
    run(page.get_by_test_id("ugoira-format").click)
    run(page.get_by_role("option", name="MP4", exact=True).click)
    run(page.get_by_test_id("ugoira-preview-button").click)
    run(expect(page.get_by_test_id("ugoira-preview")).to_contain_text, "ID 123")
    run(expect(page.get_by_test_id("ugoira-preview")).to_contain_text, "MP4")
    if restart:
        run(expect(page.get_by_test_id("ugoira-submit")).to_be_disabled)
        run(expect(page.get_by_test_id("ugoira-notice")).to_contain_text, "保存済み")
    else:
        run(expect(page.get_by_test_id("ugoira-submit")).to_be_enabled)
        run(page.get_by_test_id("ugoira-submit").click)
        run(expect(page.get_by_test_id("ugoira-notice")).to_contain_text, "キューに追加しました")
    run(page.get_by_test_id("tab-queue").click)
    row = page.get_by_test_id("queue-view").get_by_test_id("job-row").filter(
        has_text="うごイラ · MP4 · ID 123")
    run(expect(row).to_have_count, 1)
    run(expect(row).to_have_attribute, "data-state", "done", timeout=60000)
    run(expect(row.get_by_test_id("job-save-result")).to_contain_text, "保存済み")
    job = run(row.get_attribute, "data-job-id")
    run(page.get_by_test_id("tab-library").click)
    button = page.get_by_test_id("ugoira-read-123-mp4")
    run(expect(button).to_be_visible)
    run(button.click)
    canvas = page.get_by_test_id("ugoira-canvas")
    run(expect(canvas).to_be_visible)
    run(page.wait_for_function,
        "Number(document.querySelector('[data-testid=ugoira-canvas]').dataset.elapsed)>350")
    motion_script = """async c => {
        const start=Number(c.dataset.elapsed), colors=new Map();
        for(let i=0;i<36;i++) {
            const p=Array.from(c.getContext('2d').getImageData(0,0,1,1).data);
            if(p[3]===255 && Math.max(...p.slice(0,3))>80) {
                const channel=p.slice(0,3).indexOf(Math.max(...p.slice(0,3)));
                colors.set(channel,p);
            }
            await new Promise(r=>setTimeout(r,25));
        }
        return {start,end:Number(c.dataset.elapsed),colors:Array.from(colors.values()),samples:36};
    }"""
    playing = run(canvas.evaluate, motion_script, with_timeout=False)
    assert playing["end"] > playing["start"] and len(playing["colors"]) >= 2
    run(page.get_by_test_id("ugoira-play-toggle").click)
    run(expect(canvas).to_have_attribute, "data-paused", "true")
    pixels = run(canvas.evaluate, """async c => {
        const before=c.dataset.elapsed;
        const first=Array.from(c.getContext('2d').getImageData(0,0,1,1).data);
        await new Promise(r=>setTimeout(r,180));
        const p=Array.from(c.getContext('2d').getImageData(0,0,1,1).data);
        return {paused:before===c.dataset.elapsed,pixelStable:JSON.stringify(first)===JSON.stringify(p),
                width:c.width,height:c.height,pixel:p};
    }""", with_timeout=False)
    assert pixels["paused"] and pixels["pixelStable"] and pixels["width"] == 96 and pixels["height"] == 64
    assert max(pixels["pixel"][:3]) > 80 and pixels["pixel"][3] == 255
    run(page.screenshot, path=str(evidence / "playback-paused.png"), animations="disabled")
    run(page.get_by_test_id("ugoira-play-toggle").click)
    run(expect(canvas).to_have_attribute, "data-paused", "false")
    resumed = run(canvas.evaluate, motion_script, with_timeout=False)
    assert resumed["end"] > resumed["start"] and len(resumed["colors"]) >= 2
    run(page.get_by_test_id("ugoira-player-close").click)
    run(expect(canvas).to_be_hidden)
    run(expect(button).to_be_focused)
    run(page.screenshot, path=str(evidence / "library.png"), animations="disabled")
    return {"job_id": job, "completed_jobs": 1, "playback": {"playing":playing,"pause":pixels,"resumed":resumed},
            "saved_submit_disabled": restart, "focus_restored": True}


def run(exe, output, codec_python=None, *, no_force_cleanup=False):
    from playwright.sync_api import sync_playwright
    exe = exe.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sandbox = output / "managed-ugoira"
    temporary = output / "temp"
    temporary.mkdir()
    initial_exe_sha = sha(exe)
    worker_file = exe.parent / "_internal/pixivvault_nicegui/ugoira_mp4_worker.py"
    if not worker_file.is_file():
        raise AssertionError("external codec worker source was not packaged")
    results = []
    saved = None
    for restart in (False, True):
        evidence = output / ("restart" if restart else "initial")
        evidence.mkdir()
        port, debug_port = free_port(), free_port()
        while debug_port == port:
            debug_port = free_port()
        url = f"http://127.0.0.1:{port}"
        result = {"success": False, "restart": restart, "mode": "external" if codec_python else "bundled",
                  "app_port": port, "debug_port": debug_port, "console_errors": [],
                  "page_errors": [], "external_requests": [], "forced_cleanup": []}
        env = child_environment()
        env.update(TEMP=str(temporary), TMP=str(temporary),
                   WEBVIEW2_USER_DATA_FOLDER=str(output / "webview-profile"),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=
                   f"--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1")
        command = [str(exe), "--host", "127.0.0.1", "--port", str(port),
                   "--ugoira-fixture-root", str(sandbox)]
        if codec_python:
            command += ["--ugoira-codec-python", str(codec_python.resolve(strict=True))]
        process = hwnd = owner = page = None
        identities = {}
        with (evidence / "server.log").open("xb") as log, sync_playwright() as playwright:
            try:
                process = subprocess.Popen(command, cwd=exe.parent, env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                birth = process_identity(process.pid)
                if birth is None:
                    raise RuntimeError("packaged root exited before identity capture")
                identities[process.pid] = birth
                result.update(root_pid=process.pid, root_birth=birth)
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 50
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("packaged root exited before UI")
                    adopt_descendants(process.pid, identities, process_pairs(), True)
                    windows = owned_windows(valid_owned(identities))
                    if len(windows) == 1:
                        hwnd = windows[0]
                        owner = owned_window_identity(process.pid, birth, hwnd, identities)
                        try:
                            with opener.open(url + "/healthz", timeout=1) as response:
                                health = json.load(response)
                            with opener.open(f"http://127.0.0.1:{debug_port}/json/version", timeout=1) as response:
                                debug_ready = response.status == 200
                            if health.get("status") == "ok" and health.get("data") == "synthetic" and debug_ready:
                                result["health"] = health
                                break
                        except OSError:
                            pass
                    time.sleep(0.15)
                else:
                    raise TimeoutError("owned packaged window readiness")
                result.update(hwnd=hwnd, window_owner=owner)
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
                if len(browser.contexts) != 1 or len(browser.contexts[0].pages) != 1:
                    raise RuntimeError("owned WebView page is not unique")
                page = browser.contexts[0].pages[0]
                page.wait_for_url(re.compile(r"^http://(?:127\.0\.0\.1|localhost):" + str(port) + r"(?:/|$)"))
                def check_owner():
                    adopt_descendants(process.pid, identities, process_pairs(), process.poll() is None)
                    if owned_window_identity(process.pid, birth, hwnd, identities) != owner:
                        raise RuntimeError("owned window changed")
                guard = NativeUiGuard(page, port, time.monotonic() + 110, check_owner)
                def route(request_route):
                    parts = urlsplit(request_route.request.url)
                    if parts.scheme in ("data", "blob") or (
                            parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port == port):
                        request_route.continue_()
                    else:
                        result["external_requests"].append("blocked non-loopback page request")
                        request_route.abort()
                page.route("**/*", route)
                page.on("console", lambda message: result["console_errors"].append("console error") if message.type == "error" else None)
                page.on("pageerror", lambda error: result["page_errors"].append(type(error).__name__))
                result["inspection"] = inspect(page, guard, evidence, restart)
                result["viewport"] = guard.run(page.evaluate,
                    "({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})", with_timeout=False)
                assert not result["console_errors"] and not result["page_errors"] and not result["external_requests"]
                result["restore"] = run_restore_gate(process.pid, birth, hwnd, evidence / "native.json",
                    time.monotonic() + 25, {"restore_baseline": 8, "minimize": 5, "restore": 10},
                    expected_top_identity=owner)
                assert result["restore"]["passed"]
                result["success"] = True
            except Exception as error:
                result["failure_type"] = type(error).__name__
                result["failure"] = str(error)
                result["failure_location"] = [f"{Path(f.filename).name}:{f.lineno}"
                    for f in traceback.extract_tb(error.__traceback__)[-5:]]
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
                        result["confirmation"] = bool(result["wm_close"] and
                            acknowledge_owned_close_confirmation(owner, hwnd, time.monotonic() + 8))
                        process.wait(timeout=20)
                        result["normal_exit"] = bool(process.returncode == 0 and result["wm_close"] and result["confirmation"])
                    except Exception:
                        result["normal_exit"] = False
                    deadline = time.monotonic() + 8
                    while living_pids(identities) and time.monotonic() < deadline:
                        adopt_descendants(process.pid, identities, process_pairs(), process.poll() is None)
                        time.sleep(0.15)
                    if living_pids(identities):
                        if no_force_cleanup:
                            result["cleanup_failure"] = "owned_processes_remaining_no_force_cleanup"
                        else:
                            result["forced_cleanup"] = terminate_owned(identities)
                            process.wait(timeout=10)
                    result["owned_remaining"] = sorted(living_pids(identities))
                result["port_released"] = port_released(port)
                result["debug_port_released"] = port_released(debug_port)
                result["success"] &= bool(result.get("normal_exit") and result["port_released"]
                    and result["debug_port_released"] and not result["forced_cleanup"] and not result.get("owned_remaining"))
                if no_force_cleanup:
                    # The existing finally block already tried one owned WM_CLOSE
                    # and its confirmation. Never retry it or terminate survivors.
                    remaining = result.get("owned_remaining", [])
                    exit_code = process.poll() if process is not None else None
                    result["strict_cleanup"] = {
                        "policy": "normal_close_only", "forced_cleanup": list(result["forced_cleanup"]),
                        "root_pid": process.pid if process is not None else None,
                        "root_birth": identities.get(process.pid) if process is not None else None,
                        "observed_owned": [{"pid": pid, "birth": birth}
                                           for pid, birth in sorted(identities.items())],
                        "remaining_owned": [{"pid": pid, "birth": identities[pid]} for pid in remaining],
                        "root_exit_code": exit_code, "app_port": port, "debug_port": debug_port,
                        "app_port_released": result["port_released"],
                        "debug_port_released": result["debug_port_released"],
                        "needs_manual_close": bool(remaining or (process is not None and exit_code is None)),
                        "normal_exit_verified": bool(result.get("normal_exit") and exit_code == 0
                            and not remaining and result["port_released"]
                            and result["debug_port_released"] and not result["forced_cleanup"]),
                    }
                    result["success"] &= result["strict_cleanup"]["normal_exit_verified"]
        if result["success"]:
            try:
                current = saved_mp4(sandbox)
                result["saved"] = current
                if restart:
                    assert current == saved
                    assert result["inspection"]["job_id"] == results[0]["inspection"]["job_id"]
                    result["restart_media_unchanged"] = True
                saved = current
            except Exception as error:
                result.update(success=False, saved_failure=type(error).__name__)
        (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
        if not result["success"]:
            break
    success = len(results) == 2 and all(r["success"] for r in results) and sha(exe) == initial_exe_sha
    report = {"success": success, "kind": "packaged-native-synthetic",
              "codec_mode": "external" if codec_python else "bundled",
              "exe_sha256": initial_exe_sha, "worker_file_sha256": sha(worker_file),
              "checker_sha256": sha(__file__), "runs": results}
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": success, "runs": len(results), "output": str(output)}))
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codec-python", type=Path)
    parser.add_argument("--no-force-cleanup", action="store_true",
                        help="Strict acceptance: never terminate surviving owned processes; record FAIL and ownership")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    sys.exit(run(args.exe, args.output, args.codec_python, no_force_cleanup=args.no_force_cleanup))
