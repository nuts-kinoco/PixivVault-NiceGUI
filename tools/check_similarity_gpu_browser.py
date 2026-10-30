"""Real Egara GPU -> owned ledger -> real NiceGUI/Edge similarity acceptance.

Only generated images/metadata are used. The explicitly chosen original ONNX
is read-only; no Cookie, real library, external network or real IDs are used.
Two viewports share one Edge instance. Both app launches exit via their own
nonce-gated shutdown endpoint, never an assumed process kill.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_library_calendar_slider_owned import owned_output, write_new, deny_external_connections, OwnedTree
from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, wait_for_health

PREFIX = '/_gpu_acceptance'


def fingerprint(path):
    info = path.stat()
    return {'sha256': sha256(path.read_bytes()).hexdigest(), 'mtime_ns': info.st_mtime_ns,
            'bytes': info.st_size}


def inputs(root):
    from PIL import Image
    import numpy as np
    source, images = root/'frozen', root/'old-images'
    source.mkdir(); images.mkdir()
    yy, xx = np.indices((192, 320))
    array = np.stack(((xx+yy)%256, (2*xx+yy//2)%256, (xx//3+3*yy)%256), axis=2).astype('uint8')
    stream = BytesIO()
    Image.fromarray(array).save(stream, format='PNG')
    payload = stream.getvalue()
    for wid, aid, pages in (('101','12',2),('102','13',1)):
        folder = images/f'Synthetic({aid})'; folder.mkdir()
        for number in range(pages):
            (folder/f'Generated({wid})_{number+1}.png').write_bytes(payload)
    database = source/'library.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0);
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO works VALUES('101','Generated one',2,'[]','12',0);
            INSERT INTO works VALUES('102','Generated two',1,'[]','13',0);
            INSERT INTO following_users VALUES('12','Synthetic one');
            INSERT INTO following_users VALUES('13','Synthetic two');
            CREATE TABLE work_embeddings(work_id TEXT,kind TEXT,model TEXT,page INTEGER,
                dim INTEGER,vec BLOB,PRIMARY KEY(work_id,kind,model,page));
        ''')
    return {p.relative_to(root).as_posix(): fingerprint(p)
            for p in (database, *sorted(images.rglob('*.png')))}


def serve(args):
    root = owned_output(args.output)
    owner = json.loads((root/'owner.json').read_text(encoding='utf-8'))
    if not secrets.compare_digest(owner['nonce_sha256'], sha256(args.nonce.encode()).hexdigest()):
        raise ValueError('wrong fixture owner')
    blocked = deny_external_connections()
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    register_pages(library_readonly_db=root/'frozen/library.sqlite3',
        library_image_root=root/'old-images', similarity_index_root=root/'index',
        similarity_model_file=Path(args.model), similarity_provider=args.provider)
    services = get_services(app)
    stopped = False

    def auth(value):
        if not secrets.compare_digest(value or '', args.nonce):
            raise HTTPException(403)

    @app.get(PREFIX+'/state')
    def state(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        rows = services.similarity_index.snapshot_rows()
        return {'pid': os.getpid(), 'synthetic': True,
                'analysis': services.similarity_analysis_status(),
                'counts': {kind: len(values) for kind, values in rows.items()},
                'has_model': services._egara_inference is not None,
                'external_denied': blocked['denied'],
                'jobs_present': services.jobs is not None,
                'live_reader': services.live_client is not None}

    @app.post(PREFIX+'/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopped
        auth(x_acceptance)
        if stopped:
            raise HTTPException(409)
        stopped = True
        app.shutdown()
        return {'stopping': True}

    async def collect_close():
        import asyncio
        await asyncio.to_thread(services.close)
        write_new(root/f'lifecycle-{args.round}.json', {
            'pid': os.getpid(), 'services_closed': services.closed,
            'model_released': services._egara_inference is None,
            'ledger_released': services.similarity_index is None,
            'worker': services._similarity_worker.status() if services._similarity_worker else None,
            'external_denied': blocked['denied'], 'shutdown_requested': stopped,
        })
    app.on_shutdown(collect_close)
    ui.run(host='127.0.0.1', port=args.port, show=False, reload=False,
           binding_refresh_interval=None, timeout_graceful_shutdown=20)


def api(base, nonce, action, *, post=False):
    request = Request(base+PREFIX+'/'+action, method='POST' if post else 'GET',
                      headers={'X-Acceptance':nonce}, data=b'' if post else None)
    with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
        return json.loads(response.read(128*1024))


def inspect(page, base, nonce, root, *, first, size):
    from playwright.sync_api import expect
    page.set_default_timeout(30000)
    page.goto(base, wait_until='domcontentloaded')
    page.get_by_test_id('tab-library').click()
    library = page.get_by_test_id('library-view')
    expect(library).to_have_attribute('data-active','true')
    query = page.get_by_test_id('similar-status')
    state = api(base,nonce,'state')
    assert state['synthetic'] and not state['live_reader'] and not state['jobs_present']
    assert not state['external_denied']
    if first:
        assert not state['has_model'] and state['counts'] == {'style':0,'composition':0}
    def similar(aid):
        image = library.locator(f'.pv-library-author-card[data-author-id="{aid}"] .q-img img:visible').first
        expect(image).to_be_visible()
        page.wait_for_function('el=>el.complete && el.naturalWidth>0',arg=image.element_handle())
        image.click(button='right')
        page.locator('[data-testid=library-author-similar-open]:visible').click()
        expect(query).to_be_visible()
    def close():
        page.get_by_test_id('similar-close').click()
        expect(query).to_be_hidden()
    def wait_counts(count):
        until=time.monotonic()+100
        while time.monotonic()<until:
            state=api(base,nonce,'state')
            if state['counts']=={'style':count,'composition':count}:
                return state
            if state['analysis']['phase']=='failed':
                raise AssertionError('real analysis failed: '+str(state['analysis']))
            page.wait_for_timeout(150)
        raise AssertionError('expected page embeddings not published')
    similar('12')
    if first:
        expect(query).to_contain_text('解析データがありません')
        page.get_by_test_id('similar-analyze').click()
        one=wait_counts(1)
        assert one['analysis']['provider']=='cuda' if args_provider=='cuda' else one['analysis']['provider']=='cpu'
        expect(query).to_contain_text('解析済み1枚')
        page.get_by_test_id('similar-analysis-start').click()
        full=wait_counts(3)
        assert full['analysis']['errors']==0
        until=time.monotonic()+15
        while api(base,nonce,'state')['analysis']['phase']=='running' and time.monotonic()<until:
            page.wait_for_timeout(100)
        # Manual pause is an explicit user control, including waiting between scans.
        pause=page.get_by_test_id('similar-analysis-pause')
        expect(pause).to_be_enabled()
        pause.click()
        expect(page.get_by_test_id('similar-analysis-status')).to_contain_text('一時停止')
        page.get_by_test_id('similar-analysis-resume').click()
    else:
        expect(query).to_contain_text('類似度0.50以上')
        assert state['counts']=={'style':3,'composition':3}
    close()
    similar('13')
    expect(query).to_contain_text('2枚')
    result=page.get_by_test_id('similar-result').filter(has_text='101 · 2ページ')
    expect(result).to_have_count(1)
    page.screenshot(path=str(root/f'{size}-results.png'))
    result.click()
    viewer=page.get_by_test_id('library-viewer')
    expect(viewer).to_be_visible()
    expect(viewer.locator('.pv-counter')).to_have_text('2/2')
    image=page.get_by_test_id('library-viewer-image').locator('img:visible').first
    page.wait_for_function('el=>el.complete && el.naturalWidth>0',arg=image.element_handle())
    page.screenshot(path=str(root/f'{size}-page2.png'))
    viewer.get_by_role('button',name='閉じる',exact=True).click()
    return api(base,nonce,'state')


args_provider = 'cuda'


def run(args):
    from playwright.sync_api import sync_playwright
    from tools import check_native_window as native
    global args_provider
    args_provider=args.provider
    root=owned_output(args.output)
    root.mkdir(parents=True,exist_ok=False)
    before=source_hashes()
    source=inputs(root)
    nonce=secrets.token_hex(24)
    write_new(root/'owner.json',{'nonce_sha256':sha256(nonce.encode()).hexdigest(),'synthetic':True})
    result={'success':False,'rounds':[],'console_errors':[],'page_errors':[],'blocked_external':[]}
    process=None
    base=None
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=not args.headed,
                args=['--disable-background-networking','--disable-component-update'])
            try:
                for round_no in (1,2):
                    port=free_port();base=f'http://127.0.0.1:{port}'
                    record={'round':round_no,'port':port,'checks':[]}
                    result['rounds'].append(record)
                    with (root/f'server-{round_no}.log').open('wb') as log:
                        process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),
                            '--serve','--output',str(root),'--model',str(Path(args.model).absolute()),
                            '--provider',args.provider,'--port',str(port),'--nonce',nonce,'--round',str(round_no)],
                            cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                        record['pid']=process.pid
                        tree=OwnedTree(process,native)
                        endpoint=None
                        try:
                            wait_for_health(base,process,timeout=60,expected_data='library-readonly')
                            endpoint=tree.observed_endpoint(api(base,nonce,'state')['pid'])
                            assert endpoint is not None, 'ASGI endpoint is not a verified owned descendant'
                            record['endpoint']=endpoint
                            for width,height in ((1280,720),(1920,1080)):
                                context=browser.new_context(viewport={'width':width,'height':height})
                                try:
                                    def route(route):
                                        url=route.request.url
                                        if url.startswith(base+'/') or url.startswith(('data:','blob:')):
                                            route.continue_()
                                        else:
                                            result['blocked_external'].append(route.request.resource_type)
                                            route.abort()
                                    context.route('**/*',route)
                                    page=context.new_page()
                                    page.on('console',lambda event: result['console_errors'].append(event.text[:500]) if event.type=='error' else None)
                                    page.on('pageerror',lambda error: result['page_errors'].append(str(error)[:500]))
                                    record['checks'].append(inspect(page,base,nonce,root,
                                        first=round_no==1 and width==1280,size=f'r{round_no}-{width}x{height}'))
                                finally:
                                    context.close()
                        finally:
                            try:
                                if process.poll() is None:
                                    if endpoint is None:
                                        endpoint=tree.observed_endpoint(api(base,nonce,'state')['pid'])
                                    assert endpoint is not None and tree.observed_endpoint(endpoint['pid'])==endpoint
                                    api(base,nonce,'shutdown',post=True)
                                record['exit_code']=process.wait(timeout=170)
                                record['port_released']=port_released(port)
                                assert record['exit_code']==0 and record['port_released']
                                life=json.loads((root/f'lifecycle-{round_no}.json').read_text(encoding='utf-8'))
                                assert life['pid']==endpoint['pid']
                                assert life['services_closed'] and life['model_released'] and life['ledger_released']
                                assert life['external_denied']==0
                                record['lifecycle']=life
                            finally:
                                record['process_tree']=tree.finish()
                            assert record['process_tree']['living']==[] and not record['process_tree']['errors']
                result['success']=not result['page_errors'] and not result['console_errors'] and not result['blocked_external']
            finally:
                browser.close()
    except Exception as error:
        result['error']=type(error).__name__+': '+str(error)[:1500]
    finally:
        after_source={name:fingerprint(root/name) for name in source}
        result['input_unchanged']=source==after_source
        result['source_unchanged']=before==source_hashes()
        result['success']=result['success'] and result['input_unchanged'] and result['source_unchanged']
        result['source_before']=before
        result['source_after']=source_hashes()
        write_new(root/'result.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_before','source_after')},ensure_ascii=False))
    return 0 if result['success'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--provider',choices=('cuda','cpu'),default='cuda')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--port',type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--round',type=int,default=1)
    arguments=parser.parse_args()
    raise SystemExit(serve(arguments) if arguments.serve else run(arguments))
