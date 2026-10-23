"""Offline real-Edge flow for non-bookmarked ID → queue → originals → library.

Only synthetic cookies, generated PNGs and an isolated managed directory are
used. Browser traffic is restricted to the dedicated loopback test server.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

from PIL import Image

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_managed_downloads_browser import serve
from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def verify_saved(root):
    receipts = list((root / "outputs").glob("work-*/receipt.json"))
    assert len(receipts) == 1, "direct work did not produce one verified receipt"
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["work_id"] == "321" and receipt["source"] == "pixiv-original"
    assert receipt["page_count"] == 2 and len(receipt["files"]) == 2
    for index, entry in enumerate(receipt["files"]):
        content = (receipts[0].parent / entry["name"]).read_bytes()
        assert len(content) == entry["bytes"] and hashlib.sha256(content).hexdigest() == entry["sha256"]
        with Image.open(BytesIO(content)) as decoded:
            assert decoded.size == (120, 84)
            assert decoded.getpixel((0, 0)) == ((13, 37, 211) if index == 0 else (9, 191, 53))
    return receipt


def inspect(page, url, headers, root, restart=False):
    from playwright.sync_api import expect

    page.goto(url, wait_until="domcontentloaded")
    page.get_by_test_id("tab-download").click()
    download = page.get_by_test_id("managed-download-view")
    expect(download).to_be_visible()
    if not restart:
        start_count = page.request.get(url + "/_managed_test/status", headers=headers).json()["request_count"]
        direct_id = download.get_by_test_id("managed-download-direct-id")
        direct_id.fill("not-an-id")
        download.get_by_test_id("managed-download-direct-lookup").click()
        expect(download.get_by_test_id("managed-download-notice")).to_contain_text("作品IDは")
        assert page.request.get(url + "/_managed_test/status", headers=headers).json()["request_count"] == start_count
        direct_id.fill("321")
        download.get_by_test_id("managed-download-direct-lookup").click()
        expect(download.get_by_test_id("managed-download-notice")).to_contain_text("作品を確認しました", timeout=20000)
        expect(download.get_by_test_id("managed-download-count")).to_have_text("1 / 1 件表示")
        expect(download.get_by_test_id("managed-download-search")).to_have_value("321")
        other = page.context.new_page()
        try:
            other.goto(url, wait_until="domcontentloaded")
            other.get_by_test_id("tab-download").click()
            another = other.get_by_test_id("managed-download-view")
            expect(another.get_by_test_id("managed-download-count")).to_have_text("0 / 0 件表示")
            another.get_by_test_id("managed-download-search").fill("321")
            expect(another.get_by_test_id("managed-download-count")).to_have_text("0 / 0 件表示")
            expect(another.get_by_test_id("managed-download-selection")).to_have_text("対象 0 件")
        finally:
            other.close()
        row = download.locator('.pv-download-item[data-work-id="321"]')
        expect(row).to_be_visible()
        row.get_by_test_id("managed-download-select").click()
        expect(download.get_by_test_id("managed-download-selection")).to_have_text("対象 1 件")
        download.get_by_test_id("managed-download-submit").click()
        expect(download.get_by_test_id("managed-download-notice")).to_contain_text("1件をキュー", timeout=20000)

    page.get_by_test_id("tab-queue").click()
    queue = page.get_by_test_id("queue-view")
    expect(queue.get_by_test_id("queue-filter-done")).to_contain_text("完了 1", timeout=30000)
    page.get_by_test_id("tab-library").click()
    library = page.get_by_test_id("library-view")
    if not restart:
        library.get_by_test_id("library-db-refresh").click()
    author = library.locator('.pv-library-author-card[data-author-id="24"]')
    expect(author).to_be_visible(timeout=20000)
    author.get_by_test_id("library-author-open").click()
    pages = library.locator('.pv-library-page-card[data-work-id="321"]')
    expect(pages).to_have_count(2)
    pages.first.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible()
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    for index, rgb in enumerate(([13, 37, 211], [9, 191, 53])):
        expect(viewer.locator(".pv-counter")).to_have_text(f"{index+1}/2")
        expect(image).to_have_attribute("src", re.compile(rf"/media/321/{index}/view\.png\?catalog="))
        image.evaluate("img => img.decode()")
        actual = image.evaluate("""img => { const c=document.createElement('canvas');
            c.width=c.height=1; const x=c.getContext('2d');x.drawImage(img,0,0,1,1);
            return Array.from(x.getImageData(0,0,1,1).data).slice(0,3); }""")
        assert actual == rgb, "wrong managed original page pixels"
        if index == 0:
            page.keyboard.press("ArrowRight")
    viewer.get_by_role("button", name="閉じる", exact=True).click()
    expect(pages.first).to_be_focused()
    page.get_by_test_id("tab-download").click()
    if not restart:
        expect(download.get_by_test_id("managed-download-search")).to_have_value("321")
    assert verify_saved(root)["page_count"] == 2
    status = page.request.get(url + "/_managed_test/status", headers=headers).json()
    assert status["all_requests_get"] and status["allowed_https_hosts_only"]
    assert status["image_requests_cookie_free"]
    assert status["original_gets"].get("321", 0) == (0 if restart else 2)
    if restart:
        assert status["request_count"] == 0, "restart contacted the remote source"
    return {"restart": restart, "source": "direct-id-321", "pages": 2,
            "image_pixels_verified": True, "library_and_focus": True,
            "original_gets": status["original_gets"].get("321", 0),
            "cross_client_unlooked_up_hidden": True}


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
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=None if channel == "chromium" else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    root = (output / f"managed-{width}x{height}").resolve()
                    root.mkdir()
                    for restart in (False, True):
                        port, nonce = free_port(), secrets.token_hex(16)
                        url = f"http://127.0.0.1:{port}"
                        headers = {"X-Acceptance": nonce}
                        command = [sys.executable, str(Path(__file__).with_name("check_managed_downloads_browser.py")),
                            "--serve", str(port), "--root", str(root), "--nonce", nonce]
                        process, context = None, None
                        try:
                            with (output / f"server-{width}-{restart}.log").open("wb") as log:
                                process = subprocess.Popen(command, env=child_environment(),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(url, process, expected_data="pixiv-readonly")
                            context = browser.new_context(viewport={"width": width, "height": height})
                            def only_local(route):
                                parsed = urlsplit(route.request.url)
                                if (parsed.scheme, parsed.netloc) == ("http", f"127.0.0.1:{port}"):
                                    route.continue_()
                                else:
                                    result["foreign_requests"].append("blocked")
                                    route.abort()
                            context.route("**/*", only_local)
                            page = context.new_page()
                            page.on("console", lambda m: result["console_errors"].append(m.text[:250]) if m.type == "error" else None)
                            page.on("pageerror", lambda e: result["page_errors"].append(str(e)[:250]))
                            entry = inspect(page, url, headers, root, restart=restart)
                            result["viewports"].append({"viewport": [width, height], **entry})
                            context.close()
                            context = None
                            import urllib.request
                            req = urllib.request.Request(url + "/_managed_test/shutdown",
                                data=b"", headers=headers, method="POST")
                            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5):
                                pass
                            process.wait(timeout=15)
                            result["normal_exit"].append(process.returncode == 0)
                        finally:
                            if context is not None:
                                context.close()
                            stop_child(process)
                            result["ports_released"].append(port_released(port))
            finally:
                browser.close()
        assert not result["console_errors"] and not result["page_errors"]
        assert not result["foreign_requests"]
        assert len(result["normal_exit"]) == 4 and all(result["normal_exit"])
        assert all(result["ports_released"])
        assert before == source_hashes()
        result["success"] = True
    except Exception as exc:
        result["failure"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    result["source_hashes"] = before
    (output / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("msedge", "chromium"), default="chromium")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    raise SystemExit(run(options.channel, options.output))
