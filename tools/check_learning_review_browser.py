"""Owned offline learning-review Edge checks; no production data or external routes."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.run_browser_checks import child_environment,free_port,port_released,source_hashes,stop_child,wait_for_health
ROOT=Path(__file__).resolve().parents[1]

def serve(port,sandbox,nonce):
    from nicegui import app,ui
    from fastapi import Header,HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    register_pages(learning_sandbox=sandbox)
    service=get_services(app).learning
    session=None
    def auth(value):
        if not secrets.compare_digest(value or '',nonce):raise HTTPException(403)
    @app.get('/_learning_review_test/snapshot')
    async def snapshot(x_acceptance:str|None=Header(default=None)):
        nonlocal session
        auth(x_acceptance)
        if session is None:session=(await service.open_session()).session_id
        return await service.review_snapshot(session)
    @app.post('/_learning_review_test/shutdown')
    def shutdown(x_acceptance:str|None=Header(default=None)):
        auth(x_acceptance);app.shutdown();return dict(stopping=True)
    ui.run(host='127.0.0.1',port=port,show=False,reload=False,binding_refresh_interval=None)

def inspect(page,url,headers,output,*,restart=False,previous=None):
    from playwright.sync_api import expect
    expect.set_options(timeout=15000)
    def snapshot():
        response=page.request.get(url+'/_learning_review_test/snapshot',headers=headers)
        assert response.ok
        return response.json()
    page.goto(url,wait_until='domcontentloaded')
    page.get_by_test_id('tab-learning').click()
    root=page.locator('.pv-learning')
    if restart:
        assert snapshot()==previous
        expect(root).to_have_attribute('data-work-id','demo-0005')
    else:
        for index,choice in enumerate(('skip','hate','like','superLike'),1):
            expect(root).to_have_attribute('data-work-id',f'demo-{index:04d}')
            button=page.locator('.pv-judgment-'+choice)
            expect(button).to_be_enabled();button.click()
            expect(root).to_have_attribute('data-work-id',f'demo-{index+1:04d}')
    page.get_by_test_id('learning-review-open').click()
    dialog=page.get_by_test_id('learning-review-dialog')
    expect(dialog).to_be_visible()
    positive=page.get_by_test_id('learning-review-positive')
    negative=page.get_by_test_id('learning-review-negative')
    expect(positive).to_contain_text('好意 · 2 件')
    expect(negative).to_contain_text('否定 · 2 件')
    page.get_by_test_id('learning-review-query').fill('not-a-fixture-title')
    expect(positive).to_contain_text('好意 · 0 件')
    page.get_by_test_id('learning-review-query').fill('')
    expect(positive).to_contain_text('好意 · 2 件')
    if not restart:
        page.get_by_test_id('learning-review-undo-demo-0003').click()
        restore=page.get_by_test_id('learning-review-restore-demo-0003')
        expect(restore).to_be_visible()
        expect(root).to_have_attribute('data-work-id','demo-0003')
        assert len(snapshot()['rows'])==3
        restore.click()
        expect(restore).to_have_count(0)
        expect(root).to_have_attribute('data-work-id','demo-0005')
        assert len(snapshot()['rows'])==4
    page.screenshot(path=str(output/'review.png'),animations='disabled')
    page.get_by_test_id('learning-review-close').click()
    expect(dialog).to_be_hidden()
    page.get_by_test_id('learning-review-open').click()
    expect(page.get_by_test_id('learning-review-restore-demo-0003')).to_have_count(0)
    expect(page.get_by_test_id('learning-review-undo-demo-0003')).to_be_visible()
    page.get_by_test_id('learning-review-close').click()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
    return snapshot()

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
                                response = page.request.get(url + "/_learning_review_test/snapshot", headers=headers)
                                result["failure_snapshot"] = response.json()
                                page.screenshot(path=str(evidence / "failure.png"))
                                (evidence / "failure-dom.html").write_text(page.content(), encoding="utf-8")
                            except Exception:
                                pass
                        finally:
                            try:
                                page.request.post(url + "/_learning_review_test/shutdown", headers=headers, timeout=5000)
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
        serve(args.serve,args.sandbox,args.nonce);return 0
    if args.output is None:parser.error('--output required')
    return run(args.channel,args.output)
if __name__=='__main__':sys.exit(main())
