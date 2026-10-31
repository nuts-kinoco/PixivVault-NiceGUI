"""Real current NiceGUI Q14/Q15 category AND title search, Flet-matched input.

Uses the same generated FailureRecord objects as the guarded REAL gui.py test
P3-392. Screenshots are CDP at matched physical pixels but NOT owned native
GDI; full product parity and formal original acceptance remain UNVERIFIED.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from urllib.parse import urlsplit
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture
from tools.check_failed_records_browser import build_failure_fixture
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes,
    stop_child, wait_for_health,
)

FLET_RUN = ROOT / 'artifacts/p3-389-real-flet-failed17-r3'
FLET_FILTER_RUN = ROOT / 'artifacts/p3-392-real-flet-failed17-filters-r1'
CSS_VIEWPORT = (1770, 884)
DPR = 1.75
# Frozen raw read-only observations, not user-supplied self-certification.
# These are SHA256 of the exact CoS Desktop observed UIA files from P3-392.
# New runs require fresh review and a separately scoped checker/receipt.
P392_OBSERVED_WINDOW_HWND = 9308166
P392_FIXED_EVIDENCE_SHA256 = {
    'actual-original-full-gui-ready.json':
        'f3b5393900f65586229763d809ab09d8e19b2781628edc5fe f634f96033374a2'.replace(' ', ''),
    'generated-failed-17-source-guard.json':
        'a33b1bc7e023f111738bed0661bf6c39a3484432d3028d1a74408a0b478cbcb6',
    'uia-communication3.json':
        '1d5238643fa9db4371336f21de2ca53afd94a945ca99da873428b7147f79bde5',
    'uia-communication-title007.json':
        '3bb40b98b5f55489ad7ba6c1077819df1559e8d9586858c87843c18a0c96f202',
    'uia-communication-title002-nomatch.json':
        'f6f14553ee18a6464a7a5eaba1a616ce2aa66d1ccde3d69153ff10f9c8836178',
    'uia-communication-restored3.json':
        'f7a6b6b8f48041d55c8923e6ee59c3899c99441f976cea2b0c5f69922b1a846a',
    'uia-all-restored17.json':
        'a4400e141a64773f823c458a54e84a8592761c23318f6ccad4435cb06145ef02',
}


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def verify_failure_server_lifecycle(receipt, *, listener_pid, db_sha):
    """Require a normal server shutdown, no denied egress and unchanged fixture."""
    path = Path(receipt)
    try:
        meta = path.lstat()
        if (not stat.S_ISREG(meta.st_mode) or path.is_symlink()
                or not 0 < meta.st_size <= 8192):
            return False
        body = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError, UnicodeError):
        return False
    return (
        type(body) is dict
        and type(body.get('pid')) is int and body['pid'] == listener_pid
        and body.get('services_closed') is True
        and body.get('shutdown_requested') is True
        and type(body.get('external_denied')) is int
        and body['external_denied'] == 0
        and body.get('original_db_sha256') == db_sha
    )


def fixture_fingerprint():
    rows = tuple(asdict(row) for row in build_failed_records_fixture())
    return sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()


def assert_no_match_ui(root, search):
    """No stale row may pass merely because an invisible empty label has text."""
    expect(search).to_have_value('架空の失敗作品 002')
    expect(root.get_by_test_id('failed-category-communication')).to_have_attribute(
        'aria-pressed', 'true')
    expect(root.get_by_test_id('failed-count')).to_have_text('0 / 17 件')
    expect(root.get_by_test_id('failed-record')).to_have_count(0)
    expect(root.get_by_test_id('failed-empty')).to_be_visible()
    expect(root.get_by_test_id('failed-empty')).to_have_text('一致する失敗はありません')


def assert_restored_rows(root, *, all_categories):
    """P394 follow-up: counter alone is not proof that matching DOM rows render."""
    expected_ids = (
        tuple(f'failure-{i:04d}' for i in range(1, 9))
        if all_categories else ('failure-0001', 'failure-0007', 'failure-0013')
    )
    expect(root.get_by_test_id('failed-count')).to_have_text(
        '17 / 17 件' if all_categories else '3 / 17 件')
    expect(root.get_by_test_id('failed-category-communication')).to_have_attribute(
        'aria-pressed', 'false' if all_categories else 'true')
    expect(root.get_by_test_id('failed-category-all')).to_have_attribute(
        'aria-pressed', 'true' if all_categories else 'false')
    expect(root.get_by_test_id('failed-page')).to_have_text(
        '1 / 3' if all_categories else '1 / 1')
    expect(root.get_by_test_id('failed-record')).to_have_count(len(expected_ids))
    for work_id in expected_ids:
        row = root.locator(f'[data-testid="failed-record"][data-work-id="{work_id}"]')
        expect(row).to_have_count(1)
        expect(row).to_be_visible()
    if not all_categories:
        for number in range(1, 18):
            work_id = f'failure-{number:04d}'
            if work_id not in expected_ids:
                expect(root.locator(
                    f'[data-testid="failed-record"][data-work-id="{work_id}"]')).to_have_count(0)
    expect(root.get_by_test_id('failed-empty')).to_be_hidden()


def assert_title_007_filtered_ui(root, search):
    """Single title AND category (P2 negative fixture also exercises this)."""
    expect(search).to_have_value('架空の失敗作品 007')
    expect(root.get_by_test_id('failed-category-communication')).to_have_attribute(
        'aria-pressed', 'true')
    expect(root.get_by_test_id('failed-count')).to_have_text('1 / 17 件')
    expect(root.get_by_test_id('failed-page')).to_have_text('1 / 1')
    expect(root.get_by_test_id('failed-record')).to_have_count(1)
    row = root.locator('[data-testid="failed-record"][data-work-id="failure-0007"]')
    expect(row).to_have_count(1)
    expect(row).to_be_visible()
    expect(root.get_by_test_id('failed-empty')).to_be_hidden()


def verify_flet_filter_actions(root=None):
    """Only original gui.py UIA evidence, not a formal independent attestation."""
    if root is None:
        root = FLET_FILTER_RUN
    root = Path(root)
    from tools import check_stage5_original_failed17_native as original
    # A set of five internally consistent, hand-written text files is not
    # sufficient proof of actual input on the real owner HWND. Bind raw bytes
    # to the exact original desktop observation already separately captured.
    # The attestation is still not a signed, independent full-parity trust root.
    try:
        for name, expected in P392_FIXED_EVIDENCE_SHA256.items():
            path = root / name
            if (path.is_symlink() or not path.is_file() or
                    path.stat().st_size > 1024 * 1024 or sha(path) != expected):
                raise RuntimeError('original Flet fixed UIA evidence SHA mismatch')
    except (OSError, ValueError) as exc:
        raise RuntimeError('original Flet fixed UIA evidence unavailable') from exc
    guard = json.loads((root / 'generated-failed-17-source-guard.json').read_text(encoding='utf8'))
    ready = json.loads((root / 'actual-original-full-gui-ready.json').read_text(encoding='utf8'))
    if (guard.get('native_gui_main_returned') is not True
            or guard.get('source_unchanged') is not True
            or guard.get('failure') is not None
            or guard.get('fixture_sha256') != fixture_fingerprint()
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0
            or len(original.read_pinned_source()) != 33):
        raise RuntimeError('original real Flet Q14/Q15 source boundary unavailable')
    stages = (
        ('uia-communication3.json', 'communication3', ('3件を再試行', 'failure-0007', 'failure-0013'), ('failure-0002',)),
        ('uia-communication-title007.json', 'communication+title007', ('1件を再試行', 'failure-0007'), ('failure-0013',)),
        ('uia-communication-title002-nomatch.json', 'communication+title002_no_match',
         ('一致する失敗はありません', '検索を消す', '再試行できるものがありません'),
         ('1件を再試行', 'failure-0007')),
        ('uia-communication-restored3.json', 'communication3_restored', ('3件を再試行', 'failure-0007', 'failure-0013'), ('failure-0002',)),
        ('uia-all-restored17.json', 'all17_restored', ('17件を再試行', 'failure-0002'), ()),
    )
    prior = -1
    owner_hwnd = None
    for filename, expected_phase, required, absent in stages:
        try:
            file = root / filename
            if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
                raise RuntimeError('untrusted original UIA evidence file')
            row = json.loads(file.read_text(encoding='utf8'))
            frame = row.get('frame', {})
            tree = row.get('accessibility_tree')
            window = row.get('window', {})
            seq = int(frame['id'].removeprefix('frame-'))
            if (row.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                    or row.get('phase') != expected_phase or not isinstance(tree, str)
                    or not all(text in tree for text in required)
                    or any(text in tree for text in absent)
                    or seq <= prior or window.get('title') != 'PixivVault'
                    or window.get('state') != 'foreground'
                    or type(window.get('id')) is not int
                    or window['id'] != P392_OBSERVED_WINDOW_HWND):
                raise RuntimeError('real original Flet Q14/Q15 UIA semantics invalid')
            if owner_hwnd is not None and owner_hwnd != window['id']:
                raise RuntimeError('original Flet window owner changed between actions')
            owner_hwnd = window['id']
            prior = seq
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('original real Flet Q14/Q15 UIA proof unavailable') from exc
    return guard['fixture_sha256']


def verify_flet_real_evidence(root=None):
    """Require actual original gui.py source and two owned native GDI frames.

    A self-declared "GUI returned" flag is insufficient. This is still not a
    signed independent attestation or complete cross-product parity proof.
    """
    from PIL import Image
    from tools import check_stage5_original_failed17_native as original
    if root is None:
        root = FLET_RUN
    root = Path(root)

    def fail():
        raise RuntimeError('original real Flet native GUI evidence invalid or missing')

    def existing(rel):
        path = root / rel
        try:
            meta = path.lstat()
            if (not stat.S_ISREG(meta.st_mode) or path.is_symlink()
                    or meta.st_size > 8 * 1024 * 1024):
                fail()
            return path
        except (OSError, ValueError):
            fail()

    def packet(rel):
        try:
            return json.loads(existing(rel).read_text(encoding='utf8'))
        except (OSError, ValueError, UnicodeError):
            fail()

    if len(original.read_pinned_source()) != 33:
        fail()
    receipt = packet('generated-failed-17-source-guard.json')
    ready_path = existing('actual-original-full-gui-ready.json')
    ready = packet('actual-original-full-gui-ready.json')
    if (receipt.get('native_gui_main_returned') is not True
            or receipt.get('failure') is not None
            or receipt.get('source_unchanged') is not True
            or receipt.get('source_count') != 33
            or receipt.get('source_gui_sha256') != original.EXACT_GUI_SHA
            or receipt.get('original_data_cookie_used') is not False
            or receipt.get('generated_failure_count') != 17
            or receipt.get('fixture_sha256') != fixture_fingerprint()):
        fail()
    if (ready.get('actual_original_gui_py_main_window_executed') is not True
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('original_source_dependency_count') != 33
            or ready.get('original_db_cookie_used') is not False
            or ready.get('source_workers_started') != 0
            or type(ready.get('app_pid')) is not int
            or ready['app_pid'] <= 0):
        fail()
    ready_sha = sha(ready_path)
    identities = []
    native_image_hashes = []
    for folder in (Path('filtered-one'), Path('restored-seventeen')):
        meta = packet(folder / 'original-full-failed-native.json')
        img = existing(folder / 'original-full-failed-native.png')
        if (meta.get('tab') != 'failed'
                or meta.get('real_original_gui_py_main_window') is not True
                or meta.get('original_source_writes') is not False
                or meta.get('ready_sha256') != ready_sha
                or meta.get('image_sha256') != sha(img)
                or meta.get('capture_backend') != 'Flet_owned_native_PrintWindow'
                or meta.get('native_root_pid') != ready['app_pid']
                or type(meta.get('native_hwnd')) is not int
                or meta.get('native_hwnd') <= 0
                or type(meta.get('native_root_birth')) is not int
                or meta.get('native_root_birth') <= 0
                or not isinstance(meta.get('native_owner'), list)
                or len(meta['native_owner']) != 2
                or meta.get('dpi') != 168
                or meta.get('client_pixels') != [1770, 884]
                or meta.get('pixel_dimensions') != [3098, 1547]):
            fail()
        try:
            with Image.open(img) as picture:
                if picture.size != (3098, 1547):
                    fail()
                picture.verify()
        except (OSError, ValueError):
            fail()
        identities.append((meta['native_root_pid'], meta['native_root_birth'],
                           meta['native_hwnd'], tuple(meta['native_owner'])))
        native_image_hashes.append(meta['image_sha256'])
    # The two files are different required UI phases. Even a SHA-consistent
    # duplicate under another filename is not evidence of a second state.
    # Distinct pixels remain necessary but NOT sufficient to prove 1->17 rows.
    if identities[0] != identities[1] or len(set(native_image_hashes)) != 2:
        fail()
    # UIA trees here were captured from the *owned real Flet window* by the
    # desktop observation tool at each new operation phase, not inferred from
    # saved image filenames or a pure-function filter. Their SHA/source alone
    # is not a final independent attestation; see full parity trust-root gate.
    stages = (
        ('uia-initial17.json', 'initial17',
         ('17件を再試行', 'failure-0001'), None, None),
        ('uia-filtered-one.json', 'filtered-one',
         ('1件を再試行', 'failure-0001', '二行目の診断'),
         'filtered-one/original-full-failed-native.png', native_image_hashes[0]),
        ('uia-no-match.json', 'no-match',
         ('一致する失敗はありません', '検索を消す'), None, None),
        ('uia-restored17.json', 'restored17',
         ('17件を再試行', 'failure-0001'),
         'restored-seventeen/original-full-failed-native.png', native_image_hashes[1]),
    )
    prior_frame = -1
    for name, phase, expected_tokens, capture_path, capture_sha in stages:
        record = packet(name)
        window = record.get('window')
        tree = record.get('ui_tree')
        frame = record.get('frame')
        if (record.get('schema') != 1
                or record.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or record.get('phase') != phase or not isinstance(window, dict)
                or window.get('title') != 'PixivVault'
                or window.get('state') != 'foreground'
                or window.get('id') != identities[0][2]
                or not isinstance(tree, str) or not all(token in tree for token in expected_tokens)
                or not isinstance(frame, dict) or not isinstance(frame.get('id'), str)
                or not frame['id'].startswith('frame-')):
            fail()
        try:
            sequence = int(frame['id'].removeprefix('frame-'))
        except ValueError:
            fail()
        if sequence <= prior_frame:
            fail()
        prior_frame = sequence
        if capture_path is not None and (
            record.get('native_capture_path') != capture_path
            or record.get('native_capture_sha256') != capture_sha
        ):
            fail()
    return receipt['fixture_sha256']


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('fresh generated-only output required')
    flet_fixture_sha = verify_flet_real_evidence()
    if verify_flet_filter_actions() != flet_fixture_sha:
        raise RuntimeError('original Flet source fixture changed before Q14/Q15 comparison')
    out.mkdir(parents=True, exist_ok=False)
    fixture_db, images = build_failure_fixture(out / 'fixture')
    original_db_hash = sha(fixture_db)
    port, nonce = free_port(), secrets.token_hex(24)
    url = f'http://127.0.0.1:{port}'
    headers = {'X-Acceptance': nonce}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    import tools.check_failed_records_browser as source_server
    watched = [
        Path(__file__), Path(source_server.__file__),
        ROOT / 'tools/check_readonly_library_browser.py',
        ROOT / 'tools/failed_records_fixture_guard.py',
        ROOT / 'tools/check_library_calendar_slider_owned.py',
        ROOT / 'tools/owned_process_tree.py', ROOT / 'tools/run_browser_checks.py',
    ]
    harness_before = {str(p.relative_to(ROOT)): sha(p) for p in watched}
    source_before = source_hashes()
    report = {
        'schema': 1, 'scope': 'real NiceGUI Q14/Q15 category-and-title matched to original gui.py',
        'original_ids': ['Q14', 'Q15'],
        'flet_source_fixture_sha256': flet_fixture_sha,
        'nicegui_source_fixture_sha256': fixture_fingerprint(),
        'same_generated_failure_inputs': True,
        'same_logical_viewport_as_flet_css': list(CSS_VIEWPORT),
        'physical_pixels_expected': [3098, 1547], 'native_dpi_reference': 168,
        'capture_backend': 'Playwright_CDP_NOT_NATIVE_GDI',
        'same_capture_backend_as_flet': False,
        'full_product_feature_parity': False, 'formal_pass_added': 0,
        'steps': [], 'screenshots': {}, 'browser_external_requests': 0,
        'browser_external_websockets': 0, 'javascript_errors': 0,
        'console_errors': 0, 'success': False, 'failure': None,
        'app': {'normal_exit': False, 'forced_cleanup': False},
    }
    proc = tree = None
    ready = False
    lifecycle = out / 'lifecycle'
    lifecycle.mkdir(exist_ok=False)
    try:
        with (out / 'server.log').open('xb') as log:
            cmd = [sys.executable, '-B', str(Path(source_server.__file__)),
                   '--serve', '--port', str(port), '--db', str(fixture_db),
                   '--images', str(images), '--preferences', str(out / 'preferences'),
                   '--nonce', nonce, '--output', str(lifecycle)]
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            report['app']['pid'] = proc.pid
            tree = OwnedProcessTree(proc)
            if tree.errors:
                raise RuntimeError('process ownership unknown')
            wait_for_health(url, proc, expected_data='library-readonly')
            request = urllib.request.Request(url + '/_failure_acceptance/owner', headers=headers)
            with opener.open(request, timeout=8) as response:
                owner = json.loads(response.read())
            listener_pid = owner.get('pid')
            if (not owner.get('synthetic') or type(listener_pid) is not int
                    or listener_pid <= 0 or not tree.observed_endpoint(listener_pid)
                    or proc.poll() is not None):
                raise RuntimeError('generated failure server owner not proven')
            report['app']['listener_pid'] = listener_pid
            ready = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        viewport={'width': CSS_VIEWPORT[0], 'height': CSS_VIEWPORT[1]},
                        device_scale_factor=DPR, service_workers='block')

                    def deny_external(route):
                        target = urlsplit(route.request.url)
                        if (target.scheme, target.netloc) != ('http', f'127.0.0.1:{port}'):
                            report['browser_external_requests'] += 1
                            route.abort()
                        else:
                            route.continue_()

                    def deny_external_ws(route):
                        target = urlsplit(route.url)
                        if (target.scheme, target.netloc) not in (
                            ('ws', f'127.0.0.1:{port}'), ('wss', f'127.0.0.1:{port}'),
                        ):
                            report['browser_external_websockets'] += 1
                            route.close(code=1008, reason='nonloopback websocket denied')
                        else:
                            route.connect_to_server()

                    context.route('**/*', deny_external)
                    context.route_web_socket('**/*', deny_external_ws)
                    page = context.new_page()
                    page.on('pageerror', lambda _:
                            report.__setitem__('javascript_errors', report['javascript_errors'] + 1))
                    page.on('console', lambda msg:
                            report.__setitem__('console_errors', report['console_errors'] + 1)
                            if msg.type == 'error' else None)
                    page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    page.get_by_test_id('tab-failed').click()
                    root = page.get_by_test_id('failed-records-view')
                    expect(root).to_be_visible()
                    assert_restored_rows(root, all_categories=True)
                    expect(root.get_by_test_id('failed-category-all')).to_have_text('すべて 17')
                    report['steps'].append('Same 17 generated rows from full original Flet gui.py')
                    search = root.get_by_placeholder('タイトル・IDで検索')
                    communication = root.get_by_test_id('failed-category-communication')
                    communication.click()
                    assert_restored_rows(root, all_categories=False)
                    report['steps'].append('actual communication reason category filters 17 to 3')
                    search.fill('架空の失敗作品 007')
                    assert_title_007_filtered_ui(root, search)
                    report['steps'].append('actual AND title 007 against communication category yields 1')
                    page.screenshot(path=str(out / 'communication-title007-cdp.png'))
                    report['screenshots']['one'] = sha(out / 'communication-title007-cdp.png')
                    search.fill('架空の失敗作品 002')
                    assert_no_match_ui(root, search)
                    report['steps'].append('actual AND title 002 outside category yields no-match')
                    root.get_by_test_id('failed-clear-search').click()
                    assert_restored_rows(root, all_categories=False)
                    expect(search).to_have_value('')
                    report['steps'].append('clear search retains communication category and restores 3')
                    root.get_by_test_id('failed-category-all').click()
                    assert_restored_rows(root, all_categories=True)
                    report['steps'].append('all category reset restores 17')
                    page.screenshot(path=str(out / 'restored-all17-cdp.png'))
                    report['screenshots']['seventeen'] = sha(out / 'restored-all17-cdp.png')
                    dimensions = page.evaluate(
                        '({pixelWidth: Math.round(innerWidth*devicePixelRatio), '
                        'pixelHeight: Math.round(innerHeight*devicePixelRatio), '
                        'scrollWidth: document.documentElement.scrollWidth})')
                    report['dimensions'] = dimensions
                    if (dimensions['pixelWidth'] != 3098 or dimensions['pixelHeight'] != 1547
                            or dimensions['scrollWidth'] > CSS_VIEWPORT[0] + 3):
                        raise AssertionError('physical pixel or document overflow mismatch')
                    context.close()
                finally:
                    browser.close()
            if (report['browser_external_requests'] or report['browser_external_websockets']
                    or report['javascript_errors'] or report['console_errors']):
                raise AssertionError('unexpected network/console activity')
            report['tested'] = len(report['steps']) == 6
    except Exception as exc:
        report['failure'] = type(exc).__name__
        report['error_short'] = str(exc)[:260]
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and ready:
                    request = urllib.request.Request(
                        url + '/_failure_acceptance/shutdown', headers=headers,
                        data=b'', method='POST')
                    with opener.open(request, timeout=8) as response:
                        report['app']['shutdown_ack'] = json.loads(response.read())
                    report['app']['normal_exit'] = proc.wait(timeout=30) == 0
                if proc.poll() is None:
                    report['app']['forced_cleanup'] = True
                    stop_child(proc)
            except Exception:
                report['app']['shutdown_error'] = 'normal shutdown unavailable'
                if proc.poll() is None:
                    report['app']['forced_cleanup'] = True
                    try:
                        stop_child(proc)
                    except Exception:
                        pass
            report['app']['exit_code'] = proc.poll()
        report['app']['tree'] = tree.finish() if tree is not None else {}
        report['app']['port_released'] = port_released(port)
        report['generated_db_unchanged'] = original_db_hash == sha(fixture_db)
        report['app']['lifecycle_verified'] = verify_failure_server_lifecycle(
            lifecycle / 'server-lifecycle.json',
            listener_pid=report['app'].get('listener_pid'),
            db_sha=original_db_hash)
        report['source_changed'] = source_before != source_hashes()
        harness_after = {str(p.relative_to(ROOT)): sha(p) for p in watched}
        report['harness_sha_before'] = harness_before
        report['harness_sha_after'] = harness_after
        report['harness_changed'] = harness_before != harness_after
        owned = report['app']['tree']
        report['success'] = bool(
            report.get('tested') and report['failure'] is None
            and report['generated_db_unchanged']
            and report['app']['normal_exit'] and report['app']['exit_code'] == 0
            and report['app']['lifecycle_verified']
            and not report['app']['forced_cleanup'] and report['app']['port_released']
            and owned.get('remaining_count') == 0 and not owned.get('errors')
            and not report['source_changed'] and not report['harness_changed'])
        with (out / 'result.json').open('x', encoding='utf8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'success': report['success'], 'steps': len(report['steps']),
                      'failure': report['failure'], 'formal_pass_added': 0,
                      'owned_remaining': report['app']['tree'].get('remaining_count')}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed17_nicegui_matched.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
