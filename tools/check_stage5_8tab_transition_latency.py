"""Non-comparable current NiceGUI 30+ tab transitions per synthetic viewport.

Measures Playwright click-to-double-rAF end-to-end latency. Not Flet same
capture, paint timing, startup or formal performance parity. Uses generated
managed fixture and a single owned loopback server only.
"""
from __future__ import annotations
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import check_stage5_l13_cross_tabs_browser as fixture
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes,
    stop_child, wait_for_health,
)

TABS = ('download', 'queue', 'failed', 'dedup',
        'learning', 'ranking', 'library', 'recommend')
VIEWPORTS = ((720, 900), (1280, 720), (1920, 1080))
SAMPLE_COUNT = 32  # at least four visits to every real tab, not one screenshot


def sha(path):
    return sha256(path.read_bytes()).hexdigest()


def metric(samples):
    ordered = sorted(samples)
    return {"count": len(samples), "min_ms": round(ordered[0], 3),
            "median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)], 3),
            "max_ms": round(ordered[-1], 3)}


def run(out):
    out = out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('new owned generated artifact directory required')
    out.mkdir(parents=True, exist_ok=False)
    port = free_port()
    url = f'http://127.0.0.1:{port}'
    nonce = secrets.token_hex(24)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {'X-Acceptance': nonce}
    paths = [Path(__file__), Path(fixture.__file__),
             ROOT / 'tools/owned_process_tree.py', ROOT / 'tools/run_browser_checks.py']
    harness_before = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    source_before = source_hashes()
    report = {
        'schema': 1, 'scope': 'actual synthetic NiceGUI browser navigation, three CSS viewports',
        'not_flet_equivalent': True,
        'timing_contract': 'Playwright click + aria-current + 2 animation frames, includes Python/IPC/JS overhead',
        'formal_pass_added': 0, 'rows': [],
        'browser_external_requests': 0, 'javascript_errors': 0,
        'success': False, 'failure': None,
        'app': {'normal_exit': False, 'forced_cleanup': False},
    }
    app = tree = None
    ready = False
    try:
        with (out / 'server.log').open('xb') as log:
            app = subprocess.Popen(
                [sys.executable, '-B', str(Path(fixture.__file__)),
                 '--serve', str(port), '--sandbox', str(out / 'generated-managed'),
                 '--nonce', nonce],
                cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            report['app']['pid'] = app.pid
            tree = OwnedProcessTree(app)
            if tree.errors:
                raise RuntimeError('owned process identity cannot be verified')
            wait_for_health(url, app, expected_data='pixiv-readonly')
            request = urllib.request.Request(url + '/_p3_test/snapshot', headers=headers)
            with opener.open(request, timeout=8) as response:
                state = json.loads(response.read())
            if app.poll() is not None or len(state['receipts']) != 1:
                raise RuntimeError('unexpected generated fixture ownership')
            ready = True
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    for width, height in VIEWPORTS:
                        context = browser.new_context(
                            viewport={'width': width, 'height': height},
                            device_scale_factor=1)

                        def network_guard(route):
                            parsed = urlsplit(route.request.url)
                            if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                                report['browser_external_requests'] += 1
                                route.abort()
                            else:
                                route.continue_()

                        context.route('**/*', network_guard)
                        page = context.new_page()
                        page.on('pageerror', lambda _:
                                report.__setitem__('javascript_errors',
                                                   report['javascript_errors'] + 1))
                        page.goto(url, wait_until='domcontentloaded', timeout=25000)
                        expect(page.locator('nav[aria-label="メインナビゲーション"]')).to_be_visible()
                        durations = []
                        raw = []
                        for index in range(SAMPLE_COUNT):
                            tab = TABS[index % len(TABS)]
                            control = page.get_by_test_id('tab-' + tab)
                            expect(control).to_be_visible()
                            expect(control).to_be_enabled()
                            start = time.perf_counter_ns()
                            control.click()
                            expect(control).to_have_attribute('aria-current', 'page')
                            page.evaluate("""() => new Promise(resolve =>
                                requestAnimationFrame(() => requestAnimationFrame(resolve)))""")
                            elapsed_ms = (time.perf_counter_ns() - start) / 1000000.0
                            document_width = page.evaluate(
                                'document.documentElement.scrollWidth')
                            if document_width > width + 3:
                                raise AssertionError(f'full-document width overflow {width} tab={tab}')
                            durations.append(elapsed_ms)
                            raw.append({'index': index, 'tab': tab,
                                        'click_aria_2raf_ms': round(elapsed_ms, 3)})
                        report['rows'].append({
                            'viewport_css': [width, height], 'device_scale_factor': 1,
                            'actual_nav_transitions': raw,
                            'click_to_2raf': metric(durations),
                        })
                        context.close()
                finally:
                    browser.close()
            if report['browser_external_requests'] or report['javascript_errors']:
                raise AssertionError('external request or page JS error')
            report['tested'] = (len(report['rows']) == len(VIEWPORTS)
                                and all(row['click_to_2raf']['count'] == SAMPLE_COUNT
                                        for row in report['rows']))
    except Exception as exc:
        report['failure'] = type(exc).__name__
        report['error_short'] = str(exc)[:280]
    finally:
        if app is not None:
            try:
                if app.poll() is None and ready:
                    request = urllib.request.Request(
                        url + '/_p3_test/shutdown', headers=headers,
                        data=b'', method='POST')
                    with opener.open(request, timeout=8) as response:
                        report['app']['shutdown_ack'] = json.loads(response.read())
                    report['app']['normal_exit'] = app.wait(timeout=20) == 0
                if app.poll() is None:
                    report['app']['forced_cleanup'] = True
                    stop_child(app)
            except Exception:
                report['app']['shutdown_error'] = 'natural shutdown unavailable'
                if app.poll() is None:
                    report['app']['forced_cleanup'] = True
                    try:
                        stop_child(app)
                    except Exception:
                        pass
            report['app']['exit_code'] = app.poll()
        report['app']['tree'] = tree.finish() if tree else {}
        report['app']['port_released'] = port_released(port)
        report['source_changed'] = source_before != source_hashes()
        report['harness_changed'] = harness_before != {
            str(p.relative_to(ROOT)): sha(p) for p in paths}
        owner = report['app']['tree']
        report['success'] = bool(
            report.get('tested') and report['failure'] is None
            and report['app']['normal_exit'] and report['app']['exit_code'] == 0
            and not report['app']['forced_cleanup'] and report['app']['port_released']
            and owner.get('remaining_count') == 0 and not owner.get('errors')
            and not report['source_changed'] and not report['harness_changed'])
        with (out / 'result.json').open('x', encoding='utf8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({
        'success': report['success'], 'failure': report['failure'],
        'samples': sum(len(r['actual_nav_transitions']) for r in report['rows']),
        'owned_remaining': report['app']['tree'].get('remaining_count'),
        'formal_pass_added': 0,
    }))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: check_stage5_8tab_transition_latency.py NEW_ARTIFACT_ROOT')
    raise SystemExit(run(Path(sys.argv[1])))
