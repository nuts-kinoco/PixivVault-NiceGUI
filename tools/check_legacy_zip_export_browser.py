"""Actual Edge/Chromium: author new+updated -> legacy-compatible ZIP, no overwrite.

Browser I/O is loopback only. Pixiv GETs are handled by the existing fully
synthetic fixture; no actual account, Cookie, source image or legacy DB is used.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import traceback
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import Image

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_author_download_browser import inspect as author_inspect
from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      source_hashes, stop_child, wait_for_health)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_zip(export_root, managed_root):
    from pixivvault_nicegui.legacy_zip_export import EXPORT_IDENTITY, EXPORT_MARKER
    assert json.loads((export_root / EXPORT_MARKER).read_text()) == EXPORT_IDENTITY
    archives = list(export_root.glob('*.zip'))
    assert len(archives) == 1
    archive_path = archives[0]
    assert archive_path.name == '架空の直接作者(24).zip'
    index = json.loads((managed_root / 'active-generations.json').read_text())
    current = index['entries']['321']
    manifest = json.loads((managed_root / current['relative_receipt']).read_text())
    assert manifest['page_count'] == 2
    with ZipFile(archive_path, 'r') as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            '架空のID指定作品(321)_1.png', '架空のID指定作品(321)_2.png'}
        for number, expect_color in enumerate(((74, 28, 90), (17, 89, 141))):
            member = f'架空のID指定作品(321)_{number+1}.png'
            info = archive.getinfo(member)
            assert info.compress_type == ZIP_DEFLATED
            payload = archive.read(member)
            with Image.open(BytesIO(payload)) as image:
                assert image.size == (120, 84)
                assert image.getpixel((0, 0)) == expect_color
            source_page = ((managed_root / current['relative_receipt']).parent /
                           manifest['files'][number]['name']).read_bytes()
            assert payload == source_page
    assert not list((export_root / '.incomplete').glob('zip-*'))
    return archive_path, digest(archive_path)


def inspect(page, base, headers, managed, output, *, restart,
            expected_metadata_revision=None):
    from playwright.sync_api import expect
    # Existing author's browser acceptance saves NEW, then UPDATED generation;
    # it checks image decode, keyboard, focus, restart and zero extra Pixiv GET.
    # Reuse the manual two-generation author contract, not the later Phase9
    # scheduler's 70-work batch. The latter is independently covered by the
    # standalone author checker and would change the ZIP contents and pixels.
    details = author_inspect(page, base, headers, managed, restart=restart,
                             scheduler_acceptance=False,
                             expected_metadata_revision=expected_metadata_revision)
    # Establish the current tab explicitly; the composed helper has its own
    # navigation sequence and must not implicitly control this export check.
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    expect(library).to_be_visible()
    button = library.get_by_test_id('library-legacy-zip-export')
    expect(button).to_be_visible()
    expect(button).to_be_enabled()
    if not restart:
        assert not list(output.glob('*.zip'))
        button.click()
        expect(library.get_by_test_id('library-legacy-zip-status')).to_contain_text(
            '作成しました', timeout=30000)
        archive, prior = verify_zip(output, managed)
    else:
        archive, prior = verify_zip(output, managed)
        button.click()
        expect(library.get_by_test_id('library-legacy-zip-status')).to_contain_text(
            '出力済み', timeout=30000)
        assert archive.is_file() and digest(archive) == prior
    # A second client has independent selected-author state. A stale tab
    # cannot substitute another user's library selection for this action.
    other = page.context.new_page()
    try:
        other.goto(base, wait_until='domcontentloaded')
        other.get_by_test_id('tab-library').click()
        button_other = other.get_by_test_id('library-legacy-zip-export')
        expect(button_other).not_to_be_visible()
    finally:
        other.close()
    status = page.request.get(base + '/_managed_test/status', headers=headers).json()
    assert status['original_gets'].get('321', 0) == (0 if restart else 4)
    if restart:
        assert status['request_count'] == 0
    details.update({'exported_zip_sha256': prior, 'zip_members': 2,
                    'zip_non_overwrite': True})
    return details


def run(channel, output):
    if output.exists():
        raise ValueError('new --output directory required')
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True)
    before = source_hashes()
    result = {'success': False, 'synthetic_only': True, 'channel': channel,
              'viewports': [], 'page_errors': [], 'console_errors': [],
              'external_requests': [], 'normal_exit': [], 'ports_released': []}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel == 'chromium' else channel)
            try:
                for width, height in ((1280, 720), (1920, 1080)):
                    managed = (output / f'managed-{width}x{height}').resolve()
                    exported = (output / f'export-{width}x{height}').resolve()
                    managed.mkdir()
                    expected_metadata_revision = None
                    for restart in (False, True):
                        port = free_port()
                        nonce = secrets.token_hex(16)
                        base = f'http://127.0.0.1:{port}'
                        headers = {'X-Acceptance': nonce}
                        process = context = None
                        try:
                            with (output / f'server-{width}-{restart}.log').open('wb') as log:
                                process = subprocess.Popen([
                                    sys.executable,
                                    str(Path(__file__).with_name('check_managed_downloads_browser.py')),
                                    '--serve', str(port), '--root', str(managed),
                                    '--nonce', nonce, '--zip-export-root', str(exported),
                                ], env=child_environment(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=log)
                            wait_for_health(base, process, expected_data='pixiv-readonly')
                            context = browser.new_context(viewport={'width': width, 'height': height})
                            def restrict_browser(route):
                                parsed = urlsplit(route.request.url)
                                if (parsed.scheme, parsed.netloc) == ('http', f'127.0.0.1:{port}'):
                                    route.continue_()
                                else:
                                    result['external_requests'].append('blocked')
                                    route.abort()
                            context.route('**/*', restrict_browser)
                            page = context.new_page()
                            page.on('console', lambda event: result['console_errors'].append(event.text[:200])
                                    if event.type == 'error' else None)
                            page.on('pageerror', lambda error: result['page_errors'].append(str(error)[:200]))
                            result['viewports'].append({'viewport': [width, height],
                                **inspect(page, base, headers, managed, exported, restart=restart,
                                          expected_metadata_revision=expected_metadata_revision)})
                            if not restart:
                                expected_metadata_revision = result['viewports'][-1]['metadata']['settings_revision']
                            context.close()
                            context = None
                            stop = Request(base + '/_managed_test/shutdown', data=b'',
                                           headers=headers, method='POST')
                            with build_opener(ProxyHandler({})).open(stop, timeout=5):
                                pass
                            # Match the already-accepted Phase8 Windows
                            # graceful shutdown budget, not a forced cleanup.
                            process.wait(timeout=25)
                            result['normal_exit'].append(process.returncode == 0)
                        finally:
                            if context is not None:
                                context.close()
                            stop_child(process)
                            result['ports_released'].append(port_released(port))
            finally:
                browser.close()
        assert len(result['viewports']) == 4
        assert all(result['normal_exit']) and all(result['ports_released'])
        assert not result['console_errors'] and not result['page_errors']
        assert not result['external_requests']
        assert before == source_hashes()
        result['success'] = True
    except Exception as exc:
        result['failure'] = f'{type(exc).__name__}: {str(exc)[:400]}'
        # Synthetic-only diagnostics; file basenames/lines cannot leak Cookie
        # paths, user IDs, image bodies or authenticated API responses.
        result['failure_frames'] = [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                                    for frame in traceback.extract_tb(exc.__traceback__)[-7:]]
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['success'] else 1


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    raise SystemExit(run(args.channel, args.output))
