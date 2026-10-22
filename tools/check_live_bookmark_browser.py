"""Browser acceptance of the production live-bookmark path with an in-memory API double.

No real credential, account, HTTP response or image is used in this check.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import subprocess
import sys
import time

from PIL import Image

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


class SyntheticReadClient:
    """Implements the same injected read interface, with all state synthetic."""
    def __init__(self):
        self.fail = False
        self.delay = 0.0
        self.route_calls = 0
        self.completed = 0

    def verify_identity(self):
        if self.delay:
            time.sleep(self.delay)
        return "12"

    def get_bookmarked_works(self, identity, rest_type):
        self.route_calls += 1
        if rest_type == "show":
            for index in range(70):
                yield dict(id=str(202 + index), title=f"合成作品 {index:03d}",
                           user_id="23", user_name="架空作者", tags=["目標" if index == 0 else "合成"],
                           thumb_url=f"https://i.pximg.net/synthetic/{202 + index}.png",
                           page_count=2 if index == 0 else 1)

    def get_bookmarked_novels(self, identity, rest_type):
        self.route_calls += 1
        if rest_type == "hide":
            self.completed += 1
            if self.fail:
                raise RuntimeError("synthetic route failure")
        if rest_type == "show":
            yield dict(id="202", title="合成小説 同番号", user_id="23", user_name="架空作者",
                       tags=["小説"], thumb_url="https://i.pximg.net/synthetic/cover202.png")

    def get_artwork_pages(self, work_id):
        count = 2 if work_id == "202" else 1
        return tuple(f"https://i.pximg.net/synthetic/{work_id}-page{p}.png" for p in range(count))

    def get_image_bytes(self, url):
        out = BytesIO()
        Image.new("RGB", (96, 64), (0, 0, 255) if "page1" in url else (255, 0, 0)).save(out, "PNG")
        return out.getvalue()

    def close(self):
        pass


def serve(port):
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    fake = SyntheticReadClient()
    register_pages(live_client=fake)

    @app.post("/_acceptance/control")
    def control(fail: bool = False, delay_ms: int = 0):
        fake.fail = fail
        fake.delay = min(1000, max(0, delay_ms)) / 1000
        return {"synthetic": True}

    @app.get("/_acceptance/status")
    def status():
        return {"calls": fake.route_calls, "completed": fake.completed, "synthetic": True}

    @app.post("/_acceptance/shutdown")
    def shutdown():
        app.shutdown()
        return {"stopping": True}

    ui.run(host="127.0.0.1", port=port, show=False, reload=False,
           binding_refresh_interval=None)


def inspect(page, url, output, width, height):
    from playwright.sync_api import expect
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    library.get_by_test_id("library-subview-bookmarks").click()
    bookmarks = library.get_by_test_id("bookmark-view")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 71 件表示", timeout=15000)
    first = bookmarks.locator('.pv-bookmark-card[data-work-id="202"]')
    checkbox = first.get_by_role("checkbox", name="選択")
    expect(checkbox).not_to_be_checked()
    checkbox.click()
    expect(checkbox).to_be_checked()
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    bookmarks.get_by_test_id("bookmark-more").click()
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("71 / 71 件表示")
    bookmarks.get_by_test_id("bookmark-filter-toggle").click()
    filters = library.get_by_test_id("library-filter-panel")
    tag_input = filters.get_by_placeholder("タグを追加")
    tag_input.fill("目標")
    tag_input.press("Enter")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    search = bookmarks.get_by_label("ブックマーク内を検索")
    search.fill("目標")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    opener = first.get_by_test_id("bookmark-open")
    opener.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible()
    expect(viewer.locator(".pv-counter")).to_have_text("1/2")
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    expect(image).to_have_attribute("src", re.compile(r"/media/202/0/view\.png\?catalog="))
    image.evaluate("image => image.decode()")
    page.keyboard.press("ArrowRight")
    expect(viewer.locator(".pv-counter")).to_have_text("2/2")
    expect(image).to_have_attribute("src", re.compile(r"/media/202/1/view\.png\?catalog="))
    image.evaluate("image => image.decode()")
    rgb = image.evaluate("""image => {
        const canvas = document.createElement('canvas'); canvas.width=1; canvas.height=1;
        const ctx=canvas.getContext('2d'); ctx.drawImage(image,0,0,1,1);
        return Array.from(ctx.getImageData(0,0,1,1).data).slice(0,3);
    }""")
    assert rgb == [0, 0, 255], "incorrect page pixels"
    page.keyboard.press("i")
    expect(viewer.get_by_role("button", name="詳細", exact=True)).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Tab")
    expect(viewer.get_by_role("button", name="詳細", exact=True)).to_have_attribute("aria-expanded", "false")
    page.screenshot(path=str(output / f"synthetic-remote-{width}x{height}.png"))
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(opener).to_be_focused()
    page.get_by_test_id("tab-ranking").click()
    page.get_by_test_id("tab-library").click()
    expect(search).to_have_value("目標")
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    expect(library.get_by_test_id("library-filter-now-chip")).to_have_count(1)
    page.request.post(url + "/_acceptance/control?fail=true")
    bookmarks.get_by_test_id("bookmark-refresh").click()
    expect(bookmarks.get_by_test_id("bookmark-status")).to_contain_text("失敗", timeout=15000)
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    page.request.post(url + "/_acceptance/control")
    bookmarks.get_by_test_id("bookmark-refresh").click()
    expect(bookmarks.get_by_test_id("bookmark-status")).to_be_hidden(timeout=15000)
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    expect(library.get_by_test_id("library-filter-now-chip")).to_have_count(1)
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
    return {"viewport": [width, height], "remote_only_viewer": True,
            "correct_page_pixels": True, "search_selection_tab_return": True,
            "failure_preserves_cache": True, "retry_restores": True,
            "detail_keys": True, "focus_restored": True,
            "tag_filter_survives_refresh_failure_and_recovery": True}


def run(channel, output):
    from playwright.sync_api import expect, sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    process = None
    report = {"success": False, "synthetic_only": True, "viewports": []}
    try:
        with (output / "app-stderr.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve",
                                        "--port", str(port)], env=child_environment(),
                                       cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
        wait_for_health(url, process, expected_data="pixiv-readonly")
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge" if channel == "msedge" else None, headless=True)
            page_errors, external_hosts = [], set()
            def observe(page):
                page.on("pageerror", lambda error: page_errors.append(str(error)[:300]))
                page.on("request", lambda request: external_hosts.add(urlsplit(request.url).hostname)
                        if urlsplit(request.url).scheme in {"http", "https"} and
                        urlsplit(request.url).hostname not in {"127.0.0.1", "localhost"} else None)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    page = browser.new_page(viewport={"width": width, "height": height})
                    observe(page)
                    report["viewports"].append(inspect(page, url, output, width, height))
                    page.close()
                a, b = browser.new_page(), browser.new_page()
                for page in (a, b):
                    observe(page)
                    page.goto(url, wait_until="domcontentloaded")
                    page.get_by_test_id("tab-library").click()
                    page.get_by_test_id("library-subview-bookmarks").click()
                a.get_by_label("ブックマーク内を検索").fill("目標")
                expect(a.get_by_test_id("bookmark-count")).to_have_text("1 / 1 件表示")
                expect(b.get_by_test_id("bookmark-count")).to_have_text("50 / 71 件表示")
                b.locator('.pv-bookmark-card[data-work-id="202"]').get_by_test_id("bookmark-open").click()
                expect(b.get_by_test_id("library-viewer")).to_be_visible()
                image_b = b.get_by_test_id("library-viewer-image").locator("img:visible")
                old_source = image_b.get_attribute("src").split("#")[0]
                a.get_by_test_id("bookmark-refresh").click()
                expect(a.get_by_test_id("bookmark-status")).to_be_hidden(timeout=15000)
                assert b.request.get(url + old_source).status == 200
                expect(image_b).to_have_attribute("src", re.compile(re.escape(old_source)))
                report["two_clients_independent"] = True
                report["old_image_snapshot_survives_other_client_refresh"] = True
                report["page_errors"] = page_errors
                report["external_hosts"] = sorted(external_hosts)
                assert not page_errors and not external_hosts
                a.close(); b.close()
                # Do not start another gallery/socket and dozens of thumbnail
                # requests immediately before shutdown. Use a request-only
                # context after closing the UI clients instead.
                shutdown = p.request.new_context()
                try:
                    response = shutdown.post(url + "/_acceptance/shutdown", timeout=5000)
                    assert response.ok and response.json() == {"stopping": True}
                finally:
                    shutdown.dispose()
            finally:
                browser.close()
        process.wait(timeout=15)
        report["normal_exit"] = process.returncode == 0
        report["success"] = process.returncode == 0
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:600]
    finally:
        report["forced_cleanup"] = process is not None and process.poll() is None
        stop_child(process)
        report["port_released"] = port_released(port)
        report["source_changed"] = before != source_hashes()
        report["success"] &= report["port_released"] and not report["source_changed"]
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--channel", choices=["msedge", "chromium"], default="msedge")
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-bookmark-browser"))
    args = parser.parse_args()
    if args.serve:
        serve(args.port)
    else:
        raise SystemExit(run(args.channel, args.output))
