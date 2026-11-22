"""Exercise the local wireframe in a disposable Chromium profile."""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "r1"))
import check_integrated_wireframe as integrated  # noqa: E402
from check_integrated_wireframe import (  # noqa: E402
    QUIET, RATINGS, SIZES, area, geometry, locator, open_fixture,
)


def enter(page, control):
    control.focus()
    page.keyboard.press("Enter")


def tab(page, key):
    narrow = locator(page, "nav-toggle")
    if narrow.is_visible():
        enter(page, narrow)
    enter(page, page.get_by_test_id("tab-" + key))


def h1_replaced_selection(page):
    open_fixture(page, tab="library")
    gallery = locator(page, "gallery")
    gallery.get_by_role("button", name=re.compile(r"^保存作品 1保存済み$")).click()
    saved = gallery.get_by_role("button", name=re.compile(r"^作品 1保存済み$"))
    saved.click()
    expect(saved).to_have_attribute("aria-pressed", "true")
    expect(saved).to_be_focused()
    if locator(page, "filters").is_hidden():
        locator(page, "toolbar-toggle").click()
    locator(page, "scope-pixiv").check()
    locator(page, "search-pixiv").click()
    replacement = gallery.get_by_role("button", name=re.compile(r"^Pixiv作品 1未保存$"))
    expect(replacement).to_be_visible()
    after_search = replacement.get_attribute("aria-pressed")
    focus_after_search = locator(page, "screen-title").evaluate("e => e === document.activeElement")

    tab(page, "bookmarks")
    tab(page, "library")
    expect(replacement).to_be_visible()
    after_return = replacement.get_attribute("aria-pressed")
    focus_after_return = locator(page, "screen-title").evaluate("e => e === document.activeElement")
    return {
        "replacement_unselected_after_search": after_search == "false",
        "heading_focused_after_search": focus_after_search,
        "replacement_unselected_after_enter_return": after_return == "false",
        "heading_focused_after_enter_return": focus_after_return,
    }


def selection_survives_reordering(page):
    open_fixture(page, tab="library")
    gallery = locator(page, "gallery")
    gallery.get_by_role("button", name="保存作品 1保存済み", exact=True).click()
    saved = gallery.get_by_role("button", name="作品 9保存済み", exact=True)
    saved.click()
    old_position = saved.evaluate("e => Array.from(e.closest('#gallery').querySelectorAll('button')).indexOf(e)")
    if locator(page, "filters").is_hidden():
        locator(page, "toolbar-toggle").click()
    locator(page, "scope-pixiv").check()
    locator(page, "search-pixiv").click()
    assert saved.evaluate("e => Array.from(e.closest('#gallery').querySelectorAll('button')).indexOf(e)") != old_position
    expect(saved).to_be_focused()
    expect(saved).to_have_attribute("aria-pressed", "true")
    expect(gallery.locator("[aria-pressed=true]")).to_have_count(1)

    tab(page, "bookmarks")
    bookmarked = gallery.get_by_role("button", name="ブックマーク 1保存済み", exact=True)
    bookmarked.click()
    tab(page, "library")
    expect(saved).to_be_focused()
    expect(saved).to_have_attribute("aria-pressed", "true")
    tab(page, "bookmarks")
    expect(bookmarked).to_be_focused()
    expect(bookmarked).to_have_attribute("aria-pressed", "true")

    tab(page, "library")
    locator(page, "search").fill("作品 10")
    expect(gallery.locator("[aria-pressed=true]")).to_have_count(0)
    expect(locator(page, "screen-title")).to_be_focused()
    locator(page, "search").clear()
    expect(saved).to_have_attribute("aria-pressed", "false")


def automation_transitions(page):
    open_fixture(page, stress=True)
    total = locator(page, "needs-review")
    expect(total).to_have_accessible_name("自動処理：未解消 2")
    expect(total).to_have_accessible_description("未確認 2／確認済み 0")
    expect(locator(page, "automation-open")).to_have_accessible_name(
        re.compile(r"^自動処理：未解消 2、未確認 2／確認済み 0"))
    enter(page, total)
    status = locator(page, "scheduler-status")
    expect(status).to_contain_text("停止済み（取得停止ゲート）")
    enter(page, locator(page, "scheduler-ack"))
    expect(status).to_contain_text("確認済み（未解消）")
    page.keyboard.press("Escape")
    expect(total).to_be_focused()
    expect(total).to_have_accessible_name("自動処理：未解消 2")
    expect(total).to_have_accessible_description("未確認 1／確認済み 1")
    expect(locator(page, "automation-announcement")).to_contain_text("定期チェック 失敗（確認済み・未解消）")

    enter(page, total)
    enter(page, locator(page, "auto-scheduler"))
    enter(page, locator(page, "scheduler-reopen-gate"))
    expect(locator(page, "safety")).to_be_visible()
    enter(page, locator(page, "toggle-stop"))
    page.keyboard.press("Escape")
    expect(locator(page, "auto-scheduler")).to_be_focused()
    expect(status).to_contain_text("待機中")
    expect(status).to_contain_text("確認済み（未解消）")
    enter(page, locator(page, "scheduler-recover"))
    expect(status).to_have_text("待機中")
    enter(page, page.locator('[data-simulate="scheduler"]'))
    expect(status).to_have_text("実行中")
    enter(page, locator(page, "scheduler-stop"))
    expect(status).to_have_text("停止を要求中")
    expect(locator(page, "scheduler-finish")).to_be_focused()
    page.keyboard.press("Enter")
    expect(status).to_have_text("停止済み（手動）")
    locator(page, "scheduler-enabled").uncheck()
    expect(status).to_have_text("停止（設定で OFF）")
    enter(page, page.locator('[data-simulate="scheduler"]'))
    expect(status).to_have_text("停止（設定で OFF）")
    locator(page, "scheduler-enabled").check()
    expect(status).to_have_text("待機中")
    page.keyboard.press("Escape")
    expect(total).to_be_focused()
    expect(total).to_have_accessible_name("自動処理：未解消 1")
    expect(total).to_have_accessible_description("未確認 1／確認済み 0")
    enter(page, total)
    expect(locator(page, "auto-metadata")).to_be_focused()
    enter(page, locator(page, "metadata-recover"))
    page.keyboard.press("Escape")
    expect(total).to_be_hidden()
    expect(locator(page, "automation-open")).to_be_focused()


def narrow_actions(page, stress):
    toggle = locator(page, "nav-toggle")
    expect(toggle).to_have_accessible_name("カスタムランキング ／ 全画面の一覧を開く")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    nav = locator(page, "nav")
    expect(nav).to_be_hidden()
    enter(page, toggle)
    expect(toggle).to_have_attribute("aria-expanded", "true")
    buttons = nav.get_by_role("button").all()
    assert len(buttons) == 10
    for button in buttons:
        page.keyboard.press("Tab")
        expect(button).to_be_focused()
        expect(button).to_be_in_viewport(ratio=0.99)
        assert button.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()
    expect(nav).to_be_hidden()
    enter(page, locator(page, "stop-open"))
    expect(locator(page, "safety")).to_be_visible()
    page.keyboard.press("Escape")
    expect(locator(page, "stop-open")).to_be_focused()
    if stress:
        enter(page, locator(page, "needs-review"))
        expect(locator(page, "automation")).to_be_visible()
        page.keyboard.press("Escape")
        expect(locator(page, "needs-review")).to_be_focused()
    for button in page.locator(".utility").all():
        expect(button).to_be_in_viewport(ratio=0.99)


def check_learning(page):
    image = area(page, "learning-stage", "#learning-image")
    ratings = locator(page, "learning-ratings")
    buttons = [ratings.get_by_role("button", name=label, exact=True) for label in RATINGS]
    for button in buttons:
        expect(button).to_be_in_viewport(ratio=0.99)
    buttons[0].focus()
    for index, (button, label) in enumerate(zip(buttons, RATINGS)):
        if index:
            page.keyboard.press("Tab")
        expect(button).to_be_focused()
        page.keyboard.press("Enter")
        expect(locator(page, "learning-feedback")).to_have_text("判定：" + label)
    return image, len(buttons)


def measure(page, size, width, height, zoom, rows, stress):
    open_fixture(page, stress=stress)
    assert page.evaluate("() => [innerWidth, innerHeight, devicePixelRatio]") == [width, height, zoom]
    if width > 640:
        chrome = geometry(page, width, height, rows)
    else:
        chrome = {
            "rows": 0, "device_pixel_ratio": zoom,
            "banner_height": locator(page, "db-banner").bounding_box()["height"],
        }
        narrow_actions(page, stress)
    safety_controls = ["db-banner", "stop-open", "stop-indicator", "api-switch"]
    if stress:
        safety_controls += ["needs-review", "reviewed-count"]
        expect(locator(page, "needs-review")).to_have_accessible_name("自動処理：未解消 2")
    for control in safety_controls:
        element = locator(page, control)
        expect(element).to_be_in_viewport(ratio=0.99)
        assert element.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    expect(locator(page, "stop-indicator")).to_have_text("取得停止中" if stress else "取得可能")
    if stress:
        expect(locator(page, "db-banner")).to_contain_text("続ける前に")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    filters = locator(page, "filters")
    toggle = locator(page, "toolbar-toggle")
    output = []
    for expanded in (False, True):
        if filters.is_visible() != expanded:
            enter(page, toggle)
        assert filters.is_visible() == expanded
        gallery = area(page, "gallery", ".sample")
        full_cards = area(page, "gallery", ".specimen")["full_images"]
        if width <= 640 and expanded:
            more = locator(page, "more-tags")
            for _ in range(locator(page, "inline-tags").get_by_role("button").count() + 1):
                page.keyboard.press("Tab")
            expect(more).to_be_focused()
            page.keyboard.press("Space")
            expect(locator(page, "tag-popover")).to_be_in_viewport()
            page.keyboard.press("Escape")
            expect(more).to_be_focused()
        tab(page, "learning")
        expect(locator(page, "screen-title")).to_have_text("学習")
        if width <= 640:
            expect(locator(page, "nav")).to_be_hidden()
            expect(locator(page, "screen-title")).to_be_focused()
        learning, ratings = check_learning(page)
        output.append(dict(
            size=size, css=[width, height], stress=stress, expanded=expanded,
            chrome=chrome, gallery=gallery, full_cards=full_cards, learning=learning, ratings_reached=ratings,
        ))
        if not expanded:
            tab(page, "personal")
    assert output[0]["gallery"]["pct"] >= output[1]["gallery"]["pct"]
    floor = {(1280, 800): (618.6, 464), (1920, 1080): (1042.6, 782), (1024, 768): (525.3, 394)}.get((width, height))
    if floor:
        assert all(value >= target for value, target in zip(output[0]["learning"]["image"], floor))
    return output


def measurements(context, page):
    # Check the wide navigation breakpoints independently of the zoom samples.
    for width, rows in ((1159, 2), (1160, 1), (641, 2)):
        page.set_viewport_size({"width": width, "height": 800})
        open_fixture(page, stress=True)
        geometry(page, width, 800, rows)
    settings = context.new_page()
    settings.goto("chrome://settings/appearance")
    settings.locator("select").first.wait_for(timeout=5000)
    zoom_control = next(select for select in settings.locator("select").all()
                        if "200%" in [text.strip() for text in select.locator("option").all_text_contents()])
    result = []
    for theme in ("dark", "light"):
        page.emulate_media(color_scheme=theme, reduced_motion="reduce")
        for size, width, height, zoom, rows in SIZES:
            zoom_control.select_option(label=f"{zoom:.0%}")
            page.set_viewport_size({"width": round(width * zoom), "height": round(height * zoom)})
            page.bring_to_front()
            for stress in (False, True):
                result.extend(dict(theme=theme, **row) for row in measure(
                    page, size, width, height, zoom, rows, stress))
    return result


@contextmanager
def isolated_browser(external, errors, profile_cleanup):
    if QUIET.exists():
        raise SystemExit("D6 quiet flag is active")
    temp = Path(os.environ["TEMP"]).resolve()
    assert temp == Path(r"S:\Work\_tmp\PV\ui-nav").resolve()
    assert temp == Path(os.environ["TMP"]).resolve()
    with tempfile.TemporaryDirectory(prefix="chromium-", dir=temp) as profile:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                headless=True, viewport={"width": 1280, "height": 800},
                args=["--disable-crash-reporter", "--disable-background-networking",
                      "--host-resolver-rules=MAP * ~NOTFOUND", "--no-proxy-server"],
            )
            try:
                def deny(route):
                    external.append(route.request.resource_type)
                    route.abort()

                context.route(re.compile(r"^(https?|wss?)://"), deny)
                def deny_socket(socket):
                    external.append("websocket")
                    socket.close()

                context.route_web_socket(re.compile(r"^wss?://"), deny_socket)
                page = context.pages[0]
                page.set_default_timeout(3500)
                page.on("pageerror", lambda error: errors.append(str(error)))
                yield context, page
            finally:
                context.close()
        sizes = [path.stat().st_size for path in Path(profile).rglob("*") if path.is_file()]
        profile_cleanup.update(files=len(sizes), bytes=sum(sizes))
    assert not Path(profile).exists()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--h1-only", action="store_true")
    args = parser.parse_args()
    external, errors, rows = [], [], []
    profile_cleanup = {}
    with isolated_browser(external, errors, profile_cleanup) as (context, page):
        browser_version = context.browser.version if context.browser else page.evaluate(
            r"() => navigator.userAgent.match(/Chrome\/([\d.]+)/)[1]")
        h1 = h1_replaced_selection(page)
        if not args.h1_only and all(h1.values()):
            selection_survives_reordering(page)
            automation_transitions(page)
            rows = measurements(context, page)
    result = {
        "h1": {"result": "PASS" if all(h1.values()) else "FAIL", **h1},
        "wireframe_sha256": sha256(integrated.HTML.read_bytes()).hexdigest(),
        "external_requests": len(external), "page_errors": len(errors),
        "isolated_profile_removed": True,
        "profile_cleanup": profile_cleanup,
    }
    if rows:
        compact = [row for row in rows if row["css"] == [640, 400] and row["stress"]]
        result.update(
            browser=browser_version, zoom="Chromium appearance page zoom",
            selection_reordering_and_bookmarks="PASS", automation_transitions="PASS",
            navigation_breakpoints={"640": "disclosure", "641": 2, "1159": 2, "1160": 1},
            at_200_percent={
                "a11_tested_controls": "PASS",
                "stress_one_full_image_collapsed": all(
                    row["gallery"]["full_images"] >= 1 for row in compact if not row["expanded"]),
                "stress_one_full_image_expanded": all(
                    row["gallery"]["full_images"] >= 1 for row in compact if row["expanded"]),
                "stress_one_full_card_collapsed": all(
                    row["full_cards"] >= 1 for row in compact if not row["expanded"]),
            },
            measurements=rows,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(h1.values()) or external or errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
