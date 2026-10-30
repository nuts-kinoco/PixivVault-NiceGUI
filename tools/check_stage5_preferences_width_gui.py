"""New narrow/desktop width GUI measurement of the real settings dialog.

No repeat of existing setting persistence tests. Synthetic-only local app;
measure bounds and operation availability at 720 and 1280 CSS pixel widths.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright,expect
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health
from tools import check_stage5_s08_frames_next_save as server

def sha(path):return sha256(path.read_bytes()).hexdigest()

def run(out):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'artifacts'):
        raise ValueError('fresh dedicated GUI output required')
    out.mkdir(parents=True,exist_ok=False)
    port=free_port();url=f'http://127.0.0.1:{port}';nonce=secrets.token_hex(24)
    headers={'X-Acceptance':nonce}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    originals=source_hashes()
    watched=[Path(__file__),Path(server.__file__),ROOT/'tools/owned_process_tree.py',
             ROOT/'tools/run_browser_checks.py']
    harness={str(p.relative_to(ROOT)):sha(p) for p in watched}
    report={'schema':1,'scope':'generated NiceGUI settings drawer actual viewport geometries',
        'ids':['S07','S08'],'formal_pass_added':0,'success':False,'tested_widths':[],
        'steps':[],'external_requests':0,'js_errors':0,
        'app':{'normal_exit':False,'forced_cleanup':False},'failure':None}
    proc=None;tree=None;ready=False
    try:
        with (out/'server.log').open('xb') as log:
            proc=subprocess.Popen([sys.executable,'-B',str(Path(server.__file__)),
                '--serve',str(port),'--sandbox',str(out/'generated-data'),'--nonce',nonce],
                cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            tree=OwnedProcessTree(proc)
            report['app']['pid']=proc.pid
            if tree.errors:raise RuntimeError('unverified child ownership')
            wait_for_health(url,proc,expected_data='pixiv-readonly')
            ready=True
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    for width,height in ((720,900),(1280,720)):
                        ctx=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=1)
                        def guard(route):
                            p=urlsplit(route.request.url)
                            if (p.scheme,p.netloc)!=('http',f'127.0.0.1:{port}'):
                                report['external_requests']+=1;route.abort()
                            else:route.continue_()
                        ctx.route('**/*',guard)
                        page=ctx.new_page()
                        page.on('pageerror',lambda _:report.__setitem__('js_errors',report['js_errors']+1))
                        page.goto(url,wait_until='domcontentloaded',timeout=25000)
                        settings=page.get_by_test_id('preferences-open')
                        expect(settings).to_be_visible()
                        settings.click()
                        dialog=page.get_by_test_id('preferences-dialog')
                        expect(dialog).to_be_visible()
                        for key in ('novel_save_format','ugoira_save_format'):
                            item=dialog.get_by_test_id('preference-'+key)
                            expect(item).to_be_enabled()
                            item.scroll_into_view_if_needed()
                            expect(item).to_be_visible()
                        measured=dialog.evaluate("""root => {
                            const card=root.querySelector('.q-card');
                            if(!card) return {missing_card:true};
                            const rect=card.getBoundingClientRect();
                            return {viewport_width:innerWidth,viewport_height:innerHeight,
                                card_left:rect.left,card_right:rect.right,
                                card_width:rect.width,card_height:rect.height,
                                card_scroll_width:card.scrollWidth,
                                card_client_width:card.clientWidth,
                                card_scroll_height:card.scrollHeight,
                                card_client_height:card.clientHeight,
                                card_overflow_x:getComputedStyle(card).overflowX};
                        }""")
                        if (measured.get('missing_card')
                                or measured['card_left']<-.9
                                or measured['card_right']>width+.9
                                or measured['card_scroll_width']>measured['card_client_width']+2):
                            raise AssertionError(f'preferences dialog horizontal overflow at {width}: {measured}')
                        report['tested_widths'].append(measured)
                        report['steps'].append(f'opened real dialog at {width}x{height}: both setting controls visible and no horizontal overflow')
                        page.screenshot(path=str(out/f'preferences-{width}.png'),full_page=False)
                        ctx.close()
                finally:browser.close()
            if report['external_requests'] or report['js_errors']:
                raise AssertionError('unexpected external network or JS errors')
            report['tested']=True
    except Exception as exc:
        report['failure']=type(exc).__name__
        report['error_short']=str(exc)[:240]
        report['last_step']=report['steps'][-1] if report['steps'] else 'startup'
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and ready:
                    req=urllib.request.Request(url+'/_p3_test/shutdown',headers=headers,data=b'',method='POST')
                    with opener.open(req,timeout=8) as response:
                        report['app']['shutdown_ack']=json.loads(response.read())
                    report['app']['normal_exit']=proc.wait(timeout=20)==0
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    stop_child(proc)
            except Exception:
                report['app']['shutdown_error']='natural exit unavailable'
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    try:stop_child(proc)
                    except Exception:pass
            report['app']['exit_code']=proc.poll()
        report['app']['tree']=tree.finish() if tree is not None else {}
        report['app']['port_released']=port_released(port)
        report['source_changed']=originals!=source_hashes()
        report['harness_changed']=harness!={str(p.relative_to(ROOT)):sha(p) for p in watched}
        t=report['app']['tree']
        report['success']=bool(report.get('tested') and report['app'].get('exit_code')==0
            and report['app']['normal_exit'] and not report['app']['forced_cleanup']
            and report['app']['port_released'] and t.get('remaining_count')==0
            and not t.get('errors') and not report['source_changed'] and not report['harness_changed'])
        with (out/'result.json').open('x',encoding='utf8') as fp:
            json.dump(report,fp,ensure_ascii=False,indent=2)
    print(json.dumps({'success':report['success'],'widths':len(report['tested_widths']),
        'failure':report['failure'],'owned_remaining':report['app']['tree'].get('remaining_count'),
        'formal_pass_added':0}))
    return 0 if report['success'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(run(args.output))
