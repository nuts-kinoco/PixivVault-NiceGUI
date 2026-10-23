"""Offline browser acceptance for managed original downloads; no real Pixiv I/O."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from PIL import Image

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_live_bookmark_browser import (SyntheticPixivTransport,
    write_synthetic_cookie)
from tools.run_browser_checks import (child_environment, free_port, port_released,
    source_hashes, stop_child, wait_for_health)

ORIGINAL = "https://i.pximg.net/img-original/img/2026/10/01/12/00/00/{wid}_p{page}.png"


def png(size, color):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, "PNG")
    return stream.getvalue()


class ManagedTransport(SyntheticPixivTransport):
    def __init__(self):
        super().__init__("next")
        self.original_gets = Counter()
        self.regular_gets = Counter()

    def __call__(self, request, timeout, limit, deadline):
        url = urlsplit(request.full_url)
        if url.hostname == self.IMAGE_HOST:
            self.request_count += 1
            self.all_requests_get &= request.get_method() == "GET"
            self.image_requests_cookie_free &= not request.has_header("Cookie")
            self.allowed_https_hosts_only &= (url.scheme == "https" and url.port is None
                and url.username is None and url.password is None)
            if not (self.all_requests_get and self.image_requests_cookie_free
                    and self.allowed_https_hosts_only):
                raise AssertionError("invalid CDN request")
            match = re.fullmatch(
                r"/img-original/img/2026/10/01/12/00/00/([0-9]+)_p([0-9]+)\.png", url.path)
            if match:
                self.original_gets[match.group(1)] += 1
                return png((120, 84), (13, 37, 211) if match.group(2) == "0" else (9, 191, 53))
            self.regular_gets[url.path] += 1
            return png((48, 32), (230, 29, 17))
        if url.path in ("/ajax/illust/321", "/ajax/illust/322"):
            self.request_count += 1
            self.all_requests_get &= request.get_method() == "GET"
            self.allowed_https_hosts_only &= (url.scheme == "https" and
                url.hostname == self.API_HOST and url.port is None and
                url.username is None and url.password is None)
            if not (request.has_header("Cookie") and self.all_requests_get
                    and self.allowed_https_hosts_only):
                raise AssertionError("direct artwork metadata request invalid")
            wid = url.path.rsplit("/", 1)[-1]
            return self._packet({
                "id": wid, "title": "架空のID指定作品", "userId": "24",
                "userName": "架空の直接作者", "tags": {"tags": [{"tag": "合成"}]},
                "illustType": 2 if wid == "322" else 0, "pageCount": 2,
                "xRestrict": 0, "createDate": "2026-10-23T00:00:00+09:00",
                "urls": {"small": f"https://i.pximg.net/synthetic/{wid}.png"},
            })
        if url.path.startswith("/ajax/illust/") and url.path.endswith("/pages"):
            self.request_count += 1
            self.all_requests_get &= request.get_method() == "GET"
            self.allowed_https_hosts_only &= (
                url.scheme == "https" and url.hostname == self.API_HOST
                and url.port is None and url.username is None and url.password is None)
            if not (request.has_header("Cookie") and self.all_requests_get
                    and self.allowed_https_hosts_only):
                raise AssertionError("original pages request invalid")
            wid = url.path.split("/")[3]
            count = 2 if wid in {"202", "321"} else 1
            return self._packet([{"urls": {
                "regular": f"https://i.pximg.net/synthetic/{wid}-page{index}.png",
                "original": ORIGINAL.format(wid=wid, page=index)}} for index in range(count)])
        return super().__call__(request, timeout, limit, deadline)


def verify_receipts(root, expected_jobs=1):
    receipts = sorted((root / "outputs").glob("work-*/receipt.json"))
    if len(receipts) != 1:
        raise AssertionError(f"expected one reusable receipt, got {len(receipts)}")
    data = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["source"] == "pixiv-original"
    assert data["work_id"] == "202" and data["page_count"] == 2
    assert len(data["files"]) == 2
    for index, entry in enumerate(data["files"]):
        payload = (receipts[0].parent / entry["name"]).read_bytes()
        assert len(payload) == entry["bytes"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        with Image.open(BytesIO(payload)) as image:
            assert image.size == (120, 84)
            assert image.getpixel((0, 0)) == ((13, 37, 211) if index == 0 else (9, 191, 53))
    return receipts[0]


def serve(port, root, nonce):
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView
    selections = []
    original_select = ManagedDownloadView._select_event
    def traced_select(view, event, work_id, session):
        selections.append({"payload": event.args, "epoch": view.epoch,
                           "revision": view.selection_revision, "active": view.active,
                           "busy": view.busy, "current_session": session is view._render_session,
                           "candidate_present": work_id in view.records,
                           "root_props": dict(view.root.props)})
        return original_select(view, event, work_id, session)
    ManagedDownloadView._select_event = traced_select
    with tempfile.TemporaryDirectory(prefix="pv-synthetic-cookie-") as cookie_dir:
        injected = ManagedTransport()
        client = LivePixivReadClient(write_synthetic_cookie(cookie_dir),
                                     transport=injected)
        try:
            register_pages(live_client=client, pixiv_download_root=root)

            @app.get("/_managed_test/status")
            def status(x_acceptance: str | None = Header(default=None)):
                if x_acceptance != nonce:
                    raise HTTPException(403)
                t = injected
                return {"synthetic": True, "original_gets": dict(t.original_gets),
                        "request_count": t.request_count,
                        "regular_gets": sum(t.regular_gets.values()),
                        "all_requests_get": t.all_requests_get,
                        "allowed_https_hosts_only": t.allowed_https_hosts_only,
                        "image_requests_cookie_free": t.image_requests_cookie_free,
                        "routes": t.route_calls, "selection_events": selections}

            @app.post("/_managed_test/control")
            def control(fail: bool = False, x_acceptance: str | None = Header(default=None)):
                if x_acceptance != nonce:
                    raise HTTPException(403)
                injected.fail = fail
                return {"synthetic": True}

            @app.post("/_managed_test/shutdown")
            def shutdown(x_acceptance: str | None = Header(default=None)):
                if x_acceptance != nonce:
                    raise HTTPException(403)
                app.shutdown()
                return {"stopping": True}

            ui.run(host="127.0.0.1", port=port, show=False, reload=False,
                   binding_refresh_interval=None)
        finally:
            client.close()


def inspect_saved_library(page):
    from playwright.sync_api import expect
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    expect(library).to_be_visible()
    # A tab roundtrip deliberately retains author detail. Return through the
    # real back control before testing author selection a second time.
    if library.get_by_test_id("library-detail").is_visible():
        library.get_by_test_id("library-back").click()
    author = library.locator('.pv-library-author-card[data-author-id="23"]')
    expect(author).to_be_visible(timeout=20000)
    author.get_by_test_id("library-author-open").click()
    pages = library.locator('.pv-library-page-card[data-work-id="202"]')
    expect(pages).to_have_count(2)
    pages.first.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible()
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    for page_index, color in ((0, [13, 37, 211]), (1, [9, 191, 53])):
        expect(viewer.locator(".pv-counter")).to_have_text(f"{page_index + 1}/2")
        expect(image).to_have_attribute("src", re.compile(rf"/media/202/{page_index}/view\.png\?catalog="))
        image.evaluate("image => image.decode()")
        rgb = image.evaluate("""image => {
            const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;
            const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0,1,1);
            return Array.from(ctx.getImageData(0,0,1,1).data).slice(0,3);
        }""")
        assert rgb == color, f"incorrect saved original page {page_index}: {rgb}"
        if page_index == 0:
            page.keyboard.press("ArrowRight")
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(pages.first).to_be_focused()
    page.get_by_test_id("tab-ranking").click()
    page.get_by_test_id("tab-library").click()
    expect(pages).to_have_count(2)


def inspect(page, url, headers, root, *, restart=False):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded")
    if restart:
        status = page.request.get(url + "/_managed_test/status", headers=headers).json()
        assert status["request_count"] == 0, "restart performed network access"
        page.get_by_test_id("tab-queue").click()
        queue = page.get_by_test_id("queue-view")
        expect(queue.get_by_test_id("queue-filter-done")).to_contain_text("完了 1")
        inspect_saved_library(page)
        assert page.request.get(url + "/_managed_test/status", headers=headers).json()["request_count"] == 0
    page.get_by_test_id("tab-download").click()
    download = page.get_by_test_id("managed-download-view")
    if not restart:
        download.get_by_test_id("managed-download-refresh").click()
        expect(download.get_by_test_id("managed-download-count")).to_have_text("50 / 70 件表示", timeout=20000)
        download.get_by_test_id("managed-download-search").fill("202")
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
        expect(download.locator('.pv-download-item[data-work-id="202"]')).to_be_visible()
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
        page.request.post(url + "/_managed_test/control?fail=true", headers=headers)
        download.get_by_test_id("managed-download-refresh").click()
        expect(download.get_by_test_id("managed-download-notice")).to_contain_text("失敗", timeout=20000)
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
        page.request.post(url + "/_managed_test/control?fail=false", headers=headers)
        download.get_by_test_id("managed-download-refresh").click()
        expect(download.get_by_test_id("managed-download-notice")).to_have_text("ブックマークを更新しました", timeout=20000)
        expect(download.get_by_test_id("managed-download-refresh")).to_be_enabled()
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
    else:
        # Cached remote records need a user-triggered refresh after a restart.
        download.get_by_test_id("managed-download-refresh").click()
        expect(download.get_by_test_id("managed-download-count")).to_have_text("50 / 70 件表示", timeout=20000)
        download.get_by_test_id("managed-download-search").fill("202")
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
        expect(download.locator('.pv-download-item[data-work-id="202"]')).to_be_visible()
    checkbox = download.get_by_test_id("managed-download-select").first
    old_revision = download.get_attribute("data-selection-revision")
    checkbox.click()
    expect(checkbox).to_be_checked()
    expect(download.get_by_test_id("managed-download-selection")).to_have_text("対象 1 件")
    expect(download).not_to_have_attribute("data-selection-revision", old_revision)
    expect(download.get_by_test_id("managed-download-submit")).to_be_enabled()
    download.get_by_test_id("managed-download-submit").click()
    expect(download.get_by_test_id("managed-download-notice")).to_contain_text("1件をキュー", timeout=20000)
    page.get_by_test_id("tab-queue").click()
    queue = page.get_by_test_id("queue-view")
    expect(queue.get_by_test_id("queue-filter-done")).to_have_text(
        f"完了 {2 if restart else 1}", timeout=30000)
    page.get_by_test_id("tab-library").click()
    page.get_by_test_id("library-db-refresh").click()
    inspect_saved_library(page)
    return {"queued_done": True, "failure_cache_retained": not restart,
            "failure_recovered": not restart, "local_library_refresh": True,
            "author_two_pages": True, "viewer_original_pixels": True,
            "focus_and_tab_preserved": True}


def run(channel, output):
    if output.exists():
        raise ValueError("new --output directory required")
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    result = {"success": False, "synthetic_only": True, "viewports": [],
              "console_errors": [], "page_errors": [], "foreign_requests": [],
              "normal_exit": [], "ports_released": []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=None if channel == "chromium" else channel)
            try:
                for width, height in ((1920, 1080), (1280, 720)):
                    root = (output / f"managed-{width}x{height}").resolve()
                    root.mkdir()
                    for restart in (False, True):
                        port, nonce = free_port(), secrets.token_hex(16)
                        url = f"http://127.0.0.1:{port}"
                        headers = {"X-Acceptance": nonce}
                        command = [sys.executable, str(Path(__file__).resolve()),
                            "--serve", str(port), "--root", str(root), "--nonce", nonce]
                        process = None
                        context = None
                        try:
                            with (output / f"server-{width}-{restart}.log").open("wb") as log:
                                process = subprocess.Popen(command, env=child_environment(),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(url, process, expected_data="pixiv-readonly")
                            context = browser.new_context(viewport={"width": width, "height": height})
                            def only_local(route):
                                target = urlsplit(route.request.url)
                                if (target.scheme, target.netloc) == ("http", f"127.0.0.1:{port}"):
                                    route.continue_()
                                else:
                                    result["foreign_requests"].append("blocked")
                                    route.abort()
                            context.route("**/*", only_local)
                            page = context.new_page()
                            page.on("console", lambda msg: result["console_errors"].append(msg.text[:300]) if msg.type == "error" else None)
                            page.on("pageerror", lambda err: result["page_errors"].append(str(err)[:300]))
                            check = inspect(page, url, headers, root, restart=restart)
                            page.get_by_test_id("tab-download").click()
                            first = page.get_by_test_id("managed-download-view")
                            first.get_by_test_id("managed-download-search").fill("203")
                            expect_first = first.get_by_test_id("managed-download-count")
                            from playwright.sync_api import expect
                            expect(expect_first).to_have_text("1 / 1 件表示")
                            candidate = first.locator('.pv-download-item[data-work-id="203"]')
                            expect(candidate).to_be_visible()
                            candidate.get_by_test_id("managed-download-select").click()
                            expect(first.get_by_test_id("managed-download-selection")).to_have_text("対象 1 件")
                            other = context.new_page()
                            other.goto(url, wait_until="domcontentloaded")
                            other.get_by_test_id("tab-download").click()
                            expect(other.get_by_test_id("managed-download-selection")).to_have_text("対象 0 件")
                            expect(other.get_by_test_id("managed-download-search")).to_have_value("")
                            expect(first.get_by_test_id("managed-download-selection")).to_have_text("対象 1 件")
                            expect(first.get_by_test_id("managed-download-search")).to_have_value("203")
                            other.close()
                            status = page.request.get(url + "/_managed_test/status", headers=headers).json()
                            assert all(status[k] for k in ("all_requests_get", "allowed_https_hosts_only", "image_requests_cookie_free"))
                            if restart:
                                assert status["original_gets"].get("202", 0) == 0
                            else:
                                assert status["original_gets"].get("202") == 2
                            check.update({"viewport": [width, height], "restart": restart,
                                          "original_gets": status["original_gets"]})
                            result["viewports"].append(check)
                            verify_receipts(root)
                            context.close()
                            context = None
                            # Shutdown endpoint is protected by nonce, unlike the older fixture.
                            import urllib.request
                            request = urllib.request.Request(url + "/_managed_test/shutdown",
                                data=b"", headers=headers, method="POST")
                            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5):
                                pass
                            process.wait(timeout=15)
                            result["normal_exit"].append(process.returncode == 0)
                        finally:
                            if context is not None and process is not None and process.poll() is None:
                                try:
                                    diagnostics = page.request.get(url + "/_managed_test/status", headers=headers).json()
                                    (output / f"synthetic-diagnostic-{width}-{restart}.json").write_text(
                                        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
                                    page.screenshot(path=str(output / f"synthetic-failure-{width}-{restart}.png"))
                                except Exception:
                                    pass
                            if context is not None:
                                context.close()
                            stop_child(process)
                            result["ports_released"].append(port_released(port))
            finally:
                browser.close()
        assert not result["page_errors"] and not result["console_errors"]
        assert not result["foreign_requests"]
        assert len(result["normal_exit"]) == 4 and all(result["normal_exit"])
        assert all(result["ports_released"])
        assert before == source_hashes()
        result["success"] = True
    except Exception as exc:
        result["failure"] = f"{type(exc).__name__}: {str(exc)[:600]}"
    result["source_hashes"] = before
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="chromium")
    parser.add_argument("--output", required=False, type=Path)
    args = parser.parse_args()
    if args.serve:
        if not args.root or not args.root.is_absolute() or not args.nonce:
            parser.error("serve requires absolute root and nonce")
        serve(args.serve, args.root, args.nonce)
    else:
        if args.output is None:
            parser.error("--output is required")
        raise SystemExit(run(args.channel, args.output))
