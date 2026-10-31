"""P3-402 original Flet pagination and P3-403 NiceGUI evidence fail-closed."""
from __future__ import annotations

import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from tools import check_stage5_failed17_pagination_gui as check


def clone_original(tmp_path):
    target = tmp_path / 'p402-original-read-only-evidence'
    target.mkdir()
    for filename in check.FIXED_SHA:
        shutil.copy2(check.FLET_RUN / filename, target / filename)
    return target


def test_real_original_flet_paginated_ui_a_evidence_is_fixed():
    assert check.verify_original_pages() == check.shared.prior.fixture_fingerprint()


@pytest.mark.parametrize('filename,field,forge', [
    ('uia-page2-next.json', 'phase', 'page1-of-3-initial'),
    ('uia-page3-next.json', 'accessibility_tree', '3 / 3 only'),
    ('uia-page2-prev.json', 'accessibility_tree', '2 / 3 without rows'),
    ('actual-original-full-gui-ready.json', 'app_pid', -1),
])
def test_modified_real_original_state_cannot_be_used(tmp_path, filename, field, forge):
    root = clone_original(tmp_path)
    path = root / filename
    observation = json.loads(path.read_text(encoding='utf8'))
    observation[field] = forge
    path.write_text(json.dumps(observation), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        check.verify_original_pages(root)


def test_self_consistent_forged_original_hwnd_not_authoritative(tmp_path):
    root = clone_original(tmp_path)
    for file in root.glob('uia-*.json'):
        obs = json.loads(file.read_text(encoding='utf8'))
        obs['window']['id'] = 1234567
        file.write_text(json.dumps(obs), encoding='utf8')
    with pytest.raises(RuntimeError, match='original Flet'):
        check.verify_original_pages(root)


@pytest.mark.parametrize('page,first,last', [
    (1, 1, 8), (2, 9, 16), (3, 17, 17),
])
def test_hidden_correct_pagination_rows_must_fail(page, first, last):
    """Raw DOM row IDs and page label alone may not certify visible content."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            tab = browser.new_page()
            nodes = ''.join(
                f'<div data-testid="failed-record" data-work-id="failure-{i:04d}">'
                f'failure-{i:04d}</div>' for i in range(first, last+1))
            tab.set_content(f'''
                <section data-testid="failed-records-view">
                  <div data-testid="failed-count">17 / 17 件</div>
                  <div data-testid="failed-page">{page} / 3</div>
                  <div data-testid="failed-empty" hidden>一致する失敗はありません</div>
                  <div data-testid="failed-rows" hidden>{nodes}</div>
                </section>
            ''')
            with pytest.raises(AssertionError):
                check.visible_page(tab.get_by_test_id('failed-records-view'),
                                   page, tuple(range(first, last+1)))
        finally:
            browser.close()
