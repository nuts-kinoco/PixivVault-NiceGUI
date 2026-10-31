"""Real original Flet Q14/Q15 UIA sequence is required before NiceGUI GUI."""
from __future__ import annotations

import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from tools import check_stage5_failed17_filter_and_search_gui as compare


FILES = (
    'actual-original-full-gui-ready.json',
    'generated-failed-17-source-guard.json',
    'uia-communication3.json',
    'uia-communication-title007.json',
    'uia-communication-title002-nomatch.json',
    'uia-communication-restored3.json',
    'uia-all-restored17.json',
)


def private_copy(tmp_path):
    root = tmp_path / 'generated-flet-evidence'
    root.mkdir()
    for file in FILES:
        shutil.copy2(compare.FLET_FILTER_RUN / file, root / file)
    return root


def test_real_original_q14_q15_ui_a_refers_to_identical_generated_fixture():
    assert compare.verify_flet_filter_actions() == compare.fixture_fingerprint()


@pytest.mark.parametrize("file,field,value", [
    ('uia-communication3.json', 'phase', 'all17_restored'),
    ('uia-communication-title007.json', 'accessibility_tree', '1件を再試行'),
    ('uia-communication-title002-nomatch.json', 'accessibility_tree', 'fake no-match'),
    ('uia-communication-restored3.json', 'phase', 'communication3'),
    ('uia-all-restored17.json', 'accessibility_tree', '3件を再試行'),
])
def test_missing_actual_original_flet_state_is_rejected(tmp_path, file, field, value):
    root = private_copy(tmp_path)
    path = root / file
    state = json.loads(path.read_text(encoding='utf8'))
    state[field] = value
    path.write_text(json.dumps(state), encoding='utf8')
    with pytest.raises(RuntimeError):
        compare.verify_flet_filter_actions(root)


def test_duplicate_or_reordered_original_ui_a_frame_is_rejected(tmp_path):
    root = private_copy(tmp_path)
    early = json.loads((root / 'uia-communication3.json').read_text(encoding='utf8'))
    late_path = root / 'uia-all-restored17.json'
    late = json.loads(late_path.read_text(encoding='utf8'))
    late['frame']['id'] = early['frame']['id']
    late_path.write_text(json.dumps(late), encoding='utf8')
    with pytest.raises(RuntimeError):
        compare.verify_flet_filter_actions(root)


def test_self_consistent_forged_owner_and_uia_text_must_not_pass(tmp_path):
    """P2 RED: self-consistent file fields are not original-owner evidence."""
    root = private_copy(tmp_path)
    for filename in FILES:
        if not filename.startswith('uia-'):
            continue
        path = root / filename
        state = json.loads(path.read_text(encoding='utf8'))
        state['window']['id'] = 44444444
        state['accessibility_tree'] += '\n\t9999: Text "forged original display"'
        path.write_text(json.dumps(state), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet.*evidence'):
        compare.verify_flet_filter_actions(root)


def test_hidden_nomatch_label_with_live_record_must_not_pass():
    """P2 RED: the hidden empty label still has text with a visible stale row."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
              <section data-testid="failed-records-view">
                <input placeholder="タイトル・IDで検索" value="架空の失敗作品 002">
                <button data-testid="failed-category-communication"
                        aria-pressed="true">通信 3</button>
                <div data-testid="failed-count">1 / 17 件</div>
                <div data-testid="failed-rows">
                  <div data-testid="failed-record">failure-0007</div>
                </div>
                <div data-testid="failed-empty" hidden>一致する失敗はありません</div>
              </section>
            ''')
            root = page.get_by_test_id('failed-records-view')
            search = root.get_by_placeholder('タイトル・IDで検索')
            with pytest.raises(AssertionError):
                compare.assert_no_match_ui(root, search)
        finally:
            browser.close()


def test_genuine_visible_zero_rows_and_preserved_category_pass():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('''
              <section data-testid="failed-records-view">
                <input placeholder="タイトル・IDで検索" value="架空の失敗作品 002">
                <button data-testid="failed-category-communication"
                        aria-pressed="true">通信 3</button>
                <div data-testid="failed-count">0 / 17 件</div>
                <div data-testid="failed-rows"></div>
                <div data-testid="failed-empty">一致する失敗はありません</div>
              </section>
            ''')
            root = page.get_by_test_id('failed-records-view')
            compare.assert_no_match_ui(root, root.get_by_placeholder('タイトル・IDで検索'))
        finally:
            browser.close()


def test_real_p393_r2_server_lifecycle_matches_fixture_and_listener():
    root = compare.ROOT / 'artifacts/p3-393-nicegui-failed17-filters-r2'
    report = json.loads((root / 'result.json').read_text(encoding='utf8'))
    receipt = root / 'lifecycle/server-lifecycle.json'
    assert compare.verify_failure_server_lifecycle(
        receipt, listener_pid=report['app']['listener_pid'],
        db_sha=json.loads(receipt.read_text(encoding='utf8'))['original_db_sha256'])


@pytest.mark.parametrize('field,forged', [
    ('pid', 789),
    ('services_closed', False),
    ('shutdown_requested', False),
    ('external_denied', 1),
    ('original_db_sha256', 'f' * 64),
])
def test_fake_success_with_bad_server_lifecycle_rejected(tmp_path, field, forged):
    receipt = tmp_path / 'server-lifecycle.json'
    original = {'pid': 123, 'services_closed': True,
                'shutdown_requested': True, 'external_denied': 0,
                'original_db_sha256': 'a' * 64}
    receipt.write_text(json.dumps({**original, field: forged}), encoding='utf8')
    assert compare.verify_failure_server_lifecycle(
        receipt, listener_pid=123, db_sha='a' * 64) is False


def test_missing_server_lifecycle_rejected(tmp_path):
    assert compare.verify_failure_server_lifecycle(
        tmp_path / 'missing.json', listener_pid=123, db_sha='a' * 64) is False
