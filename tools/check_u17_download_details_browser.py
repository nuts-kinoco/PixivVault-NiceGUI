"""U17 real Edge mouse/keyboard/resize/tab acceptance, generated offline account.

Reuses the proven managed-download synthetic server and run lifecycle; never
contacts Pixiv, touches real data or queues a download from this checker.
"""
from __future__ import annotations

import argparse
from pathlib import Path

if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect
from tools import check_direct_work_browser as runner


def inspect(page, url, headers, root, restart=False):
    page.goto(url, wait_until='domcontentloaded')
    page.get_by_test_id('tab-download').click()
    download = page.get_by_test_id('managed-download-view')
    expect(download).to_be_visible()
    baseline = page.request.get(url+'/_managed_test/status', headers=headers).json()
    assert baseline['request_count']==0
    details = download.get_by_test_id('download-details')
    bar = details.get_by_test_id('download-detail-bar')
    button = details.get_by_test_id('download-detail-toggle')
    expect(button).to_be_visible()
    expect(button).to_have_attribute('aria-expanded', 'false')
    expect(details.get_by_test_id('download-detail-queue')).not_to_be_visible()
    # Original U-2: double click a details bar in a normal/maximized window.
    bar.dblclick(position={'x':17,'y':14})
    expect(button).to_have_attribute('aria-expanded','true')
    expect(details.get_by_test_id('download-detail-queue')).to_contain_text('0件')
    expect(details.get_by_test_id('download-detail-destination')).to_contain_text('専用管理')
    # Original U-13: a tab round trip must not silently collapse the panel.
    page.get_by_test_id('tab-queue').click()
    page.get_by_test_id('tab-download').click()
    expect(button).to_have_attribute('aria-expanded','true')
    expect(details.get_by_test_id('download-detail-queue')).to_be_visible()
    button.click()
    expect(button).to_have_attribute('aria-expanded','false')
    # Too little height is an explicit refusal with a visible reason, not a
    # momentary open/close/flicker. A normal window remains fully usable.
    original=page.viewport_size
    page.set_viewport_size({'width':original['width'],'height':430})
    button.click()
    expect(button).to_have_attribute('aria-expanded','false')
    expect(details.get_by_test_id('download-detail-notice')).to_contain_text('高さ')
    page.set_viewport_size(original)
    button.click()
    expect(button).to_have_attribute('aria-expanded','true')
    expect(details.get_by_test_id('download-detail-notice')).to_have_text('')
    page.screenshot(path=str(root / ('u17-'+str(original['width'])+('-restart' if restart else '-initial')+'.png')),
                    animations='disabled')
    # Closing one client and opening a second cannot inherit the first state.
    other=page.context.new_page()
    try:
        other.goto(url,wait_until='domcontentloaded')
        other.get_by_test_id('tab-download').click()
        other_button=other.get_by_test_id('managed-download-view').get_by_test_id('download-detail-toggle')
        expect(other_button).to_have_attribute('aria-expanded','false')
    finally:
        other.close()
    final=page.request.get(url+'/_managed_test/status',headers=headers).json()
    assert final['request_count']==0
    assert final['all_requests_get']
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
    return dict(viewport=original,restart=restart,bar_doubleclick=True,tab_roundtrip=True,
                short_height_refused=True,restored_on_resize=True,other_client_isolated=True,
                external_requests=final['request_count'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    runner.inspect=inspect
    return runner.run(args.channel,args.output)


if __name__=='__main__':
    raise SystemExit(main())
