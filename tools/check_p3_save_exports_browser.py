"""CoS-only synthetic real-UI save/export/reuse acceptance, owned loopback roots."""
from pathlib import Path
import argparse,json,sys
from urllib.parse import quote
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools import check_p3_residuals_browser as harness
from tools.p3_residuals_fixtures import FixtureReader,create_services,PREFIX,DATE


class ExportReader(FixtureReader):
    def get_bookmarked_works(self,my_user_id,rest_type):
        rest=rest_type
        self.calls.append(('bookmark-static',rest))
        if rest!='show':return ()
        return tuple(dict(id=wid,user_id='91',user_name='Fixture author',title='Generated work '+wid,
            thumb_url=PREFIX+wid+'.png',page_count=1,type=0,create_date=DATE) for wid in ('701','702'))
    def get_bookmarked_novels(self,my_user_id,rest_type):
        rest=rest_type
        self.calls.append(('bookmark-novel',rest))
        wid='501' if rest=='show' else '502'
        return (dict(id=wid,user_id='91',user_name='Fixture author',title='Generated novel '+wid,
            thumb_url=PREFIX+wid+'.png',create_date=DATE),)
    def get_novel_payload(self,wid,aid):
        return dict(super().get_novel_payload(wid,aid),content='Generated fixture text.[uploadedimage:1]',
            textEmbeddedImages={'1':{'urls':{'original':PREFIX+'asset.png'}}})


def factory(app,sandbox):
    services,reader=create_services(app,sandbox,reader=ExportReader())
    services.configure_save_exports(sandbox/'save-exports','91')
    app.on_startup(services.save_exports.start)
    return services,reader


def routes(app,services,auth):
    from fastapi import Header,HTTPException
    from fastapi.responses import FileResponse
    from pixivvault_nicegui.fs_paths import native_io_path
    from pixivvault_nicegui.managed_downloads import _read_bounded
    from hashlib import sha256
    def inventory():
        ex=services.save_exports;result=[]
        for job in ex.jobs.snapshot():
            if job.kind!='bookmark-projection' or job.state!='done':continue
            manifest=json.loads(_read_bounded(native_io_path(ex.root/job.result),2_000_000))
            for row in manifest['files']:
                # Only the actual published manifest files can be served. The
                # relative file name is retained so browser reference resolution
                # traverses the same flattened directory as the user copy.
                result.append(dict(job_id=job.job_id,path=row['path'],sha256=row['sha256'],
                    source_sha256=row.get('source_sha256',row['sha256']),
                    url='/_p3_test/projection/'+job.job_id+'/'+quote(Path(row['path']).name)))
        return result
    @app.get('/_p3_test/export-files')
    def files(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance);return inventory()
    @app.get('/_p3_test/projection/{job_id}/{name}')
    def file(job_id:str,name:str,x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance)
        matches=[row for row in inventory() if row['job_id']==job_id and Path(row['path']).name==name]
        if len(matches)!=1:raise HTTPException(404)
        row=matches[0];path=native_io_path(services.save_exports.projection.root)/row['path']
        services.save_exports._owned_bytes(path,row['sha256'])
        if sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise HTTPException(409)
        return FileResponse(path,headers={'Cache-Control':'no-store'})


def visible_job(page, prefix, job_id, expect):
    """Find an exact job using the product's bounded, ordinary pagination."""
    root = page.get_by_test_id(prefix + '-view')
    expect(root).to_be_visible()
    label = root.get_by_test_id(prefix + '-page')
    expect(label).to_have_text(__import__('re').compile(r'\d+ / \d+'))
    total = int(label.inner_text().split('/')[1])
    previous = root.get_by_test_id(prefix + '-previous')
    for _ in range(total - 1):
        if not previous.is_enabled():
            break
        before = label.inner_text()
        previous.click()
        expect(label).not_to_have_text(before)
    expect(label).to_have_text('1 / ' + str(total))
    row = root.locator('[data-testid=job-row][data-job-id="' + job_id + '"]')
    for number in range(1, total + 1):
        if row.count():
            expect(row).to_be_visible()
            return row
        if number < total:
            next_button = root.get_by_test_id(prefix + '-next')
            expect(next_button).to_be_enabled()
            next_button.click()
            expect(label).to_have_text(str(number + 1) + ' / ' + str(total))
    raise AssertionError('exact accepted job is absent from ' + prefix)


def inspect_exports(page,url,headers,output,*,restart=False,previous=None):
    from playwright.sync_api import expect
    from pixivvault_nicegui.ui.preferences_view import CHOICE_LABELS
    expect.set_options(timeout=20000)
    page.context.set_extra_http_headers(headers)
    def snap():
        response=page.request.get(url+'/_p3_test/snapshot',headers=headers)
        assert response.ok;return response.json()
    def wait(predicate):
        import time
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            value=snap()
            if predicate(value):return value
            page.wait_for_timeout(100)  # condition polling, never a completion assumption
        raise AssertionError('save/export condition not observed: '+json.dumps(snap()['jobs']))
    def tab(value):
        page.get_by_test_id('tab-'+value).click()
        expect(page.get_by_test_id('tab-'+value)).to_have_attribute('aria-current','page')
    def exported(value,wid):
        return any(j['kind']=='bookmark-projection' and j['state']=='done' for j in value['jobs']) and any(n['work_id']==wid for n in value['novels'])
    page.goto(url,wait_until='domcontentloaded')
    initial=snap();assert initial['calls']==[]
    if not restart:
        page.get_by_test_id('preferences-open').click()
        dialog=page.get_by_test_id('preferences-dialog');expect(dialog).to_be_visible()
        for key,value in (('novel_save_format','both'),('bookmark_download_mode','direct'),('use_work_folder','0'),('zip_all_after_download','1')):
            control=page.get_by_test_id('preference-'+key);expect(control).to_be_enabled()
            if control.get_attribute('data-value')!=value:
                control.click();options=page.get_by_role('listbox').filter(visible=True)
                expect(options).to_have_count(1)
                label=CHOICE_LABELS.get(value,{'0':'OFF','1':'ON'}.get(value,value))
                options.get_by_role('option',name=label,exact=True).click()
                expect(control).to_have_attribute('data-value',value)
        page.get_by_test_id('preferences-save').click()
        wait(lambda s:s['preferences']['values']['novel_save_format']=='both' and s['preferences']['values']['zip_all_after_download']=='1')
        page.screenshot(path=str(output/'export-settings.png'))
        dialog.get_by_role('button').last.click();expect(dialog).to_be_hidden()
        # Both original public/private bookmark entrances, actual catalog selection,
        # preview and submit. No fixture mutation endpoint performs these actions.
        for rest,wid in (('show','501'),('hide','502')):
            tab('download')
            page.get_by_test_id('media-download').get_by_test_id('media-select-novel').click()
            page.get_by_test_id('novel-bookmarks-'+rest).click()
            choice=page.get_by_test_id('novel-catalog-'+wid);expect(choice).to_be_visible();choice.click()
            expect(page.get_by_test_id('novel-work-id')).to_have_value(wid)
            page.get_by_test_id('novel-preview-button').click()
            button=page.get_by_test_id('novel-submit');expect(button).to_be_enabled();button.click()
            wait(lambda s:exported(s,wid))
            page.screenshot(path=str(output/('novel-'+rest+'-saved.png')))
        # Two separate bookmark saves: first creates ZIP, second must visibly
        # fail existing-author ZIP refusal without undoing either successful save.
        for wid in ('701','702'):
            tab('download');page.get_by_test_id('media-download').get_by_test_id('media-select-illustration').click()
            if wid=='701':page.get_by_test_id('managed-download-refresh').click()
            row=page.locator('.pv-download-item[data-work-id="'+wid+'"]')
            expect(row).to_be_visible();row.get_by_test_id('managed-download-select').click()
            page.get_by_test_id('managed-download-submit').click()
            wait(lambda s:any(j['kind']=='pixiv-illust' and j['target_id']==wid and j['state']=='done' for j in s['jobs']))
            wait(lambda s:any(j['kind']=='auto-zip' and j['state']==('done' if wid=='701' else 'error') for j in s['jobs']))
        value=snap();failed=next(j for j in value['jobs'] if j['kind']=='auto-zip' and j['state']=='error')
        tab('queue')
        completed=next(j for j in value['jobs'] if j['kind']=='auto-zip' and j['state']=='done')
        done_row=visible_job(page,'queue',completed['job_id'],expect)
        expect(done_row).to_have_attribute('data-state','done')
        expect(done_row.get_by_test_id('job-state')).to_have_text('\u5b8c\u4e86')
        page.screenshot(path=str(output/'export-success-queue.png'))
        row=visible_job(page,'queue',failed['job_id'],expect)
        expect(row).to_have_attribute('data-state','error');expect(row.get_by_test_id('job-state')).to_have_text('\u30a8\u30e9\u30fc')
        page.screenshot(path=str(output/'export-error-queue.png'))
        tab('failed')
        row=visible_job(page,'failed',failed['job_id'],expect)
        expect(row).to_have_attribute('data-state','error')
        expect(row.get_by_test_id('job-state')).to_have_text('\u30a8\u30e9\u30fc')
        row.get_by_test_id('job-retry').click()
        wait(lambda s:any(j['job_id']==failed['job_id'] and j['attempt']==2 and j['state']=='error' for j in s['jobs']))
        expect(row).to_have_attribute('data-state','error')
        expect(row.get_by_test_id('job-state')).to_have_text('\u30a8\u30e9\u30fc')
        page.screenshot(path=str(output/'export-retry-refused.png'))
    else:
        assert initial['calls']==[]
        assert initial['preferences']==previous['preferences']
        assert [(j['job_id'],j['state'],j['attempt']) for j in initial['jobs']]==[(j['job_id'],j['state'],j['attempt']) for j in previous['jobs']]
        tab('queue');page.screenshot(path=str(output/'export-restart-queue.png'))
    # Read the projection itself in the browser's native TXT/image reader. This
    # evidence is explicitly distinct from the product's CAS library reader.
    wait(lambda s:sum(j['kind']=='bookmark-projection' and j['state']=='done' for j in s['jobs'])>=4)
    response=page.request.get(url+'/_p3_test/export-files',headers=headers);assert response.ok
    files=response.json();(output/'projection-files.json').write_text(json.dumps(files,ensure_ascii=False,indent=2),encoding='utf-8')
    txt=next(row for row in files if row['path'].endswith('.txt'))
    response=page.request.get(url+txt['url'],headers=headers);assert response.ok
    import re,hashlib
    text=response.body().decode('utf-8');reference=re.search(r'\[挿絵: ([^\]]+)\]',text).group(1)
    image_url=txt['url'].rsplit('/',1)[0]+'/'+quote(reference)
    asset=next(row for row in files if row['url']==image_url)
    payload=page.request.get(url+image_url,headers=headers);assert payload.ok
    assert hashlib.sha256(payload.body()).hexdigest()==asset['sha256']==asset['source_sha256']
    page.goto(url+txt['url']);expect(page.locator('body')).to_contain_text(reference)
    page.screenshot(path=str(output/'projection-native-txt-reader.png'))
    page.goto(url+image_url);expect(page.locator('img')).to_be_visible()
    page.wait_for_function('document.images.length===1 && document.images[0].complete && document.images[0].naturalWidth>0')
    page.screenshot(path=str(output/'projection-native-image-reader.png'))
    final=snap()
    assert any(j['kind']=='auto-zip' and j['state']=='done' for j in final['jobs'])
    assert any(j['kind']=='auto-zip' and j['state']=='error' and j['attempt']==2 for j in final['jobs'])
    if restart:assert final['calls']==[]
    return final


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',default='msedge',choices=('msedge','chromium'))
    parser.add_argument('--output',type=Path);parser.add_argument('--serve',type=int)
    parser.add_argument('--sandbox',type=Path);parser.add_argument('--nonce')
    args=parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:parser.error('explicit owned sandbox required')
        harness.serve(args.serve,args.sandbox,args.nonce,fixture_factory=factory,extra_routes=routes);return 0
    if args.output is None:parser.error('--output required')
    harness.inspect=inspect_exports
    return harness.run(args.channel,args.output,entry_script=Path(__file__).resolve())


if __name__=='__main__':sys.exit(main())
