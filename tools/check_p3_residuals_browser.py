"""P3 real-service fixture loopback checks: learning, save, recommendation, settings."""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health
ROOT=Path(__file__).resolve().parents[1]


def within_artifacts(path):
    path=path.resolve()
    if not path.is_relative_to(ROOT/'artifacts') or path==ROOT/'artifacts':
        raise ValueError('dedicated candidate artifacts path required')
    return path


def serve(port,sandbox,nonce,*,fixture_factory=None,extra_routes=None):
    from fastapi import Header,HTTPException
    from nicegui import app,ui
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.shell import build_demo_page
    from tools.p3_residuals_fixtures import create_services
    sandbox=within_artifacts(sandbox)
    services,reader=(fixture_factory or create_services)(app,sandbox)
    register_media_routes(app,services.images)
    ui.page('/')(build_demo_page)
    async def close_services():
        await asyncio.to_thread(services.close)
    app.on_shutdown(close_services)
    def auth(value):
        if not secrets.compare_digest(value or '',nonce):raise HTTPException(403)
    if extra_routes is not None:extra_routes(app,services,auth)
    @app.get('/healthz')
    def health():
        return {'status':'ok','mode':'readonly','data':services.data_mode}
    @app.get('/_p3_test/snapshot')
    async def snapshot(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance)
        discovery=await asyncio.to_thread(services.discovery.recommendation_snapshot)
        recommendation=await asyncio.to_thread(services.recommendations.refresh)
        def capture():
            preference_snapshot=services.preferences.snapshot()
            recommendation_prefs=services.recommendations.preferences
            with recommendation_prefs._mutex:
                active_hide_reasons=[
                    list(row) for row in recommendation_prefs.db.execute(
                        'SELECT kind,target,reason FROM overrides WHERE hidden=1 ORDER BY kind,target')]
            return dict(jobs=[asdict(j) for j in services.jobs.snapshot()],
                receipts=[r[0] for r in services.managed_downloads.saved_receipts()],
                novels=[dict(work_id=r.work_id,format=r.output_format,receipt=r.relative_receipt) for r in services.novel_jobs.saved_entries()],
                ugoira=services.ugoira.library(),
                preferences=dict(revision=preference_snapshot.revision,values=dict(preference_snapshot.values)),
                cache=services.shared_image_cache.stats(),
                recommendation=dict(
                    revision=recommendation['revision'],
                    order=list(recommendation['order']),
                    hide_reasons=active_hide_reasons,
                    added=list(recommendation['added_tags']),
                    boosted=list(recommendation['boosted']),
                    groups={k:[dict(key=g.key,page=g.page,works=[w.work_id for w in g.works]) for g in rows]
                        for k,rows in recommendation['sections'].items()},
                    errors=recommendation['errors']),
                discovery=dict(judged=sorted(discovery['judged_ids']),
                    superlike=list(discovery['superlike_order'])),
                vault=services.external_vault.status(),
                calls=list(reader.calls))
        return await asyncio.to_thread(capture)
    @app.post('/_p3_test/shutdown')
    def shutdown(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance);app.shutdown();return {'stopping':True}
    ui.run(host='127.0.0.1',port=port,show=False,reload=False,binding_refresh_interval=None)


def assert_page2_result(before,after):
    calls=[c for c in after['calls'][len(before['calls']):] if c[0]=='search']
    assert calls==[['search','landscape',2,'all']], calls
    group=next(g for g in after['recommendation']['groups']['certain'] if g['key']=='landscape')
    assert group['page']==2
    assert group['works']==[str(n) for n in range(1020,1060)]


def inspect(page,url,headers,output,*,restart=False,previous=None):
    from playwright.sync_api import expect
    from pixivvault_nicegui.recommendation_state import ORDER
    expect.set_options(timeout=20000)
    def snapshot():
        response=page.request.get(url+'/_p3_test/snapshot',headers=headers)
        assert response.ok,response.text()
        return response.json()
    def tab(name):
        page.get_by_test_id('tab-'+name).click()
        expect(page.get_by_test_id('tab-'+name)).to_have_attribute('aria-current','page')
    def eventually(predicate,label,timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=snapshot()
            if predicate(value):return value
            page.wait_for_timeout(100)
        raise AssertionError(label)
    page.goto(url,wait_until='domcontentloaded')
    expect(page.get_by_test_id('tab-learning')).to_be_enabled()
    # S21/S22 header affordances: toggling theme is local, opening Pixiv is
    # user initiated and never fires any outbound request during app bootstrap.
    theme=page.get_by_test_id('theme-toggle')
    expect(theme).to_have_attribute('aria-label','ライトモードへ切り替え')
    dark_background=page.evaluate("getComputedStyle(document.body).getPropertyValue('--pv-bg').trim()")
    theme.click()
    expect(theme).to_have_attribute('aria-label','ダークモードへ切り替え')
    page.wait_for_function("""original => getComputedStyle(document.body)
        .getPropertyValue('--pv-bg').trim() !== original""", arg=dark_background)
    theme.click()
    expect(theme).to_have_attribute('aria-label','ライトモードへ切り替え')
    page.wait_for_function("""original => getComputedStyle(document.body)
        .getPropertyValue('--pv-bg').trim() === original""", arg=dark_background)
    pixiv=page.get_by_test_id('pixiv-home-link')
    expect(pixiv).to_have_attribute('href','https://www.pixiv.net/')
    expect(pixiv).to_have_attribute('target','_blank')
    assert page.request.get(url+'/_p3_test/snapshot').status==403
    assert page.request.post(url+'/_p3_test/shutdown',headers={'X-Acceptance':'wrong'}).status==403
    initial=snapshot()
    assert initial['calls']==[], 'opening the product must not fetch'
    if restart:
        assert initial['discovery']==previous['discovery']
        assert initial['preferences']==previous['preferences']
        assert initial['recommendation']==previous['recommendation']
        assert initial['receipts']==previous['receipts']
        assert initial['novels']==previous['novels'] and initial['ugoira']==previous['ugoira']
        assert [(j['job_id'],j['state'],j['attempt']) for j in initial['jobs']]==[
            (j['job_id'],j['state'],j['attempt']) for j in previous['jobs']]
        first=initial['discovery']['superlike'][0]
        tab('library')
        page.get_by_test_id('library-db-refresh').click()
        author=page.locator('.pv-library-author-card[data-author-id="91"]')
        expect(author).to_be_visible();author.get_by_test_id('library-author-open').click()
        expect(page.locator('.pv-library-page-card[data-work-id="'+first+'"]')).to_be_visible()
        page.screenshot(path=str(output/'saved-library.png'),animations='disabled')
        tab('recommend')
        expect(page.get_by_test_id('recommend-work-superlike-'+first)).to_be_visible()
        expect(page.get_by_test_id('recommend-order-reset')).to_be_disabled()
        assert snapshot()['recommendation']['order']==list(ORDER)
        page.screenshot(path=str(output/'recommendations.png'),animations='disabled')
    else:
        tab('learning')
        fetch=page.get_by_test_id('discovery-fetch')
        expect(fetch).to_be_enabled()
        fetch.click()
        yes=page.locator('.pv-judgment-superLike')
        expect(yes).to_be_enabled()
        first=yes.get_attribute('data-work-id')
        assert first
        yes.click()
        eventually(lambda s:first in s['discovery']['superlike'],'SuperLike was not persisted')
        # The original trend threshold is twenty actual judgments, not synthetic
        # inserted counters. Every judgment here traverses the product UI.
        for index in range(19):
            like=page.locator('.pv-judgment-like')
            expect(like).to_be_enabled()
            wid=like.get_attribute('data-work-id')
            if index==18:
                trend_toggle=page.get_by_test_id('discovery-trend-toggle')
                trend_toggle.click()
                expect(trend_toggle).to_have_attribute('aria-expanded','true')
                expect(page.get_by_test_id('discovery-trend-panel')).to_be_visible()
            like.click()
            eventually(lambda s:wid in s['discovery']['judged'],'judgment did not persist')
            if index==18:
                expect(trend_toggle).to_have_attribute('aria-expanded','false')
                expect(page.get_by_test_id('discovery-trend-panel')).not_to_be_visible()
        expect(page.get_by_test_id('discovery-trend')).to_contain_text('20')
        page.screenshot(path=str(output/'learning.png'),animations='disabled')
        page.get_by_test_id('learning-review-open').click()
        dialog=page.get_by_test_id('learning-review-dialog')
        expect(dialog).to_be_visible()
        dialog.locator('.q-checkbox').nth(1).click()  # original SuperLike-only filter
        save=dialog.get_by_test_id('discovery-review-save')
        expect(save).to_have_count(1)
        expect(save).to_have_text('作品を保存')
        dialog.locator('.q-checkbox').nth(0).click()  # include already saved
        save.click()
        finished=eventually(lambda s:any(j['target_id']==first and j['state']=='done' for j in s['jobs']),
            'review save did not finish')
        assert len(finished['receipts'])==1
        # Queue completion is insufficient: the UI must re-read verified save
        # metadata, then hide the item when the unsaved-only filter is restored.
        expect(save).to_have_text('保存済み')
        expect(save).to_be_disabled()
        page.screenshot(path=str(output/'review-saved.png'),animations='disabled')
        dialog.locator('.q-checkbox').nth(0).click()
        expect(save).to_have_count(0)
        page.get_by_test_id('learning-review-close').click()
        tab('queue')
        expect(page.get_by_test_id('queue-view').get_by_test_id('job-row')).to_have_attribute('data-state','done')
        tab('library')
        page.get_by_test_id('library-db-refresh').click()
        author=page.locator('.pv-library-author-card[data-author-id="91"]')
        expect(author).to_be_visible();author.get_by_test_id('library-author-open').click()
        expect(page.locator('.pv-library-page-card[data-work-id="'+first+'"]')).to_be_visible()
        page.screenshot(path=str(output/'saved-library.png'),animations='disabled')
        tab('recommend')
        expect(page.get_by_test_id('recommend-update')).to_be_enabled()
        page.get_by_test_id('recommend-update').click()
        eventually(lambda s:bool(s['recommendation']['groups'].get('certain')),'recommendation update empty')
        expect(page.get_by_test_id('recommend-view')).to_have_attribute('data-busy','false')
        trend=page.get_by_test_id('recommend-section-trend')
        expect(trend.get_by_role('button',name='landscape',exact=True)).to_be_enabled()
        trend.get_by_role('button',name='landscape',exact=True).click()
        eventually(lambda s:any(t['tag']=='landscape' for t in s['recommendation']['added']),'trend not added')
        # Persisted state may advance before the asynchronous view re-render.
        expect(page.get_by_test_id('recommend-view')).to_have_attribute('data-busy','false')
        certain=page.get_by_test_id('recommend-section-certain')
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(12)
        certain.get_by_role('button',name='もっと見る',exact=True).first.click()
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(18)
        certain.get_by_role('button',name='もっと見る',exact=True).first.click()
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(24)
        # The first generated group has 20 items, so its last local expansion
        # caps at 20; the second group retains its own independent six cards.
        certain.get_by_role('button',name='もっと見る',exact=True).first.click()
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(26)
        before_page=snapshot()
        certain.get_by_role('button',name='さらに取得',exact=True).first.click()
        paged=eventually(lambda s:any(g['page']==2 for g in s['recommendation']['groups']['certain']),'page not advanced')
        assert_page2_result(before_page,paged)
        expect(page.get_by_test_id('recommend-view')).to_have_attribute('data-busy','false')
        # Completion means rendered page2 results, not merely an early DB snapshot.
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(30)
        assert_page2_result(before_page,snapshot())
        certain.get_by_role('button',name='畳む',exact=True).first.click()
        expect(certain.locator('[data-testid^=recommend-work-certain-]')).to_have_count(12)
        # Register from the retained SuperLike; hiding is a separate preference.
        card=page.get_by_test_id('recommend-work-superlike-'+first)
        card.get_by_role('button',name='作者をもっと見る',exact=True).click()
        eventually(lambda s:bool(s['recommendation']['boosted']),'author not registered')
        # R05: change the ordered shelves, then restore the original order.
        reset=page.get_by_test_id('recommend-order-reset')
        expect(reset).to_be_disabled()
        page.get_by_test_id('recommend-section-trend_added').get_by_role('button',name='下へ',exact=True).click()
        expect(reset).to_be_enabled()
        eventually(lambda s:s['recommendation']['order']==[
            'trend','trend_added','certain','boosted','superlike'],'shelf reorder not saved')
        expect(page.get_by_test_id('recommend-section-trend')).to_be_visible()
        reset.click()
        expect(reset).to_be_disabled()
        eventually(lambda s:s['recommendation']['order']==list(ORDER),'default shelf order not restored')
        expect(page.get_by_test_id('recommend-section-trend_added')).to_be_visible()
        # R08: the original work hide dialog offers three reason choices.
        for reason,label in (('genre','ジャンルが好みでない'),
                             ('style','絵柄が好みでない'),
                             (None,'理由を選ばない')):
            card=page.get_by_test_id('recommend-work-superlike-'+first)
            expect(card).to_be_visible()
            card.get_by_role('button',name='出さない',exact=True).click()
            dialog=page.get_by_test_id('recommend-hide-reason-dialog')
            expect(dialog).to_be_visible()
            dialog.get_by_role('button',name=label,exact=True).click()
            eventually(lambda s:['work',first,reason] in s['recommendation']['hide_reasons'],
                'reasoned hide not persisted')
            expect(page.get_by_test_id('recommend-work-superlike-'+first)).to_have_count(0)
            page.get_by_test_id('recommend-undo').click()
            eventually(lambda s:s['recommendation']['hide_reasons']==[], 'hidden work not restored')
            expect(card).to_be_visible()
        page.screenshot(path=str(output/'recommendations.png'),animations='disabled')
    # Verify stored choices through the visible settings form on both runs.
    page.get_by_test_id('preferences-open').click()
    dialog=page.get_by_test_id('preferences-dialog')
    expect(dialog).to_be_visible()
    if not restart:
        from pixivvault_nicegui.ui.preferences_view import CHOICE_LABELS
        for key,value in (('novel_save_format','both'),('ugoira_save_format','folder'),('pixiv_shared_image_cache_size_mb','512')):
            control=page.get_by_test_id('preference-'+key)
            expect(control).to_be_enabled();control.click()
            options=page.get_by_role('listbox').filter(visible=True)
            expect(options).to_have_count(1)
            options.get_by_role('option',name=CHOICE_LABELS.get(value,value),exact=True).click()
            expect(control).to_have_attribute('data-value',value)
        # Preserve acknowledged drafts separately from the persisted snapshot.
        draft={key:page.get_by_test_id('preference-'+key).get_attribute('data-value')
            for key in ('novel_save_format','ugoira_save_format','pixiv_shared_image_cache_size_mb')}
        (output/'preference-draft.json').write_text(json.dumps(draft,indent=2)+'\n',encoding='utf-8')
        page.get_by_test_id('preferences-save').click()
        eventually(lambda s:s['preferences']['values']['novel_save_format']=='both'
            and s['preferences']['values']['ugoira_save_format']=='folder'
            and s['preferences']['values']['pixiv_shared_image_cache_size_mb']=='512','preferences not saved')
        clear=page.get_by_test_id('preferences-clear-cache')
        expect(clear).to_be_enabled();clear.click()
        eventually(lambda s:s['cache']['entries']==0 and s['cache']['limit']==512*1024*1024
            and len(s['receipts'])==1,'cache settings/clear did not apply or saved receipt changed')
    else:
        stored=snapshot()['preferences']['values']
        assert stored['novel_save_format']=='both' and stored['ugoira_save_format']=='folder'
    page.screenshot(path=str(output/'settings.png'),animations='disabled')
    dialog.get_by_role('button').last.click()
    expect(dialog).to_be_hidden()
    if not restart:
        # The panel is first created AFTER changing defaults. All three media
        # use the application's existing author selection and durable admission.
        tab('download')
        page.get_by_test_id('media-download').get_by_test_id('media-select-illustration').click()
        page.get_by_test_id('managed-download-author-id').fill('91')
        page.get_by_test_id('managed-download-author-add').click()
        expect(page.get_by_test_id('managed-download-author-selection')).to_contain_text('1')
        panel=page.get_by_test_id('author-media-view')
        before_preview=snapshot()
        panel.get_by_test_id('author-media-preview').click()
        expect(panel.locator('.q-checkbox')).to_have_count(3)
        expect(panel.get_by_test_id('author-media-submit')).to_be_enabled()
        assert snapshot()['jobs']==before_preview['jobs'], 'preview must not enqueue'
        page.screenshot(path=str(output/'author-preview.png'),animations='disabled')
        panel.get_by_test_id('author-media-submit').click()
        all_saved=eventually(lambda s:len(s['jobs'])==4 and all(j['state']=='done' for j in s['jobs']),
            'three-media author save did not finish',timeout=30)
        assert len(all_saved['receipts'])==2
        assert [(r['work_id'],r['format']) for r in all_saved['novels']]==[('501','BOTH')]
        assert len(all_saved['ugoira'])==1
        tab('queue')
        expect(page.get_by_test_id('queue-view').get_by_test_id('job-row')).to_have_count(4)
        page.screenshot(path=str(output/'author-queue.png'),animations='disabled')
    # Open the saved novel and animation through their actual library readers,
    # including after restart. Stored content must not cause fixture GET calls.
    tab('library')
    selector=page.get_by_test_id('media-library')
    selector.get_by_test_id('media-select-novel').click()
    novel=page.get_by_test_id('novel-read-501-BOTH')
    expect(novel).to_be_visible();novel.click()
    expect(page.get_by_test_id('novel-reader-text')).to_contain_text('Generated fixture text.')
    page.screenshot(path=str(output/'author-novel.png'),animations='disabled')
    page.get_by_test_id('novel-reader-close').click()
    selector.get_by_test_id('media-select-ugoira').click()
    animation=page.get_by_test_id('ugoira-read-123-frames')
    expect(animation).to_be_visible();animation.click()
    canvas=page.get_by_test_id('ugoira-canvas')
    expect(canvas).to_be_visible()
    page.wait_for_function("Number(document.querySelector('[data-testid=ugoira-canvas]').dataset.elapsed)>300")
    page.screenshot(path=str(output/'author-ugoira.png'),animations='disabled')
    page.get_by_test_id('ugoira-player-close').click()
    tab('external')
    expect(page.get_by_test_id('external-vault-view')).to_be_visible()
    expect(page.get_by_test_id('external-vault-create')).to_be_disabled()
    expect(page.get_by_test_id('external-vault-refresh')).to_be_disabled()
    assert page.get_by_test_id('external-vault-status').inner_text().strip()
    vault=snapshot()['vault']
    assert not vault['crypto_available'] and not vault['initialized'] and not vault['open']
    page.screenshot(path=str(output/'external-unavailable.png'),animations='disabled')
    final=snapshot()
    if not restart:
        assert_page2_result(before_page,final)
    if restart:
        assert final['calls']==[], 'restart/navigation should only reuse stored state'
        # D15 is review Like -> local download, not history export.
        # Check it after the existing zero-GET navigation assertion: opening the
        # learning view can fetch fixture image bytes for the current candidate.
        tab('learning')
        page.get_by_test_id('learning-review-open').click()
        dialog=page.get_by_test_id('learning-review-dialog')
        expect(dialog).to_be_visible()
        dialog.locator('.q-checkbox').nth(0).click()  # include saved works
        dialog.locator('.q-checkbox').nth(1).click()  # SuperLike only
        saved_control=dialog.get_by_test_id('discovery-review-save')
        expect(saved_control).to_have_count(1)
        expect(saved_control).to_have_text('保存済み')
        expect(saved_control).to_be_disabled()
        dialog.locator('.q-checkbox').nth(0).click()  # unsaved only
        expect(saved_control).to_have_count(0)
        page.screenshot(path=str(output/'review-saved-restart.png'),animations='disabled')
        page.get_by_test_id('learning-review-close').click()
        after_review=snapshot()
        assert after_review['receipts']==final['receipts'], 'review changed saved receipts on reopen'
        assert [(j['job_id'],j['state']) for j in after_review['jobs']]==[
            (j['job_id'],j['state']) for j in final['jobs']], 'review unexpectedly enqueued a job'
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    return final


def run(channel,output,*,entry_script=None):
    from playwright.sync_api import sync_playwright
    output=within_artifacts(output)
    output.mkdir(parents=True,exist_ok=False)
    temporary=output/'temp';temporary.mkdir()
    os.environ.update(TEMP=str(temporary),TMP=str(temporary),PYTHONDONTWRITEBYTECODE='1')
    before=source_hashes();results=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel=None if channel=='chromium' else channel,headless=True)
        try:
            for width,height in ((1280,720),(1920,1080)):
                sandbox=output/f'data-{width}';previous=None
                for restart in (False,True):
                    evidence=output/f"{width}-{'restart' if restart else 'initial'}";evidence.mkdir()
                    port,nonce=free_port(),secrets.token_hex(24)
                    url=f'http://127.0.0.1:{port}';headers={'X-Acceptance':nonce}
                    result=dict(viewport=[width,height],restart=restart,success=False,
                        console_errors=[],page_errors=[],external_requests=[])
                    process=None
                    context=browser.new_context(viewport={'width':width,'height':height})
                    origin=urlsplit(url)
                    def route(request_route):
                        target=urlsplit(request_route.request.url)
                        if (target.scheme,target.netloc)!=(origin.scheme,origin.netloc):
                            result['external_requests'].append(target.scheme+'://'+target.netloc)
                            request_route.abort()
                        else:request_route.continue_()
                    context.route('**/*',route)
                    page=context.new_page()
                    page.on('console',lambda message:result['console_errors'].append(message.text) if message.type=='error' else None)
                    page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
                    with (evidence/'server-stderr.log').open('wb') as stderr:
                        try:
                            process=subprocess.Popen([sys.executable,'-B',str(entry_script or Path(__file__).resolve()),
                                '--serve',str(port),'--sandbox',str(sandbox),'--nonce',nonce],
                                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,
                                cwd=ROOT,env=child_environment(),creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                            result['pid']=process.pid
                            wait_for_health(url,process,expected_data='pixiv-readonly')
                            previous=inspect(page,url,headers,evidence,restart=restart,previous=previous)
                            result['inspection']=previous
                            assert not result['page_errors'] and not result['console_errors']
                            assert not result['external_requests']
                            result['success']=True
                        except Exception as error:
                            result['failure']=f'{type(error).__name__}: {error}'
                            try:
                                result['failure_snapshot']=page.request.get(url+'/_p3_test/snapshot',headers=headers).json()
                                page.screenshot(path=str(evidence/'failure.png'))
                                (evidence/'failure-dom.html').write_text(page.content(),encoding='utf-8')
                            except Exception:pass
                        finally:
                            try:
                                page.request.post(url+'/_p3_test/shutdown',headers=headers,timeout=5000)
                                process.wait(timeout=15);result['normal_exit']=process.returncode==0
                            except Exception:result['normal_exit']=False
                            context.close()
                            stop_child(process)
                            result['port_released']=port_released(port)
                    result['success'] &= result['normal_exit'] and result['port_released']
                    (evidence/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                    results.append(result)
                    if not result['success']:break
        finally:browser.close()
    after=source_hashes()
    success=len(results)==4 and all(r['success'] for r in results) and before==after
    (output/'result.json').write_text(json.dumps(dict(success=success,channel=channel,runs=results,
        checker_sha256=hashlib.sha256(Path(entry_script or __file__).read_bytes()).hexdigest(),
        base_checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        fixture_sha256=hashlib.sha256(Path(__file__).with_name('p3_residuals_fixtures.py').read_bytes()).hexdigest(),
        source_before=before,source_after=after,source_changed=before!=after),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(success=success,runs=len(results),output=str(output))))
    return 0 if success else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel',default='msedge',choices=('msedge','chromium'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--sandbox',type=Path)
    parser.add_argument('--nonce')
    args=parser.parse_args()
    if args.serve:
        if not args.sandbox or not args.sandbox.is_absolute() or not args.nonce:parser.error('explicit private sandbox required')
        serve(args.serve,args.sandbox,args.nonce);return 0
    if args.output is None:parser.error('--output required')
    return run(args.channel,args.output)
if __name__=='__main__':sys.exit(main())
