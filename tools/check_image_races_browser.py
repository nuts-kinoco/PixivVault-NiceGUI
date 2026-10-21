"""Synthetic HTTP fault injection for gallery/learning VerifiedImage.

App must already be running. Uses its own browser and closes it on every exit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

CHOICES = ("見送る", "合わない", "いいね", "SuperLike")


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


async def image_ready(page, work_id, page_no=0):
    img = page.get_by_test_id("viewer-image").locator("img:visible")
    await expect(img).to_have_attribute("src", re.compile(rf"/media/{work_id}/{page_no}/view\.png"), timeout=15000)
    await expect(page.get_by_test_id("viewer-image")).to_have_css("opacity", "1", timeout=15000)
    await expect(page.locator(".pv-viewer").get_by_text("画像読込中")).to_be_hidden(timeout=15000)
    info = await img.evaluate("(el) => ({src:el.currentSrc, complete:el.complete, width:el.naturalWidth})")
    assert_true(info["complete"] and info["width"] > 0 and f"/media/{work_id}/{page_no}/view.png" in info["src"],
                f"wrong displayed image: {info}")
    return info


async def check_decoded_page_commit(page, result):
    """Hold the real mounted img.decode, not just its HTTP fetch or a probe."""
    viewer = page.get_by_test_id('gallery-viewer')
    await image_ready(page, 'demo-0003', 0)
    old = await page.get_by_test_id('viewer-image').locator('img:visible').element_handle()
    await page.evaluate('''() => {
        const original = HTMLImageElement.prototype.decode;
        const gate = window.__pvDecodeTest = {original, entered:false, node:null};
        HTMLImageElement.prototype.decode = function() {
            const decoded = original.call(this);
            const path = new URL(this.src, document.baseURI).pathname;
            if (!this.isConnected || !this.closest('[data-testid="viewer-image"]') ||
                path !== '/media/demo-0003/1/view.png' || gate.entered) return decoded;
            gate.entered = true;
            gate.node = this;
            return decoded.then(() => new Promise((resolve, reject) => {
                gate.release = resolve;
                gate.fail = () => reject(new DOMException('injected mounted decode failure', 'EncodingError'));
            }));
        };
    }''')
    try:
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await page.wait_for_function('() => Boolean(window.__pvDecodeTest.release)')
        snapshot = await page.evaluate('''old => {
            const visible = [...document.querySelectorAll('[data-testid="viewer-image"] img')]
                .filter(img => getComputedStyle(img).visibility !== 'hidden' &&
                    getComputedStyle(img).display !== 'none' && img.getBoundingClientRect().width > 0);
            return {old_connected:old.isConnected, visible_count:visible.length,
                    same_node:visible.length === 1 && visible[0] === old,
                    paths:visible.map(img=>new URL(img.currentSrc || img.src, document.baseURI).pathname)};
        }''', old)
        result['mounted_decode_hold'] = snapshot
        assert_true(snapshot['old_connected'] and snapshot['same_node'],
                    f'previous decoded image replaced before mounted decode finished: {snapshot}')
        result['checks'].append('page transition keeps the accepted DOM image until mounted decode finishes')
        await page.evaluate('() => window.__pvDecodeTest.fail()')
        await expect(viewer.get_by_text('画像を読み込めませんでした', exact=True)).to_be_visible()
        assert_true(await old.evaluate('img => img.isConnected && img.complete && img.naturalWidth > 0'),
                    'decode failure removed the previously accepted image')
        await expect(page.get_by_test_id('viewer-image').locator('img:visible')).to_have_attribute(
            'src', re.compile(r'/media/demo-0003/0/view\.png'))
        result['checks'].append('mounted decode failure preserves the accepted page and exposes retry')
    finally:
        await page.evaluate('''() => {
            const gate = window.__pvDecodeTest;
            if (gate) { gate.release?.(); HTMLImageElement.prototype.decode = gate.original; }
        }''')
    await viewer.get_by_role('button', name='画像を再読込', exact=True).click()
    await image_ready(page, 'demo-0003', 1)
    result['checks'].append('retry commits the correctly decoded requested page')
    await viewer.get_by_role('button', name='前のページ', exact=True).click()
    await image_ready(page, 'demo-0003', 0)


async def check_superseded_candidate(page, result):
    """A accepted, B decode pending, C decode fails: A must remain the front."""
    viewer = page.get_by_test_id('gallery-viewer')
    old = await page.get_by_test_id('viewer-image').locator('img:visible').element_handle()
    await page.evaluate('''() => {
        const original = HTMLImageElement.prototype.decode;
        const gate = window.__pvSupersededDecode = {original, entered:false, failC:true, settled:false};
        HTMLImageElement.prototype.decode = function() {
            const decoded = original.call(this);
            if (!this.isConnected || !this.closest('[data-testid="viewer-image"]')) return decoded;
            const path = new URL(this.src, document.baseURI).pathname;
            if (path === '/media/demo-0003/1/view.png' && !gate.entered) {
                gate.entered = true;
                return decoded.then(() => new Promise(resolve => {gate.release = resolve;}))
                    .finally(() => {gate.settled = true;});
            }
            if (path === '/media/demo-0003/2/view.png') {
                gate.candidate = this;
                if (gate.failC) {
                    gate.failC = false;
                    return decoded.then(() => {throw new DOMException('injected newer decode failure', 'EncodingError');});
                }
            }
            return decoded;
        };
    }''')
    try:
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await page.wait_for_function('() => Boolean(window.__pvSupersededDecode.release)')
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await expect(viewer.get_by_text('画像を読み込めませんでした', exact=True)).to_be_visible()
        accepted = page.get_by_test_id('viewer-image').locator('img:visible')
        await expect(accepted).to_have_attribute('src', re.compile(r'/media/demo-0003/0/view\.png'))
        assert_true(await accepted.evaluate('(img, old) => img === old', old),
                    'failed newer request restored an unverified candidate instead of the accepted image')
        result['checks'].append('A accepted / B pending / C failed keeps exactly the accepted A node')
        await page.evaluate('() => window.__pvSupersededDecode.release()')
        await page.wait_for_function('() => window.__pvSupersededDecode.settled')
        await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        await expect(accepted).to_have_attribute('src', re.compile(r'/media/demo-0003/0/view\.png'))
        result['checks'].append('superseded mounted decode completion cannot replace the accepted front')
        await viewer.get_by_role('button', name='画像を再読込', exact=True).click()
        await image_ready(page, 'demo-0003', 2)
        assert_true(await accepted.evaluate('(img) => img === window.__pvSupersededDecode.candidate'),
                    'promotion recreated the image instead of keeping the verified node')
        result['checks'].append('successful promotion displays the exact previously decoded candidate node')
    finally:
        await page.evaluate('''() => {
            const gate = window.__pvSupersededDecode;
            if (gate) {gate.release?.(); HTMLImageElement.prototype.decode = gate.original;}
        }''')


async def run(page, result):
    state = {"fail_gallery": True, "fail_learning": True, "delay": None,
             "delay_work": "demo-0003", "learning_id": None,
             "entered": asyncio.Event(), "release": asyncio.Event()}

    async def intercept(route):
        url = route.request.url
        match = re.search(r"/media/(demo-\d+)/0/view\.png(?:\?.*)?$", url)
        if match:
            work_id = match.group(1)
            if work_id == "demo-0003" and state["fail_gallery"]:
                state["fail_gallery"] = False
                result["injections"].append("gallery demo-0003: HTTP 404 once")
                await route.fulfill(status=404, content_type="text/plain", body="injected missing image")
                return
            if work_id == state["delay_work"] and state["delay"] == "gallery":
                state["entered"].set()
                await asyncio.wait_for(state["release"].wait(), timeout=20)
                state["delay"] = None
            if state["fail_learning"] and state.get("learning_id") == work_id:
                state["fail_learning"] = False
                result["injections"].append(f"learning {work_id}: HTTP 404 once")
                await route.fulfill(status=404, content_type="text/plain", body="injected missing image")
                return
        await route.fallback()

    await page.route("**/media/*/0/view.png*", intercept)
    await page.goto(result["url"], wait_until="domcontentloaded", timeout=20000)
    await page.locator('.pv-card[data-work-id="demo-0003"]').click()
    viewer = page.locator(".pv-viewer")
    await expect(viewer).to_be_visible()
    await expect(viewer.get_by_text("画像を読み込めませんでした", exact=True)).to_be_visible(timeout=15000)
    await expect(viewer.get_by_role("button", name="画像を再読込")).to_be_visible()
    result["checks"].append("gallery HTTP404 explicitly failed with retry")
    await viewer.get_by_role("button", name="画像を再読込").click()
    await image_ready(page, "demo-0003")
    result["checks"].append("gallery retry recovers")

    # Revisit exactly the same resource; the URL fragment is allowed to change.
    await viewer.get_by_role("button", name="閉じる").click()
    await expect(viewer).to_be_hidden()
    await page.locator('.pv-card[data-work-id="demo-0004"]').click()
    await image_ready(page, "demo-0004")
    await viewer.get_by_role("button", name="閉じる").click()
    await expect(viewer).to_be_hidden()
    await page.locator('.pv-card[data-work-id="demo-0003"]').click()
    await image_ready(page, "demo-0003")
    result["checks"].append("same-work revisit over the same HTTP resource")
    await check_decoded_page_commit(page, result)
    await check_superseded_candidate(page, result)

    # Use unseen resources for fault injection: a decoded image may be retained
    # even when the HTTP cache is disabled. App URLs and caches stay unchanged.
    await viewer.get_by_role("button", name="閉じる").click()
    await expect(viewer).to_be_hidden()
    state["delay_work"] = "demo-0006"
    state["delay"] = "gallery"
    state["entered"] = asyncio.Event()
    state["release"] = asyncio.Event()
    await page.locator('.pv-card[data-work-id="demo-0006"]').click()
    await asyncio.wait_for(state["entered"].wait(), timeout=15)
    # 原版は初回画像到着までビューアを表示しない。待機中Escapeで要求を取り消す。
    await expect(viewer).to_be_hidden()
    await page.keyboard.press("Escape")
    await expect(viewer).to_be_hidden()
    await page.locator('.pv-card[data-work-id="demo-0007"]').click()
    await image_ready(page, "demo-0007")
    state["release"].set()
    await page.wait_for_timeout(500)  # event round-trip allowance, not latency benchmark
    await image_ready(page, "demo-0007")
    result["checks"].append("old demo-0006 response cannot override demo-0007 ready")

    await viewer.get_by_role("button", name="閉じる").click()
    await expect(viewer).to_be_hidden()
    state["delay_work"] = "demo-0009"
    state["delay"] = "gallery"
    state["entered"] = asyncio.Event()
    state["release"] = asyncio.Event()
    await page.locator('.pv-card[data-work-id="demo-0009"]').click()
    await asyncio.wait_for(state["entered"].wait(), timeout=15)
    await expect(viewer).to_be_hidden()
    await page.keyboard.press("Escape")
    await expect(viewer).to_be_hidden()
    state["release"].set()
    await page.wait_for_timeout(500)
    await expect(viewer).to_be_hidden()
    result["checks"].append("closing viewer invalidates delayed response")

    # Fresh page avoids gallery image-cache state and starts at learning item 1.
    state["learning_id"] = "demo-0001"
    await page.goto(result["url"], wait_until="domcontentloaded")
    await page.get_by_test_id("tab-learning").click()
    root = page.locator('[tabindex="0"][data-active="true"]').filter(
        has=page.get_by_text("学習（デモ）", exact=True))
    await expect(root).to_be_visible()
    assert_true(await root.get_attribute("data-work-id") == state["learning_id"],
                "learning fixture must start at the injected work")
    await expect(root.get_by_text("画像を読み込めませんでした。再読込してください。")).to_be_visible(timeout=15000)
    for label in CHOICES:
        await expect(root.get_by_role("button", name=label, exact=True)).to_be_disabled()
    result["checks"].append("learning HTTP404 disables all four judgments")
    await root.get_by_role("button", name="画像を再読込").click()
    for label in CHOICES:
        await expect(root.get_by_role("button", name=label, exact=True)).to_be_enabled(timeout=15000)
    img = root.locator(".pv-view-art img")
    assert_true(await img.evaluate("(el) => el.complete && el.naturalWidth > 0"), "learning retry image invalid")
    result["checks"].append("learning retry restores image and judgments")


async def main_async(args, result):
    browser = context = page = None
    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    channel=args.channel if args.channel != "chromium" else None,
                    headless=not args.headed)
                context = await browser.new_context(viewport={"width": 1280, "height": 720})
                origin = urlsplit(args.url)
                if origin.scheme != 'http' or origin.hostname not in {'127.0.0.1', '::1', 'localhost'}:
                    raise ValueError('synthetic loopback server required')

                async def local_only(route):
                    requested = urlsplit(route.request.url)
                    if (requested.scheme, requested.hostname, requested.port) == (
                            origin.scheme, origin.hostname, origin.port):
                        await route.continue_()
                    else:
                        await route.abort()

                await context.route('**/*', local_only)
                page = await context.new_page()

                def console(message):
                    if message.type != "error":
                        return
                    text = message.text
                    # Only explicitly injected 404 request errors are expected.
                    location = message.location or {}
                    url = location.get("url", "")
                    if "404" in text and ("/media/" in text and "view.png" in text
                                          or "/media/" in url and "view.png" in url):
                        result["expected_404_console"].append(text)
                    else:
                        result["console_errors"].append(text)

                page.on("console", console)
                page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                await run(page, result)
                assert_true(not result["console_errors"] and not result["page_errors"],
                            "unexpected console/page errors")
                assert_true(len(result["injections"]) == 2, "both 404 injections must occur")
                result["success"] = True
            finally:
                if page is not None:
                    try:
                        await page.screenshot(path=str(args.output / "image-races.png"), full_page=True, timeout=5000)
                    except Exception as exc:
                        result["success"] = False
                        result["screenshot_error"] = str(exc)
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()
    except Exception:
        result["success"] = False
        result["failure"] = traceback.format_exc()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8089")
    parser.add_argument("--output", type=Path, default=Path("artifacts/image-races"))
    parser.add_argument("--channel", choices=("msedge", "chromium"), default="msedge")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"url": args.url, "channel": args.channel, "success": False,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "checks": [], "injections": [], "expected_404_console": [],
              "console_errors": [], "page_errors": [], "failure": None}
    asyncio.run(main_async(args, result))
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    (args.output / "success.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": result["success"], "checks": result["checks"], "failure": result["failure"]},
                     ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
