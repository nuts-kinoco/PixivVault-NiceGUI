"""L15/L16: Edge acceptance of My Bookmarks select -> durable managed queue.

Reuses the existing P3 subprocess, nonce, port and source-SHA checker. The only
external URLs are generated synthetic Pixiv CDN references resolved locally.
No actual account, original database, Cookie or network request is used.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from tools import check_p3_residuals_browser as base
from tools.p3_residuals_fixtures import create_services


def fixture_factory(app,sandbox):
    services,reader=create_services(app,sandbox)
    # The original read adapter compares all provenance fields; the card data
    # must match the eventual managed download record byte-for-byte.
    records=tuple(reader.get_artwork_download_record(str(wid)) for wid in (701,702))
    services._cached_remote_bookmarks=tuple(record.as_artwork() for record in records)
    services._remote_records=records
    services._publish_images(services.library,records)
    reader.calls.clear()
    return services,reader


def inspect(page,url,headers,output,*,restart,previous):
    from playwright.sync_api import expect
    def snapshot():
        response=page.request.get(url+'/_p3_test/snapshot',headers=headers)
        assert response.status==200
        return response.json()
    def eventually(predicate,label,timeout=25):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            data=snapshot()
            if predicate(data):return data
            page.wait_for_timeout(150)
        raise AssertionError(label)

    page.goto(url,wait_until='domcontentloaded')
    expect(page.get_by_test_id('tab-library')).to_be_enabled()
    if restart:
        before=snapshot()
        assert before['jobs']==previous['jobs']
        assert before['receipts']==previous['receipts']
    else:
        before=snapshot()
        assert before['jobs']==[] and before['receipts']==[]
    page.get_by_test_id('tab-library').click()
    expect(page.get_by_test_id('library-view')).to_be_visible()
    page.get_by_test_id('library-subview-bookmarks').click()
    bookmarks=page.get_by_test_id('bookmark-view')
    expect(bookmarks).to_be_visible()
    cards=bookmarks.get_by_test_id('bookmark-card')
    expect(cards).to_have_count(2)
    select=bookmarks.get_by_test_id('bookmark-select')
    save=bookmarks.get_by_test_id('bookmark-download')
    expect(save).to_be_disabled()
    if not restart:
        cards.nth(0).get_by_test_id('bookmark-select').click()
        cards.nth(1).get_by_test_id('bookmark-select').click()
        expect(bookmarks.get_by_test_id('bookmark-selection')).to_have_text('選択中: 2件')
        expect(save).to_be_enabled()
        save.click()
        accepted=eventually(lambda data:len(data['receipts'])==2 and
            sum(j['state']=='done' and j['target_id'] in ('701','702')
                for j in data['jobs'])==2,'bookmarks did not complete verified local saves')
        expect(bookmarks.get_by_test_id('bookmark-download-status')).to_contain_text('保存キュー受付 2 件')
        assert len(accepted['jobs'])==2, 'multiple clicks created duplicate queue jobs'
        page.screenshot(path=str(output/'bookmarks-admitted.png'),animations='disabled')
        # A repeated action in the same live session replays durable op IDs.
        save.click()
        expect(bookmarks.get_by_test_id('bookmark-download-status')).to_contain_text('保存キュー受付 2 件')
        assert len(snapshot()['jobs'])==2
    else:
        assert len(before['jobs'])==2 and len(before['receipts'])==2
        expect(bookmarks.get_by_test_id('bookmark-downloaded')).to_have_count(2)
        page.screenshot(path=str(output/'bookmarks-restored.png'),animations='disabled')
    page.get_by_test_id('library-subview-downloaded').click()
    refresh=page.get_by_test_id('library-db-refresh')
    expect(refresh).to_be_enabled();refresh.click()
    author=page.locator('.pv-library-author-card[data-author-id="91"]')
    expect(author).to_be_visible()
    author.get_by_test_id('library-author-open').click()
    for wid in ('701','702'):
        expect(page.locator('.pv-library-page-card[data-work-id="'+wid+'"]')).to_be_visible()
    page.screenshot(path=str(output/'saved-library.png'),animations='disabled')
    final=snapshot()
    assert len(final['receipts'])==2
    assert len(final['jobs'])==2
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    return {'jobs':final['jobs'],'receipts':final['receipts'],
            'library_works':['701','702']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',default='msedge',choices=('msedge','chromium'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--sandbox',type=Path)
    parser.add_argument('--nonce')
    args=parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:
            parser.error('explicit isolated sandbox/nonce required')
        base.serve(args.serve,args.sandbox,args.nonce,fixture_factory=fixture_factory)
        return 0
    if args.output is None:parser.error('--output required')
    base.inspect=inspect
    return base.run(args.channel,args.output,entry_script=Path(__file__).resolve())


if __name__=='__main__':sys.exit(main())
