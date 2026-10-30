"""Offline Edge regression for V03 retained frames and original error recovery.

Uses the existing V03 generated-image fixture and blocks selected localhost
image responses. No account, Cookie, external network or original files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_v03_original_browser import serve
from tools.run_browser_checks import (ROOT, child_environment, free_port,
                                      port_released, stop_child, wait_for_health)


OWNED = (
    'src/pixivvault_nicegui/image_original.py',
    'src/pixivvault_nicegui/ui/artwork_viewer.py',
    'src/pixivvault_nicegui/ui/viewer_controller.py',
    'src/pixivvault_nicegui/ui/verified_image.py',
    'tests/test_viewer_original_quality.py',
)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in OWNED}


def serve_preflight(port: int, nonce: str, fault: str) -> None:
    """Inject errors before the browser image source can be assigned."""
    from pixivvault_nicegui.media import DemoImageCatalog

    if fault == 'url':
        original_url = DemoImageCatalog.url

        def faulty_url(self, work_id, page_no=0, variant='view'):
            if work_id == '102' and variant == 'view':
                # LibraryImageRegistry treats ValueError as missing-demo
                # fallback; RuntimeError represents a closed URL provider.
                raise RuntimeError('synthetic provider URL unavailable')
            return original_url(self, work_id, page_no, variant)

        DemoImageCatalog.url = faulty_url
    elif fault == 'is_animation':
        def faulty_animation(self, work_id):
            if work_id == '102':
                raise RuntimeError('synthetic provider animation check unavailable')
            return False

        DemoImageCatalog.is_animation = faulty_animation
    else:
        raise ValueError('unknown synthetic preflight fault')
    serve(port, nonce)


def run_preflight(output: Path) -> int:
    """Two preflight faults with one Edge browser, isolated local child servers."""
    output = output.resolve()
    allowed = (ROOT / 'artifacts' / 'p3-315-v03-original').resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        raise ValueError('fresh V03-only artifact directory required')
    output.mkdir(parents=True)
    before = hashes()
    cases = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            for fault in ('url', 'is_animation'):
                case = {'fault': fault, 'success': False, 'page_errors': [],
                        'console_errors': [], 'external_requests': []}
                evidence = output / fault
                evidence.mkdir()
                port, nonce = free_port(), secrets.token_hex(24)
                origin = f'http://127.0.0.1:{port}'
                context = browser.new_context(viewport={'width': 1280, 'height': 720})
                page = context.new_page()
                page.on('pageerror', lambda error: case['page_errors'].append(str(error)))
                page.on('console', lambda msg: case['console_errors'].append(msg.text)
                        if msg.type == 'error' else None)

                def route(request_route):
                    actual = urlsplit(request_route.request.url)
                    if (actual.scheme, actual.netloc) == ('http', f'127.0.0.1:{port}'):
                        request_route.continue_()
                    else:
                        case['external_requests'].append(actual.netloc)
                        request_route.abort()

                context.route('**/*', route)
                process = None
                with (evidence / 'server-stderr.log').open('wb') as stderr:
                    try:
                        process = subprocess.Popen(
                            [sys.executable, '-B', str(Path(__file__).resolve()),
                             '--serve', str(port), '--nonce', nonce, '--preflight-fault', fault],
                            cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=stderr,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                        )
                        case['pid'] = process.pid
                        wait_for_health(origin, process)
                        page.goto(origin, wait_until='domcontentloaded')
                        page.locator('[data-work-id="101"]').click()
                        viewer = page.get_by_test_id('gallery-viewer')
                        quality = viewer.get_by_test_id('viewer-quality-toggle')
                        images = viewer.get_by_test_id('viewer-image')
                        expect(quality).to_be_enabled(timeout=15000)
                        expect(images.locator('img[data-pv-displayed="true"]')).to_have_count(1)
                        old_url = images.locator('img[data-pv-displayed="true"]').get_attribute('src')
                        old_serial = viewer.get_attribute('data-save-generation')
                        # Dispatch the user's new work selection while the viewer
                        # overlay is mounted. Do not close and clear the old frame.
                        page.locator('[data-work-id="102"]').dispatch_event('click')
                        expect(viewer.locator('.pv-counter')).to_have_text('1/1')
                        expect(viewer.locator('.pv-viewer-status')).to_contain_text(
                            '画像を準備できませんでした')
                        expect(images.locator('img[data-pv-displayed="true"]')).to_have_count(0)
                        expect(viewer).to_have_attribute('data-zoom-ready', 'false')
                        expect(viewer).to_have_attribute('data-viewer-quality', 'view')
                        expect(quality).to_be_disabled()
                        expect(viewer.get_by_test_id('viewer-download')).to_be_disabled()
                        assert old_serial != viewer.get_attribute('data-save-generation')
                        assert '/101/0/view.png' in old_url
                        assert not case['page_errors'] and not case['external_requests']
                        page.screenshot(path=str(evidence / 'preflight-failure.png'))
                        case['success'] = True
                    except Exception as error:
                        case['failure'] = f'{type(error).__name__}: {error}'[:1000]
                        try:
                            page.screenshot(path=str(evidence / 'failure.png'))
                        except Exception:
                            pass
                    finally:
                        if process is not None and process.poll() is None:
                            try:
                                response = page.request.post(
                                    origin + '/_fixture/shutdown',
                                    headers={'X-Acceptance': nonce}, timeout=6000)
                                case['shutdown_http'] = response.status
                                process.wait(timeout=15)
                            except Exception:
                                pass
                        context.close()
                        if process is not None:
                            stop_child(process)
                            case['exit_code'] = process.returncode
                            case['normal_exit'] = process.returncode == 0
                        case['port_released'] = port_released(port)
                        case['success'] &= case.get('normal_exit', False) and case['port_released']
                        (evidence / 'result.json').write_text(
                            json.dumps(case, indent=2, ensure_ascii=False), encoding='utf8')
                        cases.append(case)
        finally:
            browser.close()
    source_changed = before != hashes()
    passed = len(cases) == 2 and all(case['success'] for case in cases) and not source_changed
    manifest = {'success': passed, 'source_changed': source_changed, 'cases': cases}
    (output / 'result.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'success': passed, 'cases': [dict(fault=c['fault'],
                success=c['success'], failure=c.get('failure'),
                normal_exit=c.get('normal_exit'), port_released=c.get('port_released'))
                for c in cases], 'source_changed': source_changed}))
    return 0 if passed else 1


def run(output: Path) -> int:
    output = output.resolve()
    allowed = (ROOT / 'artifacts' / 'p3-315-v03-original').resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        raise ValueError('fresh V03-only artifact directory required')
    output.mkdir(parents=True)
    before = hashes()
    port, nonce = free_port(), secrets.token_hex(24)
    origin = f'http://127.0.0.1:{port}'
    result = {'success': False, 'external_requests': [], 'expected_failures': [],
              'page_errors': [], 'console_errors': []}
    proc = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 720})
        page = context.new_page()
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        page.on('console', lambda msg: result['console_errors'].append(msg.text)
                if msg.type == 'error' else None)
        blocked = set()

        def intercept(route):
            url = urlsplit(route.request.url)
            if (url.scheme, url.netloc) != ('http', f'127.0.0.1:{port}'):
                result['external_requests'].append(url.netloc)
                route.abort()
            elif url.path in blocked:
                result['expected_failures'].append(url.path)
                route.abort()
            else:
                route.continue_()

        context.route('**/*', intercept)
        with (output / 'server-stderr.log').open('wb') as stderr:
            try:
                proc = subprocess.Popen(
                    [sys.executable, '-B', str(Path(__file__).resolve()),
                     '--serve', str(port), '--nonce', nonce], cwd=ROOT,
                    env=child_environment(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=stderr,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                )
                result['pid'] = proc.pid
                wait_for_health(origin, proc)
                page.goto(origin, wait_until='domcontentloaded')
                page.locator('[data-work-id="101"]').click()
                viewer = page.get_by_test_id('gallery-viewer')
                quality = viewer.get_by_test_id('viewer-quality-toggle')
                stage = viewer.get_by_test_id('viewer-image')
                front = stage.locator('img[data-pv-displayed="true"]')
                expect(quality).to_be_enabled(timeout=15000)
                assert front.evaluate('image => image.naturalWidth === 640')

                # Original GET fails: previous normal pixels can remain, but
                # the quality label must admit the actual displayed frame.
                blocked.add('/media/101/0/original.png')
                quality.click()
                expect(viewer).to_have_attribute('data-viewer-quality', 'view')
                expect(quality).to_contain_text('通常画質で再表示')
                expect(viewer.locator('.pv-viewer-status')).to_contain_text(
                    '原寸画像を読み込めませんでした')
                assert front.evaluate('image => image.naturalWidth === 640')
                page.screenshot(path=str(output / 'original-failure.png'))
                quality.click()  # Explicit recovery via normal image request.
                expect(quality).to_contain_text('原寸を表示')
                expect(viewer.locator('.pv-viewer-status')).to_be_hidden()
                assert front.evaluate('image => image.naturalWidth === 640')

                # A failed new page must leave zero displayed frames under
                # new-page metadata, never a retained page-zero bitmap.
                blocked.add('/media/101/1/view.png')
                viewer.get_by_role('button', name='次のページ', exact=True).click()
                expect(viewer.locator('.pv-counter')).to_have_text('2/2')
                expect(viewer.locator('.pv-viewer-status')).to_contain_text(
                    '画像を読み込めませんでした')
                expect(stage.locator('img[data-pv-displayed="true"]')).to_have_count(0)
                assert stage.get_attribute('retain_previous') is None
                page.screenshot(path=str(output / 'new-page-failure.png'))
                assert '/media/101/0/original.png' in result['expected_failures']
                assert '/media/101/1/view.png' in result['expected_failures']
                assert not result['external_requests'] and not result['page_errors']
                result['success'] = True
            except Exception as exc:
                result['failure'] = f'{type(exc).__name__}: {exc}'[:1000]
                try:
                    page.screenshot(path=str(output / 'failure.png'))
                except Exception:
                    pass
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        response = page.request.post(
                            origin + '/_fixture/shutdown', headers={'X-Acceptance': nonce},
                            timeout=6000)
                        result['shutdown_http'] = response.status
                        proc.wait(timeout=15)
                    except Exception:
                        pass
                context.close()
                browser.close()
                if proc is not None:
                    stop_child(proc)
                    result['exit_code'] = proc.returncode
                    result['normal_exit'] = proc.returncode == 0
                result['port_released'] = port_released(port)
    result['source_changed'] = before != hashes()
    result['success'] = (result['success'] and result.get('normal_exit', False)
                         and result['port_released'] and not result['source_changed'])
    (output / 'result.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({key: result.get(key) for key in
                      ('success', 'failure', 'expected_failures', 'normal_exit',
                       'port_released', 'source_changed')}))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--preflight-fault', choices=('url', 'is_animation'))
    args = parser.parse_args()
    if args.serve is not None:
        if not args.nonce:
            parser.error('nonce required')
        if args.preflight_fault:
            serve_preflight(args.serve, args.nonce, args.preflight_fault)
        else:
            serve(args.serve, args.nonce)
    else:
        if args.output is None:
            parser.error('output required')
        raise SystemExit(run_preflight(args.output) if args.preflight else run(args.output))
