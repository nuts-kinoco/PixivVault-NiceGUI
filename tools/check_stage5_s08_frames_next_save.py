"""S08 new GUI choice folder -> next original generated ugoira frames save.

Dedicated new generated root, real NiceGUI dialog click, one normal server
shutdown, then original services with the same owned preference DB. No Cookie,
original user data, external network, original Flet or codec installation.
"""
from __future__ import annotations
import argparse
import base64
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from PIL import Image
from playwright.sync_api import sync_playwright,expect
from tools import check_p3_residuals_browser as fixture_server
from tools.p3_residuals_fixtures import create_services
from tools.owned_process_tree import OwnedProcessTree
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health

def sha(path):return sha256(path.read_bytes()).hexdigest()

def serve(port,sandbox,nonce):
    def deny(event,args):
        if event=='socket.connect':
            address=args[1];host=address[0] if isinstance(address,tuple) else None
            if host not in ('localhost','127.0.0.1','::1'):
                raise RuntimeError('network outside local denied')
        if event=='socket.getaddrinfo' and args[0] not in ('localhost','127.0.0.1','::1',None):
            raise RuntimeError('outside DNS denied')
    sys.addaudithook(deny)
    fixture_server.serve(port,sandbox,nonce)

def run(out):
    out=out.resolve()
    if out.exists() or not out.is_relative_to(ROOT/'artifacts'):
        raise ValueError('new, dedicated generated root required')
    out.mkdir(parents=True,exist_ok=False)
    data=out/'generated-data'
    port=free_port();url=f'http://127.0.0.1:{port}';nonce=secrets.token_hex(24)
    headers={'X-Acceptance':nonce}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def snapshot():
        req=urllib.request.Request(url+'/_p3_test/snapshot',headers=headers)
        with opener.open(req,timeout=8) as response:return json.loads(response.read())
    monitored=[Path(__file__),Path(fixture_server.__file__),
        ROOT/'tools/p3_residuals_fixtures.py',ROOT/'tools/run_browser_checks.py',
        ROOT/'tools/owned_process_tree.py']
    hashes={str(p.relative_to(ROOT)):sha(p) for p in monitored}
    product_before=source_hashes()
    report={'schema':1,'original_id':'S08','formal_pass_added':0,
        'scope':'real generated NiceGUI setting choice+one subsequent generated original frames job',
        'steps':[],'observations':{},'success':False,'failure':None,
        'external_requests':0,'js_errors':0,
        'app':{'normal_exit':False,'forced_cleanup':False}}
    proc=None;tree=None;confirmed=False;services=None
    try:
        with (out/'server.log').open('xb') as log:
            proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),
                '--serve',str(port),'--sandbox',str(data),'--nonce',nonce],
                cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            report['app']['pid']=proc.pid
            tree=OwnedProcessTree(proc)
            if tree.errors:raise RuntimeError('unknown owned server PID/ancestry')
            wait_for_health(url,proc,expected_data='pixiv-readonly')
            original=snapshot()
            if (original['calls'] or original['receipts'] or original['ugoira']
                    or proc.poll() is not None):
                raise RuntimeError('private fixture has unexpected initial records')
            if original['preferences']['values']['ugoira_save_format']!='gif':
                raise RuntimeError('new generated default unexpectedly changed')
            confirmed=True
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                try:
                    context=browser.new_context(viewport={'width':1280,'height':720},device_scale_factor=1)
                    def guard(route):
                        p=urlsplit(route.request.url)
                        if (p.scheme,p.netloc)!=('http',f'127.0.0.1:{port}'):
                            report['external_requests']+=1;route.abort()
                        else:route.continue_()
                    context.route('**/*',guard)
                    page=context.new_page()
                    page.on('pageerror',lambda _:report.__setitem__('js_errors',report['js_errors']+1))
                    page.goto(url,wait_until='domcontentloaded',timeout=25000)
                    page.get_by_test_id('preferences-open').click()
                    dlg=page.get_by_test_id('preferences-dialog')
                    expect(dlg).to_be_visible()
                    control=dlg.get_by_test_id('preference-ugoira_save_format')
                    expect(control).to_be_enabled()
                    control.click()
                    page.get_by_role('option',name='フォルダ（フレームとJSON）',exact=True).last.click()
                    expect(control).to_have_attribute('data-value','folder')
                    report['steps'].append('actual GUI selected ugoira folder and acknowledged pending value')
                    dlg.get_by_test_id('preferences-save').click()
                    expect(dlg.get_by_test_id('preferences-status')).to_contain_text('設定を保存しました')
                    updated=snapshot()
                    if (updated['preferences']['values']['ugoira_save_format']!='folder'
                            or updated['preferences']['revision']!=original['preferences']['revision']+1):
                        raise AssertionError('GUI-selected ugoira folder did not persist')
                    report['steps'].append('actual Save click committed folder to private marked settings DB')
                    page.screenshot(path=str(out/'s08-folder-settings.png'),full_page=False)
                    if report['external_requests'] or report['js_errors']:
                        raise AssertionError('unexpected external browser requests or JS exceptions')
                finally:browser.close()
            if snapshot()['receipts'] or snapshot()['ugoira']:
                raise AssertionError('settings GUI accidentally saved media')
            # One owned normal shutdown, before opening the same private
            # marked DB with a fresh product services instance.
            req=urllib.request.Request(url+'/_p3_test/shutdown',headers=headers,data=b'',method='POST')
            with opener.open(req,timeout=8) as response:report['app']['shutdown_ack']=json.loads(response.read())
            report['app']['normal_exit']=proc.wait(timeout=20)==0
            if not report['app']['normal_exit']:
                raise RuntimeError('normal owned server shutdown failed')
            report['steps'].append('owned GUI server exited naturally; same private preference root remains')
            services,reader=create_services(FastAPI(),data)
            saved_preference=services.preferences.snapshot()
            if (saved_preference.values['ugoira_save_format']!='folder'
                    or saved_preference.revision!=1):
                raise AssertionError('subsequent owned service did not restore GUI-selected folder')
            if reader.calls:
                raise AssertionError('new application performed unsolicited generated GET')
            import asyncio
            job=asyncio.run(services.save_explicit_work('123',uuid4().hex))
            owner=services.ugoira.jobs
            with owner._condition:
                if not owner._condition.wait_for(
                    lambda:any(j.job_id==job.job_id and j.state in ('done','error','stopped')
                               for j in owner.snapshot()),25):
                    raise AssertionError('frames output did not settle')
            current=next(j for j in owner.snapshot() if j.job_id==job.job_id)
            if current.state!='done':
                raise AssertionError(f'frames save job ended {current.state}')
            entries=services.ugoira.library()
            if (len(entries)!=1 or entries[0]['spec']['work_id']!='123'
                    or entries[0]['spec']['author_id']!='91'
                    or entries[0]['spec']['output']!='frames'):
                raise AssertionError('saved media identity or selected output is wrong')
            result=services.ugoira.playback(entries[0])
            if (len(result['frames'])!=3 or result['width']!=96 or result['height']!=64
                    or result['source_duration_ms']!=300
                    or [f['delay'] for f in result['frames']]!=[40,90,170]):
                raise AssertionError('saved frames metadata/timing changed')
            pixels=[];digests=[]
            for frame in result['frames']:
                if not frame['src'].startswith('data:image/png;base64,'):
                    raise AssertionError('saved frame MIME mismatch')
                blob=base64.b64decode(frame['src'].split(',',1)[1],validate=True)
                with Image.open(BytesIO(blob)) as img:
                    img.load()
                    if img.size!=(96,64):
                        raise AssertionError('saved frame size mismatch')
                    pixels.append(img.convert('RGB').getpixel((48,32)))
                digests.append(sha256(blob).hexdigest())
            if pixels!=[(255,0,0),(0,128,0),(0,0,255)]:
                raise AssertionError('actual saved frames changed source colors/order')
            report['steps'].append('new actual generated save from persisted folder produced three verified images and original delays')
            report['observations'].update(preferences_revision=saved_preference.revision,
                persisted_setting=saved_preference.values['ugoira_save_format'],
                output=entries[0]['spec']['output'],work_id=entries[0]['spec']['work_id'],
                author_id=entries[0]['spec']['author_id'],
                durations=[f['delay'] for f in result['frames']],colors=pixels,
                frame_sha256=digests,job_state=current.state)
            report['tested']=True
    except Exception as exc:
        report['failure']=type(exc).__name__
        report['error_short']=str(exc)[:250]
        report['last_step']=report['steps'][-1] if report['steps'] else 'startup'
    finally:
        if services is not None:
            try:services.close()
            except Exception:report['close_services_error']='failed'
        if proc is not None:
            if proc.poll() is None:
                report['app']['forced_cleanup']=True
                try:stop_child(proc)
                except Exception:report['app']['cleanup_error']='failed'
            report['app']['exit_code']=proc.poll()
        report['app']['tree']=tree.finish() if tree is not None else {}
        report['app']['port_released']=port_released(port)
        report['source_changed']=source_hashes()!=product_before
        report['harness_changed']=hashes!={str(p.relative_to(ROOT)):sha(p) for p in monitored}
        report['success']=bool(report.get('tested') and
            report['app'].get('exit_code')==0 and report['app']['normal_exit']
            and not report['app']['forced_cleanup'] and report['app']['port_released']
            and report['app']['tree'].get('remaining_count')==0
            and not report['app']['tree'].get('errors') and not report['source_changed']
            and not report['harness_changed'] and not report.get('close_services_error'))
        with (out/'result.json').open('x',encoding='utf8') as fp:
            json.dump(report,fp,ensure_ascii=False,indent=2)
    print(json.dumps({'success':report['success'],'steps':len(report['steps']),
        'failure':report['failure'],'formal_pass_added':0,
        'owned_remaining':report['app']['tree'].get('remaining_count')}))
    return 0 if report['success'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int);parser.add_argument('--sandbox',type=Path)
    parser.add_argument('--nonce');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.serve is not None:
        if not args.sandbox or not args.nonce:parser.error('server args needed')
        serve(args.serve,args.sandbox,args.nonce)
    else:
        if not args.output:parser.error('fresh private output required')
        raise SystemExit(run(args.output))
