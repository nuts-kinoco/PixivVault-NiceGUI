"""Offline M4 browser acceptance: real clicks, saved proofs, controls and restart.

Only this test server adds nonce-protected fixture gates and inspection routes.
The application and its frozen M3 services are exercised without live APIs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
from threading import Event
import time
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health,
)

ROOT = Path(__file__).resolve().parents[1]
FORMATS = {"EPUB": "EPUBのみ (推奨)", "TXT": "TXTのみ", "BOTH": "EPUBとTXT両方"}


def serve(port, sandbox, nonce, *, native=False):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services

    register_pages(novel_fixture_root=sandbox)
    services = get_services(app)
    from pixivvault_nicegui.ui.job_views import QueueView
    actions = []
    original_action = QueueView._action_event
    async def traced_action(view, event):
        actions.append({"payload": event.args, "epoch": view.epoch,
                        "view_generation": view.view_generation, "active": view.active})
        await original_action(view, event)
    QueueView._action_event = traced_action
    gate = Event()
    writer = services.jobs._runner
    durations = []

    def runner(job, context):
        if job.target_id == "123:TXT" and job.attempt == 1:
            raise OSError("synthetic first attempt failure")
        if job.target_id in {"123:EPUB", "124:BOTH"}:
            while not gate.wait(0.02):
                context.checkpoint()
        started = time.perf_counter()
        try:
            return writer(job, context)
        finally:
            durations.append({"target": job.target_id, "attempt": job.attempt,
                              "milliseconds": (time.perf_counter() - started) * 1000})

    services.jobs._runner = runner

    def authorized(header):
        if not secrets.compare_digest(header or "", nonce):
            raise HTTPException(403)

    @app.get("/_novel_test/snapshot")
    def snapshot(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        proofs = []
        for work_id in ("123", "124"):
            for output_format in FORMATS:
                saved = services.current_novel_export(work_id, output_format)
                if saved is not None:
                    folder = (services.novel_jobs.store.root / saved.relative_receipt).parent
                    proofs.append({"work_id": work_id, "format": output_format,
                                   "receipt": saved.relative_receipt,
                                   "receipt_sha256": saved.receipt_sha256,
                                   "files": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                                             for name in saved.output_names}})
        return {"jobs": [asdict(job) for job in services.jobs.snapshot()],
                "proofs": proofs, "backend_durations": durations, "actions": actions}

    @app.post("/_novel_test/gate")
    def release(open: bool = False, x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        gate.set() if open else gate.clear()
        return {"synthetic": True}

    @app.post("/_novel_test/shutdown")
    def shutdown(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        app.shutdown()
        return {"stopping": True}

    if native:
        app.native.start_args.update(storage_path=str(sandbox.parent / "webview-profile"), private_mode=True)
    ui.run(host="127.0.0.1", port=port, show=False, reload=False, native=native,
           window_size=(1280, 720) if native else None,
           binding_refresh_interval=None)


def inspect(page, url, headers, output, *, restart=False, previous=None):
    from playwright.sync_api import expect
    expect.set_options(timeout=15000)

    def snapshot():
        response = page.request.get(url + "/_novel_test/snapshot", headers=headers)
        assert response.ok
        return response.json()

    def gate(open):
        assert page.request.post(url + "/_novel_test/gate", params={"open": str(open).lower()},
                                 headers=headers).ok

    def tab(name):
        page.get_by_test_id("tab-" + name).click()
        expect(page.get_by_test_id("tab-" + name)).to_have_attribute("aria-current", "page")

    def preview(work_id, fmt):
        tab("download")
        field = page.get_by_test_id("novel-work-id")
        field.fill(work_id)
        expect(field).to_have_value(work_id)
        expect(field).to_be_focused()
        page.get_by_test_id("novel-format").click()
        page.get_by_role("option", name=FORMATS[fmt], exact=True).click()
        button = page.get_by_test_id("novel-preview-button")
        expect(button).to_be_enabled()
        button.click()
        expect(page.get_by_test_id("novel-preview")).to_be_visible()
        expect(page.get_by_test_id("novel-preview")).to_contain_text(f"小説 {work_id} · {fmt}")

    def submit(work_id, fmt):
        preview(work_id, fmt)
        page.get_by_test_id("novel-submit").click()
        expect(page.get_by_test_id("novel-notice")).to_contain_text("キューに追加しました")
        expect(page.get_by_test_id("novel-submit")).to_be_disabled()
        tab("queue")
        search = page.get_by_test_id("queue-search")
        if search.input_value() != work_id:
            queue = page.get_by_test_id("queue-view")
            generation = queue.get_attribute("data-view-generation")
            search.fill(work_id)
            expect(queue).not_to_have_attribute("data-view-generation", generation)
        return page.get_by_test_id("queue-view").get_by_test_id("job-row").filter(
            has_text=f"小説 · {fmt} · ID {work_id}")

    def read(work_id, fmt, long=False):
        tab("library")
        read_button = page.get_by_test_id(f"novel-read-{work_id}-{fmt}")
        read_button.click()
        reader = page.get_by_test_id("novel-reader-text")
        expect(reader).to_be_visible()
        expect(reader).to_contain_text("これは合成の長文です" if long else "<script> と &")
        assert reader.locator("script").count() == 0
        if long:
            expect(page.get_by_test_id("novel-reader-next")).to_be_enabled()
            page.get_by_test_id("novel-reader-next").click()
            expect(page.get_by_test_id("novel-reader-page")).to_contain_text("2 /")
            page.get_by_test_id("novel-reader-previous").click()
            expect(page.get_by_test_id("novel-reader-page")).to_contain_text("1 /")
        if long:
            reader.hover()
            page.mouse.wheel(0, 2000)
            page.wait_for_function("document.querySelector('[data-testid=novel-reader-text]').scrollTop > 0")
        page.screenshot(path=str(output / f"reader-{work_id}-{fmt}.png"))
        if fmt == "EPUB":
            page.keyboard.press("Escape")
        else:
            page.get_by_test_id("novel-reader-close").click()
        expect(reader).to_be_hidden()
        expect(read_button).to_be_focused()

    page.goto(url, wait_until="domcontentloaded")
    if restart:
        actual = snapshot()
        assert actual["proofs"] == previous["proofs"], "restart changed verified outputs"
        assert [(j["job_id"], j["state"], j["attempt"]) for j in actual["jobs"]] == [
            (j["job_id"], j["state"], j["attempt"]) for j in previous["jobs"]]
    else:
        assert snapshot()["jobs"] == [] and snapshot()["proofs"] == []
        tab("download")
        page.get_by_test_id("novel-work-id").fill("999")
        page.get_by_test_id("novel-preview-button").click()
        expect(page.get_by_test_id("novel-notice")).to_contain_text("作品を確認できません")
        expect(page.get_by_test_id("novel-submit")).to_be_disabled()
        preview("123", "EPUB")
        assert snapshot()["jobs"] == [] and snapshot()["proofs"] == [], "preview wrote output"
        page.screenshot(path=str(output / "preview.png"))
        row = submit("123", "EPUB")
        expect(row).to_have_attribute("data-state", "running")
        row.get_by_test_id("job-pause").click()
        expect(row).to_have_attribute("data-state", "paused")
        assert snapshot()["proofs"] == []
        tab("library")
        expect(page.get_by_test_id("novel-library-notice")).to_have_text("保存済み 0 件")
        tab("queue")
        expect(row).to_have_attribute("data-state", "paused")
        row.get_by_test_id("job-resume").click()
        expect(row).to_have_attribute("data-state", "running")
        gate(True)
        expect(row).to_have_attribute("data-state", "done")
        expect(row.get_by_test_id("job-save-result")).to_contain_text("保存済み")
        row = submit("123", "TXT")
        expect(row).to_have_attribute("data-state", "error")
        tab("failed")
        failure = page.get_by_test_id("failed-view").get_by_test_id("job-row")
        expect(failure).to_have_count(1)
        failure.get_by_test_id("job-retry").click()
        expect(failure).to_have_count(0)
        tab("queue")
        expect(row).to_have_attribute("data-state", "done")
        for work_id, fmt in (("123", "BOTH"), ("124", "EPUB")):
            row = submit(work_id, fmt)
            expect(row).to_have_attribute("data-state", "done")
        gate(False)
        row = submit("124", "BOTH")
        expect(row).to_have_attribute("data-state", "running")
        row.get_by_test_id("job-stop").click()
        expect(row).to_have_attribute("data-state", "stopped")
        expect(row.get_by_test_id("job-save-result")).not_to_contain_text("保存済み")
        actual = snapshot()
        assert len(actual["proofs"]) == 4
        assert len(actual["jobs"]) == 5
        assert [j for j in actual["jobs"] if j["target_id"] == "123:TXT"][0]["attempt"] == 2
        assert not any(p["work_id"] == "124" and p["format"] == "BOTH" for p in actual["proofs"])

    count = len(snapshot()["jobs"])
    preview("123", "EPUB")
    expect(page.get_by_test_id("novel-saved-status")).to_contain_text("保存済み")
    expect(page.get_by_test_id("novel-submit")).to_be_disabled()
    assert len(snapshot()["jobs"]) == count
    for work_id, fmt in (("123", "EPUB"), ("123", "TXT"), ("123", "BOTH"), ("124", "EPUB")):
        read(work_id, fmt, long=work_id == "124")
    expect(page.get_by_test_id("novel-library-notice")).to_have_text("保存済み 4 件")
    page.screenshot(path=str(output / "library.png"))
    # DOM-confirmed warm tab transitions; backend writer timings are recorded separately.
    timings = []
    for index in range(20):
        started = time.perf_counter()
        tab("queue" if index % 2 == 0 else "library")
        expect(page.get_by_test_id("queue-view" if index % 2 == 0 else "novel-library-view")).to_be_visible()
        timings.append((time.perf_counter() - started) * 1000)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "horizontal overflow"
    return {**snapshot(), "warm_navigation_ms": {"samples": timings,
            "p50": statistics.median(timings), "p95": sorted(timings)[18]}}


def run(channel, output):
    from playwright.sync_api import sync_playwright
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "temp"
    temporary.mkdir()
    os.environ.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE="1")
    before = source_hashes()
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
        try:
            for width, height in ((1280, 720), (1920, 1080)):
                sandbox = output / f"data-{width}"
                previous = None
                for restart in (False, True):
                    evidence = output / f"{width}-{'restart' if restart else 'initial'}"
                    evidence.mkdir()
                    port, nonce = free_port(), secrets.token_hex(24)
                    url = f"http://127.0.0.1:{port}"
                    headers = {"X-Acceptance": nonce}
                    result = {"viewport": [width, height], "restart": restart, "success": False,
                              "console_errors": [], "page_errors": [], "external_requests": []}
                    process = None
                    context = browser.new_context(viewport={"width": width, "height": height})
                    origin = urlsplit(url)
                    def route(request_route):
                        target = urlsplit(request_route.request.url)
                        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                            result["external_requests"].append(target.scheme + "://" + target.netloc)
                            request_route.abort()
                        else:
                            request_route.continue_()
                    context.route("**/*", route)
                    page = context.new_page()
                    page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
                    page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                    with (evidence / "server-stderr.log").open("wb") as stderr:
                        try:
                            process = subprocess.Popen(
                                [sys.executable, str(Path(__file__).resolve()), "--serve", str(port),
                                 "--sandbox", str(sandbox), "--nonce", nonce],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                cwd=ROOT, env=child_environment())
                            result["pid"] = process.pid
                            wait_for_health(url, process)
                            previous = inspect(page, url, headers, evidence, restart=restart, previous=previous)
                            result["inspection"] = previous
                            assert not result["console_errors"] and not result["page_errors"]
                            assert not result["external_requests"]
                            result["success"] = True
                        except Exception as error:
                            result["failure"] = f"{type(error).__name__}: {error}"
                            try:
                                response = page.request.get(url + "/_novel_test/snapshot", headers=headers)
                                result["failure_snapshot"] = response.json()
                                page.screenshot(path=str(evidence / "failure.png"))
                                (evidence / "failure-dom.html").write_text(page.content(), encoding="utf-8")
                            except Exception:
                                pass
                        finally:
                            try:
                                page.request.post(url + "/_novel_test/shutdown", headers=headers, timeout=5000)
                                process.wait(timeout=15)
                                result["normal_exit"] = process.returncode == 0
                            except Exception:
                                result["normal_exit"] = False
                            context.close()
                            stop_child(process)
                            result["port_released"] = port_released(port)
                    result["success"] &= result["normal_exit"] and result["port_released"]
                    (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    results.append(result)
                    if not result["success"]:
                        break
        finally:
            browser.close()
    after = source_hashes()
    success = len(results) == 4 and all(r["success"] for r in results) and before == after
    (output / "result.json").write_text(json.dumps({
        "success": success, "channel": channel, "runs": results,
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_before": before, "source_after": after, "source_changed": before != after,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": success, "runs": len(results), "output": str(output)}))
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="msedge")
    parser.add_argument("--output", type=Path, default=Path("artifacts/novel-browser"))
    parser.add_argument("--serve", type=int)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--native-window", action="store_true")
    args = parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:
            parser.error("test server needs an absolute isolated sandbox and nonce")
        serve(args.serve, args.sandbox, args.nonce, native=args.native_window)
        return 0
    return run(args.channel, args.output)


if __name__ == "__main__":
    sys.exit(main())