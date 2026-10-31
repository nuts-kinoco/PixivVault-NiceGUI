"""D07 real NiceGUI discovery exploration ratio setting, generated-only GUI.

Independent from previously accepted L13 or D08 test actions. Uses the proven
offline L13 fixture constructor only. Binds real GUI save/reload to the
generated persisted discovery settings, never to Flet production state.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import check_stage5_l13_cross_tabs_browser as fixture
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes,
    stop_child, wait_for_health,
)


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('fresh generated-only artifacts root required')
    out.mkdir(parents=True, exist_ok=False)
    port = free_port()
    url = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(24)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'X-Acceptance': nonce}
    watched = [Path(__file__), Path(fixture.__file__),
               ROOT / 'tools/check_p3_residuals_browser.py',
               ROOT / 'tools/p3_residuals_fixtures.py',
               ROOT / 'tools/owned_process_tree.py',
               ROOT / 'tools/run_browser_checks.py']
    harness_sha = {str(p.relative_to(ROOT)): sha(p) for p in watched}
    initial_source_sha = source_hashes()
    proof = {'schema': 1, 'original_id': 'D07', 'references': ['v8-D07-01'],
             'scope': 'generated live NiceGUI discovery settings 2 ratios, save, reload, new client',
             'formal_pass_added': 0, 'actual_original_flet_full_gui': False,
             'steps': [], 'success': False, 'failure': None,
             'browser_external_requests': 0, 'browser_external_websockets': 0,
             'javascript_errors': 0, 'console_errors': 0,
             'app': {'normal_exit': False, 'forced_cleanup': False}}
    proc = tree = None
    ready = False
    try:
        with (out / 'server.log').open('xb') as log:
            proc = subprocess.Popen(
                [sys.executable, '-B', str(Path(fixture.__file__)),
                 '--serve', str(port), '--sandbox', str(out / 'generated-managed'),
                 '--nonce', nonce],
                cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            proof['app']['pid'] = proc.pid
            tree = OwnedProcessTree(proc)
            if tree.errors:
                raise RuntimeError('unverified owner tree')
            wait_for_health(url, proc, expected_data='pixiv-readonly')
            request = urllib.request.Request(url + '/_p3_test/snapshot', headers=headers)
            with opener.open(request, timeout=8) as response:
                state = json.loads(response.read())
            if proc.poll() is not None or len(state['receipts']) != 1:
                raise RuntimeError('generated fixture not owned')
            ready = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        viewport={'width': 1280, 'height': 720},
                        device_scale_factor=1, service_workers='block')

                    def deny_external(route):
                        parsed = urlsplit(route.request.url)
                        if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                            proof['browser_external_requests'] += 1
                            route.abort()
                        else:
                            route.continue_()

                    context.route('**/*', deny_external)
                    def websocket_guard(route):
                        parsed = urlsplit(route.url)
                        if parsed.scheme not in ('ws', 'wss') or parsed.netloc != f'127.0.0.1:{port}':
                            proof['browser_external_websockets'] += 1
                            route.close(code=1008, reason='external websocket prohibited')
                        else:
                            route.connect_to_server()
                    context.route_web_socket('**/*', websocket_guard)
                    page = context.new_page()
                    page.on('pageerror', lambda _:
                            proof.__setitem__('javascript_errors', proof['javascript_errors'] + 1))
                    page.on('console', lambda msg:
                            proof.__setitem__('console_errors', proof['console_errors'] + 1)
                            if msg.type == 'error' else None)
                    page.goto(url, wait_until='domcontentloaded', timeout=25000)
                    tab = page.get_by_test_id('tab-learning')
                    tab.click()
                    expect(tab).to_have_attribute('aria-current', 'page')
                    page.get_by_test_id('discovery-settings-open').click()
                    dialog = page.get_by_test_id('discovery-settings-dialog')
                    expect(dialog).to_be_visible(timeout=15000)
                    # Quasar may put testid on QInput's wrapper or its native
                    # input; cover both without accepting an unrelated field.
                    subject = dialog.locator(
                        '[data-testid="discovery-settings-subject"] input, '
                        'input[data-testid="discovery-settings-subject"]')
                    style = dialog.locator(
                        '[data-testid="discovery-settings-style"] input, '
                        'input[data-testid="discovery-settings-style"]')
                    expect(dialog.get_by_test_id('discovery-settings-save')).to_be_enabled()
                    proof['steps'].append('actual learning tab and discovery settings dialog opened')
                    subject.fill('61')
                    style.fill('23')
                    subject.press('Tab')
                    expect(subject).to_have_value('61')
                    expect(style).to_have_value('23')
                    dialog.get_by_test_id('discovery-settings-save').click()
                    expect(dialog.get_by_test_id('discovery-settings-status')).to_contain_text(
                        '保存しました', timeout=15000)
                    proof['steps'].append('actual fill ratios subject61/style23 and Save acknowledged')
                    subject.fill('17')
                    style.fill('7')
                    subject.press('Tab')
                    expect(subject).to_have_value('17')
                    expect(style).to_have_value('7')
                    # The reload control has its original accessible label,
                    # but deliberately no data-testid in the product.
                    dialog.get_by_role('button', name='設定を読込', exact=True).click()
                    expect(subject).to_have_value('61')
                    expect(style).to_have_value('23')
                    expect(dialog.get_by_test_id('discovery-settings-status')).to_contain_text(
                        '設定を読み込みました', timeout=15000)
                    proof['steps'].append('unsaved 17/7 discarded on explicit reload, persistent 61/23 restored')
                    page.screenshot(path=str(out / 'd07-saved.png'))
                    proof['saved_screenshot_sha256'] = sha(out / 'd07-saved.png')
                    page.reload(wait_until='domcontentloaded')
                    page.get_by_test_id('tab-learning').click()
                    page.get_by_test_id('discovery-settings-open').click()
                    popup = page.get_by_test_id('discovery-settings-dialog')
                    expect(popup.locator(
                        '[data-testid="discovery-settings-subject"] input, '
                        'input[data-testid="discovery-settings-subject"]')).to_have_value('61')
                    expect(popup.locator(
                        '[data-testid="discovery-settings-style"] input, '
                        'input[data-testid="discovery-settings-style"]')).to_have_value('23')
                    proof['steps'].append('new browser page instance reads persisted ratios')
                    context.close()
                finally:
                    browser.close()
            if (proof['browser_external_requests'] or proof['browser_external_websockets']
                    or proof['javascript_errors'] or proof['console_errors']):
                raise AssertionError('external browser request or JavaScript error')
            proof['tested'] = len(proof['steps']) == 4
    except Exception as error:
        proof['failure'] = type(error).__name__
        proof['error_short'] = str(error)[:300]
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and ready:
                    request = urllib.request.Request(
                        url + '/_p3_test/shutdown', headers=headers,
                        data=b'', method='POST')
                    with opener.open(request, timeout=8) as response:
                        proof['app']['shutdown_ack'] = json.loads(response.read())
                    proof['app']['normal_exit'] = proc.wait(timeout=20) == 0
                if proc.poll() is None:
                    proof['app']['forced_cleanup'] = True
                    stop_child(proc)
            except Exception:
                proof['app']['shutdown_error'] = 'normal stop unavailable'
                if proc.poll() is None:
                    proof['app']['forced_cleanup'] = True
                    try:
                        stop_child(proc)
                    except Exception:
                        pass
            proof['app']['exit_code'] = proc.poll()
        proof['app']['tree'] = tree.finish() if tree else {}
        proof['app']['port_released'] = port_released(port)
        harness_after = {str(p.relative_to(ROOT)): sha(p) for p in watched}
        proof['harness_before_sha256'] = harness_sha
        proof['harness_after_sha256'] = harness_after
        proof['source_changed'] = initial_source_sha != source_hashes()
        proof['harness_changed'] = harness_sha != harness_after
        owned = proof['app']['tree']
        proof['success'] = bool(
            proof.get('tested') and proof['failure'] is None
            and proof['app'].get('exit_code') == 0 and proof['app']['normal_exit']
            and not proof['app']['forced_cleanup'] and proof['app']['port_released']
            and owned.get('remaining_count') == 0 and not owned.get('errors')
            and not proof['source_changed'] and not proof['harness_changed'])
        with (out / 'result.json').open('x', encoding='utf8') as target:
            json.dump(proof, target, ensure_ascii=False, indent=2)
    print(json.dumps({'success': proof['success'], 'steps': len(proof['steps']),
                      'failure': proof['failure'],
                      'owned_remaining': proof['app']['tree'].get('remaining_count'),
                      'formal_pass_added': 0}))
    return 0 if proof['success'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_d07_preferences_browser.py NEW_ARTIFACT_PATH')
    raise SystemExit(run(Path(sys.argv[1])))
