"""Stage4 generated-browser layout/response acceptance; never reads real Pixiv data.

One Chromium browser and one owned loopback fixture. Viewport DPR emulates pixel
density only; this is NOT a physical Windows DPI or matched Flet benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, wait_for_health)

TABS = ("download", "queue", "library", "ranking", "learning", "failed")
SIZES = ((1024, 768, 1.0), (1280, 720, 1.25),
         (1800, 960, 1.0), (1920, 1080, 1.75))

JS_GEOMETRY = """() => {
  const width = innerWidth, height = innerHeight;
  const visible = e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const rect = e => {const r=e.getBoundingClientRect(); return {
     x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}; };
  const navElements = [...document.querySelectorAll('[data-testid^="tab-"]')].filter(visible);
  const nav = navElements.map(e => ({id:e.getAttribute('data-testid'),...rect(e)}));
  const intersects = (a,b) => Math.max(0,Math.min(a.right,b.right)-Math.max(a.x,b.x))
      * Math.max(0,Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y));
  const navOverlap = [];
  for(let i=0;i<nav.length;i++)for(let j=i+1;j<nav.length;j++)
    if(intersects(nav[i],nav[j])>16)navOverlap.push([nav[i].id,nav[j].id]);
  const isEnabled = e => !e.disabled && e.getAttribute('aria-disabled')!=='true'
    && getComputedStyle(e).pointerEvents!=='none';
  const centerInViewport = e => {
    const r=e.getBoundingClientRect();
    return r.x+r.width/2>=0 && r.x+r.width/2<width
      && r.y+r.height/2>=0 && r.y+r.height/2<height;
  };
  const blocked = e => {
    if(!isEnabled(e) || !centerInViewport(e))return false;
    const r=e.getBoundingClientRect();
    const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    const field=e.closest('.q-field');
    // Quasar field labels/controls may legitimately receive the click and
    // forward focus, but arbitrary same-field siblings can mask an input.
    const fieldDecor=field && hit && hit.closest('.q-field')===field
       && (hit.matches('.q-field__label,.q-field__native,.q-field__control,.q-field__control-container,.q-field__marginal')
           || (hit.closest('label')?.control===e));
    return !hit || (hit!==e && !e.contains(hit) && !fieldDecor);
  };
  const blockedNav = navElements.filter(blocked).map(e=>e.getAttribute('data-testid'));
  const allControls=[...document.querySelectorAll('button,input,textarea,select,[role="button"],[role="checkbox"]')]
    .filter(visible);
  const controls = allControls.map(e=>({id:e.getAttribute('data-testid')||'',
       tag:e.tagName.toLowerCase(),name:(e.getAttribute('aria-label')||'').slice(0,40),...rect(e)}));
  const blockedControls = allControls.filter(blocked).map(e=>
    e.getAttribute('data-testid')||e.getAttribute('aria-label')||e.textContent.trim().slice(0,30));
  const scrollableAxis = (p,axis) => {
    const s=getComputedStyle(p);
    return (axis==='x'?['auto','scroll'].includes(s.overflowX)
                         && p.scrollWidth>p.clientWidth+1
                      :['auto','scroll'].includes(s.overflowY)
                         && p.scrollHeight>p.clientHeight+1);
  };
  const canReachByScroll = (e,axis) => {
    if(getComputedStyle(e).position==='fixed')return false;
    for(let p=e.parentElement;p;p=p.parentElement) {
      if(scrollableAxis(p,axis))return true;
    }
    return axis==='y' && (document.documentElement.scrollHeight>height+1
      || document.body.scrollHeight>height+1);
  };
  const unreachableControls = allControls.filter(e=>{
     const r=e.getBoundingClientRect();
     const outsideX=r.x < -1 || r.right > width+1;
     const outsideY=r.y < -1 || r.bottom > height+1;
     if((outsideX && !canReachByScroll(e,'x'))
       ||(outsideY && !canReachByScroll(e,'y')))return true;
     for(let p=e.parentElement;p;p=p.parentElement){
       const s=getComputedStyle(p);
       const parent=p.getBoundingClientRect();
       if((['hidden','clip'].includes(s.overflowX)
              && (r.x<parent.x-1 || r.right>parent.right+1))
           ||(['hidden','clip'].includes(s.overflowY)
              && (r.y<parent.y-1 || r.bottom>parent.bottom+1)))return true;
     }
     return false;
  }).map(e=>e.getAttribute('data-testid')||e.getAttribute('aria-label')||e.tagName);
  const clippedTextLabels=[...document.querySelectorAll('.q-field__label')]
    .filter(visible).filter(e=>e.scrollWidth>e.clientWidth+1)
    .map(e=>e.textContent.trim().slice(0,40));
  return {width,height,dpr:devicePixelRatio,
     htmlScrollWidth:document.documentElement.scrollWidth,
     bodyScrollWidth:document.body.scrollWidth,nav,navOverlap,blockedNav,
     blockedControls,unreachableControls,clippedTextLabels,controlCount:controls.length,
     offscreenControlCount:unreachableControls.length,
     horizontalOverflow:document.documentElement.scrollWidth>width+2};
}"""

def require_tabs(rows: list[dict]) -> bool:
    """Fail closed: skipped/disabled tab or covered controls cannot pass Stage4."""
    assert len(rows)==len(TABS), "required tab count mismatch"
    assert [row.get("tab") for row in rows]==list(TABS), "required tab order mismatch"
    for row in rows:
        assert not row.get("skipped"), ("required tab disabled or absent", row.get("tab"))
        geometry=row["geometry"]
        for key in ("horizontalOverflow","navOverlap","blockedNav",
                    "blockedControls","unreachableControls","clippedTextLabels"):
            assert not geometry[key], (row["tab"],key,geometry[key])
    return True

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def pctl(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered)-1)*fraction
    lower = int(index)
    return round(ordered[lower] + (ordered[min(lower+1,len(ordered)-1)]
        - ordered[lower])*(index-lower),2)

def run(output: Path, channel: str) -> int:
    from playwright.sync_api import expect, sync_playwright
    output = output.resolve()
    base_artifacts = (ROOT / "artifacts").resolve()
    if output == base_artifacts or not output.is_relative_to(base_artifacts) or output.exists():
        raise ValueError("fresh Stage4 owned output required")
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / "managed-fixture"
    fixture.mkdir()
    port = free_port()
    nonce = secrets.token_hex(24)
    origin = f"http://127.0.0.1:{port}"
    headers = {"X-Acceptance": nonce}
    before = source_hashes()
    report = dict(success=False, synthetic_only=True, channel=channel,
        limitations=["Browser DPR simulation is not physical Windows scaling",
                     "Synthetic fixture is not a real Cookie/real download/full Flet test",
                     "Playwright event-to-visible timing is not physical-paint latency"],
        checker_sha256=digest(Path(__file__)), source_before=before,
        viewports=[], samples_ms=[], page_errors=[], console_errors=[],
        external_requests=[], shutdown_ok=False, normal_exit=False,
        port_released=False, forced_cleanup=False)
    child = None
    opener = build_opener(ProxyHandler({}))
    try:
        with (output / "server.log").open("xb") as logfile:
            child = subprocess.Popen([
                sys.executable,"-B",str(ROOT/"tools/check_managed_downloads_browser.py"),
                "--serve",str(port),"--root",str(fixture),"--nonce",nonce],
                cwd=ROOT,env=child_environment(),stdin=subprocess.DEVNULL,
                stdout=logfile,stderr=logfile,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            report["owner_pid"] = child.pid
        wait_for_health(origin,child,expected_data="pixiv-readonly")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel=="chromium" else channel)
            try:
                for width,height,dpr in SIZES:
                    context = browser.new_context(
                        viewport={"width":width,"height":height},device_scale_factor=dpr)
                    def guard(route):
                        parsed=urlsplit(route.request.url)
                        if (parsed.scheme,parsed.netloc)!=("http",f"127.0.0.1:{port}"):
                            report["external_requests"].append("blocked")
                            route.abort()
                        else: route.continue_()
                    context.route("**/*",guard)
                    page=context.new_page()
                    page.on("pageerror",lambda e:report["page_errors"].append(str(e)[:180]))
                    page.on("console",lambda m:report["console_errors"].append(m.text[:180])
                            if m.type=="error" else None)
                    result=dict(viewport=[width,height],emulated_dpr=dpr,tabs=[])
                    report["viewports"].append(result)
                    try:
                        page.goto(origin,wait_until="domcontentloaded",timeout=25000)
                        for tab in TABS:
                            control=page.get_by_test_id("tab-"+tab)
                            if control.count()==0 or not control.is_enabled():
                                result["tabs"].append(dict(tab=tab,skipped=True,reason="not enabled"))
                                continue
                            start=time.perf_counter()
                            control.click()
                            expect(control).to_have_attribute("aria-current","page",timeout=15000)
                            if tab=="learning":
                                image=page.locator('.pv-learning img').first
                                expect(image).to_be_visible(timeout=15000)
                                image.evaluate('(img) => img.decode()')
                                assert image.evaluate('(img) => img.complete && img.naturalWidth>0')
                            page.wait_for_timeout(50)
                            elapsed=round((time.perf_counter()-start)*1000,2)
                            geometry=page.evaluate(JS_GEOMETRY)
                            shot=output/f"{width}x{height}-dpr{str(dpr).replace('.','_')}-{tab}.png"
                            page.screenshot(path=str(shot),animations="disabled")
                            result["tabs"].append(dict(tab=tab,elapsed_ms=elapsed,
                                screenshot=shot.name,screenshot_sha256=digest(shot),
                                geometry=geometry))
                        # 20 warm tab transitions, no external HTTP:
                        for _ in range(20):
                            start=time.perf_counter()
                            page.get_by_test_id("tab-library").click()
                            expect(page.get_by_test_id("tab-library")).to_have_attribute("aria-current","page")
                            report["samples_ms"].append(dict(viewport=width,dpr=dpr,tab="library",
                                ms=round((time.perf_counter()-start)*1000,2)))
                            page.get_by_test_id("tab-download").click()
                            expect(page.get_by_test_id("tab-download")).to_have_attribute("aria-current","page")
                        measures=[s["ms"] for s in report["samples_ms"] if s["viewport"]==width]
                        result["warm_library"] = dict(n=len(measures),p50_ms=pctl(measures,0.5),
                            p95_ms=pctl(measures,0.95),max_ms=max(measures))
                    finally:
                        context.close()
            finally:
                browser.close()
        assert len(report["viewports"])==len(SIZES)
        assert not report["external_requests"] and not report["page_errors"]
        assert not report["console_errors"]
        for sample in report["viewports"]:
            assert len(sample["tabs"])==len(TABS),sample["viewport"]
            require_tabs(sample["tabs"])
    except BaseException as error:
        report["error"]=f"{type(error).__name__}: {str(error)[:350]}"
        report["error_frames"]=[f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                for frame in traceback.extract_tb(error.__traceback__)[-8:]]
    finally:
        if child is not None:
            if child.poll() is None:
                try:
                    request=Request(origin+"/_managed_test/shutdown",data=b"",method="POST",headers=headers)
                    with opener.open(request,timeout=5) as response:
                        report["shutdown_ok"]=response.status==200
                except Exception as error:
                    report["shutdown_error"]=type(error).__name__
                try:
                    child.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    report["forced_cleanup"]=True
                    child.terminate()
                    child.wait(timeout=5)
            report["normal_exit"]=child.returncode==0 and not report["forced_cleanup"]
        report["port_released"]=port_released(port)
        report["source_after"]=source_hashes()
        report["source_changed"]=before!=report["source_after"]
        report["success"]=bool("error" not in report and report["normal_exit"]
            and report["port_released"] and not report["source_changed"]
            and not report["external_requests"] and not report["page_errors"]
            and not report["console_errors"])
        (output/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(dict(success=report["success"],error=report.get("error"),
        viewports=len(report["viewports"]),samples=len(report["samples_ms"]),
        normal_exit=report["normal_exit"],port_released=report["port_released"]),
        ensure_ascii=False))
    return 0 if report["success"] else 1

if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--output",required=True,type=Path)
    cli.add_argument("--channel",choices=("chromium","msedge"),default="chromium")
    args=cli.parse_args()
    raise SystemExit(run(args.output,args.channel))
