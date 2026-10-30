"""Playwright実クリックによる合成ライブラリ受入検証。

アプリ・合成fixtureサーバは tools/run_browser_checks.py が一度だけ起動する。
実APIや本番DBには接続せず、loopbackのサーバだけを閲覧する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect, sync_playwright


VIEWPORTS = ((1920, 1080), (1280, 720))
AUTHOR_ID = "demo-author-01"
REPRESENTATIVE = "demo-0060"
AUTHOR_COUNT = 4
DETAIL_PAGES = 98
SIZE_COLS = {"large": "6", "medium": "8", "small": "12"}


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def library(page: Page):
    root = page.get_by_test_id("library-view")
    expect(root).to_be_visible(timeout=15_000)
    expect(root).to_have_attribute("data-active", "true")
    return root


def author_cards(root):
    return root.locator(".pv-library-author-card")


def page_cards(root):
    return root.locator(".pv-library-page-card")


def author_count(root, shown: int, matched: int) -> None:
    expect(root.get_by_test_id("library-author-count")).to_have_text(
        f"{shown} / {matched} 作者表示（全{AUTHOR_COUNT}作者）", timeout=15_000,
    )
    expect(author_cards(root)).to_have_count(shown, timeout=15_000)


def page_count(root, shown: int) -> None:
    expect(root.get_by_test_id("library-page-count")).to_have_text(
        f"{shown} / {DETAIL_PAGES} 枚表示", timeout=15_000,
    )
    expect(page_cards(root)).to_have_count(shown, timeout=15_000)


def search_field(root):
    # NiceGUI ui.input attaches data-testid to the native input itself.
    return root.get_by_placeholder("作品名・作者・タグで検索")


def author_card(root):
    card = root.locator(f'.pv-library-author-card[data-author-id="{AUTHOR_ID}"]')
    expect(card).to_have_count(1)
    expect(card).to_have_attribute("data-representative-id", REPRESENTATIVE)
    return card


def open_author(root) -> None:
    opener = author_card(root).get_by_test_id("library-author-open")
    opener.click()
    expect(root.get_by_test_id("library-detail")).to_be_visible()
    expect(root.get_by_test_id("library-authors-scroll")).to_be_hidden()
    page_count(root, 50)
    expect(root.get_by_test_id("library-unknown-pages")).to_contain_text(
        "1件はページ数が分からない"
    )
    expect(root.get_by_test_id("library-detail")).to_contain_text(
        "画像 98 枚"
    )


def decode_image(image, name: str, expected_path: str) -> dict:
    expect(image).to_be_visible(timeout=15_000)
    image.scroll_into_view_if_needed(timeout=10_000)
    image.evaluate("(el) => el.decode()", timeout=15_000)
    info = image.evaluate("""el => ({
        currentSrc: el.currentSrc || el.src, complete: el.complete,
        naturalWidth: el.naturalWidth, naturalHeight: el.naturalHeight
    })""")
    check(info["complete"] and info["naturalWidth"] > 0 and info["naturalHeight"] > 0,
          f"{name}: 画像のdecode/naturalSizeが無効: {info}")
    check(expected_path in info["currentSrc"],
          f"{name}: 期待した合成画像ではない: {info}")
    return {"natural_width": info["naturalWidth"], "natural_height": info["naturalHeight"]}


def check_size(page: Page, root, size: str) -> None:
    expect(root.get_by_test_id(f"library-size-{size}")).to_have_attribute(
        "aria-pressed", "true",
    )
    expect(page.get_by_test_id(f"gallery-size-{size}")).to_have_attribute(
        "aria-pressed", "true",
    )
    expect(page.locator(".pv-gallery .pv-grid")).to_have_css(
        "--pv-max-cols", SIZE_COLS[size],
    )
    for grid in (root.locator(".pv-library-panel .pv-grid").first,
                 root.locator(".pv-library-panel .pv-grid").last):
        expect(grid).to_have_css("--pv-max-cols", SIZE_COLS[size])


def check_layout(page: Page) -> dict:
    measured = page.evaluate("""() => {
        const r = document.querySelector('.pv-library').getBoundingClientRect();
        return {width:innerWidth, height:innerHeight,
            documentWidth:document.documentElement.scrollWidth,
            documentHeight:document.documentElement.scrollHeight,
            library:{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};
    }""")
    check(measured["documentWidth"] <= measured["width"] + 1,
          f"ブラウザ全体が横にはみ出す: {measured}")
    check(measured["documentHeight"] <= measured["height"] + 1,
          f"ブラウザ全体が縦にスクロールする: {measured}")
    rect = measured["library"]
    check(rect["width"] > 100 and rect["height"] > 100 and rect["x"] >= 0
          and rect["bottom"] <= measured["height"] + 1,
          f"ライブラリがviewportに収まらない: {measured}")
    return measured


def check_sort(root) -> dict:
    """4作者の閲覧数順→保存日降順→昇順→閲覧数順を実クリックする。"""
    def first_author() -> str:
        author_count(root, 4, 4)
        value = author_cards(root).first.get_attribute("data-author-id")
        check(bool(value), "作者カードにdata-author-idがない")
        return value

    view_count = root.get_by_test_id("library-sort-view_count")
    saved = root.get_by_test_id("library-sort-last_backup")
    expect(view_count).to_have_attribute("aria-pressed", "true")
    check(first_author() == "demo-author-02",
          "初期閲覧数降順が合成fixtureの順と一致しない")
    saved.click()
    expect(saved).to_have_attribute("aria-pressed", "true")
    expect(saved).to_contain_text("保存日↓")
    newest_first = first_author()
    check(newest_first == "demo-author-04",
          f"保存日降順の先頭作者が不正: {newest_first}")
    saved.click()
    expect(saved).to_contain_text("保存日↑")
    oldest_first = first_author()
    check(oldest_first == "demo-author-01",
          f"保存日昇順の先頭作者が不正: {oldest_first}")
    view_count.click()
    expect(view_count).to_have_attribute("aria-pressed", "true")
    expect(saved).to_have_attribute("aria-pressed", "false")
    expect(view_count).to_contain_text("閲覧数↓")
    restored = first_author()
    check(restored == "demo-author-02",
          f"閲覧数順への復帰が不正: {restored}")
    return {"saved_desc_first": newest_first, "saved_asc_first": oldest_first,
            "view_count_restored_first": restored}


def image_ready(page: Page, work_id: str, page_no: int) -> dict:
    image = page.get_by_test_id("library-viewer-image").locator("img:visible")
    expect(image).to_have_count(1, timeout=15_000)
    expect(image).to_have_attribute(
        "src",
        re.compile(rf"/media/{work_id}/{page_no}/view\.png(?:#request-\d+(?:-\d+)?)?$"),
        timeout=15_000,
    )
    info = decode_image(image, "ライブラリ・ビューア",
                        f"/media/{work_id}/{page_no}/view.png")
    expect(page.get_by_test_id("library-viewer-image")).to_have_css(
        "opacity", "1", timeout=15_000,
    )
    expect(page.get_by_test_id("library-viewer").get_by_text("画像読込中")).to_be_hidden(
        timeout=15_000,
    )
    return info


def open_page_three(page: Page, root, record: dict) -> None:
    target = root.locator(
        f'.pv-library-page-card[data-work-id="{REPRESENTATIVE}"][data-page-no="2"]'
    )
    expect(target).to_have_count(1)
    target.scroll_into_view_if_needed()
    target.click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible(timeout=15_000)
    expect(viewer).to_have_attribute("aria-modal", "false")
    expect(viewer.locator(".pv-counter")).to_have_attribute("aria-label", "ページ 3 / 3")
    record["page3_decoded"] = image_ready(page, REPRESENTATIVE, 2)
    expect(viewer).to_be_focused()
    expect(viewer.get_by_role("button", name="次のページ")).to_be_disabled()
    viewer.get_by_role("button", name="前のページ").click()
    expect(viewer.locator(".pv-counter")).to_have_attribute("aria-label", "ページ 2 / 3")
    image_ready(page, REPRESENTATIVE, 1)
    viewer.get_by_role("button", name="次のページ").click()
    expect(viewer.locator(".pv-counter")).to_have_attribute("aria-label", "ページ 3 / 3")
    image_ready(page, REPRESENTATIVE, 2)
    viewer.get_by_role("button", name="閉じる").click()
    expect(viewer).to_be_hidden()
    expect(viewer).to_have_attribute("aria-hidden", "true")
    expect(target).to_be_focused(timeout=15_000)
    record["viewer_focus_restored"] = True


def check_synthetic_bookmarks(page: Page, root, output: Path, key: str, record: dict) -> None:
    """Verify click/selection/More/viewer and subview return without external API."""
    root.get_by_test_id("library-subview-bookmarks").click()
    bookmarks = root.get_by_test_id("bookmark-view")
    expect(bookmarks).to_be_visible()
    cards = bookmarks.get_by_test_id("bookmark-card")
    expect(cards).to_have_count(50)
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 110 件表示")
    expect(bookmarks.get_by_test_id("bookmark-downloaded")).to_have_count(40)
    thumb = cards.first.locator("img").first
    decode_image(thumb, "マイブックマーク・サムネイル", "/media/demo-0001/0/thumb.png")
    thumb_rect = thumb.bounding_box()
    check(thumb_rect is not None and thumb_rect["width"] >= 80 and thumb_rect["height"] >= 80,
          f"ブックマーク画像が実際の表示面積を持たない: {thumb_rect}")
    first_badge = cards.first.get_by_test_id("bookmark-downloaded")
    card_rect, badge_rect = cards.first.bounding_box(), first_badge.bounding_box()
    check(card_rect is not None and badge_rect is not None and
          card_rect["x"] <= badge_rect["x"] and
          card_rect["y"] <= badge_rect["y"] and
          badge_rect["x"] + badge_rect["width"] <= card_rect["x"] + card_rect["width"] + 1 and
          badge_rect["y"] + badge_rect["height"] <= card_rect["y"] + card_rect["height"] + 1,
          f"DL済みbadgeがカード外へ飛んでいる: {card_rect}, {badge_rect}")
    first = cards.first.element_handle()
    check(first is not None, "bookmark first card handle missing")
    chosen = bookmarks.locator('.pv-bookmark-card[data-work-id="demo-0003"]')
    chosen.get_by_test_id("bookmark-select").click()
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    more = bookmarks.get_by_test_id("bookmark-more")
    more.click()
    expect(cards).to_have_count(100)
    check(cards.first.evaluate("(el, first) => el === first", first),
          "bookmark More replaced existing thumbnail DOM")
    more.click()
    expect(cards).to_have_count(110)
    expect(more).to_be_hidden()
    chosen.get_by_test_id("bookmark-open").click()
    viewer = page.get_by_test_id("library-viewer")
    expect(viewer).to_be_visible(timeout=15_000)
    image_ready(page, "demo-0003", 0)
    viewer.get_by_role("button", name="次のページ").click()
    image_ready(page, "demo-0003", 1)
    viewer.get_by_role("button", name="閉じる").click()
    expect(viewer).to_be_hidden()
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    page.screenshot(path=str(output / f"library-{key}-bookmarks.png"), full_page=True)
    root.get_by_test_id("library-subview-downloaded").click()
    expect(bookmarks).to_be_hidden()
    expect(root.get_by_test_id("library-authors-scroll")).to_be_visible()
    root.get_by_test_id("library-subview-bookmarks").click()
    expect(bookmarks).to_be_visible()
    expect(cards).to_have_count(110)
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    check_layout(page)
    record["bookmarks"] = {"initial": 50, "final": 110, "downloaded_badges": 40,
                           "selection_retained": True, "viewer_page_2": True}


def check_tag_filters(page: Page, root, output: Path, key: str, record: dict) -> None:
    """Shared tag filters, real condition chips, push-aside layout and viewer return."""
    bookmarks = root.get_by_test_id("bookmark-view")
    panel = root.get_by_test_id("library-filter-panel")
    now = root.get_by_test_id("library-filter-now")
    expect(panel).to_be_hidden()
    expect(now).to_be_hidden()
    content = root.locator(".pv-library-filter-content")
    closed_rect = content.bounding_box()
    bookmarks.get_by_test_id("bookmark-filter-toggle").click()
    expect(panel).to_be_visible()
    panel_rect, open_rect = panel.bounding_box(), content.bounding_box()
    check(closed_rect is not None and panel_rect is not None and open_rect is not None,
          "filter/content geometry missing")
    check(abs(panel_rect["width"] - 280) <= 1 and
          open_rect["x"] >= panel_rect["x"] + panel_rect["width"] + 10 and
          open_rect["width"] < closed_rect["width"] - 275,
          "filter panel must push content aside, never overlay images")
    expect(panel.locator(".pv-library-filter-seat")).to_have_count(8)
    for key_name in ("per_author", "type_format"):
        expect(panel.get_by_test_id(f"library-filter-seat-{key_name}").locator("input,button")).to_have_count(0)
    tag = panel.get_by_placeholder("タグを追加")

    def add(value):
        tag.fill(value)
        tag.press("Enter")
        expect(tag).to_have_value("")

    add("　縦長　")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("44 / 44 件表示")
    add("横長")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("0 / 0 件表示")
    expect(bookmarks.get_by_test_id("bookmark-status")).to_have_text("条件に合うブックマークがありません")
    panel.get_by_test_id("library-filter-mode").click()
    expect(panel.get_by_test_id("library-filter-mode")).to_have_attribute("data-mode", "or")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 110 件表示")
    vertical = panel.locator('.pv-library-tag-chip[data-tag="縦長"]')
    vertical.get_by_test_id("library-filter-tag-flip").click()
    expect(vertical).to_have_attribute("data-excluded", "true")
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 66 件表示")
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    page.screenshot(path=str(output / f"library-{key}-tag-filters.png"), full_page=True)

    # Conditions remain visible and removable after the panel is closed.
    bookmarks.get_by_test_id("bookmark-filter-toggle").click()
    expect(panel).to_be_hidden()
    expect(now).to_be_visible()
    now.get_by_test_id("library-filter-now-clear").click()
    expect(now).to_be_hidden()
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("50 / 110 件表示")

    root.get_by_test_id("library-subview-downloaded").click()
    search_field(root).fill("")
    root.get_by_test_id("library-filter-toggle").click()
    add("縦長")
    author_count(root, 4, 4)
    add("横長")
    author_count(root, 0, 0)  # Tags may not match two different works of the same author.
    panel.get_by_test_id("library-filter-mode").click()
    author_count(root, 4, 4)
    before_generation = root.get_attribute("data-library-generation")
    panel.locator('.pv-library-tag-chip[data-tag="横長"]').get_by_test_id("library-filter-tag-remove").click()
    expect(root).not_to_have_attribute("data-library-generation", before_generation)
    expect(panel.locator('.pv-library-tag-chip[data-tag="横長"]')).to_have_count(0)
    open_author(root)
    page_count(root, 50)
    root.get_by_test_id("library-more").click()
    page_count(root, DETAIL_PAGES)
    open_page_three(page, root, record)
    root.get_by_test_id("library-back").click()
    author_count(root, 4, 4)
    page.get_by_test_id("tab-ranking").click()
    page.get_by_test_id("tab-library").click()
    expect(panel).to_be_visible()
    expect(now.get_by_test_id("library-filter-now-chip")).to_have_count(1)
    root.get_by_test_id("library-subview-bookmarks").click()
    expect(bookmarks.get_by_test_id("bookmark-count")).to_have_text("44 / 44 件表示")
    expect(bookmarks.get_by_test_id("bookmark-selection")).to_have_text("選択中: 1件")
    now.get_by_test_id("library-filter-now-clear").click()
    bookmarks.get_by_test_id("bookmark-filter-toggle").click()
    check_layout(page)
    record["tag_filters"] = {"normalization": True, "and_zero": True, "or_count": 110,
                             "exclude_count": 66, "panel_width": panel_rect["width"],
                             "push_aside": True, "detail_pages_unfiltered": 98,
                             "selection_and_tab_return": True, "now_chip_clear": True}


def check_metadata_filters(page: Page, root, output: Path, key: str, record: dict) -> None:
    """Real browser events for source-metadata filters; fixture unknowns stay unknown."""
    root.get_by_test_id('library-subview-downloaded').click()
    search_field(root).fill('')
    toggle = root.get_by_test_id('library-filter-toggle')
    expanded = toggle.get_attribute('aria-expanded')
    check(expanded in ('true', 'false'),
          f'filter toggle lacks truthful expanded state: {expanded!r}')
    # The previous tag-filter case can leave the shared panel open across
    # bookmark/downloaded subviews. Blindly toggling may close it and a fast
    # expect(panel).to_be_visible() can observe stale DOM before the update.
    if expanded == 'false':
        toggle.click()
    panel=root.get_by_test_id('library-filter-panel')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    expect(panel).to_be_visible()
    expect(panel.get_by_test_id('library-date-from')).to_be_visible()
    panel.get_by_test_id('library-date-from').fill('2026-01-01')
    author_count(root,0,0)
    panel.get_by_test_id('library-date-preset-all').click()
    author_count(root,4,4)
    panel.get_by_test_id('library-bookmark-preset-100').click()
    author_count(root,0,0)
    panel.get_by_test_id('library-bookmark-preset-0').click()
    author_count(root,4,4)
    panel.get_by_test_id('library-author-filter-input').fill('架空作家 01')
    panel.get_by_test_id('library-author-filter-add').click()
    author_count(root,1,1)
    panel.get_by_test_id('library-author-filter-remove').click()
    author_count(root,4,4)
    panel.get_by_test_id('library-exclude-ai').click()
    expect(panel.get_by_test_id('library-exclude-ai')).to_have_attribute('aria-checked','true')
    expect(root.get_by_test_id('library-filter-now')).to_contain_text('AI除外')
    panel.get_by_test_id('library-include-r18').click()
    expect(panel.get_by_test_id('library-include-r18')).to_have_attribute('aria-checked','false')
    expect(root.get_by_test_id('library-filter-now')).to_contain_text('R-18除外')
    panel.get_by_test_id('library-unread-only').click()
    expect(panel.get_by_test_id('library-unread-only')).to_have_attribute('aria-checked','true')
    expect(root.get_by_test_id('library-filter-now')).to_contain_text('未読のみ')
    root.get_by_test_id('library-filter-now-clear').click()
    root.get_by_test_id('library-sort-capacity').click()
    expect(root.get_by_test_id('library-sort-capacity')).to_have_attribute('aria-pressed','true')
    root.get_by_test_id('library-subview-bookmarks').click()
    panel.get_by_test_id('library-unfetched-only').click()
    expect(panel.get_by_test_id('library-unfetched-only')).to_have_attribute('aria-checked','true')
    expect(root.get_by_test_id('bookmark-count')).to_have_text('50 / 70 件表示')
    page.screenshot(path=str(output/f'library-{key}-metadata-filters.png'),full_page=True)
    root.get_by_test_id('library-filter-now-clear').click()
    expect(root.get_by_test_id('bookmark-count')).to_have_text('50 / 110 件表示')
    root.get_by_test_id('bookmark-filter-toggle').click()
    check_layout(page)
    record['metadata_filters']={'date_unknown_excluded':True,'bookmark_unknown_excluded':True,
        'author_filter':True,'r18_ai_unread_controls':True,'capacity_sort':True,'unfetched_count':70}


def check_failure_retry(page: Page, root, result: dict, record: dict) -> None:
    """未表示の合成画像だけHTTP503を一度注入し、明示的な再読込で復旧する。"""
    failed_work = "demo-0059"
    url_pattern = f"**/media/{failed_work}/0/view.png*"
    injected = {"count": 0}

    def serve_once(route):
        if injected["count"] == 0:
            injected["count"] += 1
            route.fulfill(status=503, content_type="text/plain",
                          body="synthetic image failure")
        else:
            route.continue_()

    card = root.locator(
        f'.pv-library-page-card[data-work-id="{failed_work}"][data-page-no="0"]')
    expect(card).to_have_count(1)
    page.route(url_pattern, serve_once)
    result["expecting_injected_image_failure"] = True
    try:
        card.click()
        viewer = page.get_by_test_id("library-viewer")
        expect(viewer).to_be_visible(timeout=15_000)
        expect(viewer.get_by_text("画像を読み込めませんでした")).to_be_visible(
            timeout=15_000)
        retry = viewer.get_by_role("button", name="画像を再読込")
        expect(retry).to_be_visible()
        check(injected["count"] == 1,
              f"合成HTTP503がちょうど一回注入されていない: {injected}")
    finally:
        page.unroute(url_pattern, serve_once)
        result["expecting_injected_image_failure"] = False
    retry.click()
    record["retry_decoded"] = image_ready(page, failed_work, 0)
    expect(viewer.get_by_text("画像を読み込めませんでした")).to_be_hidden()
    viewer.get_by_role("button", name="閉じる").click()
    expect(viewer).to_be_hidden()
    record["injected_503"] = {"work_id": failed_work, "page_no": 0,
                              "count": injected["count"], "retry": "decoded"}


def check_expansion(page: Page, root, record: dict) -> None:
    first = page_cards(root).first.element_handle()
    check(first is not None, "初回ページカードを取得できない")
    samples = (("demo-0060", 2), ("demo-0057", 0))
    for work_id, number in samples:
        card = root.locator(
            f'.pv-library-page-card[data-work-id="{work_id}"][data-page-no="{number}"]'
        )
        expect(card).to_have_count(1)
        decode_image(card.locator("img").first, "ページ一覧サムネイル",
                     f"/media/{work_id}/{number}/thumb.png")
    more = root.get_by_test_id("library-more")
    expect(more).to_be_visible()
    more.scroll_into_view_if_needed()
    scroll = root.get_by_test_id("library-pages-scroll")
    before = scroll.evaluate("(el) => el.scrollTop")
    more.click()
    page_count(root, DETAIL_PAGES)
    expect(more).to_be_hidden()
    after = scroll.evaluate("(el) => el.scrollTop")
    check(abs(after - before) <= 5,
          f"もっと見るで内部スクロール位置が飛んだ: {before} → {after}")
    check(page_cards(root).first.evaluate("(el, original) => el === original", first),
          "もっと見るで既存ページカードDOMが交換された")
    last = page_cards(root).last
    work_id = last.get_attribute("data-work-id")
    number = last.get_attribute("data-page-no")
    check(bool(work_id and number is not None), "末尾ページに作品/ページ番号がない")
    decode_image(last.locator("img").first, "追加末尾サムネイル",
                 f"/media/{work_id}/{number}/thumb.png")
    record["expanded"] = {"before": 50, "after": DETAIL_PAGES,
                          "same_first_card_node": True, "scroll_before": before,
                          "scroll_after": after, "last_work_id": work_id,
                          "last_page_no": int(number)}


def run_viewport(page: Page, url: str, size: tuple[int, int],
                 output: Path, result: dict) -> None:
    width, height = size
    key = f"{width}x{height}"
    record: dict = {"viewport": {"width": width, "height": height}}
    result["viewports"][key] = record
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url, wait_until="domcontentloaded", timeout=25_000)
    expect(page.get_by_test_id("tab-ranking")).to_have_attribute("aria-current", "page")
    page.get_by_test_id("tab-library").click()
    expect(page.get_by_test_id("tab-library")).to_have_attribute("aria-current", "page")
    root = library(page)
    author_count(root, 4, 4)
    check_size(page, root, "large")
    record["layout"] = check_layout(page)
    record["representative"] = author_card(root).get_attribute("data-representative-id")
    record["author_thumb_decoded"] = decode_image(
        author_card(root).locator("img").first,
        "作者代表画像", f"/media/{REPRESENTATIVE}/0/thumb.png",
    )
    page.screenshot(path=str(output / f"library-{key}-authors.png"), full_page=True)
    if size == VIEWPORTS[0]:
        record["sort"] = check_sort(root)

    # 作者検索は作者の一致判定のみ。代表作品や詳細の全ページ集合を変更しない。
    search_field(root).fill("0003")
    author_count(root, 1, 1)
    check(author_card(root).get_attribute("data-representative-id") == REPRESENTATIVE,
          "検索によって作者代表画像が変更された")
    open_author(root)
    record["search"] = {"query": "0003", "matching_authors": 1,
                        "representative_unchanged": True,
                        "detail_pages_unfiltered": DETAIL_PAGES}
    check_expansion(page, root, record)
    open_page_three(page, root, record)
    if size == VIEWPORTS[0]:
        check_failure_retry(page, root, result, record)
    page.screenshot(path=str(output / f"library-{key}-detail.png"), full_page=True)

    # 同一clientはカードサイズを共有し、タブ再訪では作者詳細と展開済みカードを維持。
    root.get_by_test_id("library-size-small").click()
    check_size(page, root, "small")
    first_page = page_cards(root).first.element_handle()
    check(first_page is not None, "タブ往復比較のページカードがない")
    page.get_by_test_id("tab-ranking").click()
    expect(root).to_be_hidden()
    expect(page.get_by_test_id("tab-ranking")).to_have_attribute("aria-current", "page")
    expect(page.get_by_test_id("gallery-size-small")).to_have_attribute(
        "aria-pressed", "true")
    expect(page.locator(".pv-gallery .pv-counter").first).to_have_text(
        "50 / 120 件表示")
    page.get_by_test_id("gallery-size-medium").click()
    check_size(page, root, "medium")
    page.get_by_test_id("tab-library").click()
    root = library(page)
    check_size(page, root, "medium")
    expect(search_field(root)).to_have_value("0003")
    page_count(root, DETAIL_PAGES)
    check(page_cards(root).first.evaluate(
        "(el, previous) => el === previous", first_page,
    ), "ライブラリのタブ再訪で詳細カードDOMが交換された")
    expect(page.get_by_test_id("library-viewer")).to_be_hidden()
    record["tab_roundtrip"] = {"size_library_to_ranking": "small",
                                "size_ranking_to_library": "medium",
                                "page_count_retained": DETAIL_PAGES,
                                "same_first_page_node": True}

    # ビューア表示中に別タブへ移れば閉じ、再訪しても勝手に開かない。
    target = root.locator(
        f'.pv-library-page-card[data-work-id="{REPRESENTATIVE}"][data-page-no="2"]')
    target.click()
    expect(page.get_by_test_id("library-viewer")).to_be_visible(timeout=15_000)
    image_ready(page, REPRESENTATIVE, 2)
    page.get_by_test_id("tab-ranking").click()
    expect(page.get_by_test_id("library-viewer")).to_be_hidden()
    page.get_by_test_id("tab-library").click()
    root = library(page)
    expect(page.get_by_test_id("library-viewer")).to_be_hidden()
    page_count(root, DETAIL_PAGES)
    record["viewer_closed_on_tab_departure"] = True

    root.get_by_test_id("library-back").click()
    expect(root.get_by_test_id("library-detail")).to_be_hidden()
    expect(root.get_by_test_id("library-authors-scroll")).to_be_visible()
    author_count(root, 1, 1)
    expect(search_field(root)).to_have_value("0003")
    opener = author_card(root).get_by_test_id("library-author-open")
    expect(opener).to_be_focused(timeout=15_000)
    open_author(root)
    page_count(root, 50)
    root.get_by_test_id("library-back").click()
    author_count(root, 1, 1)
    record["back_and_reopen"] = {"search_retained": True,
                                  "author_focus_restored": True,
                                  "reopened_page_count": 50}
    page.screenshot(path=str(output / f"library-{key}-final.png"), full_page=True)
    check_synthetic_bookmarks(page, root, output, key, record)
    check_tag_filters(page, root, output, key, record)
    check_metadata_filters(page, root, output, key, record)


def check_client_isolation(context, url: str, result: dict) -> None:
    first = context.new_page()
    second = context.new_page()
    try:
        for page in (first, second):
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            page.get_by_test_id("tab-library").click()
            author_count(library(page), 4, 4)
        a, b = library(first), library(second)
        search_field(a).fill("0003")
        author_count(a, 1, 1)
        author_count(b, 4, 4)
        open_author(a)
        a.get_by_test_id("library-more").click()
        page_count(a, DETAIL_PAGES)
        expect(b.get_by_test_id("library-detail")).to_be_hidden()
        a.get_by_test_id("library-size-small").click()
        check_size(first, a, "small")
        check_size(second, b, "large")
        b.get_by_test_id("library-size-medium").click()
        check_size(second, b, "medium")
        check_size(first, a, "small")
        search_field(b).fill("unmatched synthetic author")
        author_count(b, 0, 0)
        expect(b.get_by_test_id("library-empty")).to_have_text(
            "条件に合う作者がいません",
        )
        page_count(a, DETAIL_PAGES)
        expect(search_field(a)).to_have_value("0003")
        expect(search_field(b)).to_have_value("unmatched synthetic author")
        result["two_clients"] = {
            "first": {"query": "0003", "detail_pages": DETAIL_PAGES, "size": "small"},
            "second": {"query": "unmatched synthetic author",
                       "matched_authors": 0, "size": "medium"},
            "independent": True,
        }
        a.get_by_test_id("library-back").click()
        expect(a.get_by_test_id("library-authors-scroll")).to_be_visible()
        a.get_by_test_id("library-filter-toggle").click()
        field = a.get_by_test_id("library-filter-panel").get_by_placeholder("タグを追加")
        field.fill("縦長")
        field.press("Enter")
        expect(a.get_by_test_id("library-filter-now")).to_be_visible()
        expect(b.get_by_test_id("library-filter-now")).to_be_hidden()
        expect(b.get_by_test_id("library-filter-panel")).to_be_hidden()
        result["two_clients"]["filters_independent"] = True
    finally:
        first.close()
        second.close()



def _calendar_slider_panel(page: Page, url: str, size: tuple[int, int]):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(url, wait_until="domcontentloaded", timeout=25_000)
    page.get_by_test_id("tab-library").click()
    root = library(page)
    author_count(root, 4, 4)
    root.get_by_test_id("library-filter-toggle").click()
    panel = root.get_by_test_id("library-filter-panel")
    expect(panel).to_be_visible()
    return root, panel


def _calendar_day(dialog, number: int):
    # Select a displayed day button, not an injected model event or server call.
    button = dialog.get_by_test_id("library-date-picker").locator(
        ".q-date__calendar-item--in button").filter(
            has_text=re.compile(rf"^\s*{number}\s*$"))
    expect(button).to_have_count(1)
    expect(button).to_be_enabled()
    return button


def _open_calendar(page: Page, panel, field: str):
    panel.get_by_test_id(f"library-{field}-calendar").click()
    dialog = page.get_by_test_id("library-date-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_test_id("library-date-picker")).to_be_visible()
    return dialog


def _slider_value(panel, value: int) -> None:
    expect(panel.get_by_test_id("library-bookmark-slider")).to_have_attribute(
        "aria-valuenow", str(value))


def run_calendar_slider_viewport(page: Page, url: str, size: tuple[int, int],
                                 output: Path, result: dict) -> None:
    key = f"{size[0]}x{size[1]}"
    record = {"viewport": {"width": size[0], "height": size[1]}, "original_ids": ["L04", "L07"]}
    result["viewports"][key] = record
    root, panel = _calendar_slider_panel(page, url, size)
    start, end = (panel.get_by_test_id(f"library-date-{name}") for name in ("from", "to"))
    start.fill("2024-02-28")
    author_count(root, 0, 0)
    dialog = _open_calendar(page, panel, "date-from")
    page.screenshot(path=str(output / f"library-{key}-calendar.png"), full_page=True)
    _calendar_day(dialog, 29).click()
    expect(dialog).to_be_hidden()
    expect(start).to_have_value("2024-02-29")
    expect(panel.get_by_test_id("library-date-preset-custom")).to_have_attribute("aria-pressed", "true")
    end.fill("2024-03-01")
    expect(root.get_by_test_id("library-filter-now")).to_contain_text("2024-03-01")
    dialog = _open_calendar(page, panel, "date-to")
    _calendar_day(dialog, 2).click()
    expect(dialog).to_be_hidden()
    expect(end).to_have_value("2024-03-02")
    dialog = _open_calendar(page, panel, "date-to")
    dialog.get_by_test_id("library-date-cancel").click()
    expect(dialog).to_be_hidden()
    expect(end).to_have_value("2024-03-02")
    root.get_by_test_id("library-subview-bookmarks").click()
    expect(root.get_by_test_id("bookmark-count")).to_have_text("0 / 0 件表示")
    expect(start).to_have_value("2024-02-29")
    expect(end).to_have_value("2024-03-02")
    root.get_by_test_id("library-subview-downloaded").click()
    author_count(root, 0, 0)
    # Exercise native text/checkbox model events independently of the calendar
    # buttons. Their primitive values must survive shared subviews and a tab
    # roundtrip, including Number/Input's former envelope-preservation failure.
    manual_from, manual_to = "2024-02-20", "2024-03-03"
    start.fill(manual_from)
    expect(root.get_by_test_id("library-filter-now")).to_contain_text(manual_from)
    end.fill(manual_to)
    expect(root.get_by_test_id("library-filter-now")).to_contain_text(manual_to)
    expect(start).to_have_value(manual_from)
    expect(end).to_have_value(manual_to)
    expect(panel.get_by_test_id("library-date-preset-custom")).to_have_attribute("aria-pressed", "true")
    author_count(root, 0, 0)
    exclude_ai = panel.get_by_test_id("library-exclude-ai")
    expect(exclude_ai).to_have_attribute("aria-checked", "false")
    exclude_ai.click()
    expect(exclude_ai).to_have_attribute("aria-checked", "true")
    expect(root.get_by_test_id("library-filter-now")).to_contain_text("AI除外")
    root.get_by_test_id("library-subview-bookmarks").click()
    expect(root.get_by_test_id("bookmark-count")).to_have_text("0 / 0 件表示")
    expect(start).to_have_value(manual_from)
    expect(end).to_have_value(manual_to)
    expect(exclude_ai).to_have_attribute("aria-checked", "true")
    page.get_by_test_id("tab-ranking").click()
    expect(page.get_by_test_id("tab-ranking")).to_have_attribute("aria-current", "page")
    expect(root).to_be_hidden()
    page.get_by_test_id("tab-library").click()
    expect(page.get_by_test_id("tab-library")).to_have_attribute("aria-current", "page")
    expect(panel).to_be_visible()
    expect(start).to_have_value(manual_from)
    expect(end).to_have_value(manual_to)
    expect(exclude_ai).to_have_attribute("aria-checked", "true")
    expect(root.get_by_test_id("library-filter-now")).to_contain_text(manual_from)
    expect(root.get_by_test_id("library-filter-now")).to_contain_text(manual_to)
    expect(root.get_by_test_id("library-filter-now")).to_contain_text("AI除外")
    expect(root.get_by_test_id("bookmark-count")).to_have_text("0 / 0 件表示")
    record["manual_models"] = {
        "from": start.input_value(), "to": end.input_value(),
        "exclude_ai": exclude_ai.get_attribute("aria-checked"),
        "shared_subviews": True, "tab_return": True,
        "bookmark_count": root.get_by_test_id("bookmark-count").inner_text(),
    }
    page.screenshot(path=str(output / f"library-{key}-manual-models.png"), full_page=True)
    root.get_by_test_id("library-subview-downloaded").click()
    author_count(root, 0, 0)
    exclude_ai.click()
    expect(exclude_ai).to_have_attribute("aria-checked", "false")
    expect(root.get_by_test_id("library-filter-now")).not_to_contain_text("AI除外")
    record["manual_models"]["checkbox_restored"] = True
    for years in (1, 3):
        today = datetime.now().date()
        try:
            expected = today.replace(year=today.year - years).isoformat()
        except ValueError:
            expected = today.replace(year=today.year - years, day=28).isoformat()
        panel.get_by_test_id(f"library-date-preset-{years}").click()
        expect(start).to_have_value(expected)
        expect(end).to_have_value("")
    before_custom = start.input_value()
    panel.get_by_test_id("library-date-preset-custom").click()
    expect(start).to_have_value(before_custom)
    panel.get_by_test_id("library-date-preset-all").click()
    expect(start).to_have_value("")
    expect(end).to_have_value("")
    author_count(root, 4, 4)
    record["calendar"] = {"leap_day": "2024-02-29", "end": "2024-03-02",
                          "cancel_retained": True, "presets": [1, 3, "custom", "all"],
                          "shared_subviews": True}

    slider = panel.get_by_test_id("library-bookmark-slider")
    number = panel.get_by_test_id("library-bookmark-min")
    slider.scroll_into_view_if_needed()
    _slider_value(panel, 0)
    track = slider.locator(".q-slider__track-container")
    expect(track).to_have_count(1)
    box = track.bounding_box()
    check(box is not None and box["width"] > 0, "ブクマスライダーの実トラック矩形がない")
    generation = root.get_attribute("data-library-generation")
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 1, y)
    page.mouse.down()
    try:
        page.mouse.move(box["x"] + box["width"] / 2, y, steps=10)
        _slider_value(panel, 500)
        # Hold beyond the 50 ms model throttle; result application is release-only.
        page.wait_for_timeout(150)
        expect(number).to_have_value("0")
        author_count(root, 4, 4)
        check(root.get_attribute("data-library-generation") == generation,
              "ドラッグ中にライブラリの結果世代が変更された")
        record["drag_held"] = {"thumb": 500, "number": number.input_value(),
                               "generation": generation, "authors": 4, "hold_ms": 150}
    finally:
        page.mouse.up()
    expect(number).to_have_value("500")
    _slider_value(panel, 500)
    author_count(root, 0, 0)
    check(root.get_attribute("data-library-generation") != generation,
          "離した後にライブラリの結果が反映されていない")
    record["release"] = {"threshold": 500, "authors": 0,
                         "generation": root.get_attribute("data-library-generation")}

    for value in (0, 100, 500, 1000):
        panel.get_by_test_id(f"library-bookmark-preset-{value}").click()
        expect(number).to_have_value(str(value))
        _slider_value(panel, value)
        expect(panel.get_by_test_id(f"library-bookmark-preset-{value}")).to_have_attribute(
            "aria-pressed", "true")
        author_count(root, 4 if value == 0 else 0, 4 if value == 0 else 0)
    for value, thumb in ((137, 137), (1500, 1000)):
        number.fill(str(value))
        expect(panel.get_by_test_id("library-bookmark-value")).to_have_text(f"{value:,}+")
        expect(number).to_have_value(str(value))
        _slider_value(panel, thumb)
    page.get_by_test_id("tab-ranking").click()
    page.get_by_test_id("tab-library").click()
    expect(number).to_have_value("1500")
    _slider_value(panel, 1000)
    root.get_by_test_id("library-subview-bookmarks").click()
    expect(root.get_by_test_id("bookmark-count")).to_have_text("0 / 0 件表示")
    expect(number).to_have_value("1500")
    panel.get_by_test_id("library-bookmark-preset-0").click()
    expect(root.get_by_test_id("bookmark-count")).to_have_text("50 / 110 件表示")
    root.get_by_test_id("library-subview-downloaded").click()
    author_count(root, 4, 4)
    panel.get_by_test_id("library-filter-clear").click()
    expect(number).to_have_value("0")
    _slider_value(panel, 0)
    page.wait_for_timeout(150)
    _slider_value(panel, 0)
    record["slider"] = {"presets": [0, 100, 500, 1000],
                        "manual_thresholds": [137, 1500], "visible_thumbs": [137, 1000],
                        "tab_return": True, "shared_subviews": True, "clear": True}
    record["layout"] = check_layout(page)
    page.screenshot(path=str(output / f"library-{key}-calendar-slider.png"), full_page=True)


def check_calendar_slider_clients(context, url: str, result: dict) -> None:
    first, second = context.new_page(), context.new_page()
    try:
        a_root, a = _calendar_slider_panel(first, url, (1280, 720))
        b_root, b = _calendar_slider_panel(second, url, (1280, 720))
        a.get_by_test_id("library-date-from").fill("2024-02-28")
        b.get_by_test_id("library-date-to").fill("2024-03-01")
        a_dialog = _open_calendar(first, a, "date-from")
        b_dialog = _open_calendar(second, b, "date-to")
        _calendar_day(a_dialog, 29).click()
        expect(a.get_by_test_id("library-date-from")).to_have_value("2024-02-29")
        expect(b_dialog).to_be_visible()
        expect(b.get_by_test_id("library-date-to")).to_have_value("2024-03-01")
        a.get_by_test_id("library-bookmark-preset-500").click()
        expect(a.get_by_test_id("library-bookmark-min")).to_have_value("500")
        expect(b.get_by_test_id("library-bookmark-min")).to_have_value("0")
        _slider_value(a, 500)
        _slider_value(b, 0)
        b_dialog.get_by_test_id("library-date-cancel").click()
        expect(b_dialog).to_be_hidden()
        b.get_by_test_id("library-filter-clear").click()
        author_count(b_root, 4, 4)
        expect(a.get_by_test_id("library-date-from")).to_have_value("2024-02-29")
        expect(a.get_by_test_id("library-bookmark-min")).to_have_value("500")
        author_count(a_root, 0, 0)
        result["two_clients"] = {"calendar_pending_independent": True,
                                  "calendar_selection_independent": True,
                                  "slider_and_clear_independent": True}
    finally:
        first.close()
        second.close()

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8089")
    parser.add_argument("--output", type=Path, default=Path("artifacts/library-browser"))
    parser.add_argument("--channel", choices=("chromium", "msedge"), default="msedge")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--calendar-slider-only", action="store_true",
                        help="L04/L07: real calendar clicks and release-only slider gestures")
    args = parser.parse_args()
    if args.calendar_slider_only and args.output.exists() and any(args.output.iterdir()):
        parser.error("calendar/slider evidence requires an empty output directory")
    args.output.mkdir(parents=True, exist_ok=True)
    result = {
        "url": args.url, "channel": args.channel,
        "scope": "L04/L07 calendar-slider" if args.calendar_slider_only else "library",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "viewports": {}, "two_clients": None, "passed": [],
        "console_errors": [], "page_errors": [], "blocked_foreign_requests": [],
        "expected_injected_console_errors": [],
        "expecting_injected_image_failure": False,
        "success": False, "failure": None,
    }
    browser = context = page = None
    try:
        origin = urlsplit(args.url)
        check(origin.scheme == "http"
              and origin.hostname in {"127.0.0.1", "::1", "localhost"}
              and origin.port is not None and origin.path in {"", "/"}
              and not origin.username and not origin.password,
              "架空データのloopback URLのみ使用可能")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel=args.channel if args.channel != "chromium" else None,
                    headless=not args.headed,
                )
                context = browser.new_context(viewport={"width": 1280, "height": 720})

                def local_only(route):
                    requested = urlsplit(route.request.url)
                    if (requested.scheme, requested.hostname, requested.port) == (
                            origin.scheme, origin.hostname, origin.port):
                        route.continue_()
                    else:
                        result["blocked_foreign_requests"].append(
                            f"{requested.scheme}://{requested.hostname or '?'}"
                        )
                        route.abort()

                context.route("**/*", local_only)

                def capture_page(p):
                    def capture_console(message):
                        if message.type != "error":
                            return
                        location = message.location.get("url", "")
                        text = message.text
                        if (result["expecting_injected_image_failure"]
                                and "503" in text
                                and ("/media/demo-0059/0/view.png" in text
                                     or "/media/demo-0059/0/view.png" in location)):
                            result["expected_injected_console_errors"].append(text)
                        else:
                            result["console_errors"].append(text)

                    p.on("console", capture_console)
                    p.on("pageerror", lambda error: result["page_errors"].append(
                        str(error)))

                context.on("page", capture_page)
                if args.calendar_slider_only:
                    health = context.request.get(args.url.rstrip('/') + '/healthz',
                                                 timeout=5_000, max_redirects=0)
                    check(health.status == 200 and health.json() == {
                        'status': 'ok', 'mode': 'demo', 'data': 'synthetic'},
                        'L04/L07 browser acceptance requires the isolated synthetic app')
                page = context.new_page()
                for size in VIEWPORTS:
                    operation = run_calendar_slider_viewport if args.calendar_slider_only else run_viewport
                    operation(page, args.url, size, args.output, result)
                    result["passed"].append(f"library viewport {size[0]}x{size[1]}")
                clients = check_calendar_slider_clients if args.calendar_slider_only else check_client_isolation
                clients(context, args.url, result)
                result["passed"].append("two independent clients")
                check(not result["console_errors"] and not result["page_errors"],
                      "ブラウザconsole/pageエラー: "
                      f"{result['console_errors'][:3]}, {result['page_errors'][:3]}")
                check(not result["blocked_foreign_requests"],
                      "loopback以外への通信を検出")
                result["success"] = True
            finally:
                if page is not None:
                    try:
                        page.screenshot(path=str(args.output / "library-last.png"),
                                        full_page=True, timeout=5_000)
                    except Exception as exc:
                        result["screenshot_error"] = str(exc)
                        result["success"] = False
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    except Exception:
        result["success"] = False
        result["failure"] = traceback.format_exc()
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["passed_count"] = len(result["passed"])
        (args.output / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (args.output / "console-errors.txt").write_text(
            "\n".join(result["console_errors"] + result["page_errors"]), encoding="utf-8",
        )
    print(f"passed={result['passed_count']} console_errors={len(result['console_errors'])} "
          f"page_errors={len(result['page_errors'])}")
    if result["failure"]:
        print(result["failure"], file=sys.stderr)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
