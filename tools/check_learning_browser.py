"""Playwright実画面テスト。架空画像の学習ページ専用。

例: python tools/check_learning_browser.py --url http://127.0.0.1:8089
Playwrightとブラウザの導入、アプリの起動は呼び出し側が担当する。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright


CHOICES = ("見送る", "合わない", "いいね", "SuperLike")


def verify(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def learning_root(page: Page):
    # LearningView.root: ui.column(tabindex=0, data-active=true) 配下にラベルがある。
    return page.locator('[tabindex="0"][data-active="true"]').filter(
        has=page.get_by_text("学習（デモ）", exact=True)
    )


def get_progress(root) -> int:
    value = root.locator(".pv-counter").inner_text()
    return int(value.split("/", 1)[0].strip())


def ready(root) -> None:
    image = root.locator("img").first
    expect(image).to_be_visible(timeout=15_000)
    image.evaluate("(el) => el.decode()")
    verify(image.evaluate("(el) => el.complete && el.naturalWidth > 0"),
           "画像decodeに失敗")
    for label in CHOICES:
        expect(root.get_by_role("button", name=label, exact=True)).to_be_enabled(timeout=15_000)


def press_synthetic(root, *, key: str, repeat: bool = False,
                    composing: bool = False, target: str = "root") -> None:
    # DOMの通常キーイベントを発火させ、NiceGUI登録済みクライアントハンドラを通す。
    root.evaluate(
        """(el, p) => {
            let target = el;
            if (p.target !== 'root') {
                target = document.createElement(p.target === 'dialog' ? 'div' : 'input');
                if (p.target === 'dialog') target.setAttribute('role', 'dialog');
                el.append(target);
            }
            try {
                target.dispatchEvent(new KeyboardEvent('keydown', {
                    key: p.key, bubbles: true, cancelable: true,
                    repeat: p.repeat, isComposing: p.composing,
                }));
            } finally {
                if (target !== el) target.remove();
            }
        }""",
        {"key": key, "repeat": repeat, "composing": composing, "target": target},
    )


def assert_unchanged(page: Page, root, previous: int, label: str) -> None:
    # 350msはブラウザ→サーバ→DOM更新のイベント往復猶予。性能測定値ではない。
    page.wait_for_timeout(350)
    verify(get_progress(root) == previous, f"{label}: 判定件数が変化した")


def expect_progress(root, count: int) -> None:
    expect(root.locator(".pv-counter")).to_contain_text(f"{count} /", timeout=10_000)


def run_checks(page: Page, url: str, successes: list[str]) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    page.get_by_test_id("tab-learning").click()
    root = learning_root(page)
    expect(root).to_be_visible()
    ready(root)
    for label in CHOICES:
        button = root.get_by_role('button', name=label, exact=True)
        expect(button.locator('.q-btn__content')).to_have_css('color', 'rgb(232, 232, 239)')
    successes.append("学習タブ表示・画像decode・判定有効")

    for i, label in enumerate(CHOICES, start=1):
        old_id = root.get_attribute("data-work-id")
        old_generation = root.get_attribute("data-generation")
        verify(bool(old_id and old_generation), "作品ID/世代がDOMにない")
        root.get_by_role("button", name=label, exact=True).click()
        expect_progress(root, i)
        ready(root)
        successes.append(f"判定 {label}")
        if i == 1:
            # 新しい作品のボタンに古いID/世代を一時的に付けて通常クリック。
            # サーバーのstateは一切直接操作しない。
            button = root.get_by_role("button", name="見送る", exact=True)
            button.evaluate(
                """(el, previous) => {
                    el.setAttribute('data-work-id', previous.id);
                    el.setAttribute('data-generation', previous.generation);
                    el.click();
                    queueMicrotask(() => {
                        el.setAttribute('data-work-id', previous.currentId);
                        el.setAttribute('data-generation', previous.currentGeneration);
                    });
                }""",
                {"id": old_id, "generation": old_generation,
                 "currentId": button.get_attribute("data-work-id"),
                 "currentGeneration": button.get_attribute("data-generation")},
            )
            assert_unchanged(page, root, i, "旧世代クリック")
            successes.append("旧work_id/generationクリックを拒否")

    root.get_by_role("button", name="ひとつ戻す（Undo）").click()
    expect_progress(root, 3)
    ready(root)
    successes.append("Undoで前作品へ復帰・再読込")

    previous = get_progress(root)
    for kwargs, label in (
        ({"key": "1", "repeat": True}, "キーrepeat"),
        ({"key": "2", "target": "input"}, "入力欄"),
        ({"key": "3", "composing": True}, "IME変換中"),
        ({"key": "4", "target": "dialog"}, "ダイアログ"),
    ):
        press_synthetic(root, **kwargs)
        assert_unchanged(page, root, previous, label)
        successes.append(f"{label}キー抑止")

    # 通常キーの肯定ケースを持ち、抑止試験の見逃しを防ぐ。
    root.focus()
    page.keyboard.press("1")
    expect_progress(root, previous + 1)
    ready(root)
    successes.append("通常ショートカット判定")

    page.get_by_test_id("tab-ranking").click()
    expect(root).to_be_hidden()
    # inactiveな旧DOMでキーイベントを発火させてもサーバに届かない。
    # data-active=falseになった学習root自体はDOMに保持される。
    inactive = page.locator('[tabindex="0"][data-active="false"]').filter(
        has=page.get_by_text("学習（デモ）", exact=True)
    )
    press_synthetic(inactive, key="1")
    page.wait_for_timeout(350)
    page.get_by_test_id("tab-learning").click()
    expect_progress(learning_root(page), previous + 1)
    successes.append("非active時キー抑止")


def verify_viewports(page, output: Path, result: dict) -> None:
    result["viewport_checks"] = {}
    for width, height in ((1280, 720), (1920, 1080)):
        page.set_viewport_size({"width": width, "height": height})
        root = learning_root(page)
        root.evaluate("el => {el.scrollTop = 0}")
        for name in ("見送る", "合わない", "いいね", "SuperLike", "ひとつ戻す（Undo）"):
            expect(root.get_by_role("button", name=name, exact=True)).to_be_in_viewport(ratio=1)
        metrics = root.evaluate("""el => {
            const image = el.querySelector('.pv-view-art img');
            const r = image.getBoundingClientRect();
            return {width:innerWidth,height:innerHeight,docHeight:document.documentElement.scrollHeight,
                imageWidth:r.width,imageHeight:r.height,naturalWidth:image.naturalWidth,naturalHeight:image.naturalHeight};
        }""")
        assert metrics["docHeight"] <= height + 1, metrics
        assert metrics["imageHeight"] > 0 and metrics["imageWidth"] > 0, metrics
        assert abs(metrics["imageWidth"] / metrics["imageHeight"]
                   - metrics["naturalWidth"] / metrics["naturalHeight"]) < 0.02, metrics
        result["viewport_checks"][f"{width}x{height}"] = metrics
        page.screenshot(path=str(output / f"learning-{width}x{height}.png"), full_page=False)
        result["passed"].append(f"{width}x{height} 判定・Undoの全体表示と画像比率")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8089")
    parser.add_argument("--output", type=Path, default=Path("artifacts/learning-browser"))
    parser.add_argument("--channel", default="msedge", choices=("msedge", "chromium"))
    parser.add_argument("--headed", action="store_true", help="可視ブラウザで実行")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "url": args.url, "channel": args.channel,
        "browser_version": None, "viewport": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": [], "console_errors": [], "page_errors": [],
        "success": False, "failure": None,
    }
    browser = context = page = None
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel=args.channel if args.channel != "chromium" else None,
                    headless=not args.headed,
                )
                result["browser_version"] = browser.version
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                result["viewport"] = page.viewport_size
                page.on("console", lambda msg: result["console_errors"].append(msg.text)
                        if msg.type == "error" else None)
                page.on("pageerror", lambda exc: result["page_errors"].append(str(exc)))
                run_checks(page, args.url, result["passed"])
                verify_viewports(page, args.output, result)
                result["success"] = not result["console_errors"] and not result["page_errors"]
            finally:
                if page is not None:
                    try:
                        page.screenshot(path=str(args.output / "learning-final.png"),
                                        full_page=True, timeout=5_000)
                    except Exception as exc:
                        result["success"] = False
                        result["screenshot_error"] = str(exc)
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
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / "console-errors.txt").write_text(
            "\n".join(result["console_errors"] + result["page_errors"]),
            encoding="utf-8",
        )
    print(f"checks passed: {result['passed_count']} | console errors: "
          f"{len(result['console_errors'])} | page errors: {len(result['page_errors'])}")
    if result["failure"]:
        print(result["failure"], file=sys.stderr)
    return 0 if (result["success"] and not result["console_errors"]
                 and not result["page_errors"]) else 1


if __name__ == "__main__":
    sys.exit(main())
