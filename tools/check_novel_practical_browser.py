"""P1 headless Edge acceptance with an injected synthetic bounded-reader interface.

No credentials or outbound requests. All browser routes are confined to the
owned loopback origin. The inherited runner records hashes and normal cleanup.
"""
from __future__ import annotations
import sys
import json
import statistics
import time
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import check_novel_save_browser as harness
from pixivvault_nicegui.novel_save_presentation import FORMATS


def serve(port, sandbox, nonce, *, native=False):
    from io import BytesIO
    from PIL import Image
    from nicegui import app
    from fastapi import Header, HTTPException
    from dataclasses import asdict
    import hashlib
    import secrets
    from pixivvault_nicegui import novel_save_presentation
    from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
    from pixivvault_nicegui.services import get_services

    class Reader:
        def __init__(self):
            self.calls = []
        def close(self): pass
        def require_identity(self): return '91'
        def get_author_novel_ids(self, aid):
            assert aid == '91'
            self.calls.append(['author', aid])
            return ('123', '124')
        def get_bookmarked_novels(self, uid, rest):
            assert uid == '91' and rest in ('show', 'hide')
            self.calls.append(['bookmarks', rest])
            return [dict(id='123', user_id='91', title='合成小説123')]
        def get_novel_payload(self, wid, aid):
            assert wid in ('123', '124') and aid == '91'
            self.calls.append(['body', wid])
            return dict(id=wid, userId=aid, userName='合成作者', title='合成小説'+wid,
                content='[[rb:漢字 > かんじ]][newpage]<script> と &\n[uploadedimage:1]\n' + '長文の合成本文です。\n' * (1200 if wid == '124' else 1),
                seriesNavData={'seriesId':'9'},
                textEmbeddedImages={'1':{'urls':{'original':'https://i.pximg.net/synthetic.png'}}},
                coverUrl='https://i.pximg.net/cover.png')
        def get_image_bytes(self, url):
            assert url in ('https://i.pximg.net/synthetic.png', 'https://i.pximg.net/cover.png')
            self.calls.append(['image'])
            out = BytesIO()
            Image.new('RGB', (48, 64), '#4b77be').save(out, format='PNG')
            return out.getvalue()

    reader = Reader()
    novel_save_presentation.build_novel_ui_fixture = lambda: PinnedNovelSource(reader, {})
    # Additional route reads the exact receipt ledger, including explicit series.
    @app.get('/_p1_test/snapshot')
    def snapshot(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        services = get_services(app)
        proofs = []
        for saved in services.novel_jobs.saved_entries():
            folder = (services.novel_jobs.store.root / saved.relative_receipt).parent
            proofs.append(dict(work_id=saved.work_id, format=saved.output_format,
                receipt=saved.relative_receipt, receipt_sha256=saved.receipt_sha256,
                metadata=saved.metadata, files={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in saved.output_names}))
        return dict(jobs=[asdict(job) for job in services.jobs.snapshot()], proofs=proofs, calls=reader.calls)
    harness.serve(port, sandbox, nonce)


def inspect(page, url, headers, output, *, restart=False, previous=None):
    from playwright.sync_api import expect
    expect.set_options(timeout=15000)
    def snapshot():
        response = page.request.get(url+'/_p1_test/snapshot', headers=headers)
        assert response.ok
        return response.json()
    def gate(open):
        assert page.request.post(url+'/_novel_test/gate', params={'open':str(open).lower()}, headers=headers).ok
    def tab(name):
        page.get_by_test_id('tab-'+name).click()
        expect(page.get_by_test_id('tab-'+name)).to_have_attribute('aria-current', 'page')
    def preview(wid, fmt, series=False):
        tab('download')
        page.get_by_test_id('novel-author-id').fill('91')
        page.get_by_test_id('novel-work-id').fill(wid)
        page.get_by_test_id('novel-chapter-ids').fill('123,124' if series else '')
        page.get_by_test_id('novel-series-title').fill('合成シリーズ' if series else '')
        page.get_by_test_id('novel-format').click()
        page.get_by_role('option', name=FORMATS[fmt], exact=True).click()
        page.get_by_test_id('novel-preview-button').click()
        expect(page.get_by_test_id('novel-preview')).to_be_visible()
        expect(page.get_by_test_id('novel-preview')).to_contain_text('小説 '+wid+' · '+fmt)
    def submit(wid, fmt, series=False):
        preview(wid, fmt, series)
        page.get_by_test_id('novel-submit').click()
        expect(page.get_by_test_id('novel-notice')).to_contain_text('キューに追加しました')
        tab('queue')
        category = '小説シリーズ' if series else '小説'
        return page.get_by_test_id('queue-view').get_by_test_id('job-row').filter(has_text=f'{category} · {fmt} · ID {wid}')
    def read(proof):
        tab('library')
        series = len(proof['metadata']['chapters']) > 1
        suffix = '-series-'+proof['receipt_sha256'][:8] if series else ''
        button = page.get_by_test_id('novel-read-'+proof['work_id']+'-'+proof['format']+suffix)
        button.click()
        text = page.get_by_test_id('novel-reader-text')
        expect(text).to_be_visible()
        expect(text).to_contain_text('漢字《かんじ》')
        expect(text).to_contain_text('<script> と &')
        assert text.locator('script').count() == 0
        images = page.locator('.pv-novel-reader-images img')
        expect(images).to_have_count(3 if series else 2)
        expect(images.first).to_be_visible()
        page.wait_for_function("Array.from(document.querySelectorAll('.pv-novel-reader-images img')).every(i=>i.complete&&i.naturalWidth>0)")
        if series or proof['work_id'] == '124':
            expect(page.get_by_test_id('novel-reader-next')).to_be_enabled()
            page.get_by_test_id('novel-reader-next').click()
            expect(page.get_by_test_id('novel-reader-page')).to_contain_text('2 /')
            page.get_by_test_id('novel-reader-previous').click()
        page.screenshot(animations='disabled', path=str(output / ('reader-'+proof['work_id']+'-'+proof['format']+suffix+'.png')))
        page.get_by_test_id('novel-reader-close').click()
        expect(text).to_be_hidden()
        expect(button).to_be_focused()

    page.goto(url, wait_until='domcontentloaded')
    if restart:
        actual = snapshot()
        (output/'restart-snapshot.json').write_text(json.dumps(actual, ensure_ascii=False, indent=2), encoding='utf-8')
        assert actual['proofs'] == previous['proofs'], 'receipt list changed across restart'
        assert [(j['job_id'],j['state'],j['attempt']) for j in actual['jobs']] == [(j['job_id'],j['state'],j['attempt']) for j in previous['jobs']]
        assert actual['calls'] == [], 'restart must not fetch remote source'
    else:
        assert snapshot()['jobs'] == []
        tab('download')
        page.get_by_test_id('novel-author-id').fill('91')
        for testid, title in [('novel-author-list','123 · 123'), ('novel-bookmarks-show','合成小説123 · 123'), ('novel-bookmarks-hide','合成小説123 · 123')]:
            page.get_by_test_id(testid).click()
            choice=page.get_by_role('button', name=title, exact=True)
            expect(choice).to_be_visible()
            choice.click()
            expect(page.get_by_test_id('novel-work-id')).to_have_value('123')
        page.get_by_test_id('novel-preview-button').click()
        expect(page.get_by_test_id('novel-preview')).to_be_visible()
        assert snapshot()['jobs'] == [] and snapshot()['proofs'] == []
        page.screenshot(animations='disabled', path=str(output/'preview.png'))
        row=submit('123','EPUB')
        expect(row).to_have_attribute('data-state','running')
        row.get_by_test_id('job-pause').click()
        expect(row).to_have_attribute('data-state','paused')
        assert snapshot()['proofs'] == []
        row.get_by_test_id('job-resume').click()
        expect(row).to_have_attribute('data-state','running')
        gate(True)
        expect(row).to_have_attribute('data-state','done')
        row=submit('123','TXT')
        expect(row).to_have_attribute('data-state','error')
        tab('failed')
        failure=page.get_by_test_id('failed-view').get_by_test_id('job-row')
        expect(failure).to_have_count(1)
        failure.get_by_test_id('job-retry').click()
        expect(failure).to_have_count(0)
        tab('queue')
        expect(row).to_have_attribute('data-state','done')
        for wid, fmt, series in [('123','BOTH',False),('124','EPUB',False),('123','BOTH',True)]:
            expect(submit(wid,fmt,series)).to_have_attribute('data-state','done')
        gate(False)
        row=submit('124','BOTH')
        expect(row).to_have_attribute('data-state','running')
        row.get_by_test_id('job-stop').click()
        expect(row).to_have_attribute('data-state','stopped')
        actual=snapshot()
        assert len(actual['proofs']) == 5 and len(actual['jobs']) == 6
        assert next(j for j in actual['jobs'] if j['target_id']=='123:TXT')['attempt']==2
        assert not any(p['work_id']=='124' and p['format']=='BOTH' for p in actual['proofs'])
    for proof in snapshot()['proofs']:
        read(proof)
    expect(page.get_by_test_id('novel-library-notice')).to_have_text('保存済み 5 件')
    if restart:
        assert snapshot()['calls'] == [], 'saved reading must work without source'
    preview('123','EPUB')
    expect(page.get_by_test_id('novel-submit')).to_be_disabled()
    expect(page.get_by_test_id('novel-saved-status')).to_contain_text('保存済み')
    preview('123','BOTH',True)
    expect(page.get_by_test_id('novel-submit')).to_be_disabled()
    tab('library')
    page.screenshot(animations='disabled', path=str(output/'library.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    timings=[]
    for index in range(20):
        started=time.perf_counter()
        target='queue' if index % 2 == 0 else 'library'
        tab(target)
        expect(page.get_by_test_id('queue-view' if target=='queue' else 'novel-library-view')).to_be_visible()
        timings.append((time.perf_counter()-started)*1000)
    backend=page.request.get(url+'/_novel_test/snapshot',headers=headers).json()['backend_durations']
    return {**snapshot(), 'warm_navigation_ms':{'samples':timings,'p50':statistics.median(timings),'p95':sorted(timings)[18]},
            'backend_durations':backend}


def main():
    # Reuse unchanged lifecycle/error recording; subprocesses execute this file.
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--sandbox',type=Path)
    parser.add_argument('--nonce')
    parser.add_argument('--channel',default='msedge',choices=('msedge','chromium'))
    parser.add_argument('--output',type=Path,required=False)
    args=parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:
            parser.error('absolute sandbox and nonce required')
        serve(args.serve,args.sandbox,args.nonce)
        return 0
    if args.output is None:
        parser.error('--output required')
    harness.__file__=__file__
    harness.inspect=inspect
    original_health=harness.wait_for_health
    harness.wait_for_health=lambda url, process: original_health(url,process,expected_data='pixiv-readonly')
    return harness.run(args.channel,args.output)

if __name__=='__main__':
    sys.exit(main())