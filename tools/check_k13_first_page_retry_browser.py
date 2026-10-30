"""K13 synthetic first-page failure -> same-condition explicit retry in owned GUI.

Reuses the frozen offline K03 fixture server. No actual Pixiv authentication,
HTTP or personal data is used; failed and successful requests are synthetic.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from tools.run_browser_checks import (
    child_environment, free_port, source_hashes, wait_for_health, port_released)
from tools.owned_process_tree import OwnedProcessTree

class FixtureReader:
    """Generated three-card first page, with a one-shot 500 on weekly page1."""

    def __init__(self):
        from PIL import Image
        raw=BytesIO()
        Image.new("RGB",(100,80),(45,82,112)).save(raw,"PNG")
        self.png=raw.getvalue()
        self.calls=[]
        self.failed_weekly=False

    def require_identity(self):
        return "91"

    def get_ranking(self, **values):
        # Never capture actual auth, URLs or secret values: synthetic fields only.
        self.calls.append(dict(kind="illustration", **values))
        if values.get("mode")=="weekly" and values.get("page")==1 and not self.failed_weekly:
            self.failed_weekly=True
            from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
            raise HTTPStatusRejected(500)
        if values.get("page") != 1:
            return []
        return [dict(
            illust_id=i,user_id=91,user_name="Synthetic author",
            title=f"Synthetic {values['content']} today {i}",
            tags=["fixture"],url=f"https://i.pximg.net/synthetic/{i}.png",
            rank=i-100,yes_rank=i-99,rating_count=i,view_count=i*2,
            page_count=1,ai_type=1,illust_type=0)
            for i in (101,102,103)]

    def get_artwork_pages(self, wid):
        return (f"https://i.pximg.net/synthetic/{wid}.png",)

    def get_image_bytes(self, url):
        if not url.startswith("https://i.pximg.net/synthetic/"):
            raise AssertionError("synthetic image host only")
        return self.png

    def get_work_info(self, wid):
        return {"ai_type":1}

def serve(port: int, out: Path) -> None:
    from fastapi import Header, HTTPException
    from nicegui import app, ui
    from tools.check_library_calendar_slider_owned import deny_external_connections
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.ranking_source import RankingSource
    from pixivvault_nicegui.remote_media import RemoteBookmarkImages
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ui.shell import build_demo_page

    network=deny_external_connections()
    services=get_services(app)
    if services.data_mode!="synthetic" or services.live_client is not None:
        raise RuntimeError("non-synthetic fixture rejected")
    fixture=FixtureReader()
    services.ranking_source=RankingSource(fixture)
    services.acquire_ranking_images=lambda records:services.images.acquire_catalog(
        RemoteBookmarkImages(fixture,tuple(records)))
    register_media_routes(app,services.images)
    ui.page("/")(build_demo_page)
    nonce=os.environ["PV_SYNTHETIC_ACCEPTANCE_NONCE"]
    stopping={"value":False}

    def authorized(value):
        if not secrets.compare_digest(value or "",nonce):
            raise HTTPException(status_code=403)

    @app.get("/healthz")
    def health():
        return {"status":"ok","data":"synthetic","mode":"demo"}

    @app.get("/_ranking_fixture/identity")
    def identity(x_acceptance:str|None=Header(default=None)):
        authorized(x_acceptance)
        return {"synthetic":True,"owned_pid":os.getpid(),"parent_pid":os.getppid()}

    @app.get("/_ranking_fixture/snapshot")
    def snapshot(x_acceptance:str|None=Header(default=None)):
        authorized(x_acceptance)
        return {"synthetic":True,"calls":list(fixture.calls),
                "external_sockets_denied":network['denied']}

    @app.post("/_ranking_fixture/shutdown")
    def shutdown(x_acceptance:str|None=Header(default=None)):
        authorized(x_acceptance)
        if stopping["value"]:
            raise HTTPException(status_code=409)
        stopping["value"]=True
        app.shutdown()
        return {"stopping":True}

    app.on_shutdown(services.close)
    ui.run(host="127.0.0.1",port=port,show=False,reload=False,
           binding_refresh_interval=None,timeout_graceful_shutdown=5)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def harness_hashes() -> dict[str, str]:
    """Pin the executable checker and external test helpers, not only product src."""
    names=("check_k13_first_page_retry_browser.py", "run_browser_checks.py",
           "check_personal_ranking_k09_k12_browser.py",
           "check_library_calendar_slider_owned.py", "owned_process_tree.py",
           "check_native_window.py")
    return {name:sha(ROOT / "tools" / name) for name in names}

def scoped_success(report: dict) -> bool:
    """Fail closed on *any* attempted recovery, even if the root later exits 0."""
    abnormal = ("error_class", "shutdown_error_class", "normal_shutdown_timeout",
                "cleanup_error_class", "cleanup_owner_unverified",
                "cleanup_root_timeout", "cleanup_root_error_class")
    return bool(len(report.get("checks", [])) == 3 and
        not any(key in report for key in abnormal) and
        not report.get("page_errors") and not report.get("console_errors") and
        not report.get("external_requests") and report.get("external_sockets_denied") == 0 and
        report.get("normal_exit") is True and report.get("forced_cleanup") is False and
        report.get("port_released") is True and report.get("shutdown_acknowledged") is True and
        report.get("listener_owner_verified") is True and
        report.get("owned_tree", {}).get("remaining_count") == 0 and
        not report.get("owned_tree", {}).get("errors", ["unverified"]) and
        report.get("source_changed") is False and report.get("harness_changed") is False)

def cleanup_owned_fixture(server, tree: OwnedProcessTree, report) -> None:
    """Only after failed normal shutdown; signal pinned owned processes."""
    tree.sample()
    if tree.errors or server.pid not in tree.identities:
        report['cleanup_owner_unverified'] = True
        return
    if os.name == 'nt':
        from tools import check_native_window as native
        observed = dict(tree.identities)
        # Kernel birth check prevents reused/foreign PIDs from being signalled.
        affected = native.terminate_owned(observed)
        report['forced_cleanup_count'] = len(affected)
        report['forced_cleanup'] = bool(affected)
    elif server.poll() is None and os.getpgid(server.pid) == server.pid:
        # A fresh session was created by our own subprocess.Popen below.
        os.killpg(server.pid, signal.SIGTERM)
        report['forced_cleanup'] = True
    else:
        report['cleanup_owner_unverified'] = True
    if server.poll() is None:
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            report['cleanup_root_timeout'] = True
            if os.name != 'nt':
                try:
                    # Only the private session created by our Popen may be
                    # escalated; verify that root is still its group leader.
                    if os.getpgid(server.pid) == server.pid:
                        os.killpg(server.pid, signal.SIGKILL)
                        report['forced_cleanup'] = True
                except ProcessLookupError:
                    pass
            else:
                # Direct owned Popen handle, not a PID found by enumeration.
                server.kill()
                report['forced_cleanup'] = True
            server.wait(timeout=5)
    if os.name != 'nt':
        tree.sample()
        survivors = [pid for pid in (tree.last_living or []) if pid != server.pid]
        try:
            if any(os.getpgid(pid) == server.pid for pid in survivors):
                os.killpg(server.pid, signal.SIGKILL)
                report['forced_cleanup'] = True
        except ProcessLookupError:
            pass

def run(out: Path, channel: str) -> int:
    from playwright.sync_api import expect, sync_playwright
    from tools.check_personal_ranking_k09_k12_browser import select_option

    out = out.resolve()
    allowed = (ROOT / "artifacts").resolve()
    if out == allowed or not out.is_relative_to(allowed) or out.exists():
        raise ValueError("fresh owned output under artifacts required")
    out.mkdir(parents=True, exist_ok=False)
    port, nonce = free_port(), secrets.token_urlsafe(32)
    url = f"http://127.0.0.1:{port}"
    headers = {"X-Acceptance": nonce}
    env = child_environment()
    env.update(PYTHONDONTWRITEBYTECODE="1", PV_SYNTHETIC_ACCEPTANCE_NONCE=nonce)
    report = dict(success=False, synthetic_only=True, original_id="K13",
                  binding_id="v8-K13-01", channel=channel, checks=[],
                  checker_sha256=sha(Path(__file__)),
                  harness_before=harness_hashes(), harness_changed=None,
                  source_before=source_hashes(), source_changed=None,
                  external_requests=[], page_errors=[], console_errors=[],
                  console_error_flags=[],
                  forced_cleanup=False, normal_exit=False, port_released=False)
    server = tree = None
    opener = build_opener(ProxyHandler({}))
    try:
        with (out / "server.log").open("xb") as log:
            server = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()),
                 "--serve", str(port), "--output", str(out)],
                env=env, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != 'nt')
        report["owned_pid"] = server.pid
        tree = OwnedProcessTree(server)
        if tree.errors:
            raise RuntimeError('fixture process identity unavailable')
        wait_for_health(url, server, timeout=40)
        owner_request=Request(url+"/_ranking_fixture/identity",headers=headers)
        with opener.open(owner_request, timeout=5) as response:
            identity=json.load(response)
        report["listener_pid"] = identity.get("owned_pid")
        report["listener_parent_pid"] = identity.get("parent_pid")
        if (identity.get("synthetic") is not True or
            server.pid not in (identity.get("owned_pid"), identity.get("parent_pid")) or
            server.poll() is not None):
            raise RuntimeError("synthetic listener is not the newly owned child")
        if not tree.observed_endpoint(identity['owned_pid']):
            raise RuntimeError('synthetic listener PID/birth not owned by fixture process tree')
        report["listener_owner_verified"]=True
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=None if channel=="chromium" else channel)
            context=browser.new_context(viewport={"width":1280,"height":720})
            def guard(route):
                target=urlsplit(route.request.url)
                if (target.scheme, target.netloc) != ("http",f"127.0.0.1:{port}"):
                    report["external_requests"].append("blocked")
                    route.abort()
                else:
                    route.continue_()
            context.route("**/*", guard)
            page=context.new_page()
            page.on("pageerror", lambda e:report["page_errors"].append(type(e).__name__))
            def on_console(message):
                if message.type != "error":
                    return
                # Keep only boolean diagnostic flags, never raw URL/message.
                detail = message.text
                report["console_error_flags"].append({
                    "http_500": "500" in detail,
                    "failed_resource": "Failed to load resource" in detail,
                    "javascript_exception": "TypeError" in detail or "ReferenceError" in detail,
                })
                report["console_errors"].append("error")
            page.on("console", on_console)
            page.set_default_timeout(18000)
            def snapshot():
                response=page.request.get(url+"/_ranking_fixture/snapshot",headers=headers)
                assert response.ok
                return response.json()
            try:
                page.goto(url,wait_until="domcontentloaded")
                panel=page.get_by_test_id("ranking-view")
                expect(panel).to_be_visible()
                fetch=panel.get_by_test_id("ranking-fetch")
                cards=panel.locator(".pv-card")
                notice=panel.get_by_test_id("ranking-notice")
                fetch.click()
                expect(cards).to_have_count(3)
                expect(notice).to_contain_text("公式ランキング")
                first_ids=[e.get_attribute("data-work-id") for e in cards.all()]
                first_card_identity=cards.first.get_attribute("id")
                report["checks"].append("initial_daily_first_page_success")

                select_option(page.get_by_test_id("ranking-mode"),"週間",page)
                expect(notice).to_contain_text("取得できませんでした")
                expect(notice).to_contain_text("前の一覧を保持")
                expect(fetch).to_be_enabled()
                expect(panel.locator(".pv-more-cell button")).to_be_disabled()
                expect(cards).to_have_count(3)
                assert [e.get_attribute("data-work-id") for e in cards.all()] == first_ids
                before=snapshot()["calls"]
                weekly=[dict(x) for x in before if x.get("kind")=="illustration" and x.get("mode")=="weekly" and x.get("page")==1]
                assert len(weekly)==1
                report["checks"].append("failed_first_page_preserves_cards_and_enables_retry")

                fetch.click()
                expect(notice).to_contain_text("公式ランキング")
                expect(cards).to_have_count(3)
                expect(panel.locator(".pv-more-cell button")).to_be_enabled()
                final_snapshot=snapshot()
                final=final_snapshot["calls"]
                report["external_sockets_denied"]=final_snapshot["external_sockets_denied"]
                weekly_after=[dict(x) for x in final if x.get("kind")=="illustration" and x.get("mode")=="weekly" and x.get("page")==1]
                assert len(weekly_after)==2 and weekly_after[0]==weekly_after[1]
                assert [e.get_attribute("data-work-id") for e in cards.all()] == first_ids
                # Same synthetic payload is valid but must be a fresh accepted query.
                assert cards.first.get_attribute("id") != first_card_identity
                report["same_condition_retry_calls"] = len(weekly_after)
                report["checks"].append("explicit_same_weekly_first_page_retry_success")
                shot=out/"k13-first-page-retry-synthetic.png"
                page.screenshot(path=str(shot),animations="disabled")
                report["screenshot_sha256"]=sha(shot)
            finally:
                context.close()
                browser.close()
    except BaseException as exc:
        report["error_class"]=type(exc).__name__
        report["safe_stack"]=[f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                              for frame in traceback.extract_tb(exc.__traceback__)[-6:]]
    finally:
        if server is not None:
            if server.poll() is None:
                try:
                    req=Request(url+"/_ranking_fixture/shutdown",headers=headers,data=b"",method="POST")
                    with opener.open(req,timeout=5) as resp:
                        report["shutdown_acknowledged"]=resp.status==200
                except Exception as exc:
                    report["shutdown_error_class"]=type(exc).__name__
                try:
                    server.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    report["normal_shutdown_timeout"]=True
            if server.poll() is None:
                try:
                    if tree is None:
                        report['cleanup_owner_unverified'] = True
                    else:
                        cleanup_owned_fixture(server, tree, report)
                except Exception as exc:
                    report['cleanup_error_class'] = type(exc).__name__
                if server.poll() is None:
                    # Exact direct Popen handle created above, not an observed
                    # foreign PID. A failed cleanup is always a RED receipt.
                    report['forced_cleanup'] = True
                    try:
                        server.terminate()
                        server.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        # If the exact owned Popen still does not exit, fail
                        # closed and finish bounded cleanup without PID lookup.
                        report['cleanup_root_timeout'] = True
                        server.kill()
                        server.wait(timeout=5)
                    except Exception as exc:
                        report['cleanup_root_error_class'] = type(exc).__name__
            report["normal_exit"]=server.poll()==0
        if tree is not None:
            report['owned_tree'] = tree.finish()
        report["port_released"]=port_released(port)
        report["source_after"]=source_hashes()
        report["source_changed"]=report["source_before"]!=report["source_after"]
        report["harness_after"]=harness_hashes()
        report["harness_changed"]=report["harness_before"]!=report["harness_after"]
        report["success"] = scoped_success(report)
        (out/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:report.get(k) for k in
       ("success","checks","error_class","normal_exit","port_released","source_changed")},
       ensure_ascii=False))
    return 0 if report["success"] else 1

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--serve",type=int)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--channel",choices=("chromium","msedge"),default="chromium")
    args=parser.parse_args()
    if args.serve:
        serve(args.serve,args.output)
    else:
        raise SystemExit(run(args.output,args.channel))
