"""Synthetic failed_queue WebView checks, using the pinned owner's guard."""
import json
import time

if __package__:
    from .native_failed_csv import fresh_destination, validate_saved_csv
else:
    from native_failed_csv import fresh_destination, validate_saved_csv


def inspect_failed_records(page, screenshot, guard):
    from playwright.sync_api import expect
    run = guard.run
    run(page.get_by_test_id("tab-failed").click)
    root = page.get_by_test_id("failed-records-view")
    run(expect(root).to_be_visible)
    run(expect(root.get_by_test_id("failed-count")).to_have_text, "17 / 17 件")
    run(expect(root.get_by_test_id("failed-record")).to_have_count, 8)
    run(expect(root.get_by_test_id("failed-error")).to_be_hidden)
    run(expect(root.get_by_test_id("failed-retry-all")).to_be_disabled)
    run(expect(root.get_by_test_id("failed-row-retry").first).to_be_disabled)
    run(expect(root.get_by_test_id("failed-row-delete").first).to_be_disabled)
    run(expect(root.get_by_test_id("failed-reason").first).to_contain_text,
        "二行目の診断は省略せず表示します。")
    run(root.get_by_test_id("failed-next").click)
    run(expect(root.get_by_test_id("failed-page")).to_have_text, "2 / 3")
    run(expect(root.get_by_test_id("failed-record").first).to_have_attribute,
        "data-work-id", "failure-0009")
    search = root.get_by_placeholder("タイトル・IDで検索")
    run(search.fill, "failure-0001")
    run(expect(root.get_by_test_id("failed-count")).to_have_text, "1 / 17 件")
    run(expect(root.get_by_test_id("failed-category-all")).to_have_text, "すべて 17")
    run(search.fill, "")
    run(expect(root.get_by_test_id("failed-count")).to_have_text, "17 / 17 件")
    run(root.get_by_test_id("failed-category-format").click)
    run(expect(root.get_by_test_id("failed-count")).to_have_text, "3 / 17 件")
    run(page.get_by_test_id("tab-ranking").click)
    run(page.get_by_test_id("tab-failed").click)
    run(expect(root.get_by_test_id("failed-category-format")).to_have_attribute, "aria-pressed", "true")
    run(root.get_by_test_id("failed-menu").click)
    run(expect(page.get_by_test_id("failed-clear-all")).to_be_disabled)
    run(expect(page.get_by_test_id("failed-export")).to_be_enabled)
    run(expect(page.get_by_test_id("failed-export")).to_have_text, "表示中の3件をCSV出力")
    run(page.keyboard.press, "Escape", with_timeout=False)
    revision = int(run(root.get_attribute, "data-read-revision"))
    run(root.get_by_test_id("failed-reload").click)
    run(expect(root).to_have_attribute, "data-read-revision", str(revision + 1))
    run(expect(root.get_by_test_id("failed-reload")).to_be_enabled)
    run(expect(root.get_by_test_id("failed-count")).to_have_text, "3 / 17 件")
    shot = screenshot.with_name(screenshot.stem + "-failed-records.png")
    run(page.screenshot, path=str(shot), full_page=False)
    destination = fresh_destination(screenshot.with_name("failed-records-export.csv"))
    run(root.get_by_test_id("failed-menu").click)
    run(page.get_by_test_id("failed-export").click)
    # The operator/desktop agent fills only this application's owned native
    # SaveFileDialog. No CDP download replacement or fixture-file synthesis.
    print(json.dumps({"synthetic_only": True, "native_csv_dialog_requested": True,
                      "destination": str(destination)}), flush=True)
    saved = None
    while saved is None:
        guard.check()
        if destination.exists() or destination.is_symlink():
            try:
                saved = validate_saved_csv(destination)
            except (OSError, ValueError):
                # A newly-created download can still be receiving its bytes.
                # Invalid/incomplete contents never pass; the deadline stays fixed.
                pass
        if saved is None:
            time.sleep(0.1)
    guard.check()
    return {"passed": True, "total_records": 17, "rows_per_page": 8,
            "search_category_and_tab_state": True, "full_reason": True,
            "reload_retains_filter": True, "csv_action_enabled": True,
            "refresh_completion_observed": True,
            "mutations_disabled": True, **saved,
            "csv_destination": str(destination), "screenshot": str(shot)}
