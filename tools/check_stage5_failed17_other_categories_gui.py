"""P3-396: Real NiceGUI category clicks against P3-395 real Flet UIA.

All data is generated; native GDI visual parity and formal 152 remain open.
The previous P3-392/393 Q14/Q15 GUI runs are *not* repeated here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright
from tools import check_stage5_failed17_filter_and_search_gui as prior
from tools import check_stage5_original_failed17_native as original
from tools.check_failed_records_browser import build_failure_fixture
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health,
)

FLET_RUN = ROOT / 'artifacts/p3-395-real-flet-other-categories-r1'
OWNER_HWND = 83303156
FROZEN_SHA = {
    'actual-original-full-gui-ready.json': '3168b09cef5fee7565a08e3511cdb7f8e8ecdb99b4714844ce9fe9dc1cf05c88',
    'generated-failed-17-source-guard.json': 'a33b1bc7e023f111738bed0661bf6c39a3484432d3028d1a74408a0b478cbcb6',
    'uia-not-found3.json': '4954b6ae5114288d1dffc66b60d490087695515f04d6f1b3860185978f295514',
    'uia-permission-age3.json': '19f16c7fca425f8196d32a653cd06ef84ea708dfebcfdb14902e5e0fce420510',
    'uia-save-dest3.json': '2aa111a07426b7a07a4d9315e90b34f68fdda9e6946ebc8088883ca5024e3944',
    'uia-format3.json': 'f785daf2a519b13748866702df6fa020a36e1115b6b8dbe6f4464542ebc355e4',
    'uia-other2.json': '8c3b83c37b905a2e4e63b2e53592ec400c4fcab b f0f8120c01b4b909c8d08983'.replace(' ', ''),
    'uia-all17-restored.json': '3d8249ea1b8b7c3e09176ae68a658e34a2f2fce7cee71182230bfa f6b398c8b7'.replace(' ', ''),
}
CATEGORIES = (
    ('not_found', 'not-found3', ('failure-0002', 'failure-0008', 'failure-0014'), 3),
    ('permission_age', 'permission-age3', ('failure-0003', 'failure-0009', 'failure-0015'), 3),
    ('save_dest', 'save-dest3', ('failure-0004', 'failure-0010', 'failure-0016'), 3),
    ('format', 'format3', ('failure-0005', 'failure-0011', 'failure-0017'), 3),
    ('other', 'other2', ('failure-0006', 'failure-0012'), 2),
)


def verify_original_categories(root=None):
    """Bound read-only P3-395 real Flet evidence to exact saved observations."""
    prior_hash = prior.verify_flet_real_evidence()
    if prior.verify_flet_filter_actions() != prior_hash:
        raise RuntimeError('earlier original real Flet comparison evidence unavailable')
    if len(original.read_pinned_source()) != 33:
        raise RuntimeError('original protected source changed')
    root = Path(root) if root is not None else FLET_RUN
    try:
        raw = {}
        for name, pinned_sha in FROZEN_SHA.items():
            path = root / name
            metadata = path.lstat()
            if (not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
                    or metadata.st_size > 1024 * 1024):
                raise RuntimeError('original category UIA file is unsafe')
            content = path.read_bytes()
            from hashlib import sha256
            if sha256(content).hexdigest() != pinned_sha:
                raise RuntimeError('original category UIA fixed evidence mismatch')
            raw[name] = json.loads(content)
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError('original category UIA evidence unreadable') from exc
    guard = raw['generated-failed-17-source-guard.json']
    ready = raw['actual-original-full-gui-ready.json']
    if (guard.get('native_gui_main_returned') is not True
            or guard.get('failure') is not None or guard.get('source_unchanged') is not True
            or guard.get('generated_failure_count') != 17
            or guard.get('source_count') != 33
            or guard.get('fixture_sha256') != prior_hash
            or ready.get('original_gui_py_sha256') != original.EXACT_GUI_SHA
            or ready.get('actual_original_full_tab_ui_constructed') is not True
            or ready.get('source_workers_started') != 0):
        raise RuntimeError('original protected category GUI root unavailable')
    cases = [(phase, ids, count) for _, phase, ids, count in CATEGORIES]
    cases.append(('all17-restored', ('failure-0001', 'failure-0002', 'failure-0007'), 17))
    previous_frame = -1
    for phase, ids, count in cases:
        item = raw['uia-' + phase + '.json']
        window, frame, tree = item.get('window', {}), item.get('frame', {}), item.get('accessibility_tree')
        try:
            frame_id = int(frame['id'].removeprefix('frame-'))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError('original UIA frame invalid') from exc
        if (item.get('source') != 'Chat_On_Steroids_Desktop.get_window_state'
                or item.get('phase') != phase or window.get('id') != OWNER_HWND
                or window.get('title') != 'PixivVault' or window.get('state') != 'foreground'
                or not isinstance(tree, str) or frame_id <= previous_frame
                or f'{count}件を再試行' not in tree
                or any(work_id not in tree for work_id in ids)):
            raise RuntimeError('original real Flet category action mismatch')
        if phase != 'all17-restored':
            other_ids = {f'failure-{i:04d}' for i in range(1, 18)} - set(ids)
            if any(work_id in tree for work_id in other_ids):
                raise RuntimeError('original Flet category leaked a non-matching row')
        previous_frame = frame_id
    return prior_hash


def run(out, *, original_verifier=verify_original_categories,
        ui_steps=None, scope=None, extra_watched=(), expected_steps=7):
    """Use one owner/network/lifecycle harness for distinct frozen GUI actions.

    With no arguments beyond out, the original P3-396 seven-step Q14 check is
    unchanged. Another checked-in scenario may supply a frozen real-Flet
    validator and a different *new* NiceGUI action sequence without cloning the
    owner/shutdown/network code or repeating the original scenario.
    """
    out = out.resolve()
    if out.exists() or not out.is_relative_to((ROOT / 'artifacts').resolve()):
        raise ValueError('new generated-only output root required')
    flet_fixture_hash = original_verifier()
    out.mkdir(parents=True, exist_ok=False)
    fixture_db, images = build_failure_fixture(out / 'fixture')
    initial_db_hash = prior.sha(fixture_db)
    from tools import check_failed_records_browser as server
    watched = [Path(__file__), Path(prior.__file__), Path(server.__file__),
               ROOT / 'tools/owned_process_tree.py', ROOT / 'tools/run_browser_checks.py',
               ROOT / 'tools/check_readonly_library_browser.py',
               ROOT / 'tools/failed_records_fixture_guard.py',
               ROOT / 'tools/check_library_calendar_slider_owned.py',
               *(Path(item) for item in extra_watched)]
    harness_before = {str(p.relative_to(ROOT)): prior.sha(p) for p in watched}
    product_before = source_hashes()
    port, nonce = free_port(), secrets.token_hex(24)
    url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'X-Acceptance': nonce}
    report = {
        'scope': scope or 'P3-396 actual NiceGUI Q14 other five categories compared with fixed real Flet P3-395 UIA',
        'flet_fixture_sha256': flet_fixture_hash, 'nicegui_fixture_sha256': prior.fixture_fingerprint(),
        'formal_pass_added': 0, 'same_native_gdi_capture': False,
        'capture_backend': 'Playwright_CDP_NOT_NATIVE_GDI',
        'steps': [], 'screenshots': {}, 'browser_external_requests': 0,
        'browser_external_websockets': 0, 'javascript_errors': 0,
        'console_errors': 0, 'success': False, 'failure': None,
        'app': {'normal_exit': False, 'forced_cleanup': False},
    }
    lifecycle = out / 'lifecycle'
    lifecycle.mkdir(exist_ok=False)
    proc = tree = None
    ready = False
    try:
        with (out / 'server.log').open('xb') as log:
            proc = subprocess.Popen([
                sys.executable, '-B', str(Path(server.__file__)), '--serve', '--port', str(port),
                '--db', str(fixture_db), '--images', str(images),
                '--preferences', str(out / 'preferences'), '--nonce', nonce,
                '--output', str(lifecycle),
            ], cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            report['app']['pid'] = proc.pid
            tree = OwnedProcessTree(proc)
            if tree.errors:
                raise RuntimeError('owned process ancestry not verified')
            wait_for_health(url, proc, expected_data='library-readonly')
            with opener.open(urllib.request.Request(url + '/_failure_acceptance/owner',
                                                    headers=headers), timeout=8) as response:
                owner = json.loads(response.read())
            listener = owner.get('pid')
            if (not owner.get('synthetic') or type(listener) is not int
                    or listener <= 0 or not tree.observed_endpoint(listener)
                    or proc.poll() is not None):
                raise RuntimeError('generated NiceGUI owner not verified')
            report['app']['listener_pid'] = listener
            ready = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        viewport={'width': 1770, 'height': 884},
                        device_scale_factor=1.75, service_workers='block')

                    def http_guard(route):
                        split = urlsplit(route.request.url)
                        if (split.scheme, split.netloc) != ('http', f'127.0.0.1:{port}'):
                            report['browser_external_requests'] += 1
                            route.abort()
                        else:
                            route.continue_()

                    def ws_guard(route):
                        split = urlsplit(route.url)
                        if (split.scheme, split.netloc) not in (
                                ('ws', f'127.0.0.1:{port}'), ('wss', f'127.0.0.1:{port}')):
                            report['browser_external_websockets'] += 1
                            route.close(code=1008, reason='external ws denied')
                        else:
                            route.connect_to_server()

                    context.route('**/*', http_guard)
                    context.route_web_socket('**/*', ws_guard)
                    page = context.new_page()
                    page.on('pageerror', lambda _:
                            report.__setitem__('javascript_errors', report['javascript_errors'] + 1))
                    page.on('console', lambda m:
                            report.__setitem__('console_errors', report['console_errors'] + 1)
                            if m.type == 'error' else None)
                    page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    page.get_by_test_id('tab-failed').click()
                    panel = page.get_by_test_id('failed-records-view')
                    expect(panel).to_be_visible()
                    expect(panel.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
                    report['steps'].append('generated real NiceGUI all 17 initial')
                    if ui_steps is None:
                        for key, phase, ids, count in CATEGORIES:
                            panel.get_by_test_id('failed-category-' + key).click()
                            expect(panel.get_by_test_id('failed-count')).to_have_text(f'{count} / 17 件')
                            rows = panel.get_by_test_id('failed-rows')
                            for work_id in ids:
                                expect(rows).to_contain_text(work_id)
                            for other in ({f'failure-{i:04d}' for i in range(1, 18)} - set(ids)):
                                expect(rows).not_to_contain_text(other)
                            report['steps'].append(phase + ': ' + str(count))
                        page.screenshot(path=str(out / 'other2-cdp.png'))
                        report['screenshots']['other2'] = prior.sha(out / 'other2-cdp.png')
                        panel.get_by_test_id('failed-category-all').click()
                        expect(panel.get_by_test_id('failed-count')).to_have_text('17 / 17 件')
                        report['steps'].append('all categories restored 17')
                        page.screenshot(path=str(out / 'all17-cdp.png'))
                        report['screenshots']['all17'] = prior.sha(out / 'all17-cdp.png')
                    else:
                        ui_steps(page, panel, report, out)
                    size = page.evaluate('({w:Math.round(innerWidth*devicePixelRatio),'
                                         'h:Math.round(innerHeight*devicePixelRatio),'
                                         'scroll:document.documentElement.scrollWidth})')
                    report['physical_dimensions'] = size
                    if size['w'] != 3098 or size['h'] != 1547 or size['scroll'] > 1773:
                        raise RuntimeError('viewport/overflow mismatch')
                    context.close()
                finally:
                    browser.close()
            if (report['browser_external_requests'] or report['browser_external_websockets']
                    or report['javascript_errors'] or report['console_errors']):
                raise RuntimeError('browser/network violations')
            report['tested'] = len(report['steps']) == expected_steps
    except Exception as exc:
        report['failure'] = type(exc).__name__
        report['error_short'] = str(exc)[:250]
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
        report['generated_db_unchanged'] = initial_db_hash == prior.sha(fixture_db)
        report['app']['lifecycle_verified'] = prior.verify_failure_server_lifecycle(
            lifecycle / 'server-lifecycle.json',
            listener_pid=report['app'].get('listener_pid'),
            db_sha=initial_db_hash)
        report['source_changed'] = product_before != source_hashes()
        report['harness_changed'] = harness_before != {
            str(p.relative_to(ROOT)): prior.sha(p) for p in watched}
        report['success'] = bool(
            report.get('tested') and report['failure'] is None
            and report['generated_db_unchanged'] and not report['source_changed']
            and not report['harness_changed']
            and report['app']['normal_exit'] and report['app']['exit_code'] == 0
            and report['app']['lifecycle_verified']
            and not report['app']['forced_cleanup'] and report['app']['port_released']
            and report['app']['tree'].get('remaining_count') == 0
            and not report['app']['tree'].get('errors'))
        with (out / 'result.json').open('x', encoding='utf8') as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps({'success': report['success'], 'steps': len(report['steps']),
                      'failure': report['failure'], 'formal_pass_added': 0,
                      'remaining': report['app']['tree'].get('remaining_count')}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_failed17_other_categories_gui.py NEW_OUTPUT')
    raise SystemExit(run(Path(sys.argv[1])))
