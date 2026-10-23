"""Synthetic download/queue/retry browser acceptance with actual filesystem saves.

The deterministic gates exist only in this test server, never the product app.
No pixiv cookie, remote account, old DB or original image is used.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Event
import time
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def serve(port, sandbox, nonce):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ui.job_views import QueueView
    actions = []
    original_action = QueueView._action_event
    async def traced_action(view, event):
        actions.append({"payload": event.args, "epoch": view.epoch, "active": view.active,
                        "busy": len(view.busy), "root_id": view.root.html_id,
                        "root_props": dict(view.root.props)})
        await original_action(view, event)
    QueueView._action_event = traced_action
    register_pages(download_sandbox=sandbox)
    services = get_services(app)
    gate = Event()
    writer = services.jobs._runner
    def runner(job, context):
        if job.target_id == "demo-0002" and job.attempt == 1:
            raise OSError("synthetic first attempt failure")
        if job.target_id in {"demo-0003", "demo-0012"}:
            while not gate.wait(0.02):
                context.checkpoint()
        return writer(job, context)
    services.jobs._runner = runner
    def authorized(header):
        if not secrets.compare_digest(header or "", nonce):
            raise HTTPException(403)

    @app.get("/_jobs_test/snapshot")
    def snapshot(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        return {"jobs": [asdict(job) for job in services.jobs.snapshot()], "actions": actions}

    @app.post("/_jobs_test/gate")
    def release(open: bool = False, x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        gate.set() if open else gate.clear()
        return {"synthetic": True}

    @app.post("/_jobs_test/shutdown")
    def shutdown(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        app.shutdown()
        return {"stopping": True}

    ui.run(host="127.0.0.1", port=port, show=False, reload=False,
           binding_refresh_interval=None)


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def inspect(page, url, headers, output, width, height, sandbox):
    from playwright.sync_api import expect
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-download").click()
    download = page.get_by_test_id("download-view")
    expect(download).to_be_visible()
    # Select real fixture records in source order; 0003 blocks until explicitly released.
    for index in range(1, 12):
        download.locator(f'[data-work-id="demo-{index:04}"]').get_by_role("checkbox").click()
    expect(download.get_by_test_id("download-selection")).to_have_text("対象 11 件")
    download.get_by_test_id("download-submit").click()
    expect(download.get_by_test_id("download-notice")).to_have_text("11件をキューへ追加しました", timeout=20000)
    page.get_by_test_id("tab-queue").click()
    queue = page.get_by_test_id("queue-view")
    expect(queue.get_by_test_id("job-row")).to_have_count(8)
    expect(queue.get_by_test_id("queue-page")).to_have_text("1 / 2")
    queue.get_by_test_id("queue-next").click()
    expect(queue.get_by_test_id("job-row")).to_have_count(3)
    queue.get_by_test_id("queue-previous").click()
    queue.get_by_test_id("queue-search").fill("demo-0003")
    row = queue.get_by_test_id("job-row")
    expect(row).to_have_count(1)
    expect(row).to_have_attribute("data-state", "running", timeout=15000)
    row.get_by_test_id("job-pause").click()
    expect(row).to_have_attribute("data-state", "paused")
    # Leave/reenter while paused; the job and the local filter must survive.
    page.get_by_test_id("tab-library").click()
    expect(page.get_by_test_id("tab-library")).to_have_attribute("aria-current", "page")
    expect(queue).to_be_hidden()
    page.get_by_test_id("tab-queue").click()
    expect(page.get_by_test_id("tab-queue")).to_have_attribute("aria-current", "page")
    expect(queue).to_be_visible()
    expect(queue).to_have_attribute("data-epoch", "2")
    expect(queue.get_by_test_id("queue-search")).to_have_value("demo-0003")
    expect(row).to_have_attribute("data-state", "paused")
    row.get_by_test_id("job-resume").click()
    expect(row).to_have_attribute("data-state", "running")
    # Test a queued stop while the first active job is held.
    queue.get_by_test_id("queue-search").fill("demo-0004")
    expect(row).to_have_attribute("data-state", "queued")
    row.get_by_test_id("job-stop").click()
    expect(row).to_have_attribute("data-state", "stopping")
    page.request.post(url + "/_jobs_test/gate?open=true", headers=headers)
    expect(row).to_have_attribute("data-state", "stopped", timeout=20000)
    expect(row.get_by_test_id("job-resume")).to_be_disabled()
    queue.get_by_test_id("queue-search").fill("")
    expect(queue.get_by_test_id("queue-count")).to_have_text("11件")
    page.screenshot(path=str(output / f"queue-{width}x{height}.png"))
    page.get_by_test_id("tab-failed").click()
    failed = page.get_by_test_id("failed-view")
    expect(failed).to_be_visible()
    error_row = failed.get_by_test_id("job-row")
    expect(error_row).to_have_count(1)
    expect(error_row).to_contain_text("demo-0002")
    error_row.get_by_test_id("job-retry").click()
    expect(error_row).to_have_count(0, timeout=15000)
    # Wait on browser-observed state instead of treating a click as completion.
    page.get_by_test_id("tab-queue").click()
    expect(page.get_by_test_id("tab-queue")).to_have_attribute("aria-current", "page")
    expect(queue).to_be_visible()
    expect(queue).to_have_attribute("data-epoch", "4")
    queue.get_by_test_id("queue-search").fill("demo-0002")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("demo-0002")
    expect(row).to_have_attribute("data-state", "done", timeout=15000)
    expect(queue.get_by_test_id("queue-filter-queued")).to_have_text("待機中 0", timeout=20000)
    expect(queue.get_by_test_id("queue-filter-running")).to_have_text("実行中 0", timeout=20000)
    response = page.request.get(url + "/_jobs_test/snapshot", headers=headers)
    _assert(response.ok, "test snapshot rejected")
    jobs = response.json()["jobs"]
    _assert(sum(job["state"] == "done" for job in jobs) == 10
            and sum(job["state"] == "stopped" for job in jobs) == 1,
            "not all accepted jobs reached their intended terminal state")
    _assert(next(job for job in jobs if job["target_id"] == "demo-0002")["attempt"] == 2,
            "retry did not produce a new attempt")
    for job in jobs:
        if job["state"] == "done":
            verify_receipt(sandbox, job["result"])
    receipts_before = sorted(path.relative_to(sandbox).as_posix() for path in sandbox.rglob("receipt.json"))
    row.get_by_test_id("job-delete").click()
    page.get_by_test_id("job-delete-confirm").click()
    expect(row).to_have_count(0)
    _assert(receipts_before == sorted(path.relative_to(sandbox).as_posix() for path in sandbox.rglob("receipt.json")),
            "deleting history removed saved files")
    page.get_by_test_id("tab-download").click()
    expect(page.get_by_test_id("tab-download")).to_have_attribute("aria-current", "page")
    expect(download).to_be_visible()
    expect(download).to_have_attribute("data-epoch", "2")
    download.get_by_test_id("download-search").fill("0003")
    expect(download.get_by_test_id("download-count")).to_have_text("1 / 1 件表示")
    page.screenshot(path=str(output / f"download-{width}x{height}.png"))
    measure = page.evaluate("""() => ({w:innerWidth,h:innerHeight,
        sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight})""")
    _assert(measure["sw"] <= measure["w"] + 1 and measure["sh"] <= measure["h"] + 1,
            "job view overflows window")
    bulk_result = inspect_bulk(page, url, headers, output, width, height, sandbox)
    return {"viewport": [width, height], "initial_jobs": 11, "page_size": 8,
            "retry_attempt": 2, "stopped_not_resumable": True, "delete_kept_files": True,
            "verified_receipts": len(receipts_before), "bulk": bulk_result}


def inspect_bulk(page, url, headers, output, width, height, sandbox):
    """Exercise the original cross-page selection and batch controls through DOM."""
    from playwright.sync_api import expect
    page.request.post(url + "/_jobs_test/gate?open=false", headers=headers)
    download = page.get_by_test_id("download-view")
    download.get_by_test_id("download-search").fill("")
    expect(download.get_by_test_id("download-count")).to_have_text("50 / 120 件表示")
    for number in range(12, 25):
        download.locator(f'[data-work-id="demo-{number:04}"]').get_by_role("checkbox").click()
    expect(download.get_by_test_id("download-selection")).to_have_text("対象 13 件")
    download.get_by_test_id("download-submit").click()
    expect(download.get_by_test_id("download-notice")).to_have_text("13件をキューへ追加しました", timeout=20000)
    list_node = download.get_by_test_id("download-candidates")
    stored_scroll = list_node.evaluate("el => { window.__downloadFirst=el.firstElementChild; el.scrollTop=300; return el.scrollTop; }")
    page.get_by_test_id("tab-queue").click()
    queue = page.get_by_test_id("queue-view")
    queue.get_by_test_id("queue-search").fill("")
    queue.get_by_test_id("queue-filter-queued").click()
    expect(queue.get_by_test_id("queue-filter-queued")).to_have_attribute("aria-pressed", "true")
    expect(queue.get_by_test_id("queue-count")).to_have_text("12件")
    queue.get_by_test_id("queue-select-mode").click()
    queue.get_by_test_id("queue-select-all").click()
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("12件を選択中")
    queue.get_by_test_id("queue-next").click()
    expect(queue.get_by_test_id("job-select")).to_have_count(4)
    for checkbox in queue.get_by_test_id("job-select").all():
        expect(checkbox).to_be_checked()
    queue.get_by_test_id("queue-bulk-pause").click()
    expect(queue.get_by_test_id("queue-notice")).to_contain_text("受理12件")
    expect(queue.get_by_test_id("queue-filter-paused")).to_have_text("一時停止 12")
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("0件を選択中")
    queue.get_by_test_id("queue-filter-paused").click()
    # Wait for the new filter/render generation before issuing a new select-all.
    # Clicking the still-visible toolbar against the old DOM is correctly rejected.
    expect(queue.get_by_test_id("queue-filter-paused")).to_have_attribute("aria-pressed", "true")
    expect(queue.get_by_test_id("queue-count")).to_have_text("12件")
    queue.get_by_test_id("queue-select-all").click()
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("12件を選択中")
    queue.get_by_test_id("queue-filter-all").click()
    expect(queue.get_by_test_id("queue-filter-all")).to_have_attribute("aria-pressed", "true")
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("12件を選択中")
    queue.get_by_test_id("queue-bulk-resume").click()
    expect(queue.get_by_test_id("queue-filter-queued")).to_have_text("待機中 12")
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("0件を選択中")
    queue.get_by_test_id("queue-filter-queued").click()
    expect(queue.get_by_test_id("queue-filter-queued")).to_have_attribute("aria-pressed", "true")
    expect(queue.get_by_test_id("queue-count")).to_have_text("12件")
    queue.get_by_test_id("queue-select-all").click()
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("12件を選択中")
    queue.get_by_test_id("queue-bulk-stop").click()
    expect(queue.get_by_test_id("queue-filter-stopping")).to_have_text("停止中 12")
    page.request.post(url + "/_jobs_test/gate?open=true", headers=headers)
    expect(queue.get_by_test_id("queue-filter-running")).to_have_text("実行中 0", timeout=20000)
    expect(queue.get_by_test_id("queue-filter-stopped")).to_have_text("停止 13", timeout=20000)
    queue.get_by_test_id("queue-filter-stopped").click()
    expect(queue.get_by_test_id("queue-filter-stopped")).to_have_attribute("aria-pressed", "true")
    expect(queue.get_by_test_id("queue-count")).to_have_text("13件")
    queue.get_by_test_id("queue-select-all").click()
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("13件を選択中")
    receipts = sorted(path.relative_to(sandbox).as_posix() for path in sandbox.rglob("receipt.json"))
    _assert(len(receipts) == 11, "unexpected number of actual completed saves")
    queue.get_by_test_id("queue-bulk-delete").click()
    page.get_by_test_id("queue-bulk-delete-confirm").click()
    expect(queue.get_by_test_id("queue-count")).to_have_text("0件")
    expect(queue.get_by_test_id("queue-selection-count")).to_have_text("0件を選択中")
    _assert(receipts == sorted(path.relative_to(sandbox).as_posix() for path in sandbox.rglob("receipt.json")),
            "batch history deletion removed output files")
    for receipt in receipts:
        verify_receipt(sandbox, receipt)
    queue.get_by_test_id("queue-select-clear").click()
    queue.get_by_test_id("queue-filter-all").click()
    expect(queue.get_by_test_id("queue-count")).to_have_text("10件")
    page.screenshot(path=str(output / f"queue-bulk-{width}x{height}.png"))
    page.get_by_test_id("tab-download").click()
    _assert(list_node.evaluate("el => el.firstElementChild === window.__downloadFirst"),
            "tab return recreated download rows")
    _assert(abs(list_node.evaluate("el=>el.scrollTop") - stored_scroll) <= 1,
            "tab return lost download scroll position")
    download.get_by_test_id("download-more").click()
    expect(download.get_by_test_id("download-count")).to_have_text("100 / 120 件表示")
    return {"selected_across_pages": 12, "pause_resume_stop": True,
            "confirmed_batch_delete": 13, "saved_receipts_preserved": 11,
            "download_dom_and_scroll_retained": True, "more_after_tab_return": True}


def verify_receipt(sandbox, relative):
    path = sandbox / relative
    _assert(path.resolve().is_relative_to(sandbox.resolve()), "receipt escaped sandbox")
    data = json.loads(path.read_text(encoding="utf-8"))
    _assert(data["data"] == "synthetic" and data["page_count"] == len(data["files"]), "invalid receipt")
    for entry in data["files"]:
        payload = (path.parent / entry["name"]).read_bytes()
        _assert(len(payload) == entry["bytes"] and hashlib.sha256(payload).hexdigest() == entry["sha256"],
                "saved page differs from receipt")


def run(channel, output):
    from playwright.sync_api import expect, sync_playwright
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"success": False, "viewports": [], "console_errors": [], "page_errors": [],
              "foreign_requests": [], "normal_exit": [], "ports_released": [], "restart_retained": False}
    before = source_hashes()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=None if channel == "chromium" else channel)
            try:
                for width, height in ((1920, 1080), (1280, 720)):
                    sandbox = output / f"sandbox-{width}"
                    _assert(not sandbox.exists(), "fresh test sandbox required")
                    port, nonce = free_port(), secrets.token_hex(16)
                    url = f"http://127.0.0.1:{port}"
                    headers = {"X-Acceptance": nonce}
                    command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(port),
                               "--sandbox", str(sandbox), "--nonce", nonce]
                    with (output / f"server-{width}.log").open("wb") as log:
                        process = subprocess.Popen(command, env=child_environment(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=log)
                        context = browser.new_context(viewport={"width": width, "height": height})
                        page = None
                        try:
                            wait_for_health(url, process)
                            origin = urlsplit(url)
                            def local_only(route):
                                target = urlsplit(route.request.url)
                                if (target.scheme, target.netloc) == (origin.scheme, origin.netloc):
                                    route.continue_()
                                else:
                                    result["foreign_requests"].append("blocked")
                                    route.abort()
                            context.route("**/*", local_only)
                            def capture(page):
                                page.on("console", lambda msg: result["console_errors"].append(msg.text) if msg.type == "error" else None)
                                page.on("pageerror", lambda exc: result["page_errors"].append(str(exc)))
                            context.on("page", capture)
                            page = context.new_page()
                            result["viewports"].append(inspect(page, url, headers, output, width, height, sandbox))
                            # Separate client sees shared jobs but owns its filters.
                            other = context.new_page()
                            other.goto(url, wait_until="domcontentloaded")
                            other.get_by_test_id("tab-queue").click()
                            expect(other.get_by_test_id("queue-search")).to_have_value("")
                            expect(other.get_by_test_id("queue-count")).to_have_text("10件")
                            other.close()
                            page.request.post(url + "/_jobs_test/shutdown", headers=headers)
                            process.wait(timeout=15)
                            result["normal_exit"].append(process.returncode == 0)
                        finally:
                            if page is not None and process.poll() is None:
                                try:
                                    page.screenshot(path=str(output / f"failure-{width}.png"))
                                    diagnostic = page.request.get(url + "/_jobs_test/snapshot", headers=headers)
                                    dom = page.locator('[data-testid="queue-view"]').evaluate_all(
                                        "roots=>roots.map(root=>({id:root.id,epoch:root.dataset.epoch,active:root.dataset.active,duplicates:document.querySelectorAll('#'+root.id).length}))")
                                    (output / f"failure-dom-{width}.json").write_text(
                                        json.dumps(dom, ensure_ascii=False, indent=2), encoding="utf-8")
                                    (output / f"failure-state-{width}.json").write_text(
                                        json.dumps(diagnostic.json(), ensure_ascii=False, indent=2), encoding="utf-8")
                                    page.request.post(url + "/_jobs_test/shutdown", headers=headers)
                                    process.wait(timeout=15)
                                except Exception:
                                    pass
                            context.close()
                            stop_child(process)
                            result["ports_released"].append(port_released(port))
                # Check durable records using the production service after both apps exited.
                from pixivvault_nicegui.services import ApplicationServices
                with_services = ApplicationServices(download_sandbox=output / "sandbox-1920")
                try:
                    jobs = with_services.jobs.snapshot()
                    _assert(len(jobs) == 10 and all(job.state in {"done", "stopped"} for job in jobs),
                            "restart changed completed or interrupted history")
                    _assert(with_services.jobs._thread is None, "restart silently ran a job")
                    result["restart_retained"] = True
                finally:
                    with_services.close()
            finally:
                browser.close()
        _assert(not result["console_errors"] and not result["page_errors"], "unexpected browser error")
        _assert(not result["foreign_requests"], "unexpected external request")
        _assert(all(result["normal_exit"]) and len(result["normal_exit"]) == 2, "normal shutdown not proven")
        _assert(all(result["ports_released"]), "test port still listening")
        _assert(before == source_hashes(), "source changed during test")
        result["success"] = True
    except Exception as exc:
        result["failure"] = f"{type(exc).__name__}: {exc}"
    result["source_hashes"] = before
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": result["success"], "viewports": len(result["viewports"]),
                      "failure": result.get("failure")}, ensure_ascii=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--sandbox", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="chromium")
    parser.add_argument("--output", type=Path, default=Path("artifacts/browser-ci/download-jobs"))
    args = parser.parse_args()
    if args.serve:
        if args.sandbox is None or not args.sandbox.is_absolute() or not args.nonce:
            parser.error("test server requires explicit sandbox and nonce")
        serve(args.serve, args.sandbox, args.nonce)
    else:
        raise SystemExit(run(args.channel, args.output))
