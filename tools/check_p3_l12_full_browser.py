"""L12 original operation: synthetic Windows Edge capacity GUI initial/restart acceptance.

Uses the existing legacy capacity fixture, an isolated marked root, and never
reads actual PixivVault user databases, images, credentials or external APIs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import source_hashes

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ROOT / 'artifacts' / 'p3-capacity-auto-20261028'


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def http_json(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url + '/_fixture/state', timeout=3) as response:
        return json.loads(response.read())


def await_state(page, url, predicate, label, *, seconds=35):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        last = http_json(url)
        if predicate(last):
            return last
        page.wait_for_timeout(100)
    raise AssertionError(label + ' (last calls=' + str(len((last or {}).get('calls', []))) + ')')


def check_card(page, author, capacity_label):
    card = page.locator('.pv-library-author-card[data-author-id="' + author + '"]')
    expect(card).to_be_visible()
    expect(card).to_contain_text(capacity_label)


def inspect(page, url, output, *, restart, previous=None):
    page.goto(url, wait_until='domcontentloaded')
    page.get_by_test_id('tab-library').click()
    expect(page.locator('.pv-library-author-card')).to_have_count(50, timeout=25000)
    if restart:
        assert previous is not None
        state = await_state(page, url, lambda s: s['active'] == 0 and not s['calls'],
                            'restored cache erroneously needed a fresh disk read', seconds=20)
        for aid, previous_cache in previous.items():
            assert state['cache'][aid] == previous_cache, 'persisted capacity changed: ' + aid
    else:
        state = await_state(page, url, lambda s: s['active'] == 0 and len(s['calls']) >= 50,
                            'first fifty capacities not measured')
        assert len(state['calls']) == 50 and len(set(state['calls'])) == 50
        assert state['max_active'] == 1, 'parallel measurement violates original serial reader'
    assert state['cache']['1002'][1] == 0
    assert state['cache']['1001'][1] > 0
    check_card(page, '1002', '0 B')
    check_card(page, '1001', ' B')
    page.get_by_test_id('library-author-more').click()
    expect(page.locator('.pv-library-author-card')).to_have_count(62, timeout=15000)
    state = await_state(page, url, lambda s: s['active'] == 0 and
                        all(s['cache'][str(aid)][1] is not None for aid in range(1001, 1063)),
                        'visible author capacity did not fill all 62')
    assert state['cache']['1062'][1] > 0
    if restart:
        assert state['calls'] == [], 'cache restore must not trigger a new measured read'
    else:
        assert len(state['calls']) == 62 and len(set(state['calls'])) == 62
        # One genuinely failed disk read must not erase a prior cached value.
        before = state['cache']['1003']
        control = page.context.new_page()
        try:
            control.goto(url + '/_fixture/control', wait_until='domcontentloaded')
            control.screenshot(path=str(output / 'fault-control-before.png'), animations='disabled')
            fault = control.locator('.q-checkbox')
            expect(fault).to_have_count(1)
            fault.click()
            await_state(page,url,lambda s:s['fail_author']=='1003',
                        'fault injection did not become active',seconds=10)
        finally:
            control.close()
        measure = page.get_by_test_id('library-legacy-capacity-measure')
        expect(measure).to_be_enabled()
        measure.click()
        expect(page.get_by_test_id('library-legacy-capacity-status')).to_contain_text('未計測 1作者', timeout=30000)
        state = await_state(page, url, lambda s: s['active'] == 0 and len(s['calls']) >= 112,
                            'explicit 50-author remeasurement did not finish')
        assert state['cache']['1003'] == before, 'failure must retain the prior nonzero value'
        assert len(state['calls']) == 112, 'explicit action must only remeasure 50 visible authors'
    page.get_by_test_id('library-sort-capacity').click()
    expect(page.locator('.pv-library-author-card')).to_have_count(50, timeout=15000)
    page.get_by_test_id('library-author-more').click()
    expect(page.locator('.pv-library-author-card')).to_have_count(62, timeout=15000)
    ordered = page.locator('.pv-library-author-card').evaluate_all(
        '(elements) => elements.map(el => el.dataset.authorId)')
    assert len(ordered) == 62 and len(set(ordered)) == 62
    observed = [state['cache'][aid][1] for aid in ordered]
    assert all(a >= b for a, b in zip(observed, observed[1:])), (
        'capacity sort not descending', observed[:12])
    check_card(page, '1002', '0 B')
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
    page.screenshot(path=str(output / 'capacity-all.png'), animations='disabled')
    return dict(state_cache=state['cache'], calls=len(state['calls']),
                sorted_first=ordered[:6], measured_1001=state['cache']['1001'],
                measured_1002=state['cache']['1002'], measured_1003=state['cache']['1003'])


def run(output):
    output = output.resolve()
    if not output.is_relative_to(ALLOWED.resolve()) or output == ALLOWED.resolve():
        raise ValueError('an isolated output under artifacts/p3-capacity-auto-20261028 is required')
    output.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    runs = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            for width, height in ((1280, 720), (1920, 1080)):
                data = output / ('data-' + str(width))
                previous = None
                for restart in (False, True):
                    evidence = output / (str(width) + ('-restart' if restart else '-initial'))
                    evidence.mkdir()
                    port, nonce = free_port(), secrets.token_hex(24)
                    url = 'http://127.0.0.1:' + str(port)
                    run = dict(viewport=[width, height], restart=restart, success=False,
                               console_errors=[], page_errors=[], external_requests=[], http_404=[])
                    env = os.environ.copy()
                    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT/'src'),
                               TEMP=str(output), TMP=str(output), COS_SHUTDOWN_NONCE=nonce)
                    child = None
                    context = browser.new_context(viewport={'width':width,'height':height})
                    origin = urlsplit(url)
                    def route(redirect):
                        target = urlsplit(redirect.request.url)
                        if (target.scheme, target.netloc) == (origin.scheme, origin.netloc):
                            redirect.continue_()
                        else:
                            run['external_requests'].append(target.netloc)
                            redirect.abort()
                    context.route('**/*', route)
                    page = context.new_page()
                    page.on('pageerror', lambda error:run['page_errors'].append(str(error)))
                    page.on('console', lambda message:run['console_errors'].append(message.text)
                            if message.type == 'error' else None)
                    page.on('response', lambda response:run['http_404'].append(urlsplit(response.url).path)
                            if response.status == 404 else None)
                    with (evidence / 'server-stderr.log').open('wb') as stderr:
                        try:
                            child = subprocess.Popen([
                                sys.executable, '-B', str(ROOT/'tools/serve_capacity_auto_cancel_wrapper.py'),
                                '--root', str(data), '--port', str(port)],
                                cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                            run['pid'] = child.pid
                            for _ in range(120):
                                if child.poll() is not None:
                                    raise RuntimeError('fixture server exited ' + str(child.returncode))
                                try: http_json(url);break
                                except (urllib.error.URLError, TimeoutError):time.sleep(.2)
                            else: raise TimeoutError('fixture server startup')
                            latest = inspect(page,url,evidence,restart=restart,previous=previous)
                            previous = latest['state_cache']
                            run['observed'] = {k:v for k,v in latest.items() if k != 'state_cache'}
                            assert not run['page_errors'] and not run['external_requests']
                            assert all('404' in message for message in run['console_errors'])
                            assert all(path == '/media/9002/0/thumb.png' for path in run['http_404'])
                            run['success'] = True
                        except BaseException as error:
                            run['failure'] = type(error).__name__+': '+str(error)[:1000]
                            run['traceback'] = traceback.format_exc()[-4500:]
                            try:page.screenshot(path=str(evidence/'failure.png'),animations='disabled')
                            except Exception:pass
                        finally:
                            try:context.close()
                            except Exception as error:run['browser_close_error'] = type(error).__name__
                            if child is not None:
                                if child.poll() is None:
                                    request=urllib.request.Request(url+'/_cos/shutdown',method='POST',data=b'',
                                        headers={'X-Cos-Shutdown':nonce})
                                    try:
                                        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                                        with opener.open(request,timeout=6) as response:
                                            run['shutdown_http'] = response.status
                                    except Exception as error:
                                        run['shutdown_error']=type(error).__name__
                                    try:child.wait(timeout=15)
                                    except subprocess.TimeoutExpired:
                                        child.terminate()
                                        try:child.wait(timeout=5)
                                        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
                                run['normal_exit']=child.returncode == 0
                                run['exit_code']=child.returncode
                            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                                sock.settimeout(.5)
                                run['port_released']=sock.connect_ex(('127.0.0.1',port)) != 0
                        run['success'] = bool(run['success'] and run.get('normal_exit') and
                                              run.get('port_released') and not run.get('browser_close_error'))
                        (evidence / 'result.json').write_text(
                            json.dumps(run,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                        runs.append(run)
                        if not run['success']:
                            break
                if not runs[-1]['success']:
                    break
        finally: browser.close()
    after = source_hashes()
    success=len(runs)==4 and all(x['success'] for x in runs) and before == after
    result=dict(success=success,source_changed=before!=after,source_before=before,
                source_after=after,checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                runs=runs)
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(success=success,runs=len(runs),source_changed=before!=after,
                          output=str(output),failed=[r.get('failure') for r in runs if not r['success']])))
    return 0 if success else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    return run(args.output)


if __name__ == '__main__':
    raise SystemExit(main())
