"""P3-399: generated Q15 ID search AND category, clear and tab roundtrip.

Reuses the P3-396 actual NiceGUI lifecycle/browser harness without repeating
its five-category test. P3-398 is the separate real original Flet observation.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect
from tools import check_stage5_failed17_other_categories_gui as shared
from tools import check_stage5_original_failed17_native as original

FLET_RUN = ROOT / 'artifacts/p3-398-real-flet-failed17-id-tab-r1'
OBSERVED_HWND = 56952522
OBSERVED_PID = 122960
FIXED_SHA = {
    'actual-original-full-gui-ready.json':
        '8531c20e4e425c65835d8ab574fe6051b3c77aa094dca6584c5ac243f66dd5e0',
    'generated-failed-17-source-guard.json':
        'a33b1bc7e023f111738bed0661bf6c39a3484432d3028d1a74408a0b478cbcb6',
    'uia-initial17.json':
        '8742b079e270f7d1e4ff836d6627b83cd c697dfec097565429ab0b5e384e26ba'.replace(' ', ''),
    'uia-permission3.json':
        'ed0126509f9f50627a36a97435dae506ccf4dd997e30134a357f3386eae9581e',
    'uia-permission-id0009.json':
        'e47af034d37a8115c389df2350342860b6864bbd896def6b0e79f217fc4bc054',
    'uia-permission-id0010-no-match.json':
        '7779d18c4ec7486851b32855f8486c2d9d3f80f9212a8a6b482b2e03972347bb',
    'uia-permission3-cleared.json':
        '7f8180e1bcf506db e865396065e1c3c11bd69f9c89c765e395c9023ec30e49bf'.replace(' ', ''),
    'uia-permission3-tab-return.json':
        'e10f65c701428face530bbb99cf927dd8a82958b1ea7503ea885ec13ea4b5b4f',
    'uia-all17-final.json':
        'b4a8c0198029efb96d1181ff6175b0ecdc9e48be8970323daab90ab1c9500a5b',
}
STAGES = (
    ('initial17', ('17件を再試行', 'failure-0001', 'failure-0007'),
     ()),
    ('permission3', ('3件を再試行', 'failure-0003', 'failure-0009', 'failure-0015'),
     ('failure-0010',)),
    ('permission-id0009', ('1件を再試行', 'failure-0009'),
     ('failure-0003', 'failure-0015', 'failure-0010')),
    ('permission-id0010-no-match',
     ('再試行できるものがありません', '一致する失敗はありません', '検索を消す'),
     ('failure-0009', 'failure-0010')),
    ('permission3-cleared', ('3件を再試行', 'failure-0003', 'failure-0009', 'failure-0015'),
     ('failure-0010',)),
    ('permission3-tab-return', ('3件を再試行', 'failure-0003', 'failure-0009', 'failure-0015'),
     ('failure-0010',)),
    ('all17-final', ('17件を再試行', 'failure-0001', 'failure-0007'),
     ()),
)


def verify_original_id_search(root=None):
    """Require exact P3-398 original GUI UIA bytes and saved source guard.

    The queue intermediate UIA was observed in the live Desktop session but
    was not saved; tab roundtrip is therefore limited evidence, not an
    independently signed complete event chain.
    """
    prior_sha = shared.verify_original_categories()
    if len(original.read_pinned_source()) != 33:
        raise RuntimeError('original Flet source changed')
    root = Path(root) if root is not None else FLET_RUN
    try:
        records = {}
        for filename, pinned in FIXED_SHA.items():
            path = root / filename
            meta = path.lstat()
            if (not stat.S_ISREG(meta.st_mode) or path.is_symlink()
                    or not 0 < meta.st_size <= 1024 * 1024):
                raise RuntimeError('original Flet ID UIA unsafe file')
            contents = path.read_bytes()
            if sha256(contents).hexdigest() != pinned:
                raise RuntimeError('original Flet ID UIA SHA mismatch')
            records[filename] = json.loads(contents)
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('original Flet ID UIA file unavailable') from exc
    ready = records['actual-original-full-gui-ready.json']
    guard = records['generated-failed-17-source-guard.json']
    if (ready.get('app_pid') != OBSERVED_PID
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0
            or guard.get('native_gui_main_returned') is not True
            or guard.get('source_unchanged') is not True
            or guard.get('failure') is not None
            or guard.get('generated_failure_count') != 17
            or guard.get('source_count') != 33
            or guard.get('fixture_sha256') != prior_sha):
        raise RuntimeError('original Flet ID search protected owner or fixture mismatch')
    previous = -1
    for phase, required, absent in STAGES:
        state = records['uia-' + phase + '.json']
        window = state.get('window', {})
        frame = state.get('frame', {})
        tree = state.get('accessibility_tree')
        try:
            index = int(frame['id'].removeprefix('frame-'))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('original Flet ID search frame invalid') from exc
        if (state.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or state.get('phase') != phase
                or window.get('id') != OBSERVED_HWND
                or window.get('title') != 'PixivVault'
                or window.get('state') not in ('open', 'foreground')
                or index <= previous
                or not isinstance(tree, str)
                or any(word not in tree for word in required)
                or any(word in tree for word in absent)):
            raise RuntimeError('original Flet ID search action UIA mismatch')
        previous = index
    return prior_sha


def verify_rows(panel, expected, count):
    expect(panel.get_by_test_id('failed-count')).to_have_text(f'{count} / 17 件')
    expect(panel.get_by_test_id('failed-record')).to_have_count(count if count < 8 else 8)
    text_rows = panel.get_by_test_id('failed-rows')
    for work_id in expected:
        row = panel.locator(
            f'[data-testid="failed-record"][data-work-id="{work_id}"]')
        expect(row).to_have_count(1)
        expect(row).to_be_visible()
    for index in range(1, 18):
        work_id = f'failure-{index:04d}'
        if work_id not in expected:
            expect(text_rows).not_to_contain_text(work_id)


def verify_full_reset(panel):
    """Initial Flet 8-per-page contract, not merely the 17/17 count label."""
    expect(panel.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
    expect(panel.get_by_test_id('failed-page')).to_have_text('1 / 3')
    expect(panel.get_by_test_id('failed-record')).to_have_count(8)
    expect(panel.get_by_test_id('failed-category-all')).to_have_attribute(
        'aria-pressed', 'true')
    for index in range(1, 9):
        work_id = f'failure-{index:04d}'
        row = panel.locator(
            f'[data-testid="failed-record"][data-work-id="{work_id}"]')
        expect(row).to_have_count(1)
        expect(row).to_be_visible()


def id_category_tab_steps(page, panel, report, out):
    cat = panel.get_by_test_id('failed-category-permission_age')
    cat.click()
    expect(cat).to_have_attribute('aria-pressed', 'true')
    verify_rows(panel, ('failure-0003', 'failure-0009', 'failure-0015'), 3)
    report['steps'].append('permission-age category filters to 3')
    search = panel.get_by_placeholder('タイトル・IDで検索')
    search.fill('failure-0009')
    expect(search).to_have_value('failure-0009')
    verify_rows(panel, ('failure-0009',), 1)
    report['steps'].append('ID substring 0009 AND permission category yields 1')
    search.fill('failure-0010')
    expect(search).to_have_value('failure-0010')
    verify_rows(panel, (), 0)
    expect(panel.get_by_test_id('failed-empty')).to_be_visible()
    expect(panel.get_by_test_id('failed-empty')).to_have_text('一致する失敗はありません')
    expect(cat).to_have_attribute('aria-pressed', 'true')
    report['steps'].append('ID 0010 exists outside category and yields zero')
    page.screenshot(path=str(out / 'category-id0010-empty-cdp.png'))
    report['screenshots']['zero'] = shared.prior.sha(out / 'category-id0010-empty-cdp.png')
    panel.get_by_test_id('failed-clear-search').click()
    expect(search).to_have_value('')
    verify_rows(panel, ('failure-0003', 'failure-0009', 'failure-0015'), 3)
    expect(cat).to_have_attribute('aria-pressed', 'true')
    report['steps'].append('clear ID search retains category 3')
    page.get_by_test_id('tab-queue').click()
    expect(page.get_by_test_id('tab-queue')).to_have_attribute('aria-current', 'page')
    page.get_by_test_id('tab-failed').click()
    expect(page.get_by_test_id('tab-failed')).to_have_attribute('aria-current', 'page')
    expect(panel).to_be_visible()
    verify_rows(panel, ('failure-0003', 'failure-0009', 'failure-0015'), 3)
    expect(cat).to_have_attribute('aria-pressed', 'true')
    report['steps'].append('queue tab roundtrip retains category three rows')
    page.screenshot(path=str(out / 'category-tab-return-cdp.png'))
    report['screenshots']['tab_return'] = shared.prior.sha(out / 'category-tab-return-cdp.png')
    panel.get_by_test_id('failed-category-all').click()
    verify_full_reset(panel)
    expect(cat).to_have_attribute('aria-pressed', 'false')
    report['steps'].append('reset to all 17 with first of 3 NiceGUI pages')


def run(out):
    return shared.run(
        out, original_verifier=verify_original_id_search,
        ui_steps=id_category_tab_steps,
        scope='P3-399 Q15 real original Flet ID/category AND + tab roundtrip, generated NiceGUI',
        extra_watched=(Path(__file__),), expected_steps=7)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed17_id_category_tab_gui.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
