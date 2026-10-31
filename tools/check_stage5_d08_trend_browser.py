"""D08 actual learning trend open/close on isolated generated NiceGUI.

Reuses only the proven L13 fixture server setup, not the old L13
acceptance actions. No original Flet, database, Cookie, or remote I/O.
Formal original parity and independent acceptance remain incomplete.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
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


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('new generated-only acceptance root required')
    out.mkdir(parents=True, exist_ok=False)
    port = free_port()
    base = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(24)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'X-Acceptance': nonce}
    watched = [
        Path(__file__), Path(fixture.__file__),
        ROOT / 'tools/owned_process_tree.py',
        ROOT / 'tools/run_browser_checks.py',
    ]
    watched_before = {str(p.relative_to(ROOT)): digest(p) for p in watched}
    source_before = source_hashes()
    result = {
        'schema': 1,
        'original_id': 'D08',
        'references': ['v8-D08-01'],
        'scope': 'NiceGUI actual D08 learning trend panel open/close, generated offline GUI only',
        'formal_pass_added': 0,
        'actual_original_flet_gui': False,
        'steps': [],
        'external_requests': 0,
        'page_errors': 0,
        'success': False,
        'failure': None,
        'app': {'normal_exit': False, 'forced_cleanup': False},
    }
    proc = tree = None
    ready = False
    try:
        with (out / 'server.log').open('xb') as logfile:
            proc = subprocess.Popen(
                [sys.executable, '-B', str(Path(fixture.__file__)),
                 '--serve', str(port),
                 '--sandbox', str(out / 'generated-managed'),
                 '--nonce', nonce],
                cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=logfile, stderr=logfile,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            result['app']['pid'] = proc.pid
            tree = OwnedProcessTree(proc)
            if tree.errors:
                raise RuntimeError('owned parent tree not verified')
            wait_for_health(base, proc, expected_data='pixiv-readonly')
            request = urllib.request.Request(base + '/_p3_test/snapshot', headers=headers)
            with opener.open(request, timeout=8) as response:
                snapshot = json.loads(response.read())
            if proc.poll() is not None or len(snapshot['receipts']) != 1:
                raise RuntimeError('generated owned fixture startup not proven')
            ready = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        viewport={'width': 1280, 'height': 720},
                        device_scale_factor=1,
                    )

                    def deny_external(route):
                        parsed = urlsplit(route.request.url)
                        if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                            result['external_requests'] += 1
                            route.abort()
                        else:
                            route.continue_()

                    context.route('**/*', deny_external)
                    page = context.new_page()
                    page.on('pageerror', lambda _:
                            result.__setitem__('page_errors', result['page_errors'] + 1))
                    page.goto(base, wait_until='domcontentloaded', timeout=25000)
                    navigation = page.get_by_test_id('tab-learning')
                    expect(navigation).to_be_visible()
                    navigation.click()
                    expect(navigation).to_have_attribute('aria-current', 'page')
                    button = page.get_by_test_id('discovery-trend-toggle')
                    panel = page.get_by_test_id('discovery-trend-panel')
                    expect(button).to_be_visible(timeout=15000)
                    expect(button).to_have_attribute('aria-expanded', 'false')
                    expect(panel).to_be_hidden()
                    result['steps'].append('real learning tab: trend initially collapsed')

                    button.click()
                    expect(button).to_have_attribute('aria-expanded', 'true')
                    expect(panel).to_be_visible()
                    expect(button).to_contain_text('傾向を閉じる')
                    result['steps'].append('actual click opens a visible trend panel')
                    page.screenshot(path=str(out / 'd08-trend-open.png'))
                    result['open_screenshot_sha256'] = digest(out / 'd08-trend-open.png')

                    button.click()
                    expect(button).to_have_attribute('aria-expanded', 'false')
                    expect(panel).to_be_hidden()
                    expect(button).to_contain_text('傾向を見る')
                    result['steps'].append('actual click closes and hides trend panel')

                    button.click()
                    expect(button).to_have_attribute('aria-expanded', 'true')
                    expect(panel).to_be_visible()
                    result['steps'].append('panel can reopen without switching tabs')
                    page.screenshot(path=str(out / 'd08-trend-reopened.png'))
                    result['reopen_screenshot_sha256'] = digest(out / 'd08-trend-reopened.png')
                    context.close()
                finally:
                    browser.close()
            if result['external_requests'] or result['page_errors']:
                raise AssertionError('unexpected browser external request / JavaScript error')
            result['tested'] = len(result['steps']) == 4
    except Exception as error:
        result['failure'] = type(error).__name__
        result['error_short'] = str(error)[:280]
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and ready:
                    request = urllib.request.Request(
                        base + '/_p3_test/shutdown', headers=headers,
                        data=b'', method='POST',
                    )
                    with opener.open(request, timeout=8) as response:
                        result['app']['shutdown_ack'] = json.loads(response.read())
                    result['app']['normal_exit'] = proc.wait(timeout=20) == 0
                if proc.poll() is None:
                    result['app']['forced_cleanup'] = True
                    stop_child(proc)
            except Exception:
                result['app']['shutdown_error'] = 'normal stop unavailable'
                if proc.poll() is None:
                    result['app']['forced_cleanup'] = True
                    try:
                        stop_child(proc)
                    except Exception:
                        pass
            result['app']['exit_code'] = proc.poll()
        result['app']['tree'] = tree.finish() if tree else {}
        result['app']['port_released'] = port_released(port)
        result['source_changed'] = source_before != source_hashes()
        result['harness_changed'] = watched_before != {
            str(p.relative_to(ROOT)): digest(p) for p in watched
        }
        owned = result['app']['tree']
        result['success'] = bool(
            result.get('tested') and result['failure'] is None
            and result['app'].get('exit_code') == 0
            and result['app']['normal_exit']
            and not result['app']['forced_cleanup']
            and result['app']['port_released']
            and owned.get('remaining_count') == 0 and not owned.get('errors')
            and not result['source_changed'] and not result['harness_changed']
        )
        with (out / 'result.json').open('x', encoding='utf8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({
        'success': result['success'],
        'steps': len(result['steps']),
        'failure': result['failure'],
        'owned_remaining': result['app']['tree'].get('remaining_count'),
        'formal_pass_added': 0,
    }))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_d08_trend_browser.py NEW_ARTIFACT_PATH')
    raise SystemExit(run(Path(sys.argv[1])))
