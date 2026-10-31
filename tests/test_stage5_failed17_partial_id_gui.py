"""P3-405 real original Flet generated partial-ID search, fail closed."""
from __future__ import annotations

import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from tools import check_stage5_failed17_partial_id_gui as check


def saved_copy(tmp_path):
    root = tmp_path / 'p405-original-evidence'
    root.mkdir()
    for name in check.FIXED_SHA:
        shutil.copy2(check.FLET_RUN / name, root / name)
    return root


def test_real_flet_partial_id_states_are_frozen():
    assert check.verify_original_partial_search() == check.shared.prior.fixture_fingerprint()


@pytest.mark.parametrize('file,field,value', [
    ('uia-partial-id0009.json', 'phase', 'partial-id0017'),
    ('uia-partial-id0017.json', 'accessibility_tree', '1件を再試行'),
    ('uia-partial-id0000-no-match.json', 'accessibility_tree', 'failure-0009'),
    ('actual-original-full-gui-ready.json', 'app_pid', 99999999),
])
def test_forged_flet_partial_search_state_rejected(tmp_path, file, field, value):
    root = saved_copy(tmp_path)
    path = root / file
    original = json.loads(path.read_text(encoding='utf8'))
    original[field] = value
    path.write_text(json.dumps(original), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        check.verify_original_partial_search(root)


def test_consistent_forged_original_window_rejected(tmp_path):
    root = saved_copy(tmp_path)
    for path in root.glob('uia-*.json'):
        data = json.loads(path.read_text(encoding='utf8'))
        data['window']['id'] = 11111111
        path.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        check.verify_original_partial_search(root)


def test_invisible_correct_partial_id_row_rejected():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <section data-testid="failed-records-view">
                  <input placeholder="タイトル・IDで検索" value="0009">
                  <button data-testid="failed-category-all"
                          aria-pressed="true">すべて 17</button>
                  <div data-testid="failed-count">1 / 17 件</div>
                  <div data-testid="failed-page">1 / 1</div>
                  <div data-testid="failed-rows" hidden>
                    <div data-testid="failed-record"
                         data-work-id="failure-0009">failure-0009</div>
                  </div>
                  <div data-testid="failed-empty" hidden>一致する失敗はありません</div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                check.expected_partial_row(
                    page.get_by_test_id('failed-records-view'), '0009', 'failure-0009')
        finally:
            browser.close()


def test_hidden_formula_title_inside_visible_record_must_fail():
    """P2 negative: child title hidden, but its parent row remains visible."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <section data-testid="failed-records-view">
                  <input placeholder="タイトル・IDで検索" value="0017">
                  <button data-testid="failed-category-all"
                          aria-pressed="true">すべて17</button>
                  <div data-testid="failed-count">1 / 17 件</div>
                  <div data-testid="failed-page">1 / 1</div>
                  <div data-testid="failed-rows">
                    <div data-testid="failed-record" data-work-id="failure-0017">
                      <span class="pv-failure-title" hidden>=SUM(1,2)（ID: failure-0017）</span>
                      <span class="pv-failure-meta">形式 ・ リトライ回数: 1</span>
                    </div>
                  </div>
                  <div data-testid="failed-empty" hidden>一致する失敗はありません</div>
                </section>
            ''')
            root = page.get_by_test_id('failed-records-view')
            check.expected_partial_row(root, '0017', 'failure-0017')
            with pytest.raises(AssertionError):
                check.assert_formula_title_visible(root)
        finally:
            browser.close()


def test_actual_visible_formula_title_exact_text_passes():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-rows">
                    <div data-testid="failed-record" data-work-id="failure-0017">
                      <span class="pv-failure-title">=SUM(1,2)（ID: failure-0017）</span>
                      <span class="pv-failure-meta">形式 ・ リトライ回数: 1</span>
                    </div>
                  </div>
                </section>
            ''')
            check.assert_formula_title_visible(page.get_by_test_id('failed-records-view'))
        finally:
            browser.close()
