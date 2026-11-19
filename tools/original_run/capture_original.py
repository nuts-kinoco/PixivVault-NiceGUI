"""Playwright navigation for the original Flet app's accessibility tree."""
from __future__ import annotations

import argparse
from pathlib import Path

TABS = {
    "download": "ダウンロード", "queue": "キュー", "failed": "失敗リスト",
    "dedup": "重複整理", "learning": "学習", "ranking": "ランキング",
    "library": "ライブラリ", "recommend": "おすすめ",
}
BUTTON = "flt-semantics[role=button]"
OUTLINE_JS = """() => [...document.querySelectorAll('flt-semantics')].map(e => {
  const r = e.getBoundingClientRect();
  const label = e.getAttribute('aria-label') || e.textContent || '';
  return {role:e.getAttribute('role'), text:label.replace(/\\s+/g, ' ').trim().slice(0, 250),
          rect:[r.x,r.y,r.width,r.height].map(Math.round)};
})"""


def enable_semantics(page):
    page.locator("flt-semantics-placeholder").first.evaluate("e => e.click()", timeout=30000)
    page.locator(BUTTON).get_by_text(TABS["library"], exact=True).first.wait_for(timeout=30000)


def text_control(page, label):
    return page.locator("flt-semantics").get_by_text(label, exact=True).last


def click_label(page, label):
    text_control(page, label).click(timeout=5000)


def click_header(page, label):
    # Flet exposes the tooltip in a sibling semantics node laid over its button.
    rows = page.evaluate(OUTLINE_JS)
    box = next((row["rect"] for row in rows if row["text"] == label and 0 < row["rect"][2] <= 48), None)
    if not box:
        raise ValueError(f"header control has no bounds: {label}")
    x, y, width, height = box
    page.mouse.click(x + width / 2, y + height / 2)


def set_theme(page, theme):
    label = "ライトモードへ切り替え" if theme == "light" else "ダークモードへ切り替え"
    if any(row["text"] == label for row in page.evaluate(OUTLINE_JS)):
        click_header(page, label)
        page.wait_for_timeout(400)
    inverse = "ダークモードへ切り替え" if theme == "light" else "ライトモードへ切り替え"
    if not any(row["text"] == inverse for row in page.evaluate(OUTLINE_JS)):
        raise ValueError(f"original theme not confirmed: {theme}")


def open_screen(page, key):
    tab, _, subview = key.partition("/")
    if tab in TABS:
        page.locator(BUTTON).get_by_text(TABS[tab], exact=True).first.click(timeout=5000)
    elif tab == "history":
        click_header(page, "直近のバックアップ")
    elif tab == "settings":
        click_header(page, "設定")
        page.locator("flt-semantics[role=alertdialog]").first.wait_for(timeout=5000)
    else:
        raise ValueError(f"unknown original screen: {key}")
    if subview:
        click_label(page, subview)


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:28550/")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for width, height in ((1280, 800), (1920, 1080)):
            for theme in ("dark", "light"):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(args.url)
                enable_semantics(page)
                set_theme(page, theme)
                for key in (*TABS, "history", "settings"):
                    open_screen(page, key)
                    page.wait_for_timeout(350)
                    stem = args.out / f"{width}x{height}_{theme}_{'learn' if key == 'learning' else key}"
                    outline = page.evaluate(OUTLINE_JS)
                    page.screenshot(path=stem.with_suffix(".png"))
                    stem.with_suffix(".txt").write_text(
                        "\n".join("|".join(map(str, (e["role"] or "", e["text"], *e["rect"])))
                                  for e in outline), encoding="utf-8")
                page.close()
        browser.close()


if __name__ == "__main__":
    main()
