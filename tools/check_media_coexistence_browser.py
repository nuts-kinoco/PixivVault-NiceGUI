"""P3 coexistence Edge acceptance using private loopback, generated frames and CPU codec."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
from threading import Event
import time
from urllib.parse import urlsplit
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health
ROOT=Path(__file__).resolve().parents[1]
CODEC=Path(os.environ.get('PV_TEST_CODEC_PYTHON',sys.executable))
FORMATS={'frames':'フォルダー','gif':'GIF','mp4':'MP4'}

def serve(port,sandbox,nonce):
    from nicegui import app,ui
    from fastapi import Header,HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    sandbox.mkdir(exist_ok=True)
    register_pages(novel_fixture_root=sandbox/'novel',ugoira_fixture_root=sandbox/'ugoira',ugoira_codec_python=CODEC)
    services=get_services(app)
    from pixivvault_nicegui.ranking_source import RankingSource
    from pixivvault_nicegui.remote_media import RemoteBookmarkImages
    from io import BytesIO
    from PIL import Image
    class RankingFixture:
        def require_identity(self):return '91'
        def get_ranking(self,**kw):
            if kw['page']>1:return []
            return [dict(illust_id=987,user_id=91,user_name='架空作者',title='合成ランキング',tags=['fixture'],
                         url='https://i.pximg.net/fixture.png',rank=1,yes_rank=2,rating_count=3,view_count=4,page_count=1,illust_type=0,ai_type=1)]
        def search_illusts(self,*args,**kw):return {'works':[]}
        def get_work_info(self,wid):return {'ai_type':1}
        def get_artwork_pages(self,wid):return ('https://i.pximg.net/fixture.png',)
        def get_image_bytes(self,url):
            out=BytesIO();Image.new('RGB',(96,64),'navy').save(out,format='PNG');return out.getvalue()
    ranking=RankingFixture()
    services.ranking_source=RankingSource(ranking)
    services.acquire_ranking_images=lambda records:services.images.acquire_catalog(RemoteBookmarkImages(ranking,tuple(records)))
    owner=services.ugoira
    gate=Event()
    original=owner.jobs._runner
    durations=[]
    def runner(job,context):
        spec=owner._spec(job.target_id)
        if spec['output']=='gif' and job.attempt==1:raise OSError('synthetic once')
        if spec['output']=='frames':
            while not gate.wait(.02):context.checkpoint()
        start=time.perf_counter()
        try:return original(job,context)
        finally:durations.append(dict(format=spec['output'],milliseconds=(time.perf_counter()-start)*1000))
    owner.jobs._runner=runner
    def auth(value):
        if not secrets.compare_digest(value or '',nonce):raise HTTPException(403)
    @app.get('/_ugoira_test/snapshot')
    def snapshot(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance)
        return dict(jobs=[asdict(j) for j in services.jobs.snapshot()],entries=owner.library(),
                    calls=owner.source.reader.calls,backend_durations=durations)
    @app.post('/_ugoira_test/gate')
    def release(open:bool=False,x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance)
        gate.set() if open else gate.clear()
        return dict(open=open)
    @app.post('/_ugoira_test/shutdown')
    def shutdown(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance);app.shutdown();return dict(stopping=True)
    ui.run(host='127.0.0.1',port=port,show=False,reload=False,binding_refresh_interval=None)

def inspect(page,url,headers,output,*,restart=False,previous=None):
    from playwright.sync_api import expect
    expect.set_options(timeout=15000)
    def snapshot():
        response=page.request.get(url+'/_ugoira_test/snapshot',headers=headers)
        assert response.ok
        return response.json()
    def tab(name):
        page.get_by_test_id('tab-'+name).click()
        expect(page.get_by_test_id('tab-'+name)).to_have_attribute('aria-current','page')
    def gate(open):assert page.request.post(url+'/_ugoira_test/gate',params={'open':str(open).lower()},headers=headers).ok
    def preview(wid,fmt):
        tab('download')
        page.get_by_test_id('media-download').get_by_test_id('media-select-ugoira').click()
        page.get_by_test_id('ugoira-work-id').fill(wid)
        page.get_by_test_id('ugoira-author-id').fill('91')
        page.get_by_test_id('ugoira-format').click()
        page.get_by_role('option',name=FORMATS[fmt],exact=True).click()
        page.get_by_test_id('ugoira-preview-button').click()
        expect(page.get_by_test_id('ugoira-preview')).to_contain_text('ID '+wid)
        expect(page.get_by_test_id('ugoira-preview')).to_contain_text(FORMATS[fmt])
    def submit(wid,fmt):
        preview(wid,fmt)
        page.get_by_test_id('ugoira-submit').click()
        expect(page.get_by_test_id('ugoira-notice')).to_contain_text('キューに追加しました')
        tab('queue')
        return page.get_by_test_id('queue-view').get_by_test_id('job-row').filter(has_text=f'うごイラ · {FORMATS[fmt]} · ID {wid}')
    page.goto(url,wait_until='domcontentloaded')
    expect(page.get_by_test_id('ranking-view')).to_be_visible()
    page.get_by_test_id('ranking-fetch').click()
    expect(page.get_by_test_id('ranking-notice')).to_contain_text('公式ランキング')
    card=page.locator('.pv-card[data-work-id="987"]')
    expect(card).to_be_visible();card.click()
    expect(page.get_by_test_id('gallery-viewer')).to_have_attribute('aria-hidden','false')
    page.get_by_test_id('gallery-viewer').get_by_label('閉じる',exact=True).click()

    if restart:
        actual=snapshot()
        assert actual['entries']==previous['entries']
        assert [(j['job_id'],j['state'],j['attempt']) for j in actual['jobs']]==[(j['job_id'],j['state'],j['attempt']) for j in previous['jobs']]
        assert actual['calls']==[]
    else:
        preview('123','frames')
        assert snapshot()['jobs']==[] and not any(c[0]=='archive' for c in snapshot()['calls'])
        page.screenshot(path=str(output/'preview.png'),animations='disabled')
        row=submit('123','frames')
        expect(row).to_have_attribute('data-state','running')
        row.get_by_test_id('job-pause').click()
        expect(row).to_have_attribute('data-state','paused')
        assert snapshot()['entries']==[]
        row.get_by_test_id('job-resume').click()
        expect(row).to_have_attribute('data-state','running')
        gate(True)
        expect(row).to_have_attribute('data-state','done')
        expect(row.get_by_test_id('job-save-result')).to_contain_text('保存済み')
        row=submit('123','gif')
        expect(row).to_have_attribute('data-state','error')
        tab('failed')
        failure=page.get_by_test_id('failed-view').get_by_test_id('job-row')
        failure.get_by_test_id('job-retry').click()
        expect(failure).to_have_count(0)
        tab('queue')
        expect(row).to_have_attribute('data-state','done')
        expect(submit('123','mp4')).to_have_attribute('data-state','done')
        gate(False)
        row=submit('124','frames')
        expect(row).to_have_attribute('data-state','running')
        row.get_by_test_id('job-stop').click()
        expect(row).to_have_attribute('data-state','stopped')
        assert len(snapshot()['entries'])==3
    for fmt in FORMATS:
        tab('library')
        page.get_by_test_id('media-library').get_by_test_id('media-select-ugoira').click()
        button=page.get_by_test_id('ugoira-read-123-'+fmt)
        expect(button).to_be_visible()
        button.click()
        canvas=page.get_by_test_id('ugoira-canvas')
        expect(canvas).to_be_visible()
        page.wait_for_function("Number(document.querySelector('[data-testid=ugoira-canvas]').dataset.elapsed)>350")
        page.get_by_test_id('ugoira-play-toggle').click()
        expect(canvas).to_have_attribute('data-paused','true')
        paused=page.evaluate("async()=>{const c=document.querySelector('[data-testid=ugoira-canvas]');const value=c.dataset.elapsed;await new Promise(r=>setTimeout(r,150));return c.dataset.elapsed===value}")
        assert paused,'pause did not hold frame clock'
        page.screenshot(path=str(output/f'player-{fmt}.png'),animations='disabled')
        page.get_by_test_id('ugoira-play-toggle').click()
        expect(canvas).to_have_attribute('data-paused','false')
        page.get_by_test_id('ugoira-player-close').click()
        expect(canvas).to_be_hidden()
        expect(button).to_be_focused()
    if restart:assert snapshot()['calls']==[],'restart library must not fetch'
    preview('123','gif')
    expect(page.get_by_test_id('ugoira-submit')).to_be_disabled()
    expect(page.get_by_test_id('ugoira-notice')).to_contain_text('保存済み')
    timings=[]
    for i in range(20):
        start=time.perf_counter();tab('queue' if i%2==0 else 'library')
        expect(page.get_by_test_id('queue-view' if i%2==0 else 'ugoira-library')).to_be_visible()
        timings.append((time.perf_counter()-start)*1000)
    page.screenshot(path=str(output/'library.png'),animations='disabled')
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
    tab('download')
    page.get_by_test_id('media-download').get_by_test_id('media-select-novel').click()
    expect(page.get_by_test_id('ugoira-download')).to_be_hidden()
    if not restart:
        page.get_by_test_id('novel-work-id').fill('123')
        page.get_by_test_id('novel-format').click()
        page.get_by_role('option',name='EPUBとTXT両方',exact=True).click()
        page.get_by_test_id('novel-preview-button').click()
        expect(page.get_by_test_id('novel-preview')).to_be_visible()
        page.get_by_test_id('novel-submit').click()
        expect(page.get_by_test_id('novel-notice')).to_contain_text('キューに追加しました')
    tab('queue')
    novel_row=page.get_by_test_id('queue-view').get_by_test_id('job-row').filter(has_text='小説 · BOTH · ID 123')
    expect(novel_row).to_have_attribute('data-state','done')
    assert len(snapshot()['jobs'])==5
    tab('library')
    page.get_by_test_id('media-library').get_by_test_id('media-select-novel').click()
    read=page.get_by_test_id('novel-read-123-BOTH')
    expect(read).to_be_visible();read.click()
    expect(page.get_by_test_id('novel-reader-text')).to_contain_text('合成データ')
    page.screenshot(path=str(output/'coexist-novel.png'),animations='disabled')
    page.get_by_test_id('novel-reader-close').click()
    expect(read).to_be_focused()
    page.get_by_test_id('media-library').get_by_test_id('media-select-ugoira').click()
    expect(page.get_by_test_id('ugoira-read-123-mp4')).to_be_visible()
    return {**snapshot(),'warm_navigation_ms':dict(samples=timings,p50=statistics.median(timings),p95=sorted(timings)[18])}

def run(channel, output):
    from playwright.sync_api import sync_playwright
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "temp"
    temporary.mkdir()
    os.environ.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE="1")
    before = source_hashes()
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=None if channel == "chromium" else channel, headless=True)
        try:
            for width, height in ((1280, 720), (1920, 1080)):
                sandbox = output / f"data-{width}"
                previous = None
                for restart in (False, True):
                    evidence = output / f"{width}-{'restart' if restart else 'initial'}"
                    evidence.mkdir()
                    port, nonce = free_port(), secrets.token_hex(24)
                    url = f"http://127.0.0.1:{port}"
                    headers = {"X-Acceptance": nonce}
                    result = {"viewport": [width, height], "restart": restart, "success": False,
                              "console_errors": [], "page_errors": [], "external_requests": []}
                    process = None
                    context = browser.new_context(viewport={"width": width, "height": height})
                    origin = urlsplit(url)
                    def route(request_route):
                        target = urlsplit(request_route.request.url)
                        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
                            result["external_requests"].append(target.scheme + "://" + target.netloc)
                            request_route.abort()
                        else:
                            request_route.continue_()
                    context.route("**/*", route)
                    page = context.new_page()
                    page.on("console", lambda message: result["console_errors"].append(message.text) if message.type == "error" else None)
                    page.on("pageerror", lambda error: result["page_errors"].append(str(error)))
                    with (evidence / "server-stderr.log").open("wb") as stderr:
                        try:
                            process = subprocess.Popen(
                                [sys.executable, str(Path(__file__).resolve()), "--serve", str(port),
                                 "--sandbox", str(sandbox), "--nonce", nonce],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                cwd=ROOT, env=child_environment())
                            result["pid"] = process.pid
                            wait_for_health(url, process)
                            previous = inspect(page, url, headers, evidence, restart=restart, previous=previous)
                            result["inspection"] = previous
                            assert not result["console_errors"] and not result["page_errors"]
                            assert not result["external_requests"]
                            result["success"] = True
                        except Exception as error:
                            result["failure"] = f"{type(error).__name__}: {error}"
                            try:
                                response = page.request.get(url + "/_ugoira_test/snapshot", headers=headers)
                                result["failure_snapshot"] = response.json()
                                page.screenshot(path=str(evidence / "failure.png"))
                                (evidence / "failure-dom.html").write_text(page.content(), encoding="utf-8")
                            except Exception:
                                pass
                        finally:
                            try:
                                page.request.post(url + "/_ugoira_test/shutdown", headers=headers, timeout=5000)
                                process.wait(timeout=15)
                                result["normal_exit"] = process.returncode == 0
                            except Exception:
                                result["normal_exit"] = False
                            context.close()
                            stop_child(process)
                            result["port_released"] = port_released(port)
                    result["success"] &= result["normal_exit"] and result["port_released"]
                    (evidence / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    results.append(result)
                    if not result["success"]:
                        break
        finally:
            browser.close()
    after = source_hashes()
    success = len(results) == 4 and all(r["success"] for r in results) and before == after
    (output / "result.json").write_text(json.dumps({
        "success": success, "channel": channel, "runs": results,
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_before": before, "source_after": after, "source_changed": before != after,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": success, "runs": len(results), "output": str(output)}))
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
        serve(args.serve,args.sandbox,args.nonce)
        return 0
    if args.output is None:parser.error('--output required')
    return run(args.channel,args.output)
if __name__=='__main__':sys.exit(main())