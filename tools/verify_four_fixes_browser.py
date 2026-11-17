"""Real-browser verification of four recent fixes (Q17, X09, R01a/R07, R01b).

Starts the product's own register_pages()/ui.run() on 127.0.0.1:<port> with
isolated roots under verify/<timestamp>/ and a fake, network-free live client,
then drives it with Playwright (Edge) and records body text + screenshots.
The product source is never modified.  --profile-root is not used: bootstrap
rejects it without --native, and the native profile path would build a
Cookie-backed reader and open the shared Windows STOP owner.

Run (from work/):
  python -B tools/verify_four_fixes_browser.py --out S:/Projects/PV-DEV-20261117/verify/<ts>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

HOST = "127.0.0.1"
SCENARIOS = ("q17-mixed", "q17-abort", "x09-manifest-gone", "x09-folder-gone", "x09-payload-gone", "reco")


def png(color=(30, 20, 10), size=(16, 12)):
    from PIL import Image
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


# --------------------------------------------------------------------------- fakes
class FakeClient:
    """Network-free stand-in for the live Pixiv reader (no LivePixivReadClient)."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls: list[str] = []

    def verify_identity(self):
        return "99"

    def require_identity(self):
        return "99"

    def close(self):
        pass

    # managed downloads / failed-queue retry
    def get_artwork_download_record(self, wid):
        from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
        from pixivvault_nicegui.pixiv_read_client import ArtworkUnavailable, TransportFailed
        self.calls.append("record:" + wid)
        if self.scenario == "q17-abort":
            raise TransportFailed()
        if self.scenario == "q17-mixed" and wid == "1001":
            raise ArtworkUnavailable()
        return RemoteBookmark(wid, "illustration", "show", "Synthetic " + wid, "77", "Synthetic author",
                              (), "", "2026-11-01T00:00:00Z", 2, 0, 0)

    def get_artwork_original_pages(self, wid):
        count = 3 if wid == "123" else 2 if wid.startswith("100") else 1
        return tuple(f"https://i.pximg.net/synthetic/{wid}/{n}.png" for n in range(count))

    def get_image_bytes(self, url):
        page = int(url.rsplit("/", 1)[-1].split(".")[0])
        return png((30 + page * 20, 20, 10), (16 + page * 2, 12))

    # recommendation acquisition
    def search_recommendation_illusts(self, tag, page=1, mode="all"):
        base = 500 + (sum(map(ord, tag)) % 50) * 10
        works = [dict(id=str(base + i), title=f"Synthetic {tag} {i}", user_id="31", user_name="Synth author",
                      thumbnail_url="", page_count=1, illust_type=0, tags=[], create_date="", x_restrict=0)
                 for i in range(3)]
        return {"works": works, "total": 3}

    def get_user_works(self, author_id, early_exit_checker=None, lane=None):
        return []

    def get_discovery_work(self, wid):
        return dict(id=wid, title="Synthetic superlike " + wid, user_id="9", user_name="Synthetic author",
                    thumbnail_url="", page_count=1, illust_type=0, tags=[], create_date="", x_restrict=0)


# --------------------------------------------------------------------------- seeds
class Ctx:
    def checkpoint(self):
        pass

    def progress(self, *_):
        pass


def seed_failed_queue(db: Path):
    db.parent.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,work_type TEXT,"
                    "error_reason TEXT,failed_at TEXT,retry_count INTEGER)")
        con.executemany("INSERT INTO failed_queue VALUES(?,?,?,?,?,?,?)", [
            (str(1001 + i), "77", f"synthetic failure {i}", "illust", "Timeout", f"2026-11-0{i+1}", 1)
            for i in range(3)])
    con.close()


def seed_managed_x09(root: Path, kind: str) -> dict:
    """Two copy-mode quarantine operations, then break one (mirrors the unit test)."""
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads

    class C:
        def verify_identity(self):
            return "99"

        def get_artwork_original_pages(self, wid):
            return tuple(f"https://i.pximg.net/synthetic/{wid}/{n}.png" for n in range(3 if wid == "123" else 1))

        def get_image_bytes(self, url):
            page = int(url.rsplit("/", 1)[-1].split(".")[0])
            return png((30 + page * 20, 20, 10), (16 + page * 2, 12))

    def record(wid):
        return RemoteBookmark(wid, "illustration", "show", "Synthetic " + wid,
                              "24" if wid in ("123", "124") else "25", "Synthetic author", (), "",
                              "2026-10-29T00:00:00Z", 3 if wid == "123" else 1, 0, 0)

    writer = ManagedPixivDownloads(root, C(), resolve_record=record)
    for wid in ("123", "124", "223", "224"):
        writer.run(SimpleNamespace(kind="pixiv-illust", target_id=wid), Ctx())
    source = ManagedDuplicateInspection(writer)

    def plan_for(*keys):
        scan = source.scan()
        sel = {g.group_id: {i.key for i in g.items if i.key in set(keys)} for g in scan.groups}
        return source.plan(scan, sel)

    source.execute(plan_for("123:2"), operation_id="a" * 32, mode="copy")
    source.execute(plan_for("223:1", "123:3"), operation_id="b" * 32, mode="copy")
    ops = writer.quarantine.operations
    healthy = sorted((ops / ("b" * 32)).glob("*.png"))
    info = dict(operations_dir=str(ops), healthy_bytes=sum(p.stat().st_size for p in healthy),
                healthy_files=len(healthy), excluded_pages_expected=3, healthy_copies_expected=2)
    snap = source.quarantine_snapshot()
    info["pre_break_snapshot"] = (snap.state, snap.copy_count, snap.missing_count,
                                  snap.corrupt_count, snap.unconfirmed_count, snap.excluded_pages)
    source.close()
    writer.close()
    return info


def break_x09(kind: str, ops_dir: Path):
    import shutil
    folder = ops_dir / ("a" * 32)
    if kind == "payload-gone":
        for p in folder.glob("*.png"):
            p.unlink()
    elif kind == "folder-gone":
        shutil.rmtree(folder)
    else:
        (folder / "manifest.json").unlink()


def seed_library_and_reco(folder: Path):
    folder.mkdir(parents=True, exist_ok=False)
    db = folder / "synthetic-legacy.sqlite"
    with sqlite3.connect(db) as c:
        c.executescript("""
            CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
              tags TEXT,user_id TEXT,is_deleted INTEGER DEFAULT 0,last_backup TEXT);
            CREATE TABLE following_users (user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO following_users VALUES ('12','synthetic author');
            INSERT INTO works VALUES ('101','synthetic work',1,'["a"]','12',0,'2026-10-22');
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE discovery_swipe_history(work_id TEXT,verdict TEXT,judged_at TEXT,recommend_dismissed INTEGER);
            INSERT INTO discovery_swipe_history VALUES ('123','superLike','2026-11-01',0);
            CREATE TABLE superlike_info_cache(work_id TEXT,title TEXT,user_id TEXT,user_name TEXT);
            INSERT INTO superlike_info_cache VALUES ('123','Synthetic superlike','9','Synthetic author');
            CREATE TABLE discovery_preference(tag TEXT PRIMARY KEY,display_tag TEXT,superlike_count INTEGER,
              like_count INTEGER,skip_count INTEGER,hate_count INTEGER,shown_count INTEGER,open_count INTEGER,
              bookmark_count INTEGER,seed_saved INTEGER,seed_bookmarked INTEGER);
            INSERT INTO discovery_preference VALUES ('tagA','tagA',1,3,0,0,5,0,0,0,0);
            INSERT INTO discovery_preference VALUES ('tagB','tagB',0,2,1,0,4,0,0,0,0);
            CREATE TABLE recommend_section_carryover(section TEXT,payload_json TEXT);
        """)
        payload = json.dumps([dict(tag="tagA", display="tagA", page=1, total=2, exhausted=True, works=[
            dict(id="901", title="carry 1", user_id="31", user_name="Synth author"),
            dict(id="902", title="carry 2", user_id="31", user_name="Synth author")])])
        c.execute("INSERT INTO recommend_section_carryover VALUES('certain',?)", (payload,))
    c.close()
    images = folder / "fake-images"
    (images / "synthetic author(12)").mkdir(parents=True)
    (images / "synthetic author(12)" / "synthetic work(101)_1.png").write_bytes(png((200, 0, 0), (64, 48)))
    return db, images


# --------------------------------------------------------------------------- server
def serve(args):
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    scen = args.scenario
    base = Path(args.root)
    fake = FakeClient(scen)
    kw = dict(live_client=fake)
    if scen == "reco":
        kw.update(library_readonly_db=base / "lib" / "synthetic-legacy.sqlite",
                  library_image_root=base / "lib" / "fake-images",
                  recommendation_preferences_root=base / "prefs")
    else:
        kw.update(pixiv_download_root=base / "managed")
        if scen.startswith("q17"):
            kw.update(failed_queue_readonly_db=base / "frozen" / "failed.sqlite3")
    for v in kw.values():
        if isinstance(v, Path) and not v.resolve().is_relative_to(base.resolve()):
            raise SystemExit("path outside isolated run folder")
    register_pages(**kw)
    stopping = False

    @app.post("/_verify/shutdown", include_in_schema=False)
    def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopping
        if not secrets.compare_digest(x_acceptance or "", args.nonce):
            raise HTTPException(403)
        stopping = True
        app.shutdown()
        return {"stopping": True}

    ui.run(host=HOST, port=args.port, show=False, reload=False, native=False, binding_refresh_interval=None)


# --------------------------------------------------------------------------- browser checks
def txt(page, tid, timeout=15000):
    loc = page.get_by_test_id(tid)
    loc.wait_for(state="attached", timeout=timeout)
    return loc.inner_text()


def run_q17(page, shot, scen):
    page.get_by_test_id("tab-failed").click()
    page.get_by_test_id("failed-count").filter(has_text="3 / 3").wait_for(timeout=20000)
    btn = page.get_by_test_id("failed-retry-all")
    from playwright.sync_api import expect
    expect(btn).to_be_enabled(timeout=15000)
    before = btn.inner_text()
    shot("before-click")
    btn.click()
    sel = page.get_by_test_id("failed-managed-retry-status")
    expect(sel).to_contain_text("再試行:", timeout=30000)
    # wait for jobs to settle (pending == 0) when any were accepted
    if scen == "q17-mixed":
        expect(sel).to_contain_text("保存結果未確定 0 件", timeout=90000)
        time.sleep(1.5)
    else:
        time.sleep(1.0)
    text = sel.inner_text()
    shot("after-retry")
    first_sentence = text.split("。")[0]
    if scen == "q17-mixed":
        ok = text.startswith("再試行: 1/3件が失敗しました。") and "未処理" not in first_sentence
        expected = "先頭が「再試行: 1/3件が失敗しました。」で第1文に「未処理」を含まない"
    else:
        ok = text.startswith("再試行: 1/3件が失敗しました、未処理 2 件。")
        expected = "先頭が「再試行: 1/3件が失敗しました、未処理 2 件。」"
    return ok, expected, dict(button_before=before, notice=text)


def run_x09(page, shot, scen):
    kind = scen[4:]
    page.get_by_test_id("tab-dedup").click()
    page.get_by_test_id("duplicate-view").wait_for(timeout=15000)
    page.get_by_test_id("duplicate-trash").click()
    st = page.get_by_test_id("duplicate-trash-status")
    st.wait_for(timeout=15000)
    deadline = time.time() + 20
    while time.time() < deadline and "確認しています" in st.inner_text():
        time.sleep(0.3)
    time.sleep(0.5)
    got = dict(status=st.inner_text(), copy_count=txt(page, "duplicate-trash-copy-count"),
               copy_bytes=txt(page, "duplicate-trash-copy-bytes"), excluded=txt(page, "duplicate-trash-excluded"))
    shot("trash-dialog")
    others = "2件" in got["copy_count"] and got["excluded"].endswith("3ページ")
    if kind == "payload-gone":
        ok = others and "実体が見つからない隔離コピー: 1件" in got["status"]
        expected = "他の件数(2件/3ページ)と「実体が見つからない隔離コピー: 1件」"
    else:
        ok = others and "（確認できない項目: 1件）" in got["status"]
        expected = "他の件数(2件/3ページ)と「（確認できない項目: 1件）」"
    return ok, expected, got


def prefs_state(path: Path):
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rev = con.execute("SELECT revision FROM identity WHERE id=1").fetchone()[0]
        ov = con.execute("SELECT kind,target,hidden FROM overrides ORDER BY kind,target").fetchall()
        return rev, ov
    finally:
        con.close()


def run_reco(page, shot, scen, base: Path):
    from playwright.sync_api import expect
    results = {}
    db = base / "lib" / "synthetic-legacy.sqlite"
    prefs = base / "prefs" / "preferences.sqlite3"
    sha0 = hashlib.sha256(db.read_bytes()).hexdigest()
    page.get_by_test_id("tab-recommend").click()
    notice = page.get_by_test_id("recommend-notice")
    page.get_by_test_id("recommend-view").wait_for(timeout=15000)
    page.get_by_test_id("recommend-dismiss-superlike-123").wait_for(timeout=20000)
    time.sleep(0.5)
    shot("R07-0-initial")
    # ---- R01b: refresh first (a superlike is still visible so the count is non-zero)
    page.get_by_test_id("recommend-update").click()
    pat = re.compile(r"おすすめを更新しました（(\d+)節・(\d+)作品）。専用設定に保存しました。")
    deadline = time.time() + 40
    text = ""
    while time.time() < deadline:
        text = notice.inner_text()
        if text.startswith("おすすめを更新しました"):
            break
        time.sleep(0.3)
    time.sleep(0.5)
    text = notice.inner_text()
    m = pat.fullmatch(text.strip())
    # The page renders a placeholder block for unavailable sections; the notice counts
    # only available ones (order minus unavailable), so compare against those.
    sections = page.locator('[data-testid^="recommend-section-"]').filter(
        has_not_text="この節の保存済み結果がありません").count()
    works = page.locator('[data-testid^="recommend-work-"]').count()
    results["R01b"] = dict(ok=bool(m) and int(m.group(1)) == sections and int(m.group(2)) == works,
        expected="「おすすめを更新しました（N節・M作品）。専用設定に保存しました。」でN/Mが画面の節数・作品数と一致",
        got=dict(notice=text, dom_sections=sections, dom_works=works))
    shot("R01b-after-update")
    # ---- R01a/R07
    rev0, ov0 = prefs_state(prefs)
    btn = page.get_by_test_id("recommend-dismiss-superlike-123")
    expect(btn).to_be_visible()
    page.evaluate("""() => {const b=document.querySelector('[data-testid="recommend-dismiss-superlike-123"]');
        b.click(); b.click();}""")
    expect(notice).to_have_text("おすすめから取り下げました。作品の判定は変更していません。", timeout=20000)
    time.sleep(1.0)
    gone = page.get_by_test_id("recommend-dismiss-superlike-123").count() == 0
    rev1, ov1 = prefs_state(prefs)
    undo = page.get_by_test_id("recommend-undo")
    undo_visible = undo.is_visible()
    undo_text = undo.inner_text() if undo_visible else ""
    shot("R07-1-withdrawn")
    notice_after = notice.inner_text()
    sha1 = hashlib.sha256(db.read_bytes()).hexdigest()
    with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as c:
        verdicts = c.execute("SELECT work_id,verdict,recommend_dismissed FROM discovery_swipe_history").fetchall()
    undo.click()
    time.sleep(1.5)
    back = page.get_by_test_id("recommend-dismiss-superlike-123").count() == 1
    rev2, ov2 = prefs_state(prefs)
    shot("R07-2-undone")
    sha2 = hashlib.sha256(db.read_bytes()).hexdigest()
    results["R07a-withdraw"] = dict(ok=gone and undo_visible and rev1 == rev0 + 1
        and ov1 == [("superlike", "123", 1)] and verdicts == [("123", "superLike", 0)] and sha0 == sha1,
        expected="取り下げ1回(連打しても二重に効かない): 画面から消える/取消ボタン表示/設定リビジョン+1ちょうど/override 1件/判定verdict・DB不変",
        got=dict(card_gone=gone, undo_visible=undo_visible, undo_text=undo_text, notice=notice_after,
                 prefs_revision=[rev0, rev1], overrides=ov1, verdict_rows=verdicts, legacy_db_unchanged=sha0 == sha1))
    results["R07b-undo"] = dict(ok=back and rev2 == rev1 + 1 and ov2 == [("superlike", "123", 0)] and sha0 == sha2,
        expected="取消で作品が戻る/override hidden=0/DB不変",
        got=dict(card_back=back, prefs_revision=[rev1, rev2], overrides=ov2, legacy_db_unchanged=sha0 == sha2))
    return results


# --------------------------------------------------------------------------- orchestrator
def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) != 0


def http(url, data=None, headers=None, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=timeout)


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def run_scenario(pw, scen, run, port, py):
    base = run / scen
    base.mkdir(parents=True)
    (base / "tmp").mkdir()
    info = {}
    if scen.startswith("q17"):
        seed_failed_queue(base / "frozen" / "failed.sqlite3")
    elif scen.startswith("x09"):
        info = seed_managed_x09(base / "managed", scen[4:])
    else:
        seed_library_and_reco(base / "lib")
    nonce = secrets.token_urlsafe(24)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               TEMP=str(base / "tmp"), TMP=str(base / "tmp"), NICEGUI_STORAGE_PATH=str(base / ".nicegui"))
    env.pop("PYTEST_CURRENT_TEST", None)
    log = (base / "server.log").open("wb")
    proc = subprocess.Popen([py, "-B", str(Path(__file__).resolve()), "--serve", "--scenario", scen,
                             "--root", str(base), "--port", str(port), "--nonce", nonce],
                            cwd=str(base), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://{HOST}:{port}"
    result = dict(scenario=scen, seed=info, pid=proc.pid, checks={}, errors=[], foreign_requests=[],
                  console_errors=[], page_errors=[])
    browser = ctx = None
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.returncode}")
            try:
                if http(url + "/healthz", timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            raise TimeoutError("server health timeout")
        if scen.startswith("x09"):
            break_x09(scen[4:], Path(info["operations_dir"]))
        browser = pw.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})

        def route(r):
            from urllib.parse import urlsplit
            u = urlsplit(r.request.url)
            if u.scheme in ("http", "https") and u.netloc != f"{HOST}:{port}":
                result["foreign_requests"].append(r.request.url[:80])
                r.abort()
            else:
                r.continue_()
        ctx.route("**/*", route)
        page = ctx.new_page()
        page.on("pageerror", lambda e: result["page_errors"].append(str(e)[:200]))
        page.on("console", lambda m: result["console_errors"].append(m.text[:200]) if m.type == "error" else None)
        page.goto(url, wait_until="domcontentloaded")
        page.get_by_test_id("tab-library").wait_for(timeout=20000)
        counter = [0]

        def shot(name):
            counter[0] += 1
            page.screenshot(path=str(base / f"{counter[0]:02d}-{name}.png"))
        if scen.startswith("q17"):
            ok, exp, got = run_q17(page, shot, scen)
            result["checks"]["Q17-" + scen[4:]] = dict(ok=ok, expected=exp, got=got)
        elif scen.startswith("x09"):
            ok, exp, got = run_x09(page, shot, scen)
            result["checks"]["X09-" + scen[4:]] = dict(ok=ok, expected=exp, got=got)
        else:
            result["checks"].update(run_reco(page, shot, scen, base))
        result["body_text_tail"] = page.inner_text("body")[-1500:]
        (base / "body.txt").write_text(page.inner_text("body"), encoding="utf-8")
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {str(e)[:600]}")
        try:
            page.screenshot(path=str(base / "ERROR.png"))
        except Exception:
            pass
    finally:
        if ctx:
            ctx.close()
        if browser:
            browser.close()
        try:
            http(url + "/_verify/shutdown", data=b"", headers={"X-Acceptance": nonce}, timeout=5)
            proc.wait(timeout=30)
            result["exit_code"] = proc.returncode
        except Exception as e:
            result["errors"].append(f"graceful shutdown failed ({type(e).__name__}: {str(e)[:120]}); taskkill used")
            kill_tree(proc.pid)
        if proc.poll() is None:
            kill_tree(proc.pid)
        log.close()
        t0 = time.time()
        while not port_free(port) and time.time() - t0 < 10:
            time.sleep(0.3)
        result["port_released"] = port_free(port)
    (base / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--scenario")
    ap.add_argument("--root")
    ap.add_argument("--port", type=int, default=22487)
    ap.add_argument("--nonce")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    if args.serve:
        return serve(args)
    if args.port == 22386:
        raise SystemExit("refusing the real app's port")
    if not port_free(args.port):
        raise SystemExit(f"port {args.port} already in use")
    run = args.out
    run.mkdir(parents=True, exist_ok=False)
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        for scen in (args.only or SCENARIOS):
            print("== scenario", scen, flush=True)
            results.append(run_scenario(pw, scen, run, args.port, sys.executable))
    rows = []
    for r in results:
        for k, v in r["checks"].items():
            rows.append((k, "PASS" if v["ok"] else "FAIL", v["expected"], v["got"]))
        if r["errors"] or not r.get("port_released", False) or r["foreign_requests"]:
            rows.append((r["scenario"] + "/harness", "ERROR", "; ".join(r["errors"]),
                         dict(port_released=r.get("port_released"), foreign=r["foreign_requests"])))
    (run / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for k, s, e, g in rows:
        print(f"[{s}] {k}\n   expected: {e}\n   got: {json.dumps(g, ensure_ascii=False, default=str)[:900]}")
    return 0 if all(r[1] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
