"""Check the isolated navigation mock at fixed CSS size and actual page zoom."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from playwright.sync_api import expect


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "r2"))
from check_wireframe import (  # noqa: E402
    SIZES, automation_transitions, check_learning, enter, h1_replaced_selection,
    integrated, isolated_browser, locator, measure, open_fixture, selection_survives_reordering, tab,
)


# Comparison treatment only. Keep the full banner and the image aspect ratio.
COMPACT = """
@media (max-width: 640px) {
  .toolbar { padding-block: 3px; }
  .gallery {
    margin: 0 8px 4px; padding: 2px; gap: 8px;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 136px), 1fr));
  }
}
"""


def zoom_control(context):
    settings = context.new_page()
    settings.goto("chrome://settings/appearance")
    settings.locator("select").first.wait_for(timeout=5000)
    return next(select for select in settings.locator("select").all()
                if "200%" in [label.strip() for label in select.locator("option").all_text_contents()])


def viewport(page, zoom, width, height, percent):
    zoom.select_option(label=f"{percent}%")
    page.set_viewport_size({"width": width * percent // 100, "height": height * percent // 100})
    page.bring_to_front()


def fixture(page, stress=False):
    open_fixture(page, stress=stress)
    hint = locator(page, "dismiss-hint")
    if hint.is_visible():
        hint.click()
    expect(locator(page, "relocation-hint")).to_be_hidden()


def focus_at(page, expected):
    return page.evaluate("""selector => {
        const active = document.activeElement;
        const rect = active.getBoundingClientRect();
        const top = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        const overlay = top?.closest(".main-nav");
        return {
            expected: selector, active: active.id ? "#" + active.id : active.dataset.testid || active.tagName,
            top: overlay && !overlay.contains(active) ? "#nav" : top && (top.id || top.dataset.testid || top.tagName),
            focus_matches: active.matches(selector),
            center_uncovered: !!top && active.contains(top),
            nav_closed: document.getElementById("nav").hidden,
            aria_expanded: document.getElementById("nav-toggle").getAttribute("aria-expanded"),
        };
    }""", expected)


def navigation(page, stress):
    steps = {}
    toggle = locator(page, "nav-toggle")
    nav = locator(page, "nav")
    fixture(page, stress)
    previous = "#needs-review" if stress else "#automation-open"
    enter(page, toggle)
    for button in nav.get_by_role("button").all():
        page.keyboard.press("Tab")
        key = button.get_attribute("data-testid")
        steps[key] = focus_at(page, f'[data-testid="{key}"]')
    page.keyboard.press("Tab")
    steps["tab_out"] = focus_at(page, "#refresh")
    page.keyboard.press("Escape")
    steps["escape_after_tab"] = focus_at(page, "#refresh")

    fixture(page, stress)
    enter(page, toggle)
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    steps["reverse_to_opener"] = focus_at(page, "#nav-toggle")
    page.keyboard.press("Shift+Tab")
    steps["shift_tab_out"] = focus_at(page, previous)

    fixture(page, stress)
    enter(page, toggle)
    page.keyboard.press("Tab")
    page.keyboard.press("Escape")
    steps["escape_inside"] = focus_at(page, "#nav-toggle")
    enter(page, toggle)
    page.keyboard.press("Escape")
    steps["escape_at_opener"] = focus_at(page, "#nav-toggle")

    # Activation without moving DOM focus models a non-pointer activation while
    # an outside control is focused. The Escape handler must still see it.
    for name, outside in (("header", "#stop-open"), ("body", "#refresh")):
        fixture(page, stress)
        page.locator(outside).focus()
        toggle.evaluate("button => button.click()")
        expect(nav).to_be_visible()
        expect(page.locator(outside)).to_be_focused()
        page.keyboard.press("Escape")
        steps[f"escape_outside_{name}"] = focus_at(page, "#nav-toggle")

    for key, step in steps.items():
        should_close = not (key.startswith("tab-") or key == "reverse_to_opener")
        step["pass"] = (step["focus_matches"] and step["center_uncovered"]
                        and step["nav_closed"] == should_close
                        and step["aria_expanded"] == ("false" if should_close else "true"))
    return steps


def layout(page):
    return page.evaluate("""() => {
        const el = id => document.getElementById(id);
        const gallery = el("gallery");
        const bounds = gallery.getBoundingClientRect();
        const x = bounds.left + gallery.clientLeft;
        const y = bounds.top + gallery.clientTop;
        const right = Math.min(innerWidth, bounds.right, x + gallery.clientWidth);
        const bottom = Math.min(innerHeight, bounds.bottom, y + gallery.clientHeight);
        const full = node => {
            const r = node.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.left >= Math.max(x, 0) - .1
                && r.right <= right + .1 && r.top >= Math.max(y, 0) - .1 && r.bottom <= bottom + .1;
        };
        const rect = selector => {
            const node = document.querySelector(selector);
            const r = node.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height].map(n => +n.toFixed(3));
        };
        return {
            css: [innerWidth, innerHeight], dpr: devicePixelRatio,
            layout: Object.fromEntries([
                "#db-banner", ".masthead", ".identity", ".safety-controls", ".utilities",
                "#alert-controls", "#relocation-hint", ".navigation", ".toolbar-section",
                "#screen-title", "#refresh", "#toolbar-toggle", "#candidate-entry", "#screen-count",
                "#filters", "#gallery", ".sample",
            ].map(selector => [selector, rect(selector)])),
            gallery_client: [gallery.clientWidth, gallery.clientHeight],
            gallery_scroll_top: gallery.scrollTop,
            full_images: [...gallery.querySelectorAll(".sample")].filter(full).length,
            full_cards: [...gallery.querySelectorAll(".specimen")].filter(full).length,
            first_image_full: full(gallery.querySelector(".sample")),
        };
    }""")


def visible_center(element):
    expect(element).to_be_in_viewport(ratio=0.99)
    assert element.evaluate("""e => {
        const r = e.getBoundingClientRect();
        return e.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
    }""")


def check_db_details(page):
    message = locator(page, "db-message")
    expect(message).to_have_text("本番DBを使用中 · 保存先「PixivVault／…／ダウンロード済み作品の保管領域」")
    assert message.evaluate("e => e.getBoundingClientRect().height === parseFloat(getComputedStyle(e).lineHeight)")
    open_button = locator(page, "db-detail-open")
    visible_center(open_button)
    assert open_button.bounding_box()["height"] >= 28
    enter(page, open_button)
    dialog = page.get_by_role("dialog", name="本番DBの警告")
    expect(dialog).to_be_visible()
    expect(locator(page, "db-full-warning")).to_have_text(
        "本番DBを使用中 · 保存先「PixivVault／作品とメタデータを保管する共有フォルダー／"
        "ダウンロード済み作品の保管領域」。保存済み作品と設定に操作が反映されます。"
        "続ける前に保存先を確認してください。（合成の長文例）")
    visible_center(locator(page, "db-full-warning"))
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(open_button).to_be_focused()
    visible_center(open_button)


def measure_narrow(page, variant, stress, expanded):
    fixture(page, stress)
    if variant == "compact":
        page.add_style_tag(content=COMPACT)
    filters, toggle = locator(page, "filters"), locator(page, "toolbar-toggle")
    if filters.is_visible() != expanded:
        enter(page, toggle)
    assert filters.is_visible() == expanded
    page.evaluate("document.getElementById('gallery').scrollTop = 0")
    row = layout(page)
    assert row["gallery_scroll_top"] == 0
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    for name in ("db-banner", "stop-open", "stop-indicator", "api-switch"):
        visible_center(locator(page, name))
    if stress:
        expect(locator(page, "needs-review")).to_have_accessible_name("自動処理：未解消 2")
        visible_center(locator(page, "needs-review"))
        visible_center(locator(page, "reviewed-count"))
        if variant == "details":
            check_db_details(page)
            row["full_warning_reachable"] = True
    row.update(variant=variant, stress=stress, expanded=expanded)
    if expanded and variant == "details":
        expect(toggle).to_have_text("絞り込みを畳む")
        for region in (filters, locator(page, "gallery")):
            region.evaluate("e => e.scrollTop = e.scrollHeight")
            assert region.evaluate("e => e.scrollTop") > 0
            visible_center(toggle)
        more = locator(page, "more-tags")
        toggle.focus()
        for _ in range(locator(page, "inline-tags").get_by_role("button").count() + 1):
            page.keyboard.press("Tab")
        expect(more).to_be_focused()
        page.keyboard.press("Space")
        expect(locator(page, "tag-popover")).to_be_in_viewport()
        page.keyboard.press("Escape")
        expect(more).to_be_focused()
        enter(page, toggle)
        expect(filters).to_be_hidden()
        visible_center(toggle)
        row["collapse_reachable_after_scroll"] = True
    tab(page, "learning")
    expect(locator(page, "screen-title")).to_be_focused()
    page.keyboard.press("Tab")
    expect(locator(page, "learning-ratings").get_by_role("button").first).to_be_focused()
    image, count = check_learning(page)
    for button in locator(page, "learning-ratings").get_by_role("button").all():
        visible_center(button)
    row.update(learning=image["image"], ratings_reached=count)
    return row


def run_narrow(page, zoom, variant):
    result = {}
    for name, percent in (("fixed_640x400", 100), ("page_zoom_200", 200)):
        viewport(page, zoom, 640, 400, percent)
        states = (False, True) if variant != "compact" else ()
        nav = {"stress" if stress else "normal": navigation(page, stress) for stress in states}
        rows = [
            measure_narrow(page, variant, stress, expanded)
            for stress in (False, True) for expanded in (False, True)
        ]
        assert all(row["css"] == [640, 400] and row["dpr"] == percent / 100 for row in rows)
        result[name] = {"navigation": nav, "measurements": rows}
    return result


def regressions(page, zoom):
    results = {}
    viewport(page, zoom, 1280, 800, 100)
    results["h1"] = h1_replaced_selection(page)
    assert all(results["h1"].values())
    selection_survives_reordering(page)
    automation_transitions(page)
    results.update(selection_reordering_and_bookmarks="PASS", automation_transitions="PASS")
    for theme in ("dark", "light"):
        page.emulate_media(color_scheme=theme, reduced_motion="reduce")
        sizes = []
        for size, width, height, ratio, rows in SIZES:
            if width > 640:
                viewport(page, zoom, width, height, round(ratio * 100))
                for stress in (False, True):
                    sizes.extend(measure(page, size, width, height, ratio, rows, stress))
        results[theme] = sizes
    return results


@contextmanager
def baseline_wireframe(enabled, cleanup):
    if not enabled:
        yield
        return
    current = integrated.HTML
    source = "b267d04:" + current.relative_to(integrated.ROOT).as_posix()
    content = subprocess.check_output(["git", "show", source], cwd=integrated.ROOT)
    with TemporaryDirectory(prefix="baseline-", dir=os.environ["TEMP"]) as directory:
        integrated.HTML = Path(directory) / current.name
        try:
            integrated.HTML.write_bytes(content)
            yield
        finally:
            integrated.HTML = current
    cleanup.update(files=1, bytes=len(content))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    external, errors, profile_cleanup, baseline_cleanup = [], [], {}, {}
    regression = {}
    with baseline_wireframe(args.baseline, baseline_cleanup):
        digest = sha256(integrated.HTML.read_bytes()).hexdigest()
        with isolated_browser(external, errors, profile_cleanup) as (context, page):
            browser = page.evaluate(r"() => navigator.userAgent.match(/Chrome\/([\d.]+)/)[1]")
            zoom = zoom_control(context)
            page.emulate_media(color_scheme="dark", reduced_motion="reduce")
            runs = {"dark": run_narrow(page, zoom, "original" if args.baseline else "details")}
            if args.baseline:
                runs["compact_comparison"] = run_narrow(page, zoom, "compact")
            else:
                page.emulate_media(color_scheme="light", reduced_motion="reduce")
                runs["light"] = run_narrow(page, zoom, "details")
                regression = regressions(page, zoom)
    assert not external and not errors
    result = {
        "browser": browser, "wireframe_sha256": digest,
        "external_requests": len(external), "page_errors": len(errors),
        "profile_cleanup": profile_cleanup, "baseline_copy_cleanup": baseline_cleanup,
        "runs": runs, "regression": regression,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    treatment_runs = [run for theme, run in runs.items() if theme != "compact_comparison"]
    nav_pass = all(step["pass"] for run in treatment_runs for condition in run.values()
                   for steps in condition["navigation"].values() for step in steps.values())
    images_pass = all(row["first_image_full"] for run in treatment_runs
                      for condition in run.values() for row in condition["measurements"]
                      if row["stress"] and not row["expanded"])
    print(f"Navigation: {'PASS' if nav_pass else 'FAIL'}; collapsed first image: {'PASS' if images_pass else 'FAIL'}")
    if not (nav_pass and images_pass):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
