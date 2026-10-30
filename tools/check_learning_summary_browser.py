"""D09/D10 offline Edge/Chromium GUI acceptance with an owned synthetic server.

The checker never reads Cookie, original DB or real artwork. All browser
requests outside its loopback origin are rejected. It creates only artifacts
under this candidate worktree and terminates the process it starts.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import traceback
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
if __package__ in (None,''):
    sys.path.insert(0,str(ROOT))

from tools.run_browser_checks import (child_environment, free_port, port_released,
                                      wait_for_health)


HOST='127.0.0.1'
OWNED=(
    'src/pixivvault_nicegui/discovery_description.py',
    'src/pixivvault_nicegui/discovery_summary_fetch.py',
    'src/pixivvault_nicegui/discovery_learning.py',
    'src/pixivvault_nicegui/ui/learning_view.py',
    'src/pixivvault_nicegui/pixiv_read_client.py',
)


def source_sha():
    return {rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest() for rel in OWNED}


def serve(port, fixture, nonce):
    from nicegui import app,ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.discovery_description import MAX_DESCRIPTION
    from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ui.learning_view import LearningView
    from fastapi import Response
    from PIL import Image
    from io import BytesIO
    register_pages()
    service=get_services(app)
    long='はじめに<br>'+'<br>'.join('本文の段落%03dと続きを読みます' % i for i in range(105))
    long += '<br><a href="https://example.com/safe">外部リンク</a>'
    long += '<br><a href="javascript:alert(1)">危険なリンクの文字</a>'
    raws = {
        'demo-0001':'短い説明文。<b>強調</b>も素の文字で表示します。',
        'demo-0002':long,
        'demo-0003':'<br><b> </b>',
        'demo-0004':'長すぎる'*(MAX_DESCRIPTION//4+1),
    }
    service.artworks=tuple(replace(item,description=raws.get(item.work_id,item.description))
                           for item in service.artworks)
    # Separate generated discovery view: the existing demo page remains intact.
    # The metadata detail callback is a fake bounded call, not Pixiv or Cookie.
    class SyntheticReader:
        def __init__(self):
            self.detail_calls=[]
            self.detail_failures=0
        def require_identity(self):return None
        def get_ranking(self,**_):
            return [dict(illust_id=str(i),title=f'Generated {i}',user_id='99',
                         user_name='Generated author',url=f'https://i.pximg.net/gen/{i}.jpg',
                         tags=['generated']) for i in range(1,5)]
        def get_recommended_users(self,*_args,**_kwargs):return {'users':[],'works':[]}
        def search_recommendation_illusts(self,*_args,**_kwargs):return {'works':[]}
        def get_artwork_metadata_raw(self,work_id,expected_author_id):
            self.detail_calls.append([work_id,expected_author_id])
            if self.detail_failures:
                self.detail_failures-=1
                raise RuntimeError('synthetic HTTP failure')
            raw={'1': '画像を取り直さずに取得した詳細本文<br><b>続き</b>',
                 '2': long, '3': '', '4': '<b>失敗後の説明</b>'}[work_id]
            return {'id':work_id,'userId':expected_author_id,'description':raw}
    reader=SyntheticReader()
    live_current={'view':None}
    live_services=ApplicationServices(count=4)
    live_services.discovery=DiscoveryLearningService(fixture/'synthetic-discovery',reader)
    image_bytes=BytesIO()
    Image.new('RGB',(640,480),(45,90,120)).save(image_bytes,format='PNG')
    counters={'images':0}
    class Pin:
        def url(self, work_id, page_no=0, variant='view'):
            return f'/fixture-art/{work_id}/{page_no}/{variant}.png'
        def close(self):return None
    live_services.acquire_ranking_images=lambda records:Pin()
    @app.get('/fixture-art/{work_id}/{page_no}/{variant}.png')
    def art_image(work_id:str,page_no:int,variant:str):
        counters['images']+=1
        return Response(image_bytes.getvalue(),media_type='image/png')
    @app.get('/_fixture/summary-stats')
    def summary_stats():
        return dict(details=list(reader.detail_calls),images=counters['images'])
    @app.post('/_fixture/summary-failure')
    def summary_failure(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        view=live_current['view']
        if view is None or not view.active:
            raise HTTPException(409)
        reader.detail_failures=1
        view.set_summary_failed('synthetic failure injection')
        return {'armed':True}
    @app.post('/_fixture/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        app.shutdown()
        return {'stopping':True}
    @ui.page('/live-summary')
    def live_page():
        view=LearningView(live_services)
        live_current['view']=view
        view.set_active(True)
    app.on_shutdown(live_services.discovery.close)
    app.on_shutdown(live_services.close)
    ui.run(host=HOST,port=port,show=False,reload=False,binding_refresh_interval=None)


def inspect(page, width, height, out, report):
    from playwright.sync_api import expect
    page.set_viewport_size(dict(width=width,height=height))
    page.goto(report['url'],wait_until='domcontentloaded',timeout=20000)
    page.get_by_test_id('tab-learning').click()
    root=page.locator('.pv-learning')
    expect(root).to_be_visible()
    toggle=page.get_by_test_id('learning-summary-toggle')
    board=page.get_by_test_id('learning-summary-board')
    expect(toggle).to_be_enabled()
    expect(board).to_be_hidden()
    assert toggle.get_attribute('aria-expanded')=='false'
    toggle.click()
    expect(board).to_be_visible()
    expect(board).to_contain_text('短い説明文。強調も素の文字で表示します。')
    assert not page.get_by_test_id('learning-summary-next').is_visible()
    toggle.focus()
    page.keyboard.press('Escape')
    expect(board).to_be_hidden()
    assert toggle.get_attribute('aria-expanded')=='false'
    toggle.click()
    expect(board).to_be_visible()
    page.get_by_test_id('learning-summary-close').click()
    expect(board).to_be_hidden()
    # Now move to a multi-page document without network or persistent writes.
    page.get_by_role('button',name='見送る',exact=True).click()
    expect(root.locator('.pv-counter')).to_contain_text('1 /')
    expect(toggle).to_be_enabled()
    expect(board).to_be_hidden()
    toggle.click()
    expect(board).to_be_visible()
    prev=page.get_by_test_id('learning-summary-prev')
    following=page.get_by_test_id('learning-summary-next')
    pager=page.get_by_test_id('learning-summary-pages')
    expect(prev).to_be_disabled()
    expect(following).to_be_enabled()
    body=page.get_by_test_id('learning-summary-body')
    first=body.inner_text()
    first_label=pager.inner_text()
    following.click()
    expect(prev).to_be_enabled()
    expect(pager).not_to_have_text(first_label)
    expect(body).not_to_have_text(first)
    second=body.inner_text()
    second_label=pager.inner_text()
    prev.click()
    expect(pager).to_have_text(first_label)
    expect(body).to_have_text(first)
    expect(prev).to_be_disabled()
    following.click()
    expect(pager).to_have_text(second_label)
    expect(body).to_have_text(second)
    expect(prev).to_be_enabled()
    pages=0
    while following.is_enabled() and pages<100:
        previous=pager.inner_text()
        following.click()
        expect(pager).not_to_have_text(previous)
        pages+=1
    assert pages<100
    expect(following).to_be_disabled()
    assert ' / ' in pager.inner_text()
    # Even if the original HTML contains an active script URL, its label is
    # plain text. Valid external links remain explicitly user-clickable with
    # opener isolation; the checker does not click/open them.
    assert board.locator('a[href^="javascript:"]').count()==0
    safe_link=board.locator('a[href="https://example.com/safe"]')
    expect(safe_link).to_be_visible()
    assert 'noopener' in (safe_link.get_attribute('rel') or '')
    assert safe_link.get_attribute('target')=='_blank'
    expect(board.get_by_text('危険なリンクの文字')).to_be_visible()
    # IME and editable targets cannot accidentally dismiss the panel or judge.
    board.evaluate("""el => el.dispatchEvent(new KeyboardEvent('keydown',
        {key:'Escape',isComposing:true,bubbles:true,cancelable:true}))""")
    expect(board).to_be_visible()
    root.evaluate("""el=>{
       const editable=document.createElement('input');
       el.append(editable);
       editable.dispatchEvent(new KeyboardEvent('keydown',
          {key:'Escape',bubbles:true,cancelable:true}));
       editable.remove();
       const dialog=document.createElement('div');
       dialog.setAttribute('role','dialog');el.append(dialog);
       dialog.dispatchEvent(new KeyboardEvent('keydown',
          {key:'Escape',bubbles:true,cancelable:true}));
       dialog.remove();
    }""")
    expect(board).to_be_visible()
    before=root.locator('.pv-counter').inner_text()
    board.focus()
    page.keyboard.press('Escape')
    expect(board).to_be_hidden()
    assert root.locator('.pv-counter').inner_text()==before
    toggle.click()
    expect(board).to_be_visible()
    width_now=board.evaluate('el=>Math.round(el.getBoundingClientRect().width)')
    assert width_now == (900 if width>=1500 else 276), width_now
    metrics=page.evaluate("""()=>({innerWidth,innerHeight,
       documentWidth:document.documentElement.scrollWidth,
       board:(()=>{const r=document.querySelector('[data-testid=learning-summary-board]').getBoundingClientRect();
           return {top:r.top,right:r.right,bottom:r.bottom,left:r.left,width:r.width,height:r.height}})()})""")
    assert metrics['documentWidth']<=width+1, metrics
    assert metrics['board']['left']>=0 and metrics['board']['right']<=width+1,metrics
    assert metrics['board']['top']>=0 and metrics['board']['bottom']<=height+1,metrics
    page.screenshot(path=str(out/f'summary-{width}x{height}.png'),animations='disabled')
    if width==1280:
        # Resize the *open* board, without closing and reopening it. A real
        # browser resize event must remeasure lines/columns and preserve a
        # valid page boundary, rather than leaving stale viewport geometry.
        page.set_viewport_size(dict(width=1920,height=1080))
        expect(board).to_have_css('width','900px')
        assert following.is_enabled()
        page.set_viewport_size(dict(width=1280,height=720))
        expect(board).to_have_css('width','276px')
        assert board.evaluate('el=>el.getBoundingClientRect().bottom')<=720+1
    # Real DOM checks for an actually empty description, followed by a
    # deliberately over-limit synthetic detail whose retry stays offline.
    page.get_by_role('button',name='見送る',exact=True).click()
    expect(root.locator('.pv-counter')).to_contain_text('2 /')
    expect(toggle).to_be_disabled()
    expect(board).to_be_hidden()
    page.get_by_role('button',name='見送る',exact=True).click()
    expect(root.locator('.pv-counter')).to_contain_text('3 /')
    expect(toggle).to_have_text('概要 取り直す')
    toggle.click()
    expect(board).to_be_hidden()
    expect(toggle).to_have_text('概要 取り直す')
    report['viewports'][f'{width}x{height}']=dict(metrics=metrics,final_page=pager.inner_text(),checks=[
        'default-closed','short-one-page','Esc-close','close-button','judgment-closes',
        'long-next-prev','first-last-bounds','IME-ignored','wide-responsive',
        'live-resize' if width==1280 else 'initial-wide-layout',
        'empty-disabled','failed-local-retry-no-extra-GET',
        'safe-https-link-noopener','unsafe-link-displayed-as-plain-text'])


def inspect_explicit_discovery(page, width, height, report, nonce, out):
    """D09/D10 acceptance through actual clicked discovery.reader detail GET."""
    from playwright.sync_api import expect
    page.set_viewport_size(dict(width=width,height=height))
    origin=report['url']
    page.goto(origin+'/live-summary',wait_until='domcontentloaded',timeout=20000)
    root=page.locator('.pv-learning')
    button=page.get_by_test_id('learning-summary-toggle')
    board=page.get_by_test_id('learning-summary-board')
    expect(root).to_be_visible()
    expect(button).to_have_text('概要を取得',timeout=20000)
    expect(board).to_be_hidden()
    expect(page.locator('.pv-view-art img[data-pv-displayed="true"]')).to_have_count(1,timeout=20000)
    state=lambda:page.request.get(origin+'/_fixture/summary-stats').json()
    before=state()
    assert len(before['details'])==report.get('expected_live_detail_calls',0),before
    button.click()
    expect(board).to_be_visible(timeout=15000)
    expect(board).to_contain_text('画像を取り直さずに取得した詳細本文')
    after=state()
    assert len(after['details'])==len(before['details'])+1 and after['details'][-1]==['1','99'],after
    assert after['images']==before['images'],(before,after)
    button.focus()
    page.keyboard.press('Escape')
    expect(board).to_be_hidden()
    button.click()
    expect(board).to_be_visible()
    page.get_by_test_id('learning-summary-close').click()
    expect(board).to_be_hidden()
    assert state()==after,'Close/reopen/Esc issued additional GET or image request'
    # A failed metadata GET stays retryable, never becoming "no description".
    response=page.request.post(origin+'/_fixture/summary-failure',
                               headers={'X-Acceptance':nonce})
    assert response.status==200 and response.json()=={'armed':True}
    expect(button).to_have_text('概要 取り直す')
    button.click()
    expect(button).to_have_text('概要 取り直す',timeout=15000)
    failed=state()
    assert len(failed['details'])==len(after['details'])+1 and failed['images']==after['images']
    expect(board).to_be_hidden()
    button.click()
    expect(board).to_be_visible(timeout=15000)
    recovered=state()
    assert len(recovered['details'])==len(after['details'])+2
    assert recovered['images']==after['images']
    expect(button).to_have_text('概要 ▾')
    page.screenshot(path=str(out/f'live-summary-{width}x{height}.png'),animations='disabled')
    report['expected_live_detail_calls']=len(recovered['details'])
    report['viewports'][f'{width}x{height}']['explicit_live']=dict(
        before=before,after=after,failed=failed,recovered=recovered,checks=[
            'no-GET-before-click','single-identity-checked-detail-GET',
            'original-description-html-as-safe-layout','no-image-reload',
            'Escape-and-close-reopen-no-extra-GET','failed-HTTP-retry-on-click-only',
            'retry-does-not-reload-image'])


def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int)
    parser.add_argument('--fixture',type=Path)
    parser.add_argument('--nonce')
    args=parser.parse_args()
    if args.serve:
        if not args.port or not 1024<=args.port<=65535:raise ValueError('invalid owned port')
        if args.fixture is None or not args.fixture.is_dir() or not args.nonce:
            raise ValueError('dedicated synthetic fixture required')
        serve(args.port,args.fixture,args.nonce)
        return
    if args.output is None:
        parser.error('--output is required for the browser checker')
    output=args.output.resolve()
    allowed=(ROOT/'artifacts').resolve()
    if not output.is_relative_to(allowed) or output==allowed:
        raise ValueError('output must live under candidate artifacts')
    output.mkdir(parents=True,exist_ok=False)
    before=source_sha()
    port=free_port()
    nonce=secrets.token_hex(24)
    url=f'http://{HOST}:{port}'
    report={'url':url,'channel':args.channel,'headed':args.headed,
            'source_before':before,'viewports':{},'console_errors':[],
            'page_errors':[],'external_requests':[],'child_pid':None,'child_exit':None,
            'shutdown_attempted':False,'shutdown_http':None,'shutdown_failed':False,
            'owned_root_running':False,'remaining_owned_root_pids':[],
            'process_observation_scope':'Popen root only; descendants not observed',
            'forced_cleanup':[],
            'port_released':False,'success':False,'error':None}
    process=None
    try:
        with (output/'app-stderr.log').open('wb') as err:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),
                '--serve','--port',str(port),'--fixture',str(output),'--nonce',nonce],
                cwd=str(ROOT),env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=err)
            report['child_pid']=process.pid
            wait_for_health(url,process)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(
                    channel=None if args.channel=='chromium' else args.channel,
                    headless=not args.headed)
                context=browser.new_context(viewport=dict(width=1280,height=720))
                def guard(route):
                    req=urlsplit(route.request.url)
                    if (req.scheme,req.netloc)!=('http',f'{HOST}:{port}'):
                        report['external_requests'].append(req.scheme+'://'+req.netloc)
                        route.abort()
                    else:route.continue_()
                context.route('**/*',guard)
                page=context.new_page()
                page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                page.on('console',lambda message:report['console_errors'].append(message.text)
                    if message.type=='error' else None)
                try:
                    for width,height in ((1280,720),(1920,1080)):
                        inspect(page,width,height,output,report)
                        inspect_explicit_discovery(page,width,height,report,nonce,output)
                finally:
                    context.close()
                    browser.close()
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        report['traceback']=traceback.format_exc()[-6000:]
    finally:
        if process is not None:
            if process.poll() is None:
                report['shutdown_attempted']=True
                try:
                    from urllib.request import Request, urlopen
                    request=Request(url+'/_fixture/shutdown',method='POST',headers={'X-Acceptance':nonce})
                    with urlopen(request,timeout=6) as response:
                        report['shutdown_http']=response.status
                except Exception as error:
                    report['shutdown_failed']=True
                    report['shutdown_error']=type(error).__name__
                # Allow normal shutdown even after a lost HTTP acknowledgement.
                # A timeout is recorded; this checker never terminates the app.
                try:
                    process.wait(timeout=15)
                except Exception as error:
                    report['shutdown_failed']=True
                    report['shutdown_wait_error']=type(error).__name__
            else:
                report['shutdown_failed']=True
                report['shutdown_error']='process_exited_before_shutdown'
            report['child_exit']=process.poll()
            report['owned_root_running']=report['child_exit'] is None
            if report['owned_root_running']:
                report['remaining_owned_root_pids']=[process.pid]
        report['port_released']=port_released(port)
        report['source_after']=source_sha()
        report['source_changed']=report['source_before']!=report['source_after']
        report['success']=(report['error'] is None and
            len(report['viewports'])==2 and not report['external_requests']
            and not report['page_errors'] and not report['console_errors']
            and not report['source_changed'] and report['port_released']
            and report['shutdown_http']==200 and not report['shutdown_failed']
            and not report['owned_root_running']
            and report['child_exit']==0)
        (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,
            indent=2),encoding='utf-8')
    if not report['success']:raise SystemExit(1)


if __name__=='__main__':
    main()
