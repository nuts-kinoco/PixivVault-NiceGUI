"""E01 original settings action: isolated old-DB ZIP Edge GUI and restart checks.

Creates only synthetic SQLite/images under an explicit artifacts output root.
Never reads a real PixivVault DB, user credentials, images or external network.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
import zipfile

from playwright.sync_api import expect, sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import (ROOT, child_environment, free_port,
                                      port_released, source_hashes,
                                      stop_child, wait_for_health)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def data_setup(root):
    """Create fixture once; preserve exactly the same source on restart."""
    frozen = root / 'frozen'
    images = root / 'images'
    frozen.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    db = frozen / 'old.sqlite3'
    if not db.exists():
        with sqlite3.connect(db) as conn:
            conn.executescript('''
                CREATE TABLE works(work_id TEXT PRIMARY KEY, title TEXT,
                    user_id TEXT, page_count INTEGER, tags TEXT, is_deleted INTEGER,
                    content_type TEXT, last_backup TEXT);
                CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
                INSERT INTO following_users VALUES ('91','Generated author');
                INSERT INTO works VALUES
                    ('700','作品 A','91',1,'["synthetic"]',0,'illust','2026-10-28'),
                    ('701','作品 B','91',1,'[]',0,'illust','2026-10-27'),
                    ('702','作品 C','91',1,'[]',1,'illust','2026-10-26');
            ''')
    marker = images / 'original-media-untouched.png'
    if not marker.exists():
        marker.write_bytes(b'not a real image / must never enter ZIP')
    return db, images, marker


def serve(port, root, nonce):
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.shell import build_demo_page
    from pixivvault_nicegui.legacy_metadata_export import MARKER

    if not root.is_absolute() or not root.resolve().is_relative_to(ROOT/'artifacts'):
        raise ValueError('an isolated artifacts folder is required')
    db, images, marker = data_setup(root)
    services = get_services(app, library_readonly_db=db, library_image_root=images,
                            preferences_root=root/'owned-settings')
    register_media_routes(app, services.images)
    ui.page('/')(build_demo_page)

    def auth(value):
        if not secrets.compare_digest(value or '', nonce):
            raise HTTPException(403)

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok', 'mode': 'readonly', 'data': 'library-readonly'}

    @app.get('/_fixture/state')
    def state(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        export_dir = root/'owned-settings'/'exports'
        zip_paths = sorted(export_dir.glob('PixivVault_Export_*.zip')) if export_dir.exists() else []
        reports = []
        for archive in zip_paths:
            with zipfile.ZipFile(archive) as zf:
                assert zf.testzip() is None
                reports.append(dict(name=archive.name, bytes=archive.stat().st_size,
                    sha256=sha(archive), entries=sorted(zf.namelist()),
                    rows=len(json.loads(zf.read('metadata.json')))))
        return dict(db_sha=sha(db), source_mtime_ns=db.stat().st_mtime_ns,
                    image_sha=sha(marker), source_sidecars=sorted(p.name for p in db.parent.iterdir()
                           if p.name != db.name), exports=reports,
                    marker=(export_dir/MARKER).is_file(),
                    stages=[p.name for p in export_dir.glob('.pv-export-*')] if export_dir.exists() else [])

    @app.post('/_fixture/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        auth(x_acceptance)
        app.shutdown()
        return {'stopping': True}

    async def close_services():
        await asyncio.to_thread(services.close)
    app.on_shutdown(close_services)
    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None)


def inspect(page, url, auth, evidence, *, previous=None):
    def snapshot():
        response = page.request.get(url+'/_fixture/state', headers=auth)
        assert response.ok, response.status
        return response.json()

    initial = snapshot()
    if previous is not None:
        assert initial['db_sha'] == previous['db_sha']
        assert initial['image_sha'] == previous['image_sha']
        assert initial['source_mtime_ns'] == previous['source_mtime_ns']
        assert [x['sha256'] for x in initial['exports']] == [x['sha256'] for x in previous['exports']]
    assert initial['source_sidecars'] == []
    page.goto(url, wait_until='domcontentloaded')
    button = page.get_by_test_id('preferences-open')
    expect(button).to_be_enabled()
    button.click()
    export_button = page.get_by_test_id('preferences-export-db')
    expect(export_button).to_be_visible()
    expect(export_button).to_be_enabled()
    expect(page.get_by_test_id('preferences-dialog')).to_contain_text('画像は含まれません。')
    for number in range(2):
        export_button.click()
        expected_count = len(initial['exports'])+number+1
        deadline = time.monotonic()+25
        while True:
            now = snapshot()
            if len(now['exports']) == expected_count:
                break
            if time.monotonic() >= deadline:
                raise AssertionError('export not persisted after click: expected '
                                     + str(expected_count) + ' saw ' + str(len(now['exports'])))
            page.wait_for_timeout(100)
        status = page.get_by_test_id('preferences-export-status')
        expect(status).to_contain_text('[完了]', timeout=25000)
        expect(status).to_contain_text('画像は含まれません')
        expect(export_button).to_be_enabled()
        assert len(now['exports']) == expected_count
        assert now['db_sha'] == initial['db_sha']
        assert now['image_sha'] == initial['image_sha']
        assert now['source_mtime_ns'] == initial['source_mtime_ns']
        assert now['source_sidecars'] == []
        assert now['stages'] == []
        assert now['marker']
        assert all(x['rows']==3 and x['entries']==['metadata.json','pixiv_vault.db'] for x in now['exports'])
        assert len({x['name'] for x in now['exports']}) == len(now['exports'])
        if number == 0:
            # The original settings dialog has a close/reopen flow.
            page.get_by_test_id('preferences-dialog').get_by_role('button', name='閉じる').click()
            expect(page.get_by_test_id('preferences-dialog')).not_to_be_visible()
            button.click()
            export_button = page.get_by_test_id('preferences-export-db')
            expect(page.get_by_test_id('preferences-export-status')).to_contain_text('[完了]')
    page.screenshot(path=str(evidence/'export-settings.png'), animations='disabled')
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
    return snapshot()


def run(output, channel):
    output = output.resolve()
    if not output.is_relative_to((ROOT/'artifacts').resolve()) or output == (ROOT/'artifacts').resolve():
        raise ValueError('dedicated artifacts output directory is required')
    output.mkdir(parents=True, exist_ok=False)
    before = source_hashes()
    all_runs = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        try:
            for width, height in ((1280,720),(1920,1080)):
                root=output/f'owned-data-{width}'
                previous=None
                for restart in (False,True):
                    evidence=output/(f'{width}-'+('restart' if restart else 'initial'))
                    evidence.mkdir()
                    port=free_port()
                    nonce=secrets.token_hex(24)
                    url=f'http://127.0.0.1:{port}'
                    headers={'X-Acceptance':nonce}
                    entry=dict(viewport=[width,height],restart=restart,success=False,
                               external_requests=[],console_errors=[],page_errors=[])
                    process=None
                    context=browser.new_context(viewport={'width':width,'height':height})
                    origin=urlsplit(url)

                    def route(request_route):
                        target=urlsplit(request_route.request.url)
                        if (target.scheme,target.netloc)!=(origin.scheme,origin.netloc):
                            entry['external_requests'].append(target.netloc)
                            request_route.abort()
                        else:
                            request_route.continue_()
                    context.route('**/*', route)
                    page=context.new_page()
                    page.on('pageerror',lambda error:entry['page_errors'].append(str(error)))
                    page.on('console',lambda message:entry['console_errors'].append(message.text)
                            if message.type=='error' else None)
                    with (evidence/'server-stderr.log').open('wb') as stderr:
                        try:
                            process=subprocess.Popen([
                                sys.executable,'-B',str(Path(__file__).resolve()),
                                '--serve',str(port),'--root',str(root),'--nonce',nonce],
                                cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,stderr=stderr,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                            entry['pid']=process.pid
                            wait_for_health(url,process,expected_data='library-readonly')
                            previous=inspect(page,url,headers,evidence,previous=previous)
                            entry['observed']=dict(zip_count=len(previous['exports']),
                                source_sha=previous['db_sha'],rows=[r['rows'] for r in previous['exports']])
                            assert not entry['page_errors'] and not entry['console_errors']
                            assert not entry['external_requests']
                            entry['success']=True
                        except Exception as error:
                            entry['failure']=f'{type(error).__name__}: {error}'
                            entry['traceback']=traceback.format_exc()[-4500:]
                            try:page.screenshot(path=str(evidence/'failure.png'),animations='disabled')
                            except Exception:pass
                        finally:
                            if process is not None and process.poll() is None:
                                try:
                                    response=page.request.post(url+'/_fixture/shutdown',headers=headers,timeout=6000)
                                    entry['shutdown_http']=response.status
                                    process.wait(timeout=15)
                                except Exception:
                                    pass
                            try:context.close()
                            except Exception as error:entry['browser_close_error']=str(error)
                            if process is not None:
                                stop_child(process)
                                entry['exit_code']=process.returncode
                                entry['normal_exit']=process.returncode==0
                            entry['port_released']=port_released(port)
                            entry['success'] &= entry.get('normal_exit',False) and entry['port_released']
                            (evidence/'result.json').write_text(json.dumps(entry,ensure_ascii=False,indent=2),encoding='utf8')
                            all_runs.append(entry)
                    if not entry['success']:
                        break
                if not all_runs[-1]['success']:
                    break
        finally:
            browser.close()
    after=source_hashes()
    success=len(all_runs)==4 and all(x['success'] for x in all_runs) and before==after
    (output/'result.json').write_text(json.dumps(dict(success=success,
        source_changed=before!=after,source_before=before,source_after=after,
        checker_sha256=sha(__file__),channel=channel,runs=all_runs),
        ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(dict(success=success,runs=len(all_runs),source_changed=before!=after,
        failed=[r.get('failure') for r in all_runs if not r['success']])))
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--nonce')
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.serve:
        if not args.root or not args.nonce or not args.root.is_absolute():
            parser.error('explicit isolated root/nonce required')
        serve(args.serve,args.root,args.nonce)
    else:
        if not args.output:
            parser.error('--output required')
        raise SystemExit(run(args.output,args.channel))
