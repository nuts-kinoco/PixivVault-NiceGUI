"""P3-408 genuine Flet true empty and P3-409 NiceGUI synthetic DB contract."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
from playwright.sync_api import sync_playwright

from tools import check_stage5_failed_empty_comparison_gui as check


def clone_original(tmp_path):
    root = tmp_path / 'p408-frozen-true-empty-flet'
    root.mkdir()
    for filename in check.PINNED:
        shutil.copy2(check.FLET_RUN / filename, root / filename)
    return root


def test_genuine_original_flet_two_true_empty_states_are_frozen():
    assert check.verify_true_original_empty() == check.EMPTY_FINGERPRINT


@pytest.mark.parametrize('filename,field,forged', [
    ('uia-true-empty.json', 'phase', 'filtered-no-match'),
    ('uia-empty-search.json', 'accessibility_tree', '一致する失敗はありません'),
    ('generated-failed-empty-source-guard.json', 'generated_failure_count', 17),
    ('actual-original-full-gui-ready.json', 'app_pid', 44444),
])
def test_original_empty_flet_forged_state_rejected(tmp_path, filename, field, forged):
    root = clone_original(tmp_path)
    path = root / filename
    data = json.loads(path.read_text(encoding='utf8'))
    data[field] = forged
    path.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        check.verify_true_original_empty(root)


def test_generated_owned_empty_fixture_is_real_zero_row_sqlite(tmp_path):
    from tools.failed_records_fixture_guard import verify_fixture_manifest
    from tools.check_stage5_failed17_other_categories_gui import generated_db_fingerprint
    db, images = check.build_true_empty_fixture(tmp_path / 'new-synthetic-only')
    assert db.is_file() and images.is_dir()
    assert verify_fixture_manifest(db, images)
    assert generated_db_fingerprint(db) == check.EMPTY_FINGERPRINT


def test_generated_zero_row_fixture_manifest_refuses_late_injected_failed_record(tmp_path):
    import sqlite3
    from tools.failed_records_fixture_guard import verify_fixture_manifest
    from tools.check_stage5_failed17_other_categories_gui import generated_db_fingerprint
    db, images = check.build_true_empty_fixture(tmp_path / 'owned-zero')
    with sqlite3.connect(db) as conn:
        conn.execute(
            'INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)',
            ('failure-9999', 'synthetic', 'forged', 'illust',
             'Connection: generated', '2026-01-01T00:00:00', 0),
        )
    assert generated_db_fingerprint(db) != check.EMPTY_FINGERPRINT
    with pytest.raises(ValueError, match='synthetic failure fixture changed'):
        verify_fixture_manifest(db, images)


@pytest.mark.parametrize('bad', ['no-match', 'hidden-empty', 'one-stale-row'])
def test_false_true_empty_dom_must_not_pass(bad):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            phrase = ('一致する失敗はありません' if bad == 'no-match'
                      else '失敗はありません\n失敗した取得は0件です')
            hidden = 'hidden' if bad == 'hidden-empty' else ''
            stale = ('<div data-testid="failed-record" data-work-id="failure-0009">'
                     'failure-0009</div>' if bad == 'one-stale-row' else '')
            page.set_content(f'''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-count">0 / 0 件</div>
                  <button data-testid="failed-category-all">すべて 0</button>
                  <div data-testid="failed-page">1 / 1</div>
                  <div data-testid="failed-rows">{stale}</div>
                  <div data-testid="failed-empty" {hidden}>{phrase}</div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                check.assert_true_empty_ui(page.get_by_test_id('failed-records-view'))
        finally:
            browser.close()


def test_genuine_true_empty_dom_passes():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-count">0 / 0 件</div>
                  <button data-testid="failed-category-all">すべて 0</button>
                  <div data-testid="failed-page">1 / 1</div>
                  <div data-testid="failed-rows"></div>
                  <div data-testid="failed-empty">失敗はありません
失敗した取得は0件です</div>
                </section>
            ''')
            check.assert_true_empty_ui(page.get_by_test_id('failed-records-view'))
        finally:
            browser.close()


def test_reload_requires_explicit_failed_tab_reselection():
    """Failure tab is initially unmounted after a new default-tab document."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <button data-testid="tab-failed" aria-current="false"
                        onclick="this.setAttribute('aria-current','page');
                          document.getElementById('failed').hidden=false">失敗リスト</button>
                <section id="failed" data-testid="failed-records-view" hidden>
                  <div data-testid="failed-count">0 / 0 件</div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                from playwright.sync_api import expect
                expect(page.get_by_test_id('failed-records-view')).to_be_visible(timeout=100)
            check.open_failed_after_document_reload(page)
        finally:
            browser.close()
