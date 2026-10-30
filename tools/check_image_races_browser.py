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
EXPECTED_404_RESOURCES = ("/media/demo-0003/0/view.png", "/media/demo-0001/0/view.png")


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
    """Hold mounted decode: a requested *different page* must not display the old one."""
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
                path !== '/media/demo-0003/1/view.png') return decoded;
            if (gate.entered) { gate.retryNode = this; return decoded; }
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
        # Product safety contract since test_viewer_original_quality.py:
        # page changes clear the old frame. Retention is only allowed for
        # same-page quality/retry, never display page zero as pending page one.
        assert_true(not snapshot['old_connected'] and snapshot['visible_count'] == 0,
                    f'wrong-page frame was visible while next page decode was pending: {snapshot}')
        result['checks'].append('page transition hides previous page until mounted decode finishes')
        await page.evaluate('() => window.__pvDecodeTest.fail()')
        await expect(viewer.get_by_text('画像を読み込めませんでした', exact=True)).to_be_visible()
        await expect(page.get_by_test_id('viewer-image').locator('img:visible')).to_have_count(0)
        assert_true(not await old.evaluate('img => img.isConnected'),
                    'failed second page retained misleading page zero image')
        result['checks'].append('mounted decode failure keeps image hidden and exposes retry')
        await viewer.get_by_role('button', name='画像を再読込', exact=True).click()
        await image_ready(page, 'demo-0003', 1)
        promoted = await page.evaluate('''() => {
            const gate = window.__pvDecodeTest;
            const visible = document.querySelector('[data-testid="viewer-image"] img[data-pv-displayed="true"]');
            return Boolean(gate.entered && gate.node && gate.retryNode &&
                gate.node !== gate.retryNode && visible === gate.retryNode &&
                visible.isConnected && visible.complete && visible.naturalWidth > 0);
        }''')
        assert_true(promoted, 'retry did not promote the exact mounted decoded DOM candidate')
        result['checks'].append('retry promotes its verified mounted image without replacing DOM node')
    finally:
        await page.evaluate('''() => {
            const gate = window.__pvDecodeTest;
            if (gate) { gate.release?.(); HTMLImageElement.prototype.decode = gate.original; }
        }''')
    await viewer.get_by_role('button', name='前のページ', exact=True).click()
    await image_ready(page, 'demo-0003', 0)


async def check_superseded_candidate(page, result):
    """A accepted, B pending, C fails: never render an old page as selected C."""
    viewer = page.get_by_test_id('gallery-viewer')
    old = await page.get_by_test_id('viewer-image').locator('img:visible').element_handle()
    await page.evaluate('''() => {
        const original = HTMLImageElement.prototype.decode;
        const gate = window.__pvSupersededDecode = {original, entered:false, failC:true, injectedC:false, settled:false};
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
                if (gate.failC) {
                    gate.failedCandidate = this;
                    gate.candidateConnected = this.isConnected;
                    gate.failC = false;
                    return decoded.then(() => {
                        gate.injectedC = true;
                        throw new DOMException('injected newer decode failure', 'EncodingError');
                    });
                }
                gate.retryCandidate = this;
            }
            return decoded;
        };
    }''')
    try:
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await page.wait_for_function('() => Boolean(window.__pvSupersededDecode.release)')
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await expect(viewer.get_by_text('画像を読み込めませんでした', exact=True)).to_be_visible()
        injected = await page.evaluate('''() => {
            const gate = window.__pvSupersededDecode;
            return Boolean(gate.entered && gate.failedCandidate instanceof HTMLImageElement &&
                gate.candidateConnected && !gate.failC && gate.injectedC &&
                new URL(gate.failedCandidate.src, document.baseURI).pathname === '/media/demo-0003/2/view.png');
        }''')
        assert_true(injected, 'C mounted decode failure was not injected on the intended requested image')
        await expect(page.get_by_test_id('viewer-image').locator('img:visible')).to_have_count(0)
        assert_true(not await old.evaluate('(img) => img.isConnected'),
                    'failed newer page left an old page falsely visible')
        result['checks'].append('A accepted / B pending / C failed never displays wrong-page A/B')
        await page.evaluate('() => window.__pvSupersededDecode.release()')
        await page.wait_for_function('() => window.__pvSupersededDecode.settled')
        await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        await expect(page.get_by_test_id('viewer-image').locator('img:visible')).to_have_count(0)
        result['checks'].append('superseded mounted decode completion cannot appear as selected page')
        await viewer.get_by_role('button', name='画像を再読込', exact=True).click()
        await image_ready(page, 'demo-0003', 2)
        assert_true(await page.evaluate('''() => {
            const gate = window.__pvSupersededDecode;
            const front = document.querySelector('[data-testid="viewer-image"] img[data-pv-displayed="true"]');
            return Boolean(gate.failedCandidate && gate.retryCandidate && front &&
                front !== gate.failedCandidate && front === gate.retryCandidate &&
                front.isConnected && front.complete && front.naturalWidth > 0);
        }'''), 'successful C retry reused the injected failed DOM candidate')
        result['checks'].append('explicit retry decodes only the requested third page')
    finally:
        await page.evaluate('''() => {
            const gate = window.__pvSupersededDecode;
            if (gate) {gate.release?.(); HTMLImageElement.prototype.decode = gate.original;}
        }''')


async def check_same_page_quality_fallback(page, result):
    """Original decode error keeps only the already accepted same-page frame."""
    viewer = page.get_by_test_id('gallery-viewer')
    for page_no in (1, 0):
        await viewer.get_by_role('button', name='前のページ', exact=True).click()
        await image_ready(page, 'demo-0003', page_no)
    front = await page.get_by_test_id('viewer-image').locator(
        'img[data-pv-displayed="true"]').element_handle()
    await page.evaluate('''() => {
        const original = HTMLImageElement.prototype.decode;
        const gate = window.__pvQualityDecode = {original, injected:false, failedOriginal:false};
        HTMLImageElement.prototype.decode = function() {
            const decoded = original.call(this);
            if (!this.isConnected || !this.closest('[data-testid="viewer-image"]')) return decoded;
            const path = new URL(this.src, document.baseURI).pathname;
            if (path === '/media/demo-0003/0/original.png' && !gate.injected) {
                gate.originalNode = this;
                gate.injected = true;
                return decoded.then(() => {
                    gate.failedOriginal = true;
                    throw new DOMException('injected original decode failure', 'EncodingError');
                });
            }
            if (path === '/media/demo-0003/0/view.png' && gate.failedOriginal)
                gate.recoveryNode = this;
            return decoded;
        };
    }''')
    try:
        quality = viewer.get_by_test_id('viewer-quality-toggle')
        await expect(quality).to_be_enabled()
        await quality.click()
        await expect(viewer.get_by_text('原寸画像を読み込めませんでした。通常画質で再表示できます')).to_be_visible()
        retained = await front.evaluate('''(img) => img.isConnected &&
            img.dataset.pvDisplayed === 'true' && img.complete && img.naturalWidth > 0 &&
            new URL(img.currentSrc || img.src, document.baseURI).pathname === '/media/demo-0003/0/view.png' ''')
        injected = await page.evaluate('''() => {
            const gate = window.__pvQualityDecode;
            return Boolean(gate.injected && gate.failedOriginal && gate.originalNode &&
                !gate.originalNode.isConnected);
        }''')
        assert_true(retained and injected, 'same-page original failure lost accepted normal frame or skipped mounted injection')
        result['checks'].append('same-page original decode failure retains accepted verified front')
        await quality.click()
        # The retained normal frame is already ready when quality is switched
        # back. image_ready() alone can therefore observe that old frame before
        # the *new* decode candidate has finished. Wait on candidate identity.
        await page.wait_for_function('''() => {
            const gate = window.__pvQualityDecode;
            const img = document.querySelector('[data-testid="viewer-image"] img[data-pv-displayed="true"]');
            return Boolean(gate.recoveryNode && img === gate.recoveryNode &&
                img.isConnected && img.complete && img.naturalWidth > 0);
        }''', timeout=15000)
        assert_true(await front.evaluate('(img) => !img.isConnected'),
                    'same-page recovery retained the superseded old DOM node after promotion')
        result['checks'].append('same-page quality recovery promotes exact decoded mounted candidate')
    finally:
        await page.evaluate('''() => {
            const gate = window.__pvQualityDecode;
            if (gate) HTMLImageElement.prototype.decode = gate.original;
        }''')


async def check_tab_departure_during_decode(page, result):
    viewer = page.get_by_test_id('gallery-viewer')
    await page.locator('.pv-card[data-work-id="demo-0003"]').click()
    await image_ready(page, 'demo-0003')
    await page.evaluate('''() => {
        window.__pvTabOriginalDecode = HTMLImageElement.prototype.decode;
        HTMLImageElement.prototype.decode = function() {
            const decoded = window.__pvTabOriginalDecode.call(this);
            if (!this.isConnected || !this.closest('[data-testid="viewer-image"]') ||
                !this.src.includes('/demo-0003/1/view.png')) return decoded;
            return decoded.then(() => new Promise(resolve => {
                window.__pvTabReleaseDecode = resolve;
            }));
        };
    }''')
    try:
        await viewer.get_by_role('button', name='次のページ', exact=True).click()
        await page.wait_for_function("typeof window.__pvTabReleaseDecode === 'function'")
        await page.get_by_test_id('tab-learning').click()
        learning = page.locator('.pv-learning[data-active="true"]')
        await expect(learning).to_be_focused()
        await expect(viewer).to_be_hidden()
        await page.evaluate('window.__pvTabReleaseDecode()')
        await page.wait_for_timeout(500)
        await expect(viewer).to_be_hidden()
        await expect(learning).to_be_focused()
        await page.get_by_test_id('tab-ranking').click()
        await expect(viewer).to_be_hidden()
        await page.locator('.pv-card[data-work-id="demo-0012"]').click()
        await image_ready(page, 'demo-0012')
        await viewer.get_by_role('button', name='閉じる', exact=True).click()
        await expect(viewer).to_be_hidden()
        result['checks'].append('tab departure invalidates mounted decode and preserves learning focus')
    finally:
        await page.evaluate('''() => {
            window.__pvTabReleaseDecode?.();
            HTMLImageElement.prototype.decode = window.__pvTabOriginalDecode;
            delete window.__pvTabOriginalDecode;
            delete window.__pvTabReleaseDecode;
        }''')


async def check_deferred_focus_order(page, result):
    viewer = page.get_by_test_id('gallery-viewer')
    await page.locator('.pv-card[data-work-id="demo-0013"]').click()
    await image_ready(page, 'demo-0013')
    await page.evaluate('''() => {
        window.__pvOriginalAnimationFrame = window.requestAnimationFrame;
        window.__pvHeldFocusFrames = [];
        window.requestAnimationFrame = callback => {
            if (callback.toString().includes('__pvFocusGeneration')) {
                window.__pvHeldFocusFrames.push(callback);
                return window.__pvOriginalAnimationFrame(() => {});
            }
            return window.__pvOriginalAnimationFrame(callback);
        };
    }''')
    try:
        await viewer.get_by_role('button', name='閉じる', exact=True).click()
        await expect(viewer).to_be_hidden()
        target = page.locator('.pv-card[data-work-id="demo-0014"]')
        await target.click()
        await image_ready(page, 'demo-0014')
        await viewer.get_by_role('button', name='閉じる', exact=True).click()
        await expect(viewer).to_be_hidden()
        await page.wait_for_function('window.__pvHeldFocusFrames.length >= 3')
        await page.evaluate('''() => {
            const held = window.__pvHeldFocusFrames.splice(0);
            held.reverse().forEach(callback => callback(performance.now()));
        }''')
        await expect(target).to_be_focused()
        result['checks'].append('older close focus callback cannot override a newer close')
    finally:
        await page.evaluate('''() => {
            window.requestAnimationFrame = window.__pvOriginalAnimationFrame;
            window.__pvHeldFocusFrames.splice(0).forEach(callback => callback(performance.now()));
            delete window.__pvOriginalAnimationFrame;
            delete window.__pvHeldFocusFrames;
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
    await check_same_page_quality_fallback(page, result)

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

    state['delay_work'] = 'demo-0011'
    state['delay'] = 'gallery'
    state['entered'] = asyncio.Event()
    state['release'] = asyncio.Event()
    await page.locator('.pv-card[data-work-id="demo-0011"]').click()
    await asyncio.wait_for(state['entered'].wait(), timeout=15)
    await page.get_by_test_id('tab-learning').click()
    learning = page.locator('.pv-learning[data-active="true"]')
    await expect(learning).to_be_focused()
    state['release'].set()
    await page.wait_for_timeout(500)
    await expect(viewer).to_be_hidden()
    await expect(learning).to_be_focused()
    await page.get_by_test_id('tab-ranking').click()
    await expect(viewer).to_be_hidden()
    result['checks'].append('tab departure during first load rejects the delayed response')
    await check_tab_departure_during_decode(page, result)
    await check_deferred_focus_order(page, result)

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

                def response(event):
                    if event.status == 404:
                        # All requests are confined to the dedicated synthetic
                        # listener. Save only fixture paths, not URL/headers.
                        result["observed_404_paths"].append(urlsplit(event.url).path)

                page.on("response", response)

                def console(message):
                    if message.type != "error":
                        return
                    text = message.text
                    # Browser console errors can be generic and omit the URL.
                    # Their exact resource attribution is checked using the
                    # response event before accepting them as injected faults.
                    if "404" in text and "Failed to load resource" in text:
                        result["expected_404_console"].append("HTTP 404 resource")
                    else:
                        result["console_errors"].append(text)

                page.on("console", console)
                page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                await run(page, result)
                assert_true(not result["console_errors"] and not result["page_errors"],
                            "unexpected console/page errors")
                assert_true(len(result["injections"]) == 2, "both 404 injections must occur")
                assert_true(sorted(result["observed_404_paths"]) == sorted(EXPECTED_404_RESOURCES),
                            f"unexpected failed HTTP resource(s): {result['observed_404_paths']}")
                assert_true(len(result["expected_404_console"]) <= len(EXPECTED_404_RESOURCES),
                            "additional unmatched HTTP404 console errors")
                result["checks"].append("HTTP404 responses match exactly the two injected fixture resources")
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
              "observed_404_paths": [],
              "console_errors": [], "page_errors": [], "failure": None}
    asyncio.run(main_async(args, result))
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    (args.output / "success.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": result["success"], "checks": result["checks"], "failure": result["failure"]},
                     ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
