"""Bounded S07/S08 actual NiceGUI preferences selections and persistence.

Only generated loopback fixture and its dedicated marked preference DB. This
does not claim next actual media-save format or Flet full product acceptance.
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
from tools import check_p3_residuals_browser as fixture_server
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health

def sha(p):return sha256(p.read_bytes()).hexdigest()

def serve(port,sandbox,nonce):
    def deny(event,args):
        if event=='socket.connect':
            a=args[1];h=a[0] if isinstance(a,tuple) else None
            if h not in ('127.0.0.1','::1','localhost'):raise RuntimeError('external network blocked')
        if event=='socket.getaddrinfo' and args[0] not in ('127.0.0.1','::1','localhost',None):
            raise RuntimeError('external DNS blocked')
    sys.addaudithook(deny)
    fixture_server.serve(port,sandbox,nonce)

def run(out):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'artifacts'):
        raise ValueError('fresh private artifacts directory required')
    out.mkdir(parents=True,exist_ok=False)
    port=free_port();url=f'http://127.0.0.1:{port}';nonce=secrets.token_hex(24)
    headers={'X-Acceptance':nonce}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def snapshot():
        req=urllib.request.Request(url+'/_p3_test/snapshot',headers=headers)
        with opener.open(req,timeout=8) as resp:return json.loads(resp.read())
    watched=[Path(__file__),Path(fixture_server.__file__),ROOT/'tools/p3_residuals_fixtures.py',
             ROOT/'tools/owned_process_tree.py',ROOT/'tools/run_browser_checks.py']
    harness={str(p.relative_to(ROOT)):sha(p) for p in watched}
    original_source=source_hashes()
    report={'schema':1,'ids':['S07','S08'],'scope':'generated settings GUI and marked DB only',
       'formal_pass_added':0,'steps':[],'success':False,'failure':None,
       'external_requests':0,'page_errors':0,'app':{'normal_exit':False,'forced_cleanup':False}}
    proc=None;tree=None;owned=False
    try:
        with (out/'server.log').open('xb') as log:
            proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),
                '--serve',str(port),'--sandbox',str(out/'generated-data'),'--nonce',nonce],
                cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            report['app']['pid']=proc.pid
            tree=OwnedProcessTree(proc)
            if tree.errors:raise RuntimeError('unknown process owner')
            wait_for_health(url,proc,expected_data='pixiv-readonly')
            before=snapshot()
            if before['calls'] or before['receipts'] or proc.poll() is not None:
                raise RuntimeError('unexpected private initial state')
            assert before['preferences']['values']['novel_save_format']=='epub'
            assert before['preferences']['values']['ugoira_save_format']=='gif'
            owned=True
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    ctx=browser.new_context(viewport={'width':1280,'height':720},device_scale_factor=1)
                    def guard(route):
                        p=urlsplit(route.request.url)
                        if (p.scheme,p.netloc)!=('http',f'127.0.0.1:{port}'):
                            report['external_requests']+=1
                            route.abort()
                        else:route.continue_()
                    ctx.route('**/*',guard)
                    page=ctx.new_page()
                    page.on('pageerror',lambda _:report.__setitem__('page_errors',report['page_errors']+1))
                    page.goto(url,wait_until='domcontentloaded',timeout=25000)
                    page.get_by_test_id('preferences-open').click()
                    dialog=page.get_by_test_id('preferences-dialog')
                    expect(dialog).to_be_visible()
                    for testid,label,value in [
                        ('preference-novel_save_format','TXTのみ','txt'),
                        ('preference-ugoira_save_format','MP4動画','mp4')]:
                        control=dialog.get_by_test_id(testid)
                        expect(control).to_be_enabled()
                        control.click()
                        page.get_by_role('option',name=label,exact=True).last.click()
                        expect(control).to_have_attribute('data-value',value)
                        report['steps'].append(f'actual click {testid} -> {label}')
                    dialog.get_by_test_id('preferences-save').click()
                    expect(dialog.get_by_test_id('preferences-status')).to_contain_text('設定を保存しました',timeout=10000)
                    current=snapshot()
                    for key,value in [('novel_save_format','txt'),('ugoira_save_format','mp4')]:
                        assert current['preferences']['values'][key]==value
                    assert current['preferences']['revision']==before['preferences']['revision']+1
                    report['steps'].append('one actual Save click persisted both choices transactionally')
                    dialog.get_by_test_id('preferences-reload').click()
                    expect(dialog.get_by_test_id('preference-novel_save_format')).to_have_attribute('data-value','txt')
                    expect(dialog.get_by_test_id('preference-ugoira_save_format')).to_have_attribute('data-value','mp4')
                    report['steps'].append('actual reload controls restored persisted selections')
                    page.reload(wait_until='domcontentloaded')
                    page.get_by_test_id('preferences-open').click()
                    dlg=page.get_by_test_id('preferences-dialog')
                    expect(dlg.get_by_test_id('preference-novel_save_format')).to_have_attribute('data-value','txt')
                    expect(dlg.get_by_test_id('preference-ugoira_save_format')).to_have_attribute('data-value','mp4')
                    report['steps'].append('browser reload and new NiceGUI client state restored selections')
                    second=ctx.new_page()
                    second.goto(url,wait_until='domcontentloaded')
                    second.get_by_test_id('preferences-open').click()
                    second_dialog=second.get_by_test_id('preferences-dialog')
                    expect(second_dialog.get_by_test_id('preference-novel_save_format')).to_have_attribute('data-value','txt')
                    expect(second_dialog.get_by_test_id('preference-ugoira_save_format')).to_have_attribute('data-value','mp4')
                    report['steps'].append('second independently connected client reads private persisted preferences')
                    report['final_preference_values']={k:current['preferences']['values'][k]
                        for k in ('novel_save_format','ugoira_save_format')}
                    report['before_revision']=before['preferences']['revision']
                    report['after_revision']=current['preferences']['revision']
                    page.screenshot(path=str(out/'preferences-after-reload.png'),full_page=False)
                    if snapshot()['receipts'] or report['external_requests'] or report['page_errors']:
                        raise AssertionError('unexpected file save, external request or page error')
                finally:browser.close()
            report['tested']=True
    except Exception as error:
        report['failure']=type(error).__name__
        report['error_short']=str(error)[:250]
        report['last_step']=report['steps'][-1] if report['steps'] else 'startup'
    finally:
        if proc is not None:
            try:
                if proc.poll() is None and owned:
                    req=urllib.request.Request(url+'/_p3_test/shutdown',headers=headers,data=b'',method='POST')
                    with opener.open(req,timeout=8) as resp:report['app']['shutdown_ack']=json.loads(resp.read())
                    report['app']['normal_exit']=proc.wait(timeout=20)==0
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    stop_child(proc)
            except Exception:
                report['app']['shutdown_error']='owned normal shutdown unavailable'
                if proc.poll() is None:
                    report['app']['forced_cleanup']=True
                    try:stop_child(proc)
                    except Exception:pass
            report['app']['exit_code']=proc.poll()
        report['app']['tree']=tree.finish() if tree is not None else {}
        report['app']['port_released']=port_released(port)
        report['source_changed']=source_hashes()!=original_source
        report['harness_changed']=harness!={str(p.relative_to(ROOT)):sha(p) for p in watched}
        part=report['app']['tree']
        report['success']=bool(report.get('tested') and report['app'].get('exit_code')==0
            and report['app']['normal_exit'] and not report['app']['forced_cleanup']
            and report['app']['port_released'] and part.get('remaining_count')==0
            and not part.get('errors') and not report['source_changed'] and not report['harness_changed'])
        with (out/'result.json').open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({'success':report['success'],'steps':len(report['steps']),
        'failure':report['failure'],'owner_remaining':report['app']['tree'].get('remaining_count'),
        'formal_pass_added':0}))
    return 0 if report['success'] else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serve',type=int);p.add_argument('--sandbox',type=Path)
    p.add_argument('--nonce');p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.serve is not None:
        if not args.sandbox or not args.nonce:p.error('serve requires sandbox and nonce')
        serve(args.serve,args.sandbox,args.nonce)
    else:
        if not args.output:p.error('fresh output required')
        raise SystemExit(run(args.output))
