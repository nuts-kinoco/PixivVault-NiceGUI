"""P3-406: original Flet and current NiceGUI ID substring search comparison.

Generated 17 records only. P3-405 original real gui.py raw UIA must be
byte-bound before NiceGUI is started. Formal original feature PASS stays zero.
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
from tools import check_stage5_failed17_pagination_gui as pagination
from tools import check_stage5_failed17_other_categories_gui as shared
from tools import check_stage5_original_failed17_native as original

FLET_RUN = ROOT / 'artifacts/p3-405-real-flet-failed17-partial-id-r1'
OWNER_HWND = 43909378
OWNER_PID = 133152
FIXED_SHA = {
    'actual-original-full-gui-ready.json':
        '4e7f86e7d5f7b19e090dadd9f52d33b91807df0d2946c87f7164c21e1f63423f',
    'generated-failed-17-source-guard.json':
        'a33b1bc7e023f111738bed0661bf6c39a3484432d3028d1a74408a0b478cbcb6',
    'uia-initial17.json':
        '4eb23695b33885c80108e534442d84e23bc08da324cc6ab1b378a50ce7afa7b9',
    'uia-partial-id0009.json':
        '2714d57d9017724b854644b667536ed9c4466f198c534df1b9893c5b1bef5f60',
    'uia-partial-id0017.json':
        '0581526a88b515e500be85156ee7555e70f017192b72665334387e07506e54b6',
    'uia-partial-id0000-no-match.json':
        'ce4439d7c32c6ee48b0988f77a709ed7e7e08a8f820762e8fa271f77250196f1',
    'uia-search-cleared17.json':
        'c6f54aa4ff9ba61a24a0ca5cb03d1a65137ea535a3d5e4d0e9f10d46ae7c04f9',
}
PHASES = (
    ('uia-initial17.json', 'initial17',
     ('17件を再試行', 'failure-0001', 'failure-0008'), ('failure-0009',)),
    ('uia-partial-id0009.json', 'partial-id0009',
     ('1件を再試行', 'failure-0009'), ('failure-0001', 'failure-0017')),
    ('uia-partial-id0017.json', 'partial-id0017',
     ('1件を再試行', 'failure-0017', '=SUM(1,2)'), ('failure-0009',)),
    ('uia-partial-id0000-no-match.json', 'partial-id0000-no-match',
     ('再試行できるものがありません', '一致する失敗はありません', '検索を消す'),
     tuple(f'failure-{i:04d}' for i in range(1, 18))),
    ('uia-search-cleared17.json', 'search-cleared17',
     ('17件を再試行', 'failure-0001', 'failure-0008'), ('failure-0009',)),
)


def verify_original_partial_search(root=None):
    """Pin the P3-405 actual original gui.py result, not reconstructed UI."""
    expected_fixture = pagination.verify_original_pages()
    if len(original.read_pinned_source()) != 33:
        raise RuntimeError('original Flet source dependency boundary unavailable')
    root = Path(root) if root is not None else FLET_RUN
    try:
        records = {}
        for filename, digest in FIXED_SHA.items():
            file = root / filename
            meta = file.lstat()
            if (not stat.S_ISREG(meta.st_mode) or file.is_symlink()
                    or not 0 < meta.st_size <= 1024 * 1024):
                raise RuntimeError('original Flet partial-ID proof unsafe file')
            contents = file.read_bytes()
            if sha256(contents).hexdigest() != digest:
                raise RuntimeError('original Flet partial-ID evidence SHA mismatch')
            records[filename] = json.loads(contents)
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('original Flet partial-ID evidence missing') from exc
    ready = records['actual-original-full-gui-ready.json']
    guard = records['generated-failed-17-source-guard.json']
    if (ready.get('app_pid') != OWNER_PID
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0
            or guard.get('native_gui_main_returned') is not True
            or guard.get('source_unchanged') is not True
            or guard.get('failure') is not None
            or guard.get('generated_failure_count') != 17
            or guard.get('source_count') != 33
            or guard.get('fixture_sha256') != expected_fixture):
        raise RuntimeError('original Flet partial-ID source/fixture unavailable')
    frame_prior = -1
    for filename, phase, required, absent in PHASES:
        record = records[filename]
        window = record.get('window', {})
        frame = record.get('frame', {})
        tree = record.get('accessibility_tree')
        try:
            frame_index = int(frame['id'].removeprefix('frame-'))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('original Flet partial-ID frame invalid') from exc
        if (record.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or record.get('phase') != phase
                or window.get('id') != OWNER_HWND
                or window.get('title') != 'PixivVault'
                or window.get('state') not in ('open', 'foreground')
                or not isinstance(tree, str)
                or frame_index <= frame_prior
                or any(token not in tree for token in required)
                or any(token in tree for token in absent)):
            raise RuntimeError('original Flet partial-ID GUI semantics invalid')
        frame_prior = frame_index
    return expected_fixture


def expected_partial_row(panel, query, work_id):
    search = panel.get_by_placeholder('タイトル・IDで検索')
    expect(search).to_have_value(query)
    expect(panel.get_by_test_id('failed-category-all')).to_have_attribute(
        'aria-pressed', 'true')
    expect(panel.get_by_test_id('failed-count')).to_have_text('1 / 17 件')
    expect(panel.get_by_test_id('failed-page')).to_have_text('1 / 1')
    expect(panel.get_by_test_id('failed-record')).to_have_count(1)
    row = panel.get_by_test_id('failed-record')
    expect(row).to_have_attribute('data-work-id', work_id)
    expect(row).to_be_visible()
    expect(panel.get_by_test_id('failed-empty')).to_be_hidden()


def assert_formula_title_visible(panel):
    """Require the exact user-visible Flet title, not hidden row textContent."""
    title = panel.get_by_test_id('failed-record').locator('.pv-failure-title')
    expect(title).to_have_count(1)
    expect(title).to_be_visible()
    expect(title).to_have_text('=SUM(1,2)（ID: failure-0017）')


def search_steps(page, panel, report, out):
    pagination.visible_page(panel, 1, tuple(range(1, 9)))
    search = panel.get_by_placeholder('タイトル・IDで検索')
    search.fill('0009')
    expected_partial_row(panel, '0009', 'failure-0009')
    report['steps'].append('search partial work-id 0009 returns only record 0009')
    search.fill('0017')
    expected_partial_row(panel, '0017', 'failure-0017')
    assert_formula_title_visible(panel)
    report['steps'].append('search partial work-id 0017 returns formula-like title as text')
    page.screenshot(path=str(out / 'id0017-cdp.png'))
    report['screenshots']['id0017'] = shared.prior.sha(out / 'id0017-cdp.png')
    search.fill('0000')
    expect(search).to_have_value('0000')
    expect(panel.get_by_test_id('failed-count')).to_have_text('0 / 17 件')
    expect(panel.get_by_test_id('failed-record')).to_have_count(0)
    expect(panel.get_by_test_id('failed-empty')).to_be_visible()
    expect(panel.get_by_test_id('failed-empty')).to_have_text('一致する失敗はありません')
    expect(panel.get_by_test_id('failed-category-all')).to_have_attribute(
        'aria-pressed', 'true')
    report['steps'].append('nonexistent 0000 returns zero with visible clear path')
    panel.get_by_test_id('failed-clear-search').click()
    expect(search).to_have_value('')
    pagination.visible_page(panel, 1, tuple(range(1, 9)))
    report['steps'].append('clear partial ID restores all 17 with first eight visible')
    page.screenshot(path=str(out / 'cleared-all17-cdp.png'))
    report['screenshots']['cleared'] = shared.prior.sha(out / 'cleared-all17-cdp.png')


def run(out):
    return shared.run(
        out,
        original_verifier=verify_original_partial_search,
        ui_steps=search_steps,
        scope='P3-406 original Flet Q15 partial work-id and current NiceGUI generated UI',
        extra_watched=(Path(__file__), Path(pagination.__file__)),
        expected_steps=5,
    )


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed17_partial_id_gui.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
