"""Exercise fixture branches on the real product UI with an owned headless browser."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from playwright.sync_api import Error, expect, sync_playwright


def check(page, port):
    click = lambda name: page.get_by_test_id(name).click()
    label = page.get_by_test_id
    url = f'http://127.0.0.1:{port}'
    response = page.goto(url, wait_until='domcontentloaded')
    if response.status != 200:
        raise AssertionError(f'root HTTP {response.status}')
    if port in (22701, 22702):
        branch = 'stop-recovery' if port == 22701 else 'stop-uncertain'
        page.goto(f'{url}/_tone/{branch}', wait_until='domcontentloaded')
        click('shared-get-recovery-open')
        expect(label('shared-get-recovery-notice')).to_be_visible()
        if port == 22702:
            page.goto(url, wait_until='domcontentloaded')
            click('personal-fetch')
            expect(label('personal-notice')).to_contain_text(re.compile('確認|通信'))
    elif port == 22703:
        click('ranking-fetch')
        expect(label('ranking-notice')).to_contain_text(re.compile('失敗|失われ|取得でき'))
    elif port == 22704:
        expect(label('failed-empty')).to_be_visible()
    elif port == 22705:
        expect(label('failed-record').first).to_be_visible()
        if max(map(len, label('failed-record').all_text_contents())) < 200:
            raise AssertionError('Expected a long synthetic failure record')
    elif port == 22706:
        click('tab-dedup')
        click('duplicate-scan')
        expect(label('duplicate-status')).to_contain_text(re.compile('失敗|エラー|でき'))
    elif port == 22707:
        click('preferences-open')
        click('preferences-save')
        expect(label('preferences-status')).to_contain_text('設定を保存できません。入力を保持しています。')
    elif port == 22708:
        click('view-history-open')
        expect(label('view-history-dialog')).to_contain_text('Synthetic history')
        page.keyboard.press('Escape')
        click('tab-history')
        expect(label('backup-grid')).to_contain_text('Synthetic backup')
    elif port == 22709:
        row = label('job-row').filter(has_text='Synthetic resumable job')
        expect(row).to_have_attribute('data-state', 'paused')
        row.get_by_test_id('job-resume').click()
        expect(row).to_have_attribute('data-state', 'running')
        row.get_by_test_id('job-pause').click()
        expect(row).to_have_attribute('data-state', 'paused')
        click('tab-failed')
        expect(label('failed-record').first).to_be_visible()
        click('tab-dedup')
        click('duplicate-scan')
        expect(label('duplicate-status')).not_to_contain_text('まだスキャンしていません')
        click('tab-recommend')
        click('recommend-update')
        expect(label('recommend-notice')).to_contain_text('おすすめを更新しました')
        expect(page.get_by_text('Synthetic recommendation', exact=True).first).to_be_visible()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    if args.run.resolve().parent != Path(r'S:\Work\_tmp\PV\cosprep-20261124'):
        parser.error('Expected an owned run directory')
    blocked = 0

    def route(request):
        nonlocal blocked
        url = urlsplit(request.request.url)
        if url.hostname == '127.0.0.1' and url.port in range(22701, 22710):
            request.continue_()
        else:
            blocked += 1
            request.abort()

    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            args.run / 'browser', channel='msedge', headless=True,
            viewport={'width': 1280, 'height': 800}, service_workers='block',
            args=['--disable-background-networking', '--disable-component-update',
                  '--no-first-run', '--disable-sync', '--no-default-browser-check'],
        )
        browser.set_default_timeout(6000)
        expect.set_options(timeout=6000)
        browser.route('**/*', route)
        try:
            for port in range(22701, 22710):
                page = browser.new_page()
                try:
                    check(page, port)
                except (AssertionError, Error) as error:
                    failures += 1
                    # Output only the assertion/selector, never the product payload.
                    print(port, 'FAIL', str(error).splitlines()[0])
                else:
                    print(port, 'PASS')
                finally:
                    page.close()
        finally:
            browser.close()
    opener = build_opener(ProxyHandler({}))
    denied = 0
    for port in range(22701, 22710):
        local_key = (args.run / str(port) / 'control.txt').read_text(encoding='ascii').strip()
        request = Request(f'http://127.0.0.1:{port}/_smoke/network', headers={'x-acceptance': local_key})
        with opener.open(request, timeout=5) as response:
            denied += json.load(response)['denied']
    print('server external connections denied:', denied)
    print('browser external requests denied:', blocked)
    return int(bool(failures or blocked or denied))


if __name__ == '__main__':
    raise SystemExit(main())
