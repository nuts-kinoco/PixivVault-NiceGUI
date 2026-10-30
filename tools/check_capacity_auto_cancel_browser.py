"""Synthetic UI cancellation boundary for first old capacity worker."""
from __future__ import annotations
import hashlib,json,os,secrets,socket,subprocess,sys,time,traceback,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
R=Path(__file__).resolve().parents[1]
OUT=R/'artifacts/p3-capacity-auto-20261028/cos-owned-gui-cancel-r1'
DATA=OUT/'owned-data'
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]
def get(url):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url+'/_fixture/state',timeout=2) as r:
        return json.loads(r.read())
def wait(page,url,fn,why,seconds=20):
    end=time.monotonic()+seconds
    last=None
    while time.monotonic()<end:
        last=get(url)
        if fn(last):return last
        page.wait_for_timeout(100)
    raise AssertionError(why+' calls='+str((last or {}).get('calls',[])[-7:]))
def main():
    assert not (OUT/'result.json').exists(),'Existing cancel GUI result'
    assert DATA.resolve(strict=False).is_relative_to(OUT.resolve(strict=False))
    assert not DATA.exists()
    n=port();url=f'http://127.0.0.1:{n}';nonce=secrets.token_hex(24)
    result={'candidate':'capacity-fixture-repair','scenario':'Old ongoing disk read cancelled by tab inactive',
        'port':n,'no_real_API_cookie_or_user_data':True,'success':False,
        'http_404':[],'page_errors':[],'console_errors':[],'external_requests':[]}
    env=os.environ.copy()
    env.update(PYTHONPATH=str(R/'src'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
      TEMP=str(OUT),TMP=str(OUT),COS_SHUTDOWN_NONCE=nonce)
    child=None
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        ctx=browser.new_context(viewport={'width':1280,'height':720})
        origin=urlsplit(url)
        def guard(route):
            target=urlsplit(route.request.url)
            if (target.scheme,target.netloc)!=(origin.scheme,origin.netloc):
                result['external_requests'].append(target.netloc);route.abort()
            else:route.continue_()
        ctx.route('**/*',guard)
        page=ctx.new_page()
        page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
        page.on('console',lambda m:result['console_errors'].append(m.text) if m.type=='error' else None)
        page.on('response',lambda response:result['http_404'].append(urlsplit(response.url).path) if response.status==404 else None)
        with (OUT/'server-stderr.log').open('wb') as stderr,(OUT/'server-stdout.log').open('wb') as stdout:
            try:
                child=subprocess.Popen([sys.executable,'-B',str(R/'tools/serve_capacity_auto_cancel_wrapper.py'),
                        '--root',str(DATA),'--port',str(n),'--pause-first'],cwd=str(R),
                        env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                result['pid']=child.pid
                for _ in range(130):
                    if child.poll() is not None:raise RuntimeError('Fixture unexpectedly exited with '+str(child.returncode))
                    try:get(url);break
                    except (urllib.error.URLError,TimeoutError):time.sleep(.2)
                else:raise TimeoutError('fixture start')
                page.goto(url,wait_until='domcontentloaded',timeout=25000)
                page.get_by_test_id('tab-library').click()
                expect(page.locator('.pv-library-author-card')).to_have_count(50,timeout=16000)
                blocked=wait(page,url,lambda s:s['calls']==['1001'] and s['active']==1,
                             'old first worker never entered',seconds=12)
                result['initial_calls']=blocked['calls']
                result['max_active_initial']=blocked['max_active']
                assert blocked['cache']['1001']==[0,None]
                page.screenshot(path=str(OUT/'before-inactivation.png'),animations='disabled')
                page.get_by_test_id('tab-ranking').click()
                control=ctx.new_page()
                control.goto(url+'/_fixture/control',wait_until='domcontentloaded',timeout=15000)
                control.get_by_test_id('fixture-release-capacity').click()
                control.close()
                cancelled=wait(page,url,lambda s:s['active']==0,'cancelled disk worker did not drain',seconds=15)
                assert cancelled['calls']==['1001'] and cancelled['cache']['1001']==[0,None]
                result['old_worker_did_not_commit']={'calls':cancelled['calls'],'cache1001':cancelled['cache']['1001']}
                page.get_by_test_id('tab-library').click()
                recovered=wait(page,url,lambda s:len(s['calls'])==51 and s['active']==0,
                         'post-invalidation first author not retried',seconds=24)
                assert recovered['calls'][:2]==['1001','1001']
                assert recovered['cache']['1001'][0]==1 and recovered['cache']['1001'][1]>0
                assert recovered['max_active']==1
                result['new_generation']={'call_count':len(recovered['calls']),
                    'first_two':recovered['calls'][:2],'recovered_cache1001':recovered['cache']['1001']}
                page.get_by_test_id('library-author-more').click()
                expect(page.locator('.pv-library-author-card')).to_have_count(62,timeout=12000)
                all_62=wait(page,url,lambda s:len(s['calls'])==63 and s['active']==0,
                      '51-to-62 after cancellation',seconds=22)
                assert all_62['max_active']==1 and all_62['cache']['1062'][0]==1
                result['visible62_after_cancel']=True
                page.screenshot(path=str(OUT/'after-new-generation.png'),animations='disabled')
                result['functional_success']=True
            except BaseException as e:
                result['functional_success']=False
                result['error_type']=type(e).__name__
                result['error']=str(e).replace(nonce,'<redacted>')[:900]
                result['traceback']=traceback.format_exc().replace(nonce,'<redacted>')[-4300:]
                try:page.screenshot(path=str(OUT/'failure.png'),animations='disabled')
                except Exception:pass
            finally:
                # Finish observing functional console events before normal server shutdown.
                try:ctx.close();browser.close()
                except Exception as e:result['browser_close_error']=type(e).__name__
                if child is not None:
                    if child.poll() is None:
                        req=urllib.request.Request(url+'/_cos/shutdown',method='POST',data=b'',
                               headers={'X-Cos-Shutdown':nonce})
                        try:
                            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=6) as r:
                                result['shutdown_http']=r.status
                        except Exception as e:result['shutdown_error']=type(e).__name__
                        try:child.wait(timeout=17)
                        except subprocess.TimeoutExpired:
                            child.terminate()
                            try:child.wait(timeout=6)
                            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=6)
                    result['normal_exit']=child.returncode==0
                    result['exit_code']=child.returncode
                with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                    s.settimeout(.5)
                    result['port_released']=s.connect_ex(('127.0.0.1',n))!=0
                result['success']=bool(result.get('functional_success') and result.get('normal_exit')
                    and result.get('port_released') and not result.get('browser_close_error') and not result['page_errors']
                    and not result['external_requests']
                    and all('404' in x for x in result['console_errors'])
                    and all(x == '/media/9002/0/thumb.png' for x in result['http_404']))
    dest=OUT/'result.json'
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'success':result['success'],'functional':result.get('functional_success'),
          'cancelled_not_committed':result.get('old_worker_did_not_commit'),
          'retry':result.get('new_generation'),'normal_exit':result.get('normal_exit'),
          'http404':len(result['http_404']),'result_sha256':sha(dest),'error':result.get('error')},
          ensure_ascii=False))
    return 0 if result['success'] else 2
if __name__=='__main__':raise SystemExit(main())