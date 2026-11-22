"""Verify the integrated local HTML and print its browser measurements as JSON."""

import json
import os
from pathlib import Path
import re
import tempfile

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[3]
HTML = ROOT / "docs/design/UI_NAV_RESTRUCTURE_20261122.html"
TEMP = Path(r"S:\Work\_tmp\PV")
QUIET = TEMP / "sup/D6_QUIET.flag"
LABELS = ["ダウンロード", "キュー", "失敗リスト", "重複整理", "学習", "ランキング",
          "カスタムランキング", "ライブラリ", "マイブックマーク", "おすすめ"]
RATINGS = ["見送る", "合わない", "いいね", "SuperLike"]
SIZES = (
    ("1280x800", 1280, 800, 1, 1),
    ("1920x1080", 1920, 1080, 1, 1),
    ("1024x768", 1024, 768, 1, 2),
    ("1280x800@125%", 1024, 640, 1.25, 2),
    ("1280x800@200%", 640, 400, 2, 2),
)


def locator(page, identifier):
    return page.locator("#" + identifier)


def open_fixture(page, *, stress=False, tab="personal"):
    fixture = "&db=long&stop=on&failure=both" if stress else ""
    page.goto(f"{HTML.as_uri()}?tab={tab}{fixture}")


def choose_tab(page, key):
    page.get_by_test_id("tab-" + key).click()


def interactions(page):
    open_fixture(page)
    banner = locator(page, "db-banner")
    assert banner.is_visible(), "Missing the integrated production DB warning"
    for key, label in (("personal", "カスタムランキング"), ("learning", "学習"),
                       ("bookmarks", "マイブックマーク"), ("library", "ライブラリ")):
        expect(page.get_by_test_id("tab-" + key)).to_have_accessible_name(label)
    expect(locator(page, "relocation-hint")).to_be_visible()
    locator(page, "dismiss-hint").click()
    page.reload()
    expect(locator(page, "relocation-hint")).to_be_hidden()

    more = locator(page, "more-tags")
    row = locator(page, "personal-candidates")
    expect(row).to_be_visible()
    expect(more).to_have_attribute("title", "ほか " + more.inner_text().removeprefix("+") + " 件")
    more.focus()
    page.keyboard.press("Space")
    popover = locator(page, "tag-popover")
    expect(popover).to_be_visible()
    first = popover.get_by_role("button").first
    expect(first).to_be_focused()
    page.keyboard.press("Escape")
    expect(more).to_be_focused()
    more.click()
    selected = first.inner_text()
    first.click()
    expect(locator(page, "selected-tag")).to_have_text(selected)
    toggle = locator(page, "toolbar-toggle")
    toggle.click()
    expect(row).to_be_hidden()
    expect(locator(page, "candidate-entry")).to_contain_text("22")
    locator(page, "candidate-entry").click()
    expect(row).to_be_visible()
    toggle.click()
    page.reload()
    expect(row).to_be_hidden()

    choose_tab(page, "library")
    expect(locator(page, "screen-description")).to_have_text("このPCに保存した作品")
    expect(page.get_by_text("保存済み", exact=True).first).to_be_visible()
    locator(page, "toolbar-toggle").click()
    locator(page, "search").fill("作品")
    library_card = locator(page, "gallery").get_by_role("button").first
    library_card.click()
    expect(locator(page, "library-back")).to_be_visible()
    choose_tab(page, "bookmarks")
    expect(locator(page, "screen-description")).to_have_text("Pixivでブックマークした作品")
    expect(page.get_by_text("未保存", exact=True).first).to_be_visible()
    locator(page, "toolbar-toggle").click()
    expect(locator(page, "search")).to_have_value("")
    locator(page, "search").fill("ブックマーク")
    choose_tab(page, "library")
    expect(locator(page, "search")).to_have_value("作品")
    expect(locator(page, "library-back")).to_be_visible()
    expect(library_card).to_have_attribute("aria-pressed", "true")
    expect(library_card).to_be_focused()
    locator(page, "scope-pixiv").check()
    expect(page.get_by_text("未保存", exact=True)).to_have_count(0)
    locator(page, "search-pixiv").click()
    expect(page.get_by_text("未保存", exact=True).first).to_be_visible()

    safety = locator(page, "stop-open")
    safety.focus()
    page.keyboard.press("Enter")
    expect(locator(page, "safety")).to_be_visible()
    page.keyboard.press("Escape")
    expect(safety).to_be_focused()
    switch = page.get_by_role("switch", name="API切り替え")
    switch.focus()
    page.keyboard.press("Space")
    expect(switch).to_have_attribute("aria-checked", "true")

    opener = locator(page, "automation-open")
    status = locator(page, "automation-announcement")
    badge = locator(page, "automation-badge")
    auto = page.get_by_role("dialog", name="自動処理")
    opener.click()
    scheduler = auto.get_by_role("tab", name="定期チェック")
    metadata = auto.get_by_role("tab", name="メタデータ補完")
    expect(scheduler).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(metadata).to_be_focused()
    page.keyboard.press("Home")
    expect(scheduler).to_be_focused()
    close = auto.get_by_role("button", name="閉じる", exact=True)
    close.focus()
    page.keyboard.press("Shift+Tab")
    assert auto.evaluate("dialog => dialog.contains(document.activeElement)")
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Escape")
    expect(opener).to_be_focused()
    locator(page, "preferences-open").click()
    settings = page.get_by_role("dialog", name="設定")
    expect(settings.locator(".auto-summary-line")).to_have_count(2)
    expect(settings.locator(".auto-summary-line").first).to_contain_text("次回予定")
    locator(page, "metadata-enabled").uncheck()
    expect(badge).to_be_hidden()
    expect(opener).to_have_attribute("title", re.compile("停止（設定で OFF）"))
    locator(page, "metadata-enabled").check()
    locator(page, "db-control").select_option("off")
    expect(banner).to_be_hidden()
    locator(page, "db-control").select_option("long")
    expect(banner).to_contain_text("続ける前に")
    locator(page, "stop-control").check()
    expect(badge).to_have_attribute("data-state", "blocked")
    locator(page, "stop-control").uncheck()
    locator(page, "db-control").select_option("short")
    settings.get_by_test_id("preferences-automation-metadata").click()
    expect(metadata).to_be_focused()
    auto.get_by_role("button", name="いま埋める").click()
    expect(badge).to_be_hidden()
    locator(page, "metadata-stop").click()
    expect(auto.locator("#metadata-status")).to_have_text("停止を要求中")
    expect(badge).to_be_hidden()
    locator(page, "metadata-finish").click()
    expect(auto.locator("#metadata-status")).to_have_text("停止済み（手動）")
    page.keyboard.press("Escape")
    expect(locator(page, "preferences-open")).to_be_focused()

    opener.click()
    locator(page, "scheduler-close-fail").click()
    expect(auto).to_be_hidden()
    expect(opener).to_be_focused()
    expect(status).to_contain_text("定期チェック 失敗")
    expect(locator(page, "needs-review")).to_have_text("自動処理：要確認 1")
    expect(opener).to_be_focused()
    opener.click()
    auto.get_by_text("共有ログ", exact=True).click()
    expect(locator(page, "sample-log")).to_contain_text("失敗を記録")
    page.keyboard.press("Escape")

    open_fixture(page, stress=True)
    expect(locator(page, "needs-review")).to_have_text("自動処理：要確認 2")
    expect(status).to_have_attribute("role", "status")
    expect(status).to_contain_text("メタデータ補完")
    expect(badge).to_have_attribute("data-state", "failed")
    locator(page, "needs-review").click()
    expect(scheduler).to_be_focused()
    locator(page, "scheduler-ack").click()
    expect(auto.locator("#scheduler-status")).to_contain_text("確認済み")
    page.keyboard.press("Escape")
    expect(locator(page, "needs-review")).to_be_focused()
    expect(locator(page, "needs-review")).to_have_text("自動処理：要確認 2")
    expect(locator(page, "reviewed-count")).to_have_text("確認済み 1")
    locator(page, "needs-review").click()
    expect(metadata).to_be_focused()
    scheduler.click()
    locator(page, "scheduler-recover").click()
    page.keyboard.press("Escape")
    expect(locator(page, "needs-review")).to_have_text("自動処理：要確認 1")
    locator(page, "needs-review").click()
    expect(metadata).to_be_focused()
    locator(page, "metadata-recover").click()
    page.keyboard.press("Escape")
    expect(locator(page, "needs-review")).to_be_hidden()
    expect(opener).to_be_focused()
    expect(badge).to_have_attribute("data-state", "blocked")
    for key in ("scheduler", "metadata"):
        open_fixture(page, tab=key)
        expect(locator(page, "auto-" + key)).to_have_attribute("aria-selected", "true")
        page.keyboard.press("Escape")
        expect(page.get_by_test_id("tab-download")).to_have_attribute("aria-current", "page")
        expect(opener).to_be_focused()


def geometry(page, width, height, rows):
    return page.evaluate("""({width, height, rows, labels}) => {
        const inside = (r, margin = 0) => r.left >= margin && r.right <= width - margin
            && r.top >= margin && r.bottom <= height - margin;
        const rect = e => e.getBoundingClientRect();
        const nav = [...document.querySelectorAll('.nav-button')];
        const icons = [...document.querySelectorAll('.utility')];
        const require = (condition, subject) => { if (!condition) throw Error(subject); };
        require(nav.length === 10 && icons.length === 7, 'tab/icon count');
        require(new Set(nav.map(e => e.offsetTop)).size === rows, 'navigation rows');
        require(Math.max(...nav.map(e => rect(e).width)) - Math.min(...nav.map(e => rect(e).width)) < 1, 'tab widths');
        require(nav.map(e => e.querySelector('.nav-label').textContent).every(
            (label, i) => label === labels[i]), 'full labels');
        for (const button of nav) {
            const range = document.createRange();
            range.selectNodeContents(button);
            const text = range.getBoundingClientRect(), bounds = rect(button);
            require(text.left >= bounds.left - .25 && text.right <= bounds.right + .25, 'text: ' + button.innerText);
            require(inside(bounds, 4), 'navigation focus outline: ' + button.innerText);
        }
        for (const el of [...icons, ...document.querySelectorAll(
            '#db-banner, #stop-open, #api-switch, .identity')]) {
            require(inside(rect(el)), 'header bounds: ' + (el.id || el.innerText));
            require(el.scrollWidth <= el.clientWidth + 1, 'header overflow: ' + (el.id || el.innerText));
        }
        require(document.documentElement.scrollWidth <= width, 'document width');
        const banner = rect(document.querySelector('#db-banner'));
        const logo = rect(document.querySelector('.identity h1'));
        const auth = rect(document.querySelector('.identity .auth'));
        require(auth.top >= logo.bottom - .25, 'login state line');
        return {rows, banner_height: banner.height, device_pixel_ratio: devicePixelRatio};
    }""", {"width": width, "height": height, "rows": rows, "labels": LABELS})


def area(page, target, child):
    return page.evaluate("""({target, child}) => {
        const root = document.getElementById(target);
        const viewport = root.getBoundingClientRect();
        const clip = {
            top: Math.max(0, viewport.top), left: Math.max(0, viewport.left),
            right: Math.min(innerWidth, viewport.right),
            bottom: Math.min(innerHeight, viewport.bottom),
        };
        const visible = rect => [
            Math.max(0, Math.min(clip.right, rect.right) - Math.max(clip.left, rect.left)),
            Math.max(0, Math.min(clip.bottom, rect.bottom) - Math.max(clip.top, rect.top)),
        ];
        const [width, height] = visible(viewport);
        const artwork = [...root.querySelectorAll(child)].map(e => e.getBoundingClientRect());
        const visibleArt = artwork.filter(r => visible(r).every(n => n > 1));
        const full = visibleArt.filter(r => visible(r).every((n, i) => n >= [r.width, r.height][i] - 1));
        const first = visibleArt[0];
        return {y: clip.top, pct: 100 * width * height / (innerWidth * innerHeight),
            visible: visibleArt.length, full_images: full.length, image_y: first ? Math.max(first.top, clip.top) : null,
            image: first ? visible(first) : [0, 0]};
    }""", {"target": target, "child": child})


def measure(page, width, height, rows, dpr, stress):
    open_fixture(page, stress=stress)
    chrome = geometry(page, width, height, rows)
    assert abs(chrome["device_pixel_ratio"] - dpr) < 0.01
    expect(locator(page, "stop-indicator")).to_have_text("取得停止中" if stress else "取得可能")
    assert locator(page, "db-banner").inner_text().startswith("本番DBを使用中")
    if stress:
        expect(locator(page, "needs-review")).to_have_text("自動処理：要確認 2")
    filters = locator(page, "filters")
    if filters.is_hidden():
        locator(page, "toolbar-toggle").click()
    expanded = area(page, "gallery", ".sample")
    locator(page, "toolbar-toggle").click()
    collapsed = area(page, "gallery", ".sample")
    assert collapsed["pct"] >= expanded["pct"]
    assert expanded["visible"] and collapsed["visible"], (width, height, stress, chrome, expanded, collapsed)
    if dpr == 2 and stress:
        locator(page, "api-switch").focus()
        page.keyboard.press("Tab")
        expect(locator(page, "stop-open")).to_be_focused()
        page.keyboard.press("Enter")
        expect(locator(page, "safety")).to_be_visible()
        page.keyboard.press("Escape")
        expect(locator(page, "stop-open")).to_be_focused()
        locator(page, "needs-review").click()
        page.keyboard.press("ArrowRight")
        expect(locator(page, "auto-metadata")).to_be_focused()
        page.keyboard.press("Escape")
        expect(locator(page, "needs-review")).to_be_focused()
        locator(page, "candidate-entry").click()
        more = locator(page, "more-tags")
        more.focus()
        page.keyboard.press("Space")
        expect(locator(page, "tag-popover")).to_be_in_viewport()
        page.keyboard.press("Escape")
        expect(more).to_be_focused()
    choose_tab(page, "learning")
    lesson = area(page, "learning-stage", "#learning-image")
    assert lesson["visible"], "No visible learning image"
    floor = {(1280, 800): (618.6, 464), (1920, 1080): (1042.6, 782), (1024, 768): (525.3, 394)}.get((width, height))
    if floor:
        assert all(result >= threshold for result, threshold in zip(lesson["image"], floor)), (lesson, floor)
    ratings = locator(page, "learning-ratings")
    ratings.get_by_role("button", name=RATINGS[0], exact=True).focus()
    for index, label in enumerate(RATINGS):
        button = ratings.get_by_role("button", name=label, exact=True)
        expect(button).to_be_in_viewport(ratio=0.99)
        if index:
            page.keyboard.press("Tab")
        expect(button).to_be_focused()
        page.keyboard.press("Enter")
        expect(locator(page, "learning-feedback")).to_contain_text(label)
    return dict(chrome=chrome, expanded=expanded, collapsed=collapsed, learning=lesson,
                ratings_reached=len(RATINGS))


def main():
    if QUIET.exists():
        raise SystemExit("SKIP: D6 quiet flag")
    assert Path(os.environ["TEMP"]).resolve() == TEMP.resolve() and os.environ["TEMP"] == os.environ["TMP"]
    measurements, requests, errors = [], [], []
    with tempfile.TemporaryDirectory(prefix="ui-nav-integrated-", dir=TEMP) as profile:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                profile, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                headless=True, args=["--disable-crash-reporter", "--disable-background-networking"],
                viewport={"width": 1280, "height": 800},
            )
            try:
                def deny(route):
                    requests.append(route.request.resource_type)
                    route.abort()

                browser.route("http**://**/*", deny)
                page = browser.pages[0]
                page.set_default_timeout(3500)
                page.on("pageerror", lambda error: errors.append(str(error)))
                interactions(page)
                page.set_viewport_size({"width": 1159, "height": 800})
                open_fixture(page, stress=True)
                geometry(page, 1159, 800, 2)
                page.set_viewport_size({"width": 1160, "height": 800})
                geometry(page, 1160, 800, 1)
                settings = browser.new_page()
                settings.goto("chrome://settings/appearance")
                settings.locator("select").first.wait_for(timeout=5000)
                zoom = next(select for select in settings.locator("select").all()
                            if "200%" in (text.strip() for text in select.locator("option").all_text_contents()))
                for theme in ("dark", "light"):
                    page.emulate_media(color_scheme=theme, reduced_motion="reduce")
                    for name, width, height, dpr, rows in SIZES:
                        zoom.select_option(label=f"{dpr:.0%}")
                        page.set_viewport_size({"width": round(width * dpr), "height": round(height * dpr)})
                        page.bring_to_front()
                        page.evaluate("localStorage.removeItem('pv-wireframe-ranking_personal_candidates_open')")
                        for stress in (False, True):
                            measurements.append(dict(
                                size=name, css=[width, height], theme=theme, stress=stress,
                                **measure(page, width, height, rows, dpr, stress)))
                assert not requests and not errors, (requests, errors)
            finally:
                browser.close()
    assert not Path(profile).exists()
    print(json.dumps({
        "interaction_checks": "PASS", "http_requests": len(requests), "page_errors": len(errors),
        "isolated_profile_removed": True, "zoom": "Chromium appearance page zoom",
        "breakpoint": {"1159": 2, "1160": 1}, "measurements": measurements,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
