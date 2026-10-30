"""Negative controls for P3-366's two reviewer P2 blind spots.

Use only in-memory generated DOM; never launch the original Flet/product data.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from tools.check_stage5_k14_more_browser import (
    validate_card_ids, inspect_more_grid,
)


@pytest.mark.parametrize('n', [100, 120])
def test_middle_duplicate_and_missing_work_is_rejected_even_with_same_count_and_tail(n):
    expected = [str(i) for i in range(1, n + 1)]
    broken = expected.copy()
    broken[57] = broken[56]  # duplicate 57, omit 58; total and tail unchanged

    # The old P3-366 checks (count and last ID) could not see this corruption.
    assert len(broken) == len(expected)
    assert broken[-1] == expected[-1]
    with pytest.raises(AssertionError, match='card IDs'):
        validate_card_ids(broken, n)
    assert validate_card_ids(expected, n) == expected


def test_failed_append_more_cell_moved_outside_grid_is_rejected():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 700, 'height': 360})
            cards = ''.join(f'<div class="pv-card" data-work-id="{i}"></div>'
                            for i in range(1, 121))
            page.set_content(
                '<section data-testid="ranking-view">'
                f'<div class="pv-grid">{cards}'
                '<div class="pv-more-cell"><button>もっと見る</button></div>'
                '</div></section>')
            ranking = page.get_by_test_id('ranking-view')
            assert inspect_more_grid(ranking, expected_count=120)['direct_child_count'] == 1

            page.evaluate("""() => {
                const grid=document.querySelector('.pv-grid');
                grid.parentElement.appendChild(grid.querySelector('.pv-more-cell'));
            }""")
            # The old P3-366 returned an unconditional true, even after this.
            assert page.locator('.pv-more-cell button').is_enabled()
            assert page.locator('.pv-card').count() == 120
            with pytest.raises(AssertionError, match='more-cell'):
                inspect_more_grid(ranking, expected_count=120)
        finally:
            browser.close()


def test_more_cell_inside_grid_but_not_at_tail_is_rejected():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(
                '<section data-testid="ranking-view"><div class="pv-grid">'
                '<div class="pv-card" data-work-id="1"></div>'
                '<div class="pv-more-cell"><button>もっと見る</button></div>'
                '</div></section>')
            ranking = page.get_by_test_id('ranking-view')
            assert inspect_more_grid(ranking, expected_count=1)['last_element_is_more']
            page.evaluate("""() => {
                const grid=document.querySelector('.pv-grid');
                grid.prepend(grid.querySelector('.pv-more-cell'));
            }""")
            with pytest.raises(AssertionError, match='more-cell'):
                inspect_more_grid(ranking, expected_count=1)
        finally:
            browser.close()
