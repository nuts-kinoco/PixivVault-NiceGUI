"""P3-403: exact generated 17-row pagination in real NiceGUI vs real Flet UIA.

The real original Flet source was run separately at P3-402 and its fixed UIA
is required. This is limited functional evidence, not native GDI parity.
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
from tools import check_stage5_failed17_id_category_tab_gui as previous
from tools import check_stage5_failed17_other_categories_gui as shared
from tools import check_stage5_original_failed17_native as original

FLET_RUN = ROOT / 'artifacts/p3-402-real-flet-failed17-pagination-r1'
ORIGINAL_HWND = 48831320
ORIGINAL_PID = 129912
FIXED_SHA = {
    'actual-original-full-gui-ready.json':
        'd6b2ad68438136cdf4b17316d498616752cce54e2b0ea3dd0453f294412cee36',
    'generated-failed-17-source-guard.json':
        'a33b1bc7e023f111738bed0661bf6c39a3484432d3028d1a74408a0b478cbcb6',
    'uia-page1-initial.json':
        '0dbcc979ce206329b5d2226706f38bb18a2d824cf365620e346c6b801f0640c6',
    'uia-page2-next.json':
        '9c52695de3bea8ca765f7d707dd1ba83ebeaf489103778cdf904414baf5ff0dd',
    'uia-page3-next.json':
        'ffb3bd00ccf770c686ef3deac9a882559753c04d27fcb59ef6fcf139b3e34fd4',
    'uia-page2-prev.json':
        '3228ad8be2a84dffedd2b86b4e8707e9ffc9adcc71e033432097f6bfd42a3784',
    'uia-page1-search-reset.json':
        '7ae2b1d7aaea389cbec16a13291ca13fa92658a729d6678dcb66332e0c83122c',
}
PAGES = (
    ('page1-initial', 'page1-of-3-initial', tuple(range(1, 9)), None),
    ('page2-next', 'page2-of-3-next', tuple(range(9, 17)), '2 / 3'),
    ('page3-next', 'page3-of-3-next', (17,), '3 / 3'),
    ('page2-prev', 'page2-of-3-prev', tuple(range(9, 17)), '2 / 3'),
    ('page1-search-reset', 'page1-search-change-reset', tuple(range(1, 9)), None),
)


def verify_original_pages(root=None):
    """SHA-pin all P3-402 raw owner observations and compare actual row phases."""
    fixture = previous.verify_original_id_search()
    if len(original.read_pinned_source()) != 33:
        raise RuntimeError('original Flet source boundary unavailable')
    root = Path(root) if root is not None else FLET_RUN
    records = {}
    try:
        for filename, expected in FIXED_SHA.items():
            path = root / filename
            metadata = path.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
                    or not 0 < metadata.st_size <= 1024 * 1024):
                raise RuntimeError('original Flet pagination UIA unsafe')
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != expected:
                raise RuntimeError('original Flet pagination SHA mismatch')
            records[filename] = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('original Flet pagination evidence unavailable') from exc
    ready = records['actual-original-full-gui-ready.json']
    guard = records['generated-failed-17-source-guard.json']
    if (ready.get('app_pid') != ORIGINAL_PID
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0
            or guard.get('native_gui_main_returned') is not True
            or guard.get('source_unchanged') is not True
            or guard.get('failure') is not None
            or guard.get('generated_failure_count') != 17
            or guard.get('source_count') != 33
            or guard.get('fixture_sha256') != fixture):
        raise RuntimeError('original Flet pagination source/fixture proof invalid')
    last_frame = -1
    for stem, phase, ids, page_label in PAGES:
        record = records['uia-' + stem + '.json']
        window = record.get('window', {})
        frame = record.get('frame', {})
        tree = record.get('accessibility_tree')
        try:
            seq = int(frame['id'].removeprefix('frame-'))
        except (KeyError, ValueError, TypeError) as exc:
            raise RuntimeError('original Flet pagination frame missing') from exc
        expected_ids = tuple(f'failure-{n:04d}' for n in ids)
        absent_ids = tuple(f'failure-{n:04d}' for n in range(1, 18) if n not in ids)
        if (record.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or record.get('phase') != phase or window.get('id') != ORIGINAL_HWND
                or window.get('title') != 'PixivVault'
                or window.get('state') not in ('open', 'foreground')
                or seq <= last_frame or not isinstance(tree, str)
                or any(item not in tree for item in expected_ids)
                or any(item in tree for item in absent_ids)
                or '17件を再試行' not in tree
                or (page_label is not None and page_label not in tree)):
            raise RuntimeError('original Flet pagination GUI state mismatch')
        last_frame = seq
    return fixture


def visible_page(panel, page, ids):
    """Prove actual row visibility, ordering and exact first-page membership."""
    expect(panel.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
    expect(panel.get_by_test_id('failed-page')).to_have_text(f'{page} / 3')
    expect(panel.get_by_test_id('failed-record')).to_have_count(len(ids))
    expect(panel.get_by_test_id('failed-empty')).to_be_hidden()
    for index, work_id in enumerate(ids):
        row = panel.get_by_test_id('failed-record').nth(index)
        expect(row).to_have_attribute('data-work-id', f'failure-{work_id:04d}')
        expect(row).to_be_visible()
    rows = panel.get_by_test_id('failed-rows')
    for number in range(1, 18):
        if number not in ids:
            expect(rows).not_to_contain_text(f'failure-{number:04d}')


def pagination_steps(page, panel, report, out):
    prior = panel.get_by_test_id('failed-previous')
    next_button = panel.get_by_test_id('failed-next')
    expect(panel.get_by_test_id('failed-category-all')).to_have_attribute('aria-pressed', 'true')
    visible_page(panel, 1, tuple(range(1, 9)))
    expect(prior).to_be_disabled()
    expect(next_button).to_be_enabled()
    next_button.click()
    visible_page(panel, 2, tuple(range(9, 17)))
    expect(prior).to_be_enabled()
    expect(next_button).to_be_enabled()
    report['steps'].append('next page 2 shows IDs 0009 through 0016')
    next_button.click()
    visible_page(panel, 3, (17,))
    expect(next_button).to_be_disabled()
    report['steps'].append('next page 3 shows only ID 0017 and disables next')
    page.screenshot(path=str(out / 'last-page3-cdp.png'))
    report['screenshots']['last'] = shared.prior.sha(out / 'last-page3-cdp.png')
    prior.click()
    visible_page(panel, 2, tuple(range(9, 17)))
    report['steps'].append('previous restores page 2 exact 8 IDs')
    search = panel.get_by_placeholder('タイトル・IDで検索')
    search.fill('failure-')
    expect(search).to_have_value('failure-')
    visible_page(panel, 1, tuple(range(1, 9)))
    expect(prior).to_be_disabled()
    expect(next_button).to_be_enabled()
    report['steps'].append('change ID query on page 2 resets to first page')
    page.screenshot(path=str(out / 'search-page1-cdp.png'))
    report['screenshots']['reset'] = shared.prior.sha(out / 'search-page1-cdp.png')


def run(out):
    return shared.run(
        out,
        original_verifier=verify_original_pages,
        ui_steps=pagination_steps,
        scope='P3-403 real Flet 17-record 3-page navigation and NiceGUI pagination synthetic GUI',
        extra_watched=(Path(__file__), Path(previous.__file__)),
        expected_steps=5,
    )


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed17_pagination_gui.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
