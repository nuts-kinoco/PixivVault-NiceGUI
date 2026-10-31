"""P3-409: real Flet vs real NiceGUI TRUE empty failure-list distinction.

Tests the genuine source GUI (not a mock tab) with a generated FakeDB that
contains ZERO failed_queue rows. Does not use original DB or real Pixiv APIs.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect
from tools import check_stage5_original_failed17_native as original
from tools import check_stage5_failed17_other_categories_gui as shared
from tools import check_stage5_failed17_partial_id_gui as prior
from tools.check_readonly_library_browser import build_fixture
from tools.failed_records_fixture_guard import write_fixture_manifest

FLET_RUN = ROOT / 'artifacts/p3-408-real-flet-failed-empty-r1'
FLET_PID = 133060
FLET_HWND = 50404184
PINNED = {
    'actual-original-full-gui-ready.json':
        '83bcb5d6d4c2a5b58f06fd2964c09b645bde3aa5ab078598f0f3cc9fcef9943c',
    'generated-failed-empty-source-guard.json':
        '9d35f60396d728d6f713ef9b3c32556cb2a9405e329f8619f7774e28b74746d2',
    'uia-true-empty.json':
        'fc6f265033a5d087c511fe90514c1b7b4f7015b95084726c3665757f726fda8e',
    'uia-empty-search.json':
        'db552ab13e2491ad3274cf6edb5805fbd f5ff58ef3c47c57f83ef8df9e885e43'.replace(' ', ''),
}
EMPTY_FINGERPRINT = sha256(b'[]').hexdigest()


def verify_true_original_empty(root=None):
    """Require actual frozen original Flet source, true empty guard and raw UIA."""
    if len(original.read_pinned_source()) != 33:
        raise RuntimeError('original Flet 33-source guard missing')
    from tools import check_stage5_original_failed_empty_native as empty_launcher
    if empty_launcher.original.EXACT_GUI_SHA != original.EXACT_GUI_SHA:
        raise RuntimeError('original Flet source contract not pinned')
    # The previous original GUI's zero-*match* condition must remain separate
    # evidence from the present zero-*records* case.
    assert prior.verify_original_partial_search() != EMPTY_FINGERPRINT
    root = Path(root) if root is not None else FLET_RUN
    records = {}
    try:
        for name, expected in PINNED.items():
            file = root / name
            metadata = file.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or file.is_symlink()
                    or not 0 < metadata.st_size <= 1024 * 1024):
                raise RuntimeError('original Flet empty evidence path unsafe')
            contents = file.read_bytes()
            if sha256(contents).hexdigest() != expected:
                raise RuntimeError('original Flet empty UIA SHA mismatch')
            records[name] = json.loads(contents)
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('original Flet empty UIA proof unavailable') from exc
    ready = records['actual-original-full-gui-ready.json']
    guard = records['generated-failed-empty-source-guard.json']
    if (ready.get('app_pid') != FLET_PID
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0
            or ready.get('original_db_cookie_used') is not False
            or guard.get('source_gui_sha256') != original.EXACT_GUI_SHA
            or guard.get('source_count') != 33
            or guard.get('source_unchanged') is not True
            or guard.get('original_data_cookie_used') is not False
            or guard.get('generated_failure_count') != 0
            or guard.get('real_original_gui_returned') is not True
            or guard.get('failure') is not None):
        raise RuntimeError('original Flet genuine empty source/guard invalid')
    previous_frame = -1
    for name, phase in (('uia-true-empty.json', 'truly-empty-failed-queue'),
                        ('uia-empty-search.json', 'truly-empty-with-search')):
        row = records[name]
        frame, window, text = row.get('frame', {}), row.get('window', {}), row.get('accessibility_tree')
        try:
            seq = int(frame['id'].removeprefix('frame-'))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('original Flet true-empty UIA frame invalid') from exc
        if (row.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or row.get('phase') != phase or window.get('id') != FLET_HWND
                or window.get('title') != 'PixivVault'
                or window.get('state') not in ('open', 'foreground')
                or not isinstance(text, str) or seq <= previous_frame
                or any(token not in text for token in (
                    '失敗はありません', '失敗した取得は0件です',
                    '再試行できるものがありません'))
                or any(token in text for token in (
                    '一致する失敗はありません', 'failure-0001', 'failure-0009'))):
            raise RuntimeError('original Flet empty GUI state invalid')
        previous_frame = seq
    return EMPTY_FINGERPRINT


def build_true_empty_fixture(root):
    """New owned SQLite with failed_queue table but zero rows; no source access."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    db, images = build_fixture(root)
    with sqlite3.connect(db) as conn:
        conn.execute(
            'CREATE TABLE failed_queue ('
            'work_id TEXT PRIMARY KEY,user_id TEXT,title TEXT,work_type TEXT,'
            'error_reason TEXT,failed_at TEXT,retry_count INTEGER)'
        )
        if conn.execute('SELECT COUNT(*) FROM failed_queue').fetchone()[0] != 0:
            raise RuntimeError('new synthetic failed_queue not empty')
    write_fixture_manifest(db, images)
    return db, images


def assert_true_empty_ui(panel):
    """The *real* view must not show the filtered-no-match wording."""
    expect(panel.get_by_test_id('failed-count')).to_have_text('0 / 0 件')
    expect(panel.get_by_test_id('failed-category-all')).to_have_text('すべて 0')
    expect(panel.get_by_test_id('failed-record')).to_have_count(0)
    expect(panel.get_by_test_id('failed-empty')).to_be_visible()
    expect(panel.get_by_test_id('failed-empty')).to_have_text(
        '失敗はありません\n失敗した取得は0件です')
    expect(panel.get_by_test_id('failed-empty')).not_to_contain_text(
        '一致する失敗はありません')
    expect(panel.get_by_test_id('failed-page')).to_have_text('1 / 1')


def open_failed_after_document_reload(page):
    """The default selected tab on a new document is not the failure list."""
    tab = page.get_by_test_id('tab-failed')
    tab.click()
    expect(tab).to_have_attribute('aria-current', 'page')
    panel = page.get_by_test_id('failed-records-view')
    expect(panel).to_be_visible()
    return panel


def true_empty_steps(page, panel, report, out):
    expect(panel.get_by_test_id('failed-status')).to_be_hidden()
    assert_true_empty_ui(panel)
    page.screenshot(path=str(out / 'actual-empty-cdp.png'))
    report['screenshots']['empty'] = shared.prior.sha(out / 'actual-empty-cdp.png')
    search = panel.get_by_placeholder('タイトル・IDで検索')
    search.fill('0009')
    expect(search).to_have_value('0009')
    assert_true_empty_ui(panel)
    report['steps'].append('true empty with nonmatching search remains true empty not filtered no-match')
    page.screenshot(path=str(out / 'actual-empty-with-search-cdp.png'))
    report['screenshots']['empty_search'] = shared.prior.sha(out / 'actual-empty-with-search-cdp.png')
    page.reload(wait_until='domcontentloaded')
    panel = open_failed_after_document_reload(page)
    expect(panel.get_by_test_id('failed-status')).to_be_hidden()
    assert_true_empty_ui(panel)
    report['steps'].append('true empty state preserved across browser document reload')


def run(out):
    return shared.run(
        out,
        original_verifier=verify_true_original_empty,
        ui_steps=true_empty_steps,
        scope='P3-409 real original Flet true zero failed records vs NiceGUI genuine empty state',
        extra_watched=(
            Path(__file__), Path(prior.__file__), Path(pagination_file()),
            ROOT / 'tools/check_stage5_original_failed_empty_native.py',
        ),
        expected_steps=3,
        fixture_builder=build_true_empty_fixture,
        initial_total=0,
    )


def pagination_file():
    from tools import check_stage5_failed17_pagination_gui as pagination
    return pagination.__file__


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed_empty_comparison_gui.py NEW_ROOT')
    raise SystemExit(run(Path(sys.argv[1])))
