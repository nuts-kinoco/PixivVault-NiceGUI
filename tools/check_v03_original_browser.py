"""V03 original-page quality real Edge acceptance, generated offline images.

The user must click before the first original GET. The application and browser
only touch localhost; there is no Cookie, Pixiv call, original DB, or saved job.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

if __package__ in (None,''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import (ROOT,child_environment,free_port,port_released,
                                      source_hashes,stop_child,wait_for_health)


def serve(port, nonce):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from types import SimpleNamespace
    from pixivvault_nicegui.models import Artwork
    from pixivvault_nicegui.media import DemoImageCatalog
    from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.gallery import GalleryView
    from pixivvault_nicegui.ui.theme import CSS

    class CountingImages(DemoImageCatalog):
        def __init__(self,arts):
            super().__init__(arts)
            self.requests=Counter()
        def get(self, work_id, page_no=0, variant='view'):
            blob=super().get(work_id,page_no,variant)
            self.requests[(work_id,page_no,variant)] += 1
            return blob

    arts=(Artwork('101','First generated','Synthetic author',author_id='91',page_count=2,aspect_ratio=1.5),
          Artwork('102','Second generated','Synthetic author',author_id='91',page_count=1,aspect_ratio=1.5))
    catalog=CountingImages(arts)
    registry=LibraryImageRegistry(catalog)
    service=SimpleNamespace(artworks=arts,images=registry,live_client=None,managed_downloads=None)
    register_media_routes(app,registry)

    def auth(value):
        if not secrets.compare_digest(value or '',nonce):
            raise HTTPException(403)

    @app.get('/healthz')
    def health():
        return dict(status='ok',mode='demo',data='synthetic')

    @app.get('/_fixture/state')
    def state(x_acceptance: str|None=Header(default=None)):
        auth(x_acceptance)
        return {'originals':sum(n for (_,_,v),n in catalog.requests.items() if v=='original'),
                'by_page':{'%s:%d:%s'%k:v for k,v in catalog.requests.items()}}

    @app.post('/_fixture/shutdown')
    def shutdown(x_acceptance: str|None=Header(default=None)):
        auth(x_acceptance)
        app.shutdown()
        return {'stopping': True}

    @ui.page('/')
    def page():
        ui.add_css(CSS)
        with ui.column().classes('pv-shell'):
            with ui.column().classes('pv-content w-full'):
                GalleryView(service)

    app.on_shutdown(registry.close)
    ui.run(host='127.0.0.1',port=port,show=False,reload=False,
           binding_refresh_interval=None,timeout_graceful_shutdown=5)


def inspect(page,url,headers,evidence):
    def state():
        response=page.request.get(url+'/_fixture/state',headers=headers)
        assert response.ok
        return response.json()
    page.goto(url,wait_until='domcontentloaded')
    assert state()['originals']==0, 'original pages fetched on page bootstrap'
    page.locator('[data-work-id="101"]').click()
    viewer=page.get_by_test_id('gallery-viewer')
    expect(viewer).to_be_visible()
    quality=viewer.get_by_test_id('viewer-quality-toggle')
    image=viewer.get_by_test_id('viewer-image').locator('img[data-pv-displayed="true"]')
    expect(quality).to_be_enabled(timeout=15000)
    expect(viewer).to_have_attribute('data-viewer-quality','view')
    assert image.evaluate('img => img.complete && img.naturalWidth === 640')
    assert state()['originals']==0, 'opening viewer fetched original without consent'
    regular_src=image.get_attribute('src')
    quality.click()
    expect(viewer).to_have_attribute('data-viewer-quality','original')
    expect(image).to_have_attribute('src',re.compile(r'/original\.png'),timeout=15000)
    expect(quality).to_be_enabled(timeout=15000)
    assert image.evaluate('img => img.complete && img.naturalWidth === 1280')
    assert '/original.png' in image.get_attribute('src')
    after=state()
    assert after['originals']==1 and after['by_page'].get('101:0:original')==1
    quality.click()
    expect(viewer).to_have_attribute('data-viewer-quality','view')
    expect(image).to_have_attribute('src',re.compile(r'/view\.png'),timeout=15000)
    expect(quality).to_be_enabled(timeout=15000)
    assert image.evaluate('img => img.complete && img.naturalWidth === 640')
    assert image.get_attribute('src') != regular_src or '/view.png' in image.get_attribute('src')
    assert state()['originals']==1
    # Changing the page returns to regular, does not prefetch the next original.
    viewer.get_by_role('button',name='次のページ',exact=True).click()
    expect(viewer.locator('.pv-viewer-header .pv-counter')).to_have_text('2/2')
    expect(quality).to_be_enabled(timeout=15000)
    expect(viewer).to_have_attribute('data-viewer-quality','view')
    expect(image).to_have_attribute('src',re.compile(r'/view\.png'),timeout=15000)
    assert image.evaluate('img => img.complete && img.naturalWidth === 640')
    assert state()['originals']==1
    quality.click()
    expect(image).to_have_attribute('src',re.compile(r'/original\.png'),timeout=15000)
    expect(quality).to_be_enabled(timeout=15000)
    assert image.evaluate('img => img.complete && img.naturalWidth === 1280')
    assert state()['by_page'].get('101:1:original')==1
    viewer.get_by_role('button',name='閉じる',exact=True).click()
    expect(viewer).not_to_be_visible()
    page.locator('[data-work-id="102"]').click()
    expect(viewer).to_be_visible()
    expect(quality).to_be_enabled(timeout=15000)
    expect(viewer).to_have_attribute('data-viewer-quality','view')
    expect(image).to_have_attribute('src',re.compile(r'/view\.png'),timeout=15000)
    assert image.evaluate('img => img.complete && img.naturalWidth === 640')
    assert state()['by_page'].get('102:0:original') is None
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    page.screenshot(path=str(evidence/'viewer-original.png'),animations='disabled')
    return dict(start_original_gets=0,first_original_gets=1,second_page_original_gets=1,
                default_width=640,original_width=1280,reset_on_page=True,
                reset_on_new_work=True,observed=state())


def run(output,channel):
    output=output.resolve()
    allowed=(ROOT/'artifacts'/'p3-315-v03-original').resolve()
    if output==allowed or not output.is_relative_to(allowed) or output.exists():
        raise ValueError('fresh V03-owned evidence folder required')
    output.mkdir(parents=True)
    before=source_hashes()
    entries=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel=channel,headless=True)
        try:
            for width,height in ((1280,720),(1920,1080)):
                for restart in (False,True):
                    evidence=output/(f'{width}-'+('restart' if restart else 'initial'))
                    evidence.mkdir()
                    port,nonce=free_port(),secrets.token_hex(24)
                    url=f'http://127.0.0.1:{port}';headers={'X-Acceptance':nonce}
                    result=dict(viewport=[width,height],restart=restart,success=False,
                                external_requests=[],console_errors=[],page_errors=[])
                    process=None
                    context=browser.new_context(viewport={'width':width,'height':height})
                    origin=urlsplit(url)
                    def route(r):
                        to=urlsplit(r.request.url)
                        if (to.scheme,to.netloc)==(origin.scheme,origin.netloc):r.continue_()
                        else:
                            result['external_requests'].append(to.netloc)
                            r.abort()
                    context.route('**/*',route)
                    page=context.new_page()
                    page.on('pageerror',lambda exc:result['page_errors'].append(str(exc)))
                    page.on('console',lambda m:result['console_errors'].append(m.text)
                            if m.type=='error' else None)
                    with (evidence/'server-stderr.log').open('wb') as stderr:
                        try:
                            process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),
                                '--serve',str(port),'--nonce',nonce],cwd=ROOT,env=child_environment(),
                                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                            result['pid']=process.pid
                            wait_for_health(url,process)
                            result['checks']=inspect(page,url,headers,evidence)
                            assert not result['page_errors'] and not result['console_errors']
                            assert not result['external_requests']
                            result['success']=True
                        except Exception as exc:
                            result['failure']=f'{type(exc).__name__}: {exc}'[:1000]
                            result['traceback']=traceback.format_exc()[-5000:]
                            try:page.screenshot(path=str(evidence/'failure.png'))
                            except Exception:pass
                        finally:
                            if process is not None and process.poll() is None:
                                try:
                                    response=page.request.post(url+'/_fixture/shutdown',headers=headers,timeout=6000)
                                    result['shutdown_http']=response.status
                                    process.wait(timeout=15)
                                except Exception:pass
                            try:context.close()
                            except Exception as exc:result['browser_close_error']=type(exc).__name__
                            if process is not None:
                                stop_child(process)
                                result['exit_code']=process.returncode
                                result['normal_exit']=process.returncode==0
                            result['port_released']=port_released(port)
                            result['success'] &= (result.get('normal_exit',False) and
                                                  result['port_released'] and
                                                  not result.get('browser_close_error'))
                            (evidence/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
                            entries.append(result)
                    if not result['success']:
                        break
                if not entries[-1]['success']:
                    break
        finally:browser.close()
    after=source_hashes()
    success=len(entries)==4 and all(x['success'] for x in entries) and before==after
    manifest=dict(success=success,source_changed=before!=after,source_before=before,
                  source_after=after,checker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  runs=entries)
    (output/'result.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(success=success,runs=len(entries),source_changed=before!=after,
                          failed=[x.get('failure') for x in entries if not x['success']])))
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    options=parser.parse_args()
    if options.serve:
        if not options.nonce or options.serve in (8089,):parser.error('dedicated port/nonce required')
        serve(options.serve,options.nonce)
    else:
        if options.output is None:parser.error('--output required')
        raise SystemExit(run(options.output,options.channel))
