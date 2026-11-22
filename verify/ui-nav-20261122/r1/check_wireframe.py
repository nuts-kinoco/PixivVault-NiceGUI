"""Exercise the local navigation wireframe in an isolated Chromium context."""

from pathlib import Path
import os
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[3]
WIREFRAME = ROOT / "docs/design/UI_NAV_RESTRUCTURE_20261122.html"
TEMP_ROOT = Path(r"S:\Work\_tmp\PV")
QUIET_FLAG = TEMP_ROOT / "sup/D6_QUIET.flag"
TABS = (
    "download", "queue", "failed", "dedup", "learning", "ranking",
    "personal", "library", "bookmarks", "recommend",
)


def check(page):
    page.goto(WIREFRAME.as_uri())
    nav = page.get_by_role("navigation", name="メインナビゲーション")
    assert nav.get_by_role("button").count() == 10
    assert nav.get_by_role("button").evaluate_all("tabs => tabs.map(tab => tab.dataset.key)") == list(TABS)
    for key in TABS:
        tab = nav.get_by_test_id(f"tab-{key}")
        tab.click()
        expect(tab).to_have_attribute("aria-current", "page")
    nav.get_by_test_id("tab-library").click()
    expect(page.get_by_role("heading", name="ライブラリ", exact=True)).to_be_visible()
    expect(page.get_by_test_id("library-subview-bookmarks")).to_have_count(0)
    nav.get_by_test_id("tab-bookmarks").click()
    expect(page.get_by_role("heading", name="マイブックマーク", exact=True)).to_be_visible()

    opener = page.get_by_test_id("automation-open")
    dialog = page.get_by_role("dialog", name="自動処理")
    opener.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    scheduler = dialog.get_by_role("tab", name="定期チェック")
    metadata = dialog.get_by_role("tab", name="メタデータ補完")
    expect(scheduler).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(metadata).to_be_focused()
    expect(metadata).to_have_attribute("aria-selected", "true")
    expect(dialog.get_by_test_id("metadata-view")).to_be_visible()
    expect(dialog.get_by_test_id("scheduler-view")).to_be_hidden()
    close = dialog.get_by_role("button", name="閉じる", exact=True)
    close.focus()
    page.keyboard.press("Shift+Tab")
    assert dialog.evaluate("element => element.contains(document.activeElement)")
    page.keyboard.press("Tab")
    expect(close).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    expect(nav.get_by_test_id("tab-bookmarks")).to_have_attribute("aria-current", "page")

    settings = page.get_by_test_id("preferences-open")
    settings.click()
    page.get_by_test_id("preferences-automation-metadata").click()
    expect(metadata).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")
    expect(settings).to_be_focused()

    badge = page.locator("#automation-badge")
    expect(badge).to_be_hidden()
    opener.click()
    run_scheduler = dialog.get_by_role("button", name="いまチェック")
    run_scheduler.click()
    expect(dialog.locator("#scheduler-status")).to_have_text("実行中")
    expect(badge).to_have_attribute("data-state", "running")
    metadata.click()
    dialog.get_by_role("button", name="いま埋める").click()
    dialog.get_by_role("button", name="取得の停止を要求").click()
    expect(dialog.locator("#metadata-status")).to_have_text("停止")
    expect(badge).to_have_attribute("data-state", "running")
    scheduler.click()
    run_scheduler.click()
    expect(badge).to_have_attribute("data-state", "failed")
    dialog.get_by_text("共有ログ", exact=True).click()
    expect(dialog.locator("#sample-log")).to_contain_text("定期チェック 失敗")
    page.keyboard.press("Escape")
    expect(badge).to_be_visible()
    expect(opener).to_have_accessible_name("自動処理、定期チェック 失敗、メタデータ補完 停止")
    opener.click()
    expect(scheduler).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Escape")

    for requested, destination in (("scheduler", scheduler), ("metadata", metadata)):
        page.goto(f"{WIREFRAME.as_uri()}?tab={requested}")
        expect(destination).to_have_attribute("aria-selected", "true")
        page.keyboard.press("Escape")
        expect(nav.get_by_test_id("tab-download")).to_have_attribute("aria-current", "page")
        expect(opener).to_be_focused()
    page.goto(f"{WIREFRAME.as_uri()}?tab=bookmarks")
    expect(nav.get_by_test_id("tab-bookmarks")).to_have_attribute("aria-current", "page")
    page.goto(f"{WIREFRAME.as_uri()}?tab=unknown")
    expect(page.get_by_role("status").filter(has_text="不明な起動先")).to_be_visible()
    expect(nav.get_by_test_id("tab-download")).to_have_attribute("aria-current", "page")

    nav.get_by_test_id("tab-personal").click()
    page.get_by_test_id("toolbar-toggle").click()
    row = page.get_by_test_id("personal-candidates-row")
    more = page.get_by_test_id("personal-candidates-more")
    popover = page.get_by_test_id("personal-candidates-popover")
    expect(more).to_be_visible()
    assert row.evaluate("row => row.scrollWidth <= row.clientWidth + 1")
    more.focus()
    page.keyboard.press("Space")
    expect(popover).to_be_visible()
    count = popover.get_by_role("button").count()
    expect(more).to_have_text(f"+{count}")
    first = popover.get_by_role("button").first
    expect(first).to_be_focused()
    page.keyboard.press("Escape")
    expect(more).to_be_focused()
    expect(popover).to_be_hidden()
    more.click()
    label = first.inner_text()
    first.click()
    expect(page.get_by_test_id("selected-tag")).to_have_text(label)
    expect(more).to_be_focused()
    page.get_by_test_id("toolbar-toggle").click()
    expect(row).to_be_hidden()
    page.get_by_test_id("toolbar-toggle").click()
    more.click()
    page.get_by_role("heading", name="自分のランキング").click()
    expect(popover).to_be_hidden()


def measure(page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{WIREFRAME.as_uri()}?tab=personal")
    nav = page.get_by_role("navigation", name="メインナビゲーション")
    geometry = nav.get_by_role("button").evaluate_all(
        "buttons => buttons.map(b => [b.getBoundingClientRect().x, b.offsetTop, b.offsetWidth])"
    )
    rows = len({item[1] for item in geometry})
    assert max(item[2] for item in geometry) - min(item[2] for item in geometry) <= 1
    assert rows == (1 if width >= 1280 else 2 if width >= 640 else 5)
    assert nav.get_by_role("button").evaluate_all(
        """buttons => buttons.every(button => {
            const range = document.createRange();
            range.selectNodeContents(button);
            const text = range.getBoundingClientRect();
            const box = button.getBoundingClientRect();
            return text.left >= box.left - 1 && text.right <= box.right + 1;
        })"""
    )
    assert page.locator(".utilities button").evaluate_all(
        """buttons => buttons.length === 7 && buttons.every(button => {
            const box = button.getBoundingClientRect();
            return box.left >= 0 && box.right <= innerWidth && box.width === 40 && box.height === 40;
        })"""
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.get_by_test_id("toolbar-toggle").click()
    row = page.get_by_test_id("personal-candidates-row")
    assert row.evaluate("row => row.scrollWidth <= row.clientWidth + 1")
    more = page.get_by_test_id("personal-candidates-more")
    more.click()
    menu = page.get_by_test_id("personal-candidates-popover").bounding_box()
    assert menu["x"] >= 0 and menu["x"] + menu["width"] <= width + 1
    assert menu["y"] >= 0 and menu["y"] + menu["height"] <= height + 1
    assert row.locator("#inline-tags button").count() + page.locator("#popover-items button").count() == 22
    page.keyboard.press("Escape")
    page.get_by_test_id("automation-open").click()
    dialog = page.get_by_role("dialog", name="自動処理")
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1
    assert box["y"] >= 0 and box["y"] + box["height"] <= height + 1
    expect(dialog.get_by_role("button", name="閉じる", exact=True)).to_be_in_viewport()
    page.keyboard.press("Escape")
    print(f"PASS {width}x{height}: {rows} navigation row(s), labels and 7 icons fit, no page overflow, overlays fit")


def main():
    if not WIREFRAME.is_file():
        sys.exit("RED: navigation wireframe is missing")
    if QUIET_FLAG.exists():
        sys.exit("SKIP: D6 quiet flag is present")
    if Path(os.environ["TEMP"]).resolve() != TEMP_ROOT.resolve() or os.environ["TMP"] != os.environ["TEMP"]:
        sys.exit("Set TEMP and TMP to S:\\Work\\_tmp\\PV")
    network = []
    errors = []
    with tempfile.TemporaryDirectory(prefix="ui-nav-wireframe-", dir=TEMP_ROOT) as profile:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch_persistent_context(
                profile, channel="chromium", headless=True,
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                args=["--disable-crash-reporter", "--disable-background-networking"],
                viewport={"width": 1280, "height": 800},
            )
            try:
                def deny(route):
                    network.append(route.request.resource_type)
                    route.abort()

                browser.route("http**://**/*", deny)
                page = browser.pages[0]
                page.on("pageerror", lambda error: errors.append(str(error)))
                check(page)
                print("PASS navigation, keyboard, focus, settings, launch targets, badges, shared log, and tag selection")
                for theme in ("dark", "light"):
                    page.emulate_media(color_scheme=theme)
                    print(f"{theme}:")
                    for viewport in ((1024, 768), (1280, 800), (1920, 1080), (640, 400), (320, 540)):
                        measure(page, *viewport)
                assert not network, network
                assert not errors, errors
                print("PASS 0 HTTP(S) requests and 0 page errors")
            finally:
                browser.close()
    assert not Path(profile).exists()
    print("PASS isolated browser profile removed")


if __name__ == "__main__":
    main()
