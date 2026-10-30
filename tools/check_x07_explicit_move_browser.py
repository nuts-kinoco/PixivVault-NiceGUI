"""X07 opt-in physical move and GUI restore on the owned synthetic product shell.

This checker uses the generated X07 server fixture, with no actual Pixiv
requests, real IDs, original Flet data, or OS trash changes.
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
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, wait_for_health


def run(output: Path, channel: str) -> int:
    from playwright.sync_api import expect, sync_playwright

    output = output.resolve()
    permitted = (ROOT / 'artifacts' / 'p3-318-x07-quarantine').resolve()
    if output == permitted or not output.is_relative_to(permitted) or output.exists():
        raise ValueError('output must be a fresh owned X07 artifact directory')
    output.mkdir(parents=True)
    fixture = output / 'fixture'
    port = free_port()
    nonce = secrets.token_hex(24)
    base = f'http://127.0.0.1:{port}'
    headers = {'X-Acceptance': nonce}
    before = source_hashes()
    checker = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fixture_checker = hashlib.sha256((ROOT / 'tools/check_managed_quarantine_browser.py').read_bytes()).hexdigest()
    report = dict(success=False, synthetic_only=True, channel=channel,
                  checker_sha256=checker, fixture_checker_sha256=fixture_checker,
                  source_before=before, state_before=None, state_after_move=None,
                  state_after_restore=None, move_ui=False, restore_ui=False,
                  normal_exit=False, port_released=False, forced_cleanup=False,
                  page_errors=[], console_errors=[], foreign_requests=[])
    process = None
    opener = build_opener(ProxyHandler({}))
    try:
        with (output / 'server.log').open('xb') as stream:
            env = child_environment()
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            process = subprocess.Popen([
                sys.executable, '-B', str(ROOT / 'tools/check_managed_quarantine_browser.py'),
                '--serve', str(port), '--root', str(fixture), '--nonce', nonce
            ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
               stdout=stream, stderr=stream,
               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            report['owned_pid'] = process.pid
        wait_for_health(base, process)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=None if channel == 'chromium' else channel)
            context = browser.new_context(viewport={'width': 1280, 'height': 720})
            def block_foreign(route):
                parsed = urlsplit(route.request.url)
                if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                    report['foreign_requests'].append('blocked')
                    route.abort()
                else:
                    route.continue_()
            context.route('**/*', block_foreign)
            page = context.new_page()
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.on('console', lambda message: report['console_errors'].append(message.text)
                    if message.type == 'error' else None)
            try:
                def state():
                    for _ in range(5):
                        response = page.request.get(base + '/_x07/state', headers=headers)
                        if response.status == 409:
                            page.wait_for_timeout(60)
                            continue
                        assert response.status == 200, response.status
                        return response.json()
                    raise AssertionError('revision unstable')

                def wait_for(predicate, explanation):
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        value = state()
                        if predicate(value):
                            return value
                        page.wait_for_timeout(100)
                    raise AssertionError(explanation)

                page.goto(base, wait_until='domcontentloaded')
                initial = state()
                assert initial['original_unchanged']
                assert initial['source_pages']['123'] == [0, 1, 2]
                originals = list((fixture / 'managed' / 'outputs').glob('work-123-*/page-2.png'))
                assert len(originals) == 1, 'generated original page must have unique owned path'
                source_page = originals[0]
                assert source_page.is_file() and not source_page.is_symlink()
                original_bytes_sha256 = hashlib.sha256(source_page.read_bytes()).hexdigest()
                report['state_before'] = dict(pages=initial['source_pages']['123'],
                                              excluded=initial['excluded_count'],
                                              operations=initial['operation_count'],
                                              original_unchanged=initial['original_unchanged'])
                page.get_by_test_id('tab-dedup').click()
                page.get_by_test_id('duplicate-scan').click()
                expect(page.get_by_test_id('duplicate-mode-near')).to_be_enabled()
                page.get_by_test_id('duplicate-mode-near').click()
                chosen = page.get_by_test_id('duplicate-select-123-2')
                chosen.click()
                expect(chosen).to_be_checked()
                page.get_by_test_id('duplicate-group-move').first.click()
                dialog = page.get_by_test_id('duplicate-confirm-dialog')
                expect(dialog).to_be_visible()
                choice = page.get_by_test_id('duplicate-physical-move')
                expect(choice).not_to_be_checked()
                # Quasar renders a div role=checkbox, not an input; exercise
                # its ordinary user-click event and assert the settled state.
                choice.click()
                expect(choice).to_be_checked()
                page.get_by_test_id('duplicate-confirm-execute').click()
                after_move = wait_for(lambda s: s['source_pages']['123'] == [0, 2]
                    and s['excluded_count'] == 1,
                    'confirmed physical move did not settle')
                # Product's source state returns 'moved', while the fixture
                # status counts only state='committed'. Read the real owned
                # durable ledger rather than treating that summary as a move count.
                ledger_path = fixture / 'managed' / 'active-generations.json'
                move_ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
                move_items = list(move_ledger['quarantine_operations'].items())
                move_ops = [row for _, row in move_items]
                assert len(move_ops) == 1
                assert move_ops[0]['mode'] == 'move' and move_ops[0]['state'] == 'moved'
                # A logical exclusion / 'moved' status is not physical removal.
                # Inspect the exact original generated file and relocated bytes.
                moved_copy = fixture / 'managed' / 'quarantine' / 'operations' / move_items[0][0] / '123-p2.png'
                assert not source_page.exists() and not source_page.is_symlink(), 'original physical page still exists after move'
                assert moved_copy.is_file() and not moved_copy.is_symlink()
                assert hashlib.sha256(moved_copy.read_bytes()).hexdigest() == original_bytes_sha256
                expect(page.get_by_test_id('duplicate-status')).to_contain_text('物理移動しました')
                report['state_after_move'] = dict(pages=after_move['source_pages']['123'],
                    excluded=after_move['excluded_count'], operations=after_move['operation_count'],
                    original_unchanged=after_move['original_unchanged'])
                report['move_ui'] = True
                page.screenshot(path=str(output / 'move-synthetic.png'), animations='disabled')

                page.get_by_test_id('duplicate-trash').click()
                trash = page.get_by_test_id('duplicate-trash-dialog')
                expect(trash).to_be_visible()
                restore = page.get_by_test_id('duplicate-restore-choice')
                expect(restore).to_have_count(1)
                restore.click()
                restore_dialog = page.get_by_test_id('duplicate-restore-dialog')
                expect(restore_dialog).to_be_visible()
                page.get_by_test_id('duplicate-restore-execute').click()
                after_restore = wait_for(lambda s: s['source_pages']['123'] == [0, 1, 2]
                    and s['excluded_count'] == 0 and s['original_unchanged'],
                    'explicit restore did not return original pages and verified bytes')
                restored_ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
                restored_ops = list(restored_ledger['quarantine_operations'].values())
                assert len(restored_ops) == 1 and restored_ops[0]['state'] == 'restored'
                assert source_page.is_file() and not source_page.is_symlink(), 'physical original not restored'
                assert hashlib.sha256(source_page.read_bytes()).hexdigest() == original_bytes_sha256
                expect(page.get_by_test_id('duplicate-status')).to_contain_text('復元が完了しました')
                report['state_after_restore'] = dict(pages=after_restore['source_pages']['123'],
                    excluded=after_restore['excluded_count'], operations=after_restore['operation_count'],
                    original_unchanged=after_restore['original_unchanged'])
                report['restore_ui'] = True
                page.screenshot(path=str(output / 'restored-synthetic.png'), animations='disabled')
            finally:
                context.close()
                browser.close()
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()[-4500:]
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    request = Request(base + '/_x07/shutdown', data=b'', headers=headers, method='POST')
                    with opener.open(request, timeout=5) as response:
                        report['shutdown_status'] = response.status
                except Exception as error:
                    report['shutdown_error'] = type(error).__name__
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    report['forced_cleanup'] = True
                    process.terminate()
                    process.wait(timeout=5)
            report['normal_exit'] = process.returncode == 0 and not report['forced_cleanup']
        report['port_released'] = port_released(port)
        report['source_after'] = source_hashes()
        report['source_changed'] = report['source_before'] != report['source_after']
        report['success'] = bool(report['move_ui'] and report['restore_ui']
                    and report['normal_exit'] and report['port_released']
                    and not report['source_changed'] and not report['page_errors']
                    and not report['console_errors'] and not report['foreign_requests'])
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in
        ('success','move_ui','restore_ui','normal_exit','port_released','source_changed')},
        ensure_ascii=False))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--channel', choices=('chromium', 'msedge'), default='chromium')
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.channel))
