"""Actual copied Windows exe: Egara CUDA -> saved vectors -> WebView2 -> restart.

Synthetic images/SQLite only, original ONNX read-only. No Cookie or external
API. Owned process/HWND/birth and native close dialog are verified; no force
termination is attempted even if a gate fails. This is separate from source
Python + Edge and from GDI minimize/restore acceptance.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

if __package__:
    from . import check_native_window as native
    from .check_library_calendar_slider_owned import OwnedTree
    from .check_similarity_gpu_browser import inputs
else:
    import check_native_window as native
    from check_library_calendar_slider_owned import OwnedTree
    from check_similarity_gpu_browser import inputs


def new_json(path: Path, content):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2)


def fingerprint(path: Path):
    before = path.stat()
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise RuntimeError('input identity changed while hashing')
    return {'sha256': digest.hexdigest(), 'mtime_ns': after.st_mtime_ns,
            'bytes': after.st_size}


def owned_identities(tree):
    with tree._lock:
        if tree.errors:
            raise RuntimeError('owned process observer failed')
        return dict(tree.identities)


def page_rows(root: Path):
    path = root/'index/embeddings.sqlite3'
    if not path.is_file():
        return {'style': 0, 'composition': 0}
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5) as connection:
        return {kind: connection.execute(
            'SELECT COUNT(*) FROM work_embeddings WHERE kind=? AND model=?',
            (kind, 'egara_net_fp32')).fetchone()[0]
            for kind in ('style', 'composition')}


def wait_count(root: Path, page, target: int):
    deadline = time.monotonic() + 105
    while time.monotonic() < deadline:
        counts = page_rows(root)
        if counts == {'style': target, 'composition': target}:
            return counts
        error = page.get_by_test_id('similar-analysis-status').inner_text()
        if 'エラー 1' in error or '失敗' in error:
            raise RuntimeError('packaged analysis reported failure')
        page.wait_for_timeout(200)
    raise TimeoutError('saved embeddings did not reach expected page count')


def owned_webview(page, base, root, run_no, *, provider='cuda',
                  hide_cuda_devices=False, expect_backend_unavailable=False):
    page.set_default_timeout(30000)
    # This gate must use the actual native startup target. Navigating an
    # auxiliary about:blank target or reloading the app would hide a startup
    # or attachment error and cannot certify the normal user-visible route.
    if not native.matching_app_page(page.url, urlsplit(base).port):
        raise RuntimeError('native startup page identity was not established')
    if not native.matching_app_page(page.evaluate('window.location.href'), urlsplit(base).port):
        raise RuntimeError('native DOM location does not match the owned app')
    observed_provider = None
    observed_status = None
    expected_provider = 'cpu' if provider == 'cpu' or (
        provider == 'auto' and hide_cuda_devices) else 'cuda'
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    expect(library).to_have_attribute('data-active','true')
    def open_similar(author):
        thumb = library.locator(
            f'.pv-library-author-card[data-author-id="{author}"] .q-img img:visible').first
        expect(thumb).to_be_visible()
        page.wait_for_function('node => node.complete && node.naturalWidth > 0',
                               arg=thumb.element_handle())
        thumb.click(button='right')
        page.locator('[data-testid=library-author-similar-open]:visible').click()
        expect(page.get_by_test_id('similar-status')).to_be_visible()
    def close_similar():
        page.get_by_test_id('similar-close').click()
        expect(page.get_by_test_id('similar-status')).to_be_hidden()
    status = page.get_by_test_id('similar-status')
    if run_no == 1:
        assert page_rows(root) == {'style': 0, 'composition': 0}
        open_similar('12')
        expect(status).to_contain_text('解析データがありません')
        page.get_by_test_id('similar-analyze').click()
        if expect_backend_unavailable:
            expect(status).to_contain_text('保存済み画像を解析できません', timeout=105000)
            expect(page.get_by_test_id('similar-analyze')).to_be_enabled()
            analysis_status = page.get_by_test_id('similar-analysis-status')
            expect(analysis_status).to_contain_text('実行先 未確定（モデル未ロード）')
            expect(analysis_status).to_contain_text('希望 GPU (CUDA)')
            assert page_rows(root) == {'style': 0, 'composition': 0}
            # The batch control must also stop with an explicit failure, rather
            # than publishing CPU vectors under a requested CUDA label.
            page.get_by_test_id('similar-analysis-start').click()
            expect(analysis_status).to_contain_text('失敗・再開待ち', timeout=105000)
            expect(analysis_status).to_contain_text('実行先 未確定（モデル未ロード）')
            expect(page.get_by_test_id('similar-analysis-resume')).to_be_enabled()
            assert page_rows(root) == {'style': 0, 'composition': 0}
            observed_status = analysis_status.inner_text()
            page.screenshot(path=str(root/'native-backend-unavailable-synthetic.png'))
            close_similar()
            return {'counts': page_rows(root), 'provider_observed_this_round': None,
                    'expected_backend_failure_observed': True,
                    'analysis_status_text': observed_status,
                    'startup_navigation_injected': False}
        wait_count(root, page, 1)
        analysis_status = page.get_by_test_id('similar-analysis-status')
        expect(analysis_status).to_contain_text(
            '実行先 '+('CPU' if expected_provider == 'cpu' else 'GPU (CUDA)'))
        if provider == 'auto' and hide_cuda_devices:
            expect(analysis_status).to_contain_text('希望 自動（GPU優先）')
            expect(analysis_status).to_contain_text('フォールバック:')
            expect(analysis_status).to_contain_text('CPUへ切替')
        elif provider == 'cpu':
            expect(analysis_status).to_contain_text('希望 CPU')
            expect(analysis_status).not_to_contain_text('フォールバック:')
        observed_provider = expected_provider
        expect(status).to_contain_text('解析済み1枚')
        page.get_by_test_id('similar-analysis-start').click()
        wait_count(root, page, 3)
        expect(analysis_status).to_contain_text(
            '実行先 '+('CPU' if expected_provider == 'cpu' else 'GPU (CUDA)'))
        observed_status = analysis_status.inner_text()
        close_similar()
    else:
        assert page_rows(root) == {'style': 3, 'composition': 3}
    open_similar('13')
    if run_no == 2:
        analysis_status = page.get_by_test_id('similar-analysis-status')
        expect(analysis_status).to_contain_text('実行先 未確定（モデル未ロード）')
        observed_status = analysis_status.inner_text()
    expect(status).to_contain_text('2枚')
    result = page.get_by_test_id('similar-result').filter(has_text='101 · 2ページ')
    expect(result).to_have_count(1)
    page.screenshot(path=str(root/f'native-r{run_no}-similar-synthetic.png'))
    result.click()
    viewer=page.get_by_test_id('library-viewer')
    expect(viewer).to_be_visible()
    expect(viewer.locator('.pv-counter')).to_have_text('2/2')
    image=page.get_by_test_id('library-viewer-image').locator('img:visible').first
    page.wait_for_function('node => node.complete && node.naturalWidth > 0',
                           arg=image.element_handle())
    viewer.get_by_role('button',name='閉じる',exact=True).click()
    return {'counts':page_rows(root),'viewer_page':2,
            'provider_observed_this_round':observed_provider,
            'analysis_status_text': observed_status,
            'startup_navigation_injected':False}


def next_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1',0))
        return connection.getsockname()[1]


def run(args):
    exe=args.exe.absolute()
    model=args.model.absolute()
    root=args.output.absolute()
    if not exe.is_file() or not model.is_file() or root.exists():
        raise ValueError('inputs absent or output already exists')
    if args.expect_backend_unavailable and not (
            args.provider == 'cuda' and args.hide_cuda_devices):
        raise ValueError('expected failure requires explicit CUDA with hidden devices')
    root.mkdir(parents=True, exist_ok=False)
    original=inputs(root)
    original_model=fingerprint(model)
    report={'passed':False,'rounds':[],'original_source_unchanged':False,
            'model_unchanged':False,'packaged_exe_sha256':sha256(exe.read_bytes()).hexdigest(),
            'provider_requested': args.provider,
            'child_cuda_visibility_override': '-1' if args.hide_cuda_devices else None,
            'expected_backend_unavailable': args.expect_backend_unavailable,
            'errors':[]}
    checker_before = fingerprint(Path(__file__))
    exe_dir=exe.parent
    round_numbers = (1,) if args.expect_backend_unavailable else (1, 2)
    try:
        with sync_playwright() as pw:
            for round_no in round_numbers:
                app_port, debug_port=next_port(),next_port()
                while debug_port==app_port:
                    debug_port=next_port()
                base=f'http://127.0.0.1:{app_port}'
                round_report={'round':round_no,'app_port':app_port,'debug_port':debug_port,
                              'result':None,'errors':[],'process_tree':None}
                report['rounds'].append(round_report)
                cmd=[str(exe),'--native','--host','127.0.0.1','--port',str(app_port),
                     '--library-readonly-db',str(root/'frozen/library.sqlite3'),
                     '--library-image-root',str(root/'old-images'),
                     '--similarity-index-root',str(root/'index'),
                     '--similarity-model-file',str(model),'--similarity-provider',args.provider]
                environment=os.environ.copy()
                if args.hide_cuda_devices:
                    # A first invalid ordinal hides all GPUs. This Windows
                    # CUDA runtime ignored an empty visibility value; its
                    # cudaGetDeviceCount probe returned no device for '-1'.
                    # Only this child environment changes, never the driver.
                    environment['CUDA_VISIBLE_DEVICES']='-1'
                environment['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']=(
                    f'--remote-debugging-port={debug_port} --remote-debugging-address=127.0.0.1')
                process=None
                tree=None
                hwnd=None
                owner=None
                browser=None
                try:
                    with (root/f'packaged-native-r{round_no}.log').open('wb') as log:
                        process=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,
                            stdout=log,stderr=subprocess.STDOUT,cwd=exe_dir,env=environment)
                    tree=OwnedTree(process,native)
                    if not native.wait_until(
                            lambda: tree.root_is_owned()
                            and native.health_ready(app_port,'library-readonly'),
                            time.monotonic()+100):
                        raise RuntimeError('owned packaged exe health timeout')
                    if not native.wait_until(
                            lambda: bool(native.owned_windows(
                                set(owned_identities(tree)))), time.monotonic()+40):
                        raise RuntimeError('owned native HWND not found')
                    tree.sample()
                    identities=owned_identities(tree)
                    windows=native.owned_windows(set(identities))
                    if len(windows)!=1:
                        raise RuntimeError('expected exactly one owned top window')
                    hwnd=windows[0]
                    owner=native.owned_window_identity(
                        process.pid,identities[process.pid],hwnd,identities)
                    endpoint=f'http://127.0.0.1:{debug_port}'
                    def connect():
                        nonlocal browser
                        try:
                            browser=pw.chromium.connect_over_cdp(endpoint,timeout=1500)
                            return True
                        except Exception:
                            return False
                    if not native.wait_until(connect,time.monotonic()+45):
                        raise RuntimeError('packaged WebView2 CDP unavailable')
                    # WebView2 starts CDP before its actual navigation to the
                    # local UI. A successful CDP handshake does not imply a
                    # stable page target; do not reject an about:blank startup.
                    pages=[]
                    until=time.monotonic()+50
                    while time.monotonic()<until:
                        pages=[candidate for context in browser.contexts
                               for candidate in context.pages
                               if native.matching_app_page(candidate.url,app_port)]
                        if len(pages)==1 and native.ui_ready(app_port):
                            break
                        # In sync Playwright, time.sleep() does not pump its
                        # transport events. A local .url property can therefore
                        # remain about:blank even after WebView2 has navigated.
                        # Yield through the existing target, never create/navigate
                        # a replacement page just to turn this gate green.
                        candidates=[candidate for context in browser.contexts
                                    for candidate in context.pages]
                        if candidates:
                            candidates[0].wait_for_timeout(150)
                        else:
                            # A CDP request also dispatches pending target events.
                            session=browser.new_browser_cdp_session()
                            try:
                                session.send('Target.getTargets')
                            finally:
                                session.detach()
                    if len(pages)!=1:
                        round_report['local_page_count']=len(pages)
                        round_report['observed_page_count']=sum(
                            len(context.pages) for context in browser.contexts)
                        # Only scheme/host/port; never copy URL paths, query
                        # parameters, content, tokens, or Cookie values.
                        round_report['observed_page_origins']=[]
                        for context in browser.contexts:
                            for observed in context.pages:
                                parts=urlsplit(observed.url)
                                round_report['observed_page_origins'].append(
                                    {'scheme':parts.scheme, 'host':parts.hostname,
                                     'port':parts.port})
                        round_report['readyz']=native.ui_ready(app_port)
                        raise RuntimeError('packaged WebView2 local UI target unavailable')
                    page=pages[0]
                    round_report['native_startup_target_verified']=True
                    round_report['explicit_cdp_navigation']=False
                    blocked=[]
                    def route(request):
                        if request.request.url.startswith(base+'/') or request.request.url.startswith(('data:','blob:')):
                            request.continue_()
                        else:
                            blocked.append(request.request.resource_type)
                            request.abort()
                    page.route('**/*',route)
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(type(error).__name__))
                    page.on('console',lambda entry:errors.append('console-error') if entry.type=='error' else None)
                    round_report['result']=owned_webview(page,base,root,round_no,
                        provider=args.provider, hide_cuda_devices=args.hide_cuda_devices,
                        expect_backend_unavailable=args.expect_backend_unavailable)
                    round_report['external_requests']=blocked
                    round_report['page_errors']=errors
                    if blocked or errors:
                        raise RuntimeError('unwanted webview external traffic or page error')
                except Exception as exc:
                    round_report['errors'].append(type(exc).__name__+': '+str(exc)[:220])
                finally:
                    if browser is not None:
                        try:browser.close()
                        except Exception:round_report['errors'].append('CDP_disconnect_failed')
                    if tree is not None and process is not None:
                        try:
                            tree.sample()
                            identities=owned_identities(tree)
                            if hwnd is None:
                                choices=native.owned_windows(set(identities))
                                if len(choices)==1:
                                    hwnd=choices[0]
                                    owner=native.owned_window_identity(
                                        process.pid,identities[process.pid],hwnd,identities)
                            if hwnd is not None and owner is not None and process.poll() is None:
                                round_report['wm_close']=native.close_owned_window(
                                    process.pid,identities[process.pid],hwnd,identities,owner)
                                round_report['confirmation']=(
                                    native.acknowledge_owned_close_confirmation(
                                        owner,hwnd,time.monotonic()+9)
                                    if round_report['wm_close'] else False)
                            if process.poll() is None:
                                try:
                                    round_report['exit_code']=process.wait(timeout=95)
                                except subprocess.TimeoutExpired:
                                    round_report['errors'].append('normal_exit_timeout; no force')
                            else:
                                round_report['exit_code']=process.returncode
                            round_report['ports_released']=(native.port_released(app_port)
                                                             and native.port_released(debug_port))
                        except Exception as exc:
                            round_report['errors'].append(type(exc).__name__+': ownership_close_gate')
                        finally:
                            round_report['process_tree']=tree.finish()
                    new_json(root/f'native-round-{round_no}.json',round_report)
                if (round_report['errors'] or round_report.get('exit_code')!=0
                        or not round_report.get('ports_released')
                        or not round_report.get('confirmation')
                        or (round_report['process_tree'] or {}).get('living')!=[]):
                    report['errors'].append(f'round {round_no} did not pass')
                    break
        report['original_source_unchanged']=all(
            fingerprint(root/name)==value for name,value in original.items())
        report['model_unchanged']=fingerprint(model)==original_model
        report['checker_before']=checker_before
        report['checker_unchanged']=fingerprint(Path(__file__))==checker_before
        report['passed']=(not report['errors'] and len(report['rounds'])==len(round_numbers)
                          and report['original_source_unchanged']
                          and report['model_unchanged'] and report['checker_unchanged'])
    except Exception as error:
        report['errors'].append(type(error).__name__+': '+str(error)[:220])
    finally:
        new_json(root/'result.json',report)
    print(json.dumps({'passed':report['passed'],'rounds':len(report['rounds']),
                      'errors':report['errors']},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--provider', choices=('auto','cuda','cpu'), default='cuda')
    parser.add_argument('--hide-cuda-devices', action='store_true')
    parser.add_argument('--expect-backend-unavailable', action='store_true')
    raise SystemExit(run(parser.parse_args()))
