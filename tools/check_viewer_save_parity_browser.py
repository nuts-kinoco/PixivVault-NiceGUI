"""CoS synthetic acceptance against an already owned loopback fixture.

Normal DOM clicks only; /_fixture/state is read-only observation, never a save API.
The caller owns server startup/shutdown and runs initial/restart with one root.
"""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--width',type=int,default=1280);parser.add_argument('--height',type=int,default=720)
    parser.add_argument('--light',action='store_true');parser.add_argument('--restart',action='store_true')
    args=parser.parse_args();address=urlsplit(args.url)
    if address.scheme!='http' or address.hostname not in ('127.0.0.1','localhost') or address.username or address.password or address.port==8089:
        raise ValueError('owned loopback fixture required')
    output=args.output.absolute();allowed=ROOT/'artifacts/p3-viewer-save-parity-20261028'
    if output==allowed or not output.is_relative_to(allowed):raise ValueError('new candidate artifact directory required')
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.managed_downloads import _safe_path
    output=native_io_path(output);_safe_path(output);output.mkdir(parents=True,exist_ok=False)
    result=dict(success=False,restart=args.restart,viewport=[args.width,args.height],light=args.light,external_requests=[],page_errors=[],console_errors=[])
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':args.width,'height':args.height})
        def guard(route):
            target=urlsplit(route.request.url)
            if (target.scheme,target.netloc)!=(address.scheme,address.netloc):
                result['external_requests'].append(target.scheme+'://'+target.netloc);route.abort()
            else:route.continue_()
        context.route('**/*',guard);page=context.new_page()
        page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
        page.on('console',lambda m:result['console_errors'].append(m.text) if m.type=='error' else None)
        def state():
            response=page.request.get(args.url.rstrip('/')+'/_fixture/state');assert response.ok
            return response.json()
        def save(viewer):
            before=len(state()['completed']);viewer.get_by_test_id('viewer-download').click()
            page.wait_for_function("async n => (await (await fetch('/_fixture/state')).json()).completed.length>n",arg=before)
            expect(viewer.get_by_test_id('viewer-download')).to_have_text(re.compile(r'保存済\s*$'))
            latest=state()['completed'][-1];assert latest['saved'] and not latest['export_error']
        def open_work(wid):
            page.get_by_test_id('tab-ranking').click()
            page.locator(f'[data-work-id="{wid}"]').click()
            viewer=page.get_by_test_id('gallery-viewer');expect(viewer).to_be_visible()
            expect(viewer.get_by_test_id('viewer-download')).to_be_enabled()
            return viewer
        def close(viewer):viewer.get_by_role('button',name='閉じる',exact=True).click()
        try:
            page.goto(args.url,wait_until='domcontentloaded')
            if args.light:page.get_by_test_id('theme-toggle').click()
            if not args.restart:
                viewer=open_work('123');viewer.get_by_role('button',name='次のページ',exact=True).click()
                expect(viewer.get_by_test_id('viewer-download')).to_be_enabled();save(viewer)
                first=state();assert len(first['exports'])==1
                entry=first['exports'][0]
                assert entry['identity']['page_no']==1 and entry['identity']['page_count']==3
                assert entry['output']['relative']=='合成作者(456)/合成3ページ(123)_2.png'
                assert entry['actual_sha256']==first['expected_pages'][1]
                save(viewer);again=state()
                assert again['exports']==first['exports']
                assert [c for c in again['calls'] if c[0]=='original_image']==[['original_image',1]]
                close(viewer)
                page.get_by_test_id('preferences-open').click();dialog=page.get_by_test_id('preferences-dialog')
                control=dialog.get_by_test_id('preference-use_work_folder');control.click()
                page.get_by_role('option',name='ON',exact=True).last.click()
                dialog.get_by_test_id('preferences-save').click()
                expect(dialog.get_by_test_id('preferences-status')).to_have_text('設定を保存しました。')
                dialog.get_by_role('button',name='閉じる',exact=True).click()
                viewer=open_work('123')
                for _ in range(2):viewer.get_by_role('button',name='次のページ',exact=True).click()
                expect(viewer.get_by_test_id('viewer-download')).to_be_enabled();save(viewer);close(viewer)
                current=state();entry=next(e for e in current['exports'] if e['identity']['page_no']==2)
                assert entry['output']['relative']=='合成作者(456)/合成3ページ(123)/2.png'
                assert entry['actual_sha256']==current['expected_pages'][2]
                viewer=open_work('299')
                gif_hash=viewer.get_by_test_id('viewer-image').locator('img[data-pv-displayed="true"]').evaluate("""async image => {
                    const raw=atob(image.src.split(',')[1]);const bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));
                    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
                }""")
                save(viewer);gif=next(e for e in state()['exports'] if e['identity']['work_id']=='299')
                assert gif['actual_sha256']==gif_hash==gif['output']['sha256']
                assert gif['output']['relative']=='合成動画作者(789)/合成うごイラ(299)/合成うごイラ(299).gif'
                page.screenshot(path=str(output/'displayed-gif.png'));close(viewer)
                assert [c for c in state()['calls'] if c[0]=='ugoira_zip']==[['ugoira_zip','299']]
            page.get_by_test_id('tab-history').click()
            history=page.get_by_test_id('backup-history');expect(history).to_be_visible()
            expect(history.get_by_test_id('backup-author-456')).to_be_visible()
            expect(history.get_by_test_id('backup-author-789')).to_be_visible()
            expect(history.get_by_test_id('backup-status')).to_have_text('2 作者')
            history.get_by_test_id('backup-open-456').click();history.get_by_test_id('backup-open-789').click()
            page.wait_for_function("async () => (await (await fetch('/_fixture/state')).json()).opened.length===2")
            current=state();assert set(current['opened'])=={'exports/projection/合成作者(456)','exports/projection/合成動画作者(789)'}
            assert current['static_jobs']==0 and current['complete_receipts']==0
            if args.restart:
                assert not [c for c in current['calls'] if c[0] in ('original_image','ugoira_zip')]
                before=current['exports'];viewer=open_work('123')
                for _ in range(2):viewer.get_by_role('button',name='次のページ',exact=True).click()
                expect(viewer.get_by_test_id('viewer-download')).to_be_enabled();save(viewer);close(viewer)
                assert not [c for c in state()['calls'] if c[0]=='original_image']
                assert state()['exports']==before
            page.get_by_test_id('tab-history').click()
            expect(history.get_by_test_id('backup-author-456')).to_be_visible()
            page.screenshot(path=str(output/'history.png'))
            result['state']=state()
            assert not result['external_requests'] and not result['page_errors'] and not result['console_errors']
            result['success']=True
        except Exception as error:
            result['error_type']=type(error).__name__;result['error']=str(error)
            page.screenshot(path=str(output/'failure.png'))
            (output/'failure-dom.html').write_text(page.content(),encoding='utf-8')
            raise
        finally:
            (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            context.close();browser.close()


if __name__=='__main__':main()
