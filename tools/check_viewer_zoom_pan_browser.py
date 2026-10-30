"""CoS-owned synthetic V07/V08 browser acceptance; controls are real DOM inputs.

Start the existing serve_viewer_author_fixture.py on an owned loopback port.
No Pixiv requests, saved media, account writes, or artificial image-load events.
"""
import argparse
import json
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "artifacts" / "p3-viewer-zoom-pan-20261028"


def inspect_zoom(viewport):
    return viewport.evaluate("""node => ({
        scale: Number(node.dataset.zoomScale),
        x: Number(node.dataset.zoomX),
        y: Number(node.dataset.zoomY),
        dragging: node.dataset.dragging,
    })""")


def track_author_image_request(page, parsed, result):
    """Bounded observation of only the owned synthetic 575 thumbnail request."""
    trace = result["target_image_network"] = {
        "path": "/media/575/0/thumb.png", "event_limit": 64, "dropped": 0,
        "lifecycle_phase": "operations", "events": [],
        "clock": "Python observer wall_ns plus monotonic_ns; not browser performance",
    }

    def record(kind, request, status=None):
        target = urlsplit(request.url)
        if ((target.scheme, target.netloc) != (parsed.scheme, parsed.netloc)
                or target.path != trace["path"]):
            return
        if len(trace["events"]) >= trace["event_limit"]:
            trace["dropped"] += 1
            return
        event = {"event": kind, "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                 "phase": result.get("author_revisit", {}).get("phase", "navigation"),
                 "lifecycle_phase": trace["lifecycle_phase"],
                 "path": target.path, "query": target.query}
        if status is not None:
            event["status"] = status
        if kind == "requestfailed":
            event["failure"] = (request.failure or "")[:160]
        trace["events"].append(event)

    page.on("request", lambda request: record("request", request))
    page.on("response", lambda response: record("response", response.request, response.status))
    page.on("requestfinished", lambda request: record("requestfinished", request))
    page.on("requestfailed", lambda request: record("requestfailed", request))


def inspect_author_revisit(page, url, output, result):
    """Exercise the real author button and retain the same browser DOM nodes."""
    from urllib.parse import urljoin

    record = result["author_revisit"] = {"expected_author_count": 100}
    def calls():
        response = page.request.get(urljoin(url.rstrip("/") + "/", "_fixture/state"))
        assert response.ok
        return response.json()["calls"]

    def author_reads():
        return [call for call in calls() if call[:2] == ["author", "91"]]

    record["reads_before"] = author_reads()
    assert record["reads_before"] == []
    record["phase"] = "open-primary-author"
    original_card = page.locator('.pv-card[data-work-id="501"]').element_handle()
    assert original_card is not None
    original_card.click()
    viewer = page.get_by_test_id("gallery-viewer").first
    expect(viewer).to_be_visible()
    # The author gallery adds another viewer with the same test-id. Pin this
    # gallery's actual viewer identity before the later DOM order changes.
    viewer_id = viewer.get_attribute("id")
    assert viewer_id and viewer_id.startswith("c") and viewer_id[1:].isdigit()
    viewer = page.locator(f'#{viewer_id}')
    record["primary_viewer_dom_id"] = viewer_id
    expect(viewer).to_have_attribute("data-quality-work", "501")
    expect(viewer).to_have_attribute("data-zoom-ready", "true")
    viewer.get_by_test_id("viewer-author-open").click()
    overlay = page.get_by_test_id("author-browse-overlay")
    expect(overlay).to_be_visible()
    counter = overlay.locator(".pv-gallery > .pv-counter").first
    expect(counter).to_have_text("50 / 100 件表示")
    search = overlay.get_by_placeholder("作品名・作者・タグで検索")
    record["phase"] = "search-and-expand-primary"
    search.fill("合成タグ")
    search.press("Enter")
    expect(counter).to_have_text("50 / 100 件表示")
    more = overlay.get_by_role("button", name="もっと見る", exact=True)
    more.scroll_into_view_if_needed()
    more.click()
    expect(counter).to_have_text("100 / 100 件表示")
    expect(overlay.locator(".pv-card")).to_have_count(100)
    overlay.get_by_role("button", name="中", exact=True).click()
    expect(overlay.locator(".pv-grid")).to_have_css("--pv-max-cols", "8")
    target = overlay.locator('.pv-card[data-work-id="575"]')
    target.scroll_into_view_if_needed()
    target_image = target.locator("img").first
    expect(target_image).to_be_visible()
    record["phase"] = "decode-scrolled-primary-card"
    record["target_image_before_wait"] = target_image.evaluate(
        "img => ({complete: img.complete, width: img.naturalWidth, height: img.naturalHeight})")
    gate_start = time.monotonic_ns()
    record["target_image_gate_started_wall_ns"] = time.time_ns()
    try:
        expect(target_image).to_have_js_property("complete", True)
        assert target_image.evaluate("img => img.complete && img.naturalWidth > 0")
    finally:
        record["target_image_gate_elapsed_ns"] = time.monotonic_ns() - gate_start
        record["target_image_gate_finished_wall_ns"] = time.time_ns()
        try:
            record["target_image_after_wait"] = target_image.evaluate(
                "img => ({complete: img.complete, width: img.naturalWidth, height: img.naturalHeight})")
        except Exception as observation_error:
            record["target_image_after_wait_error"] = type(observation_error).__name__
    scroll = overlay.get_by_test_id("gallery-scroll")
    scroll_node = scroll.element_handle()
    first_node = overlay.locator(".pv-card").first.element_handle()
    assert scroll_node is not None and first_node is not None

    def measure():
        return {
            "count": overlay.locator(".pv-card").count(),
            "counter": counter.inner_text(),
            "query": search.input_value(),
            "scroll_top": scroll.evaluate("el => el.scrollTop"),
            "target_top": target.evaluate("el => el.getBoundingClientRect().top"),
            "columns": overlay.locator(".pv-grid").evaluate(
                "el => getComputedStyle(el).getPropertyValue('--pv-max-cols').trim()"),
            "same_scroll_node": scroll.evaluate("(el, original) => el === original", scroll_node),
            "same_first_card": overlay.locator(".pv-card").first.evaluate(
                "(el, original) => el === original", first_node),
            "original_scroll_connected": scroll_node.evaluate("el => el.isConnected"),
            "original_card_connected": first_node.evaluate("el => el.isConnected"),
        }

    record["before"] = measure()
    record["phase"] = "primary-revisit"
    record["reads_after_first_open"] = author_reads()
    assert record["before"]["scroll_top"] > 0
    assert record["reads_after_first_open"] == [["author", "91", "interactive"]]
    page.screenshot(path=str(output / "a02-before-revisit.png"))
    overlay.get_by_test_id("author-browse-close").click()
    expect(overlay).not_to_be_visible()
    original_card.click()
    expect(viewer).to_be_visible()
    expect(viewer).to_have_attribute("data-quality-work", "501")
    expect(viewer).to_have_attribute("data-zoom-ready", "true")
    viewer.get_by_test_id("viewer-author-open").click()
    expect(overlay).to_be_visible()
    expect(counter).to_have_text("100 / 100 件表示")
    # Do not focus or scroll any author child before measuring the returned view.
    record["after"] = measure()
    record["phase"] = "compare-primary-revisit"
    record["reads_after_revisit"] = author_reads()
    page.screenshot(path=str(output / "a02-after-revisit.png"))
    after, before = record["after"], record["before"]
    assert after["count"] == 100 and after["query"] == "合成タグ"
    assert after["columns"] == before["columns"] == "8"
    assert after["same_scroll_node"] and after["same_first_card"]
    assert after["original_scroll_connected"] and after["original_card_connected"]
    assert abs(after["scroll_top"] - before["scroll_top"]) <= 5
    assert abs(after["target_top"] - before["target_top"]) <= 5
    assert record["reads_after_revisit"] == record["reads_after_first_open"]
    result["checks"].append("A02 50-to-100, same-author cached state/DOM/scroll/query retained")
    # Original A02 terminal condition and actual valid Retry, in the same run.
    # Keep every primary-author measurement above unchanged.
    secondary = record["secondary"] = {
        "scope": "A02 small-author terminal and valid Retry",
        "expected_count": 3, "synthetic_delay_seconds": 0.75,
    }
    record["phase"] = "secondary-terminal-retry"

    def secondary_reads():
        return [call for call in calls() if call[:2] == ["author", "92"]]

    assert secondary_reads() == []
    overlay.get_by_test_id("author-browse-close").click()
    expect(overlay).not_to_be_visible()
    secondary_card = page.locator('.pv-card[data-work-id="601"]').element_handle()
    assert secondary_card is not None
    secondary_card.click()
    expect(viewer).to_be_visible()
    expect(viewer).to_have_attribute("data-quality-work", "601")
    expect(viewer).to_have_attribute("data-zoom-ready", "true")
    viewer.get_by_test_id("viewer-author-open").click()
    expect(overlay).to_be_visible()
    expect(overlay).to_have_attribute("data-author-id", "92")
    status = overlay.get_by_test_id("author-browse-status")
    retry = overlay.get_by_test_id("author-browse-retry")
    expect(status).to_have_text("作者の作品を取得しています…")
    expect(retry).to_be_hidden()
    secondary["initial_loading"] = status.inner_text()
    expect(status).to_have_text("作者の作品を取得できませんでした。再取得できます。")
    expect(retry).to_be_visible()
    expect(retry).to_be_enabled()
    secondary["reads_after_failure"] = secondary_reads()
    assert secondary["reads_after_failure"] == [["author", "92", "interactive"]]
    secondary["failure"] = {"status": status.inner_text(), "retry_enabled": retry.is_enabled()}
    page.screenshot(path=str(output / "a02-small-author-failed.png"))

    retry.click()
    expect(status).to_have_text("作者の作品を取得しています…")
    expect(retry).to_be_hidden()
    secondary["retry_loading"] = status.inner_text()
    expect(counter).to_have_text("3 / 3 件表示")
    expect(overlay.locator(".pv-card")).to_have_count(3)
    expect(more).to_be_hidden()
    expect(status).to_have_text("")
    expect(retry).to_be_hidden()
    ids = overlay.locator(".pv-card").evaluate_all(
        "nodes => nodes.map(node => node.dataset.workId)")
    assert ids == ["601", "602", "603"]
    for work_id in ids:
        image = overlay.locator(f'.pv-card[data-work-id="{work_id}"] img').first
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("img => img.naturalWidth > 0")
    secondary["reads_after_retry"] = secondary_reads()
    assert secondary["reads_after_retry"] == [["author", "92", "interactive"]] * 2
    secondary["completed"] = {"count": 3, "ids": ids, "more_visible": more.is_visible()}

    overlay.get_by_test_id("author-browse-close").click()
    expect(overlay).not_to_be_visible()
    secondary_card.click()
    expect(viewer).to_be_visible()
    expect(viewer).to_have_attribute("data-quality-work", "601")
    expect(viewer).to_have_attribute("data-zoom-ready", "true")
    viewer.get_by_test_id("viewer-author-open").click()
    expect(overlay).to_be_visible()
    expect(counter).to_have_text("3 / 3 件表示")
    expect(overlay.locator(".pv-card")).to_have_count(3)
    expect(more).to_be_hidden()
    expect(retry).to_be_hidden()
    expect(status).to_have_text("")
    secondary["reads_after_revisit"] = secondary_reads()
    assert secondary["reads_after_revisit"] == secondary["reads_after_retry"]
    assert author_reads() == record["reads_after_first_open"]
    page.screenshot(path=str(output / "a02-small-author-revisited.png"))
    result["checks"].append("A02 small-author failure, valid Retry, 3/3 terminal and cached revisit")
    record["phase"] = "complete"



def run(url, output, width, height, *, light=False, author_revisit=False):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1")
            or not parsed.port or parsed.port == 8089 or parsed.username or parsed.password):
        raise ValueError("use the already owned synthetic loopback server")
    output = output.absolute()
    allowed_output = ROOT / 'artifacts' / 'p3-318-author-revisit' if author_revisit else OUTPUT_ROOT
    if output == allowed_output or not output.is_relative_to(allowed_output) or output.exists():
        raise ValueError("output must be a fresh run directory within the candidate")
    output.mkdir(parents=True)
    result = {"viewport": [width, height], "light": light, "success": False, "console_errors": [],
              "page_errors": [], "external_requests": [], "checks": []}
    result["scope"] = "A02 cached author revisit only" if author_revisit else "V07/V08 zoom and pan"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": width, "height": height})
            def guard(route):
                target = urlsplit(route.request.url)
                if (target.scheme, target.netloc) != (parsed.scheme, parsed.netloc):
                    result["external_requests"].append(target.scheme + "://" + target.netloc)
                    route.abort()
                else:
                    route.continue_()
            context.route("**/*", guard)
            page = context.new_page()
            if author_revisit:
                track_author_image_request(page, parsed, result)
            page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
            page.on("console", lambda message: result["console_errors"].append(message.text)
                    if message.type == "error" else None)
            try:
                page.goto(url, wait_until="domcontentloaded")
                if light:
                    page.get_by_role("button", name="明暗を切替").click()
                if author_revisit:
                    inspect_author_revisit(page, url, output, result)
                    assert not result["console_errors"] and not result["page_errors"] and not result["external_requests"]
                    result["success"] = True
                    return result
                page.locator('[data-work-id="501"]').click()
                viewer = page.get_by_test_id("gallery-viewer")
                expect(viewer).to_be_visible()
                expect(viewer).to_have_attribute("data-zoom-ready", "true")
                viewport = viewer.get_by_test_id("viewer-zoom-viewport")
                image = viewer.get_by_test_id("viewer-image").locator('img[data-pv-displayed="true"]')
                expect(image).to_be_visible()
                assert image.evaluate("img => img.complete && img.naturalWidth > 0")
                expect(viewer.get_by_test_id("viewer-zoom-value")).to_have_text("100%")
                assert inspect_zoom(viewport) == {"scale": 1, "x": 0, "y": 0, "dragging": "false"}
                initial_box = viewport.bounding_box()
                assert initial_box and initial_box["width"] > 300
                result["checks"].append("decoded original image and initial 100%")
                viewer.focus()
                page.keyboard.press("Equal")
                assert inspect_zoom(viewport)["scale"] > 1
                page.keyboard.press("0")
                assert inspect_zoom(viewport)["scale"] == 1
                result["checks"].append("accessible keyboard zoom and reset with viewer focus")

                for _ in range(4):
                    viewer.get_by_role("button", name="拡大", exact=True).click()
                enlarged = inspect_zoom(viewport)
                assert enlarged["scale"] > 2 and enlarged["scale"] <= 4
                expect(viewer.get_by_test_id("viewer-zoom-value")).not_to_have_text("100%")
                box = viewport.bounding_box()
                assert box and abs(box["width"] - initial_box["width"]) < 1
                assert abs(box["height"] - initial_box["height"]) < 1
                center_x = box["x"] + box["width"] / 2
                center_y = box["y"] + box["height"] / 2
                viewer.get_by_role("button", name="詳細", exact=True).click()
                expect(viewer.get_by_test_id("viewer-detail-tags")).to_be_visible()
                page.mouse.move(center_x, center_y)
                page.mouse.down()
                page.mouse.move(center_x + 90, center_y + 65, steps=5)
                page.mouse.up()
                dragged = inspect_zoom(viewport)
                assert dragged["scale"] == enlarged["scale"]
                assert dragged["x"] != 0 or dragged["y"] != 0
                assert dragged["dragging"] == "false"
                expect(viewer.get_by_test_id("viewer-detail-tags")).to_be_visible()
                page.mouse.click(center_x, center_y)
                expect(viewer.get_by_test_id("viewer-detail-tags")).not_to_be_visible()
                result["checks"].append("zoom, pointer pan without click-dismiss, simple image click preserved")

                page.mouse.move(center_x, center_y)
                page.mouse.wheel(0, -140)
                wheeled = inspect_zoom(viewport)
                assert wheeled["scale"] > enlarged["scale"]
                for _ in range(20):
                    page.mouse.wheel(0, -100)
                assert inspect_zoom(viewport)["scale"] <= 4
                viewer.get_by_role("button", name="縮小", exact=True).click()
                assert 1 < inspect_zoom(viewport)["scale"] < 4
                result["checks"].append("wheel zoom and 400% clamp")

                viewer.get_by_role("button", name="倍率と位置をリセット").click()
                assert inspect_zoom(viewport) == {"scale": 1, "x": 0, "y": 0, "dragging": "false"}
                expect(viewer.get_by_test_id("viewer-zoom-value")).to_have_text("100%")
                viewer.get_by_role("button", name="拡大", exact=True).focus()
                page.keyboard.press("Enter")
                assert inspect_zoom(viewport)["scale"] > 1
                viewer.get_by_role("button", name="次のページ", exact=True).click()
                expect(viewer).to_have_attribute("data-zoom-ready", "true")
                expect(viewer.get_by_test_id("viewer-zoom-value")).to_have_text("100%")
                expect(viewer.locator(".pv-viewer-header .pv-counter")).to_have_text("2/2")
                assert inspect_zoom(viewport)["scale"] == 1
                assert image.evaluate("img => img.complete && img.naturalWidth > 0")
                viewer.get_by_role("button", name="拡大", exact=True).click()
                assert inspect_zoom(viewport)["scale"] > 1
                viewer.get_by_role("button", name="倍率と位置をリセット").click()
                viewer.locator(".pv-viewer-stage").click(position={"x": 8, "y": 8})
                page.keyboard.press("ArrowLeft")
                expect(viewer.locator(".pv-viewer-header .pv-counter")).to_have_text("1/2")
                expect(viewer).to_have_attribute("data-zoom-ready", "true")
                result["checks"].append("page change resets zoom, controls re-enable and arrows survive")

                viewer.get_by_role("button", name="詳細", exact=True).click()
                expect(viewer.get_by_test_id("viewer-detail-tags")).to_be_visible()
                page.keyboard.press("Escape")
                expect(viewer.get_by_test_id("viewer-detail-tags")).not_to_be_visible()
                page.keyboard.press("Escape")
                expect(viewer).not_to_be_visible()
                page.locator('[data-work-id="601"]').click()
                expect(viewer).to_be_visible()
                expect(viewer).to_have_attribute("data-zoom-ready", "true")
                assert inspect_zoom(viewport)["scale"] == 1
                expect(viewer.get_by_test_id("viewer-zoom-value")).to_have_text("100%")
                viewer.get_by_role("button", name="拡大", exact=True).click()
                assert inspect_zoom(viewport)["scale"] > 1
                viewer.get_by_role("button", name="倍率と位置をリセット").click()
                result["checks"].append("details/Escape two-step and another work at 100%")

                page.screenshot(path=str(output / "final.png"))
                assert not result["console_errors"] and not result["page_errors"] and not result["external_requests"]
                result["success"] = True
            except Exception:
                # Preserve the original failure position before browser closure.
                # This checker only attaches the caller-owned synthetic fixture.
                result["traceback"] = traceback.format_exc()
                try:
                    page.screenshot(path=str(output / "failure.png"))
                except Exception as capture_error:
                    result["failure_capture_error"] = type(capture_error).__name__
                raise
            finally:
                if author_revisit:
                    result["target_image_network"]["lifecycle_phase"] = "browser-close"
                context.close()
                browser.close()
    except Exception as error:
        result["success"] = False
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["success"]:
        raise AssertionError(result.get("error", result))
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--url", required=True)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--width", default=1280, type=int)
    cli.add_argument("--height", default=720, type=int)
    cli.add_argument("--light", action="store_true")
    cli.add_argument("--author-revisit", action="store_true",
                     help="A02 only; use the existing fixture with --author-count 100")
    arguments = cli.parse_args()
    print(json.dumps(run(arguments.url, arguments.output, arguments.width, arguments.height,
                         light=arguments.light, author_revisit=arguments.author_revisit),
                     ensure_ascii=False))
