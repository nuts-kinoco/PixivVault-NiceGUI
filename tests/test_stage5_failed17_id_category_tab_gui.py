"""P3-398 preserved original Flet Q15 ID/category GUI evidence."""
from __future__ import annotations

import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from tools import check_stage5_failed17_id_category_tab_gui as audit


def copied_observations(tmp_path):
    out = tmp_path / 'p398-flet-evidence'
    out.mkdir()
    for name in audit.FIXED_SHA:
        shutil.copy2(audit.FLET_RUN / name, out / name)
    return out


def test_real_original_id_category_and_tab_return_pinned_to_generated_fixture():
    assert audit.verify_original_id_search() == audit.shared.prior.fixture_fingerprint()


@pytest.mark.parametrize('filename,field,forged', [
    ('uia-permission3.json', 'phase', 'all17-final'),
    ('uia-permission-id0009.json', 'accessibility_tree', '1件を再試行'),
    ('uia-permission-id0010-no-match.json', 'accessibility_tree', '0 rows'),
    ('uia-permission3-tab-return.json', 'phase', 'permission3-cleared'),
    ('actual-original-full-gui-ready.json', 'app_pid', -1),
])
def test_forged_original_gui_phase_or_owner_fails_closed(tmp_path, filename, field, forged):
    root = copied_observations(tmp_path)
    path = root / filename
    data = json.loads(path.read_text(encoding='utf8'))
    data[field] = forged
    path.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        audit.verify_original_id_search(root)


def test_consistent_fake_hwnd_and_added_rows_fails_closed(tmp_path):
    root = copied_observations(tmp_path)
    for path in root.glob('uia-*.json'):
        data = json.loads(path.read_text(encoding='utf8'))
        data['window']['id'] = 4444444
        data['accessibility_tree'] += '\n 9999: Text "failure-9999"'
        path.write_text(json.dumps(data), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        audit.verify_original_id_search(root)


def test_missing_original_gui_search_state_is_rejected(tmp_path):
    root = copied_observations(tmp_path)
    (root / 'uia-permission-id0009.json').unlink()
    with pytest.raises(RuntimeError, match='original Flet'):
        audit.verify_original_id_search(root)


def test_p399_hidden_expected_three_rows_must_be_rejected():
    """RED: a correct 3/17 count cannot make an entirely hidden list GREEN."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-count">3 / 17 件</div>
                  <div data-testid="failed-rows" hidden>
                    <div data-testid="failed-record" data-work-id="failure-0003">failure-0003</div>
                    <div data-testid="failed-record" data-work-id="failure-0009">failure-0009</div>
                    <div data-testid="failed-record" data-work-id="failure-0015">failure-0015</div>
                  </div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                audit.verify_rows(page.get_by_test_id('failed-records-view'),
                                  ('failure-0003', 'failure-0009', 'failure-0015'), 3)
        finally:
            browser.close()


def test_p399_all17_reset_with_eight_hidden_rows_must_be_rejected():
    """RED: the page1/3 and eight DOM nodes can be correct but invisible."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            rows = ''.join(
                f'<div data-testid="failed-record" data-work-id="failure-{i:04d}">'
                f'failure-{i:04d}</div>' for i in range(1, 9))
            page.set_content(f'''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-count">17 / 17 件</div>
                  <div data-testid="failed-page">1 / 3</div>
                  <button data-testid="failed-category-all" aria-pressed="true">すべて17</button>
                  <div data-testid="failed-rows" hidden>{rows}</div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                audit.verify_full_reset(page.get_by_test_id('failed-records-view'))
        finally:
            browser.close()
