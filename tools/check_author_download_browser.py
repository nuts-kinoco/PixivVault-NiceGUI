"""Real Edge synthetic author-ID/follow selection to managed originals and viewer.

Only fake Pixiv data and a disposable managed root are used. The loopback
browser cannot contact any non-local address. No real account IDs are logged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import urlsplit

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def verify_saved(root):
    """Require the active v2 generation, not just an orphan on disk."""
    import hashlib
    from io import BytesIO
    from PIL import Image
    index = json.loads((root / 'active-generations.json').read_text(encoding='utf-8'))
    assert type(index['version']) is int and index['version'] == 1
    assert tuple(index['entries']) == ('321',)
    selected = index['entries']['321']
    assert selected['update_date'] == '2026-10-24T10:00:00+09:00'
    relative = selected['relative_receipt']
    assert relative.startswith('generations/work-321-') and relative.endswith('/receipt.json')
    receipt_path = root / relative
    data = receipt_path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == selected['sha256']
    receipt = json.loads(data)
    assert receipt['version'] == 1 and receipt['work_id'] == '321'
    assert receipt['page_count'] == 2
    versions = sorted((root / 'generations').glob('work-321-*/receipt.json'))
    assert len(versions) == 2, 'updated generation must retain its old receipt'
    for row_no, row in enumerate(receipt['files']):
        source = (receipt_path.parent / row['name']).read_bytes()
        assert hashlib.sha256(source).hexdigest() == row['sha256']
        with Image.open(BytesIO(source)) as decoded:
            assert decoded.size == (120, 84)
            assert decoded.getpixel((0, 0)) == (
                (74, 28, 90) if row_no == 0 else (17, 89, 141))
    for historical in versions:
        assert json.loads(historical.read_text())['work_id'] == '321'
    return receipt


def inspect(page, base, headers, root, *, restart):
    from playwright.sync_api import expect
    page.goto(base, wait_until='domcontentloaded')
    if not restart:
        page.get_by_test_id('tab-download').click()
        view = page.get_by_test_id('managed-download-view')
        expect(view).to_be_visible()
        author_id = view.get_by_test_id('managed-download-author-id')
        author_id.fill('bad')
        view.get_by_test_id('managed-download-author-add').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('正しい作者ID')
        author_id.fill('24')
        view.get_by_test_id('managed-download-author-add').click()
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        view.get_by_test_id('managed-download-follow-refresh').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('フォロー作者 1 件', timeout=20000)
        view.get_by_test_id('managed-download-follow-search').fill('合成フォロー')
        follow = view.get_by_test_id('managed-download-follow-select')
        expect(follow).to_have_count(1)
        follow.click()  # Same author does not double-select or replace ID origin.
        expect(view.get_by_test_id('managed-download-author-selection')).to_have_text('選択作者 1 件')
        view.get_by_test_id('managed-download-author-fetch').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text('新規 1 件', timeout=25000)
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')
        other = page.context.new_page()
        try:
            other.goto(base, wait_until='domcontentloaded')
            other.get_by_test_id('tab-download').click()
            another = other.get_by_test_id('managed-download-view')
            expect(another.get_by_test_id('managed-download-selection')).to_have_text('対象 0 件')
        finally:
            other.close()
        view.get_by_test_id('managed-download-submit').click()
        expect(view.get_by_test_id('managed-download-notice')).to_contain_text('1件をキュー', timeout=20000)

    page.get_by_test_id('tab-queue').click()
    queue = page.get_by_test_id('queue-view')
    expect(queue.get_by_test_id('queue-filter-done')).to_contain_text(
        '完了 2' if restart else '完了 1', timeout=30000)
    if not restart:
        # Change only the synthetic GET fixture: no Pixiv write is involved.
        page.request.post(base + '/_managed_test/control?author_updated=true', headers=headers)
        page.get_by_test_id('tab-download').click()
        view = page.get_by_test_id('managed-download-view')
        view.get_by_test_id('managed-download-author-fetch').click()
        expect(view.get_by_test_id('managed-download-author-status')).to_contain_text(
            '更新 1 件', timeout=25000)
        expect(view.get_by_test_id('managed-download-selection')).to_have_text('対象 1 件')
        view.get_by_test_id('managed-download-submit').click()
        expect(view.get_by_test_id('managed-download-notice')).to_contain_text(
            '1件をキュー', timeout=20000)
        page.get_by_test_id('tab-queue').click()
        expect(queue.get_by_test_id('queue-filter-done')).to_contain_text('完了 2', timeout=30000)
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    if not restart:
        library.get_by_test_id('library-db-refresh').click()
    author = library.locator('.pv-library-author-card[data-author-id="24"]')
    expect(author).to_be_visible(timeout=20000)
    author.get_by_test_id('library-author-open').click()
    cards = library.locator('.pv-library-page-card[data-work-id="321"]')
    expect(cards).to_have_count(2)
    cards.first.click()
    viewer = page.get_by_test_id('library-viewer')
    image = viewer.locator('img:visible')
    for index in range(2):
        expect(viewer.locator('.pv-counter')).to_have_text(f'{index+1}/2')
        image.evaluate('img => img.decode()')
        expect(image).to_have_attribute('src', re.compile(rf'/media/321/{index}/view\.png\?catalog='))
        if index == 0:
            page.keyboard.press('ArrowRight')
    viewer.get_by_role('button', name='閉じる', exact=True).click()
    expect(cards.first).to_be_focused()
    assert verify_saved(root)['page_count'] == 2
    status = page.request.get(base + '/_managed_test/status', headers=headers).json()
    assert status['all_requests_get'] and status['allowed_https_hosts_only']
    assert status['image_requests_cookie_free']
    assert status['original_gets'].get('321', 0) == (0 if restart else 4)
    if restart:
        assert status['request_count'] == 0
    return {'restart': restart, 'pages': 2, 'viewer_decode_and_focus': True,
            'original_gets': status['original_gets'].get('321', 0)}


def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    results = {'success': False, 'synthetic_only': True, 'viewports': [],
               'channel': channel,
               'console_errors': [], 'page_errors': [], 'foreign_requests': [],
               'normal_exit': [], 'ports_released': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel == 'chromium' else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    root = (output / f'author-{width}x{height}').resolve()
                    root.mkdir()
                    for restart in (False, True):
                        port, nonce = free_port(), secrets.token_hex(16)
                        base, headers = f'http://127.0.0.1:{port}', {'X-Acceptance': nonce}
                        process, context = None, None
                        try:
                            with (output / f'server-{width}-{restart}.log').open('wb') as log:
                                process = subprocess.Popen([
                                    sys.executable,
                                    str(Path(__file__).with_name('check_managed_downloads_browser.py')),
                                    '--serve', str(port), '--root', str(root), '--nonce', nonce,
                                ], env=child_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(base, process, expected_data='pixiv-readonly')
                            context = browser.new_context(viewport={'width': width, 'height': height})
                            def block_external(route):
                                parsed = urlsplit(route.request.url)
                                if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                                    route.continue_()
                                else:
                                    results['foreign_requests'].append('blocked')
                                    route.abort()
                            context.route('**/*', block_external)
                            page = context.new_page()
                            page.on('console', lambda m: results['console_errors'].append(m.text[:250])
                                    if m.type == 'error' else None)
                            page.on('pageerror', lambda e: results['page_errors'].append(str(e)[:250]))
                            results['viewports'].append({'viewport': [width, height],
                                                         **inspect(page, base, headers, root, restart=restart)})
                            context.close(); context = None
                            from urllib.request import Request, ProxyHandler, build_opener
                            request = Request(base + '/_managed_test/shutdown', data=b'',
                                              headers=headers, method='POST')
                            with build_opener(ProxyHandler({})).open(request, timeout=5):
                                pass
                            process.wait(timeout=15)
                            results['normal_exit'].append(process.returncode == 0)
                        finally:
                            if context is not None:
                                context.close()
                            stop_child(process)
                            results['ports_released'].append(port_released(port))
            finally:
                browser.close()
        assert len(results['viewports']) == 4
        assert not results['console_errors'] and not results['page_errors']
        assert not results['foreign_requests']
        assert all(results['normal_exit']) and all(results['ports_released'])
        assert before == source_hashes()
        results['success'] = True
    except Exception as exc:
        results['failure'] = f'{type(exc).__name__}: {str(exc)[:400]}'
    results['source_hashes'] = before
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if results['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    raise SystemExit(run(args.channel, args.output))
