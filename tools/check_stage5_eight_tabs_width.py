"""Unmeasured 720/1280 responsive navigation of all 8 REAL NiceGUI tabs.

Only a new managed generated fixture root; existing 1768px/Flet snapshots are
not rerun. This is not equal-input original Flet full-parity acceptance.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit
from hashlib import sha256
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import check_stage5_l13_cross_tabs_browser as fixture
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health

TABS=('download','queue','failed','dedup','learning','ranking','library','recommend')
def sha(p):return sha256(p.read_bytes()).hexdigest()

def run(out):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'artifacts'):
        raise ValueError('fresh generated-only owner path required')
    out.mkdir(parents=True,exist_ok=False)
    port=free_port();base=f'http://127.0.0.1:{port}'
    nonce=secrets.token_hex(24)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request_headers={'X-Acceptance':nonce}
    watched=[Path(__file__),Path(fixture.__file__),ROOT/'tools/owned_process_tree.py',
             ROOT/'tools/run_browser_checks.py']
    hashes={str(p.relative_to(ROOT)):sha(p) for p in watched}
    source_before=source_hashes()
    report={'schema':1,'scope':'real NiceGUI 8-tab generated managed shell at 720/1280 CSS px',
            'formal_pass_added':0,'full_original_flet_comparison':False,
            'rows':[],'browser_external_requests':0,'javascript_errors':0,
            'success':False,'failure':None,'app':{'normal_exit':False,'forced_cleanup':False}}
    proc=tree=None;ready=False
    try:
        with (out/'server.log').open('xb') as logfile:
            proc=subprocess.Popen([sys.executable,'-B',str(Path(fixture.__file__)),
                '--serve',str(port),'--sandbox',str(out/'generated-managed'),
                '--nonce',nonce],cwd=ROOT,env=child_environment(),
                stdin=subprocess.DEVNULL,stdout=logfile,stderr=logfile,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            report['app']['pid']=proc.pid
            tree=OwnedProcessTree(proc)
            if tree.errors:raise RuntimeError('unknown owned process')
            wait_for_health(base,proc,expected_data='pixiv-readonly')
            req=urllib.request.Request(base+'/_p3_test/snapshot',headers=request_headers)
            with opener.open(req,timeout=8) as response:state=json.loads(response.read())
            if proc.poll() is not None or len(state['receipts'])!=1:
                raise RuntimeError('generated managed startup not confirmed')
            ready=True
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    for width,height in ((720,900),(1280,720)):
                        ctx=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=1)
                        def guard(route):
                            u=urlsplit(route.request.url)
                            if (u.scheme,u.netloc)!=('http',f'127.0.0.1:{port}'):
                                report['browser_external_requests']+=1;route.abort()
                            else:route.continue_()
                        ctx.route('**/*',guard)
                        page=ctx.new_page()
                        page.on('pageerror',lambda _:report.__setitem__(
                            'javascript_errors',report['javascript_errors']+1))
                        page.goto(base,wait_until='domcontentloaded',timeout=25000)
                        expect(page.locator('nav[aria-label="メインナビゲーション"]')).to_be_visible()
                        for tab in TABS:
                            button=page.get_by_test_id('tab-'+tab)
                            expect(button).to_be_visible()
                            expect(button).to_be_enabled()
                            button.click()
                            expect(button).to_have_attribute('aria-current','page',timeout=15000)
                            metrics=page.evaluate("""() => {
                                const nav=document.querySelector('nav[aria-label="メインナビゲーション"]');
                                const nr=nav?.getBoundingClientRect();
                                const body=document.body.getBoundingClientRect();
                                return {viewport_width:innerWidth,
                                    document_scroll_width:document.documentElement.scrollWidth,
                                    body_left:body.left, body_right:body.right,
                                    nav_left:nr?.left,nav_right:nr?.right,
                                    nav_scroll_width:nav?.scrollWidth,
                                    nav_client_width:nav?.clientWidth};
                            }""")
                            if (metrics['viewport_width']!=width or
                                metrics['document_scroll_width']>width+3 or
                                metrics['body_left']<-.9 or metrics['body_right']>width+3):
                                raise AssertionError(f'unexpected full-page overflow {width}/{tab}: {metrics}')
                            img=out/f'{width}-{tab}.png'
                            page.screenshot(path=str(img),full_page=False)
                            report['rows'].append({'width':width,'tab':tab,
                                'tab_enabled':True,'click_active':True,
                                'layout':metrics,'screenshot_sha256':sha(img)})
                        ctx.close()
                finally:browser.close()
            if report['browser_external_requests'] or report['javascript_errors']:
                raise AssertionError('external browser request or JS error')
            report['tested']=len(report['rows'])==16
    except Exception as error:
        report['failure']=type(error).__name__
        report['error_short']=str(error)[:260]
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and ready:
                    req=urllib.request.Request(base+'/_p3_test/shutdown',
                        headers=request_headers,data=b'',method='POST')
                    with opener.open(req,timeout=8) as resp:
                        report['app']['shutdown_ack']=json.loads(resp.read())
                    report['app']['normal_exit']=proc.wait(timeout=20)==0
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    stop_child(proc)
            except Exception:
                report['app']['shutdown_error']='natural stop unavailable'
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    try:stop_child(proc)
                    except Exception:pass
            report['app']['exit_code']=proc.poll()
        report['app']['tree']=tree.finish() if tree else {}
        report['app']['port_released']=port_released(port)
        report['source_changed']=source_before!=source_hashes()
        report['harness_changed']=hashes!={str(p.relative_to(ROOT)):sha(p) for p in watched}
        owned=report['app']['tree']
        report['success']=bool(report.get('tested') and report['app'].get('exit_code')==0
            and report['app']['normal_exit'] and not report['app']['forced_cleanup']
            and report['app']['port_released'] and owned.get('remaining_count')==0
            and not owned.get('errors') and not report['source_changed']
            and not report['harness_changed'])
        with (out/'result.json').open('x',encoding='utf8') as f:
            json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({'success':report['success'],'screens':len(report['rows']),
        'failure':report['failure'],'remaining':report['app']['tree'].get('remaining_count'),
        'formal_pass_added':0}))
    return 0 if report['success'] else 1

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('usage: check_stage5_eight_tabs_width.py NEW_ARTIFACT_PATH')
    raise SystemExit(run(Path(sys.argv[1])))
