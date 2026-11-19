"""UI smoke regression (D4): every navigation tab x 2 sizes x light/dark in a real browser.

Starts the product's own register_pages()/ui.run() on 127.0.0.1:<port> (default 22488)
with an isolated data root under verify/<timestamp>/, a network-free fake live client
and an isolated, Cookie-less native profile (so the header auth label exists), then
drives it with Playwright (Edge).  The product source is never modified.

Per screen/size/theme it checks: no console error / unhandled exception, the expected
headings/buttons (tools/ui_smoke_expectations.json), no "Traceback"/"Internal Server
Error", no horizontal scroll.  Over the whole run it checks that the fake live client
received ZERO calls and that nothing tried to leave loopback (startup and tab tour go
through the stop gate / stay offline).

Run (from the work tree root):
  PYTHONPATH=src PYTHONUTF8=1 <venv>/python.exe -B tools/ui_smoke_browser.py
      [--out S:/Projects/PV-DEV-20261117/verify/<timestamp>] [--port 22488]
      [--only download queue ...] [--dump-testids]
See tools/UI_SMOKE_README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ui_smoke_helpers as H

HOST = "127.0.0.1"
VERIFY_ROOT = Path("S:/Projects/PV-DEV-20261117/verify")
SETTINGS_SCREEN = "settings"


# --------------------------------------------------------------------------- fakes
class FakeClient:
    """Network-free stand-in for the live Pixiv reader.

    Every method that would talk to Pixiv records its name in ``calls``; the smoke
    requires that list to stay empty (no automatic traffic from startup / tab open).
    """

    def __init__(self):
        self.calls: list[str] = []
        self.sites: list[str] = []  # parallel to calls: where in the product the call came from

    def _hit(self, name):
        import traceback
        self.calls.append(name)
        frames = [f for f in traceback.extract_stack()[:-2] if "pixivvault_nicegui" in f.filename]
        self.sites.append(" > ".join(f"{Path(f.filename).stem}.{f.name}" for f in frames[-4:]) or "?")

    def verify_identity(self):
        self._hit("verify_identity")
        return "99"

    def require_identity(self):
        self._hit("require_identity")
        return "99"

    def close(self):
        pass

    def get_following_users(self, uid, rest="show"):
        self._hit("get_following_users:" + str(rest))
        return []

    def __getattr__(self, name):
        # Only called for attributes the fake lacks.  A caller that swallows AttributeError (e.g.
        # start_discovery_initial_load) would otherwise truncate the call sequence silently, so
        # record every public name the product asked for; capability probes still see it missing.
        if not name.startswith("_"):
            self.__dict__.setdefault("missing", []).append(name)
        raise AttributeError(name)

    def get_artwork_download_record(self, wid):
        self._hit("get_artwork_download_record:" + str(wid))
        from pixivvault_nicegui.pixiv_read_client import ArtworkUnavailable
        raise ArtworkUnavailable()

    def get_artwork_original_pages(self, wid):
        self._hit("get_artwork_original_pages:" + str(wid))
        return ()

    def get_image_bytes(self, url):
        self._hit("get_image_bytes")
        raise OSError("smoke: network is not available")

    def search_recommendation_illusts(self, tag, page=1, mode="all"):
        self._hit("search_recommendation_illusts")
        return {"works": [], "total": 0}

    def get_user_works(self, author_id, early_exit_checker=None, lane=None):
        self._hit("get_user_works")
        return []

    def get_discovery_work(self, wid):
        self._hit("get_discovery_work")
        raise OSError("smoke: network is not available")

    # ranking / search / work info (their presence makes the product build its ranking source)
    def get_ranking(self, *a, **k):
        self._hit("get_ranking")
        return []

    def search_illusts(self, *a, **k):
        self._hit("search_illusts")
        return []

    def get_work_info(self, *a, **k):
        self._hit("get_work_info")
        raise OSError("smoke: network is not available")


# --------------------------------------------------------------------------- seeds
def seed_all(base: Path) -> dict:
    """Synthetic legacy library (product's own legacy schema) + failed queue.

    Only ``lib`` is pre-created; every owned root (managed, prefs, vault, profile ...) must NOT
    exist beforehand: the product creates and marks them itself.
    """
    import json
    from dataclasses import astuple
    from io import BytesIO
    from PIL import Image
    from pixivvault_nicegui.failed_records_fixtures import build_failed_records_fixture
    from pixivvault_nicegui.legacy.database import Database

    lib = base / "lib"
    lib.mkdir(parents=True)
    db = lib / "synthetic-legacy.sqlite"
    legacy = Database(db, sandbox_root=lib)  # full legacy schema, incl. every discovery_* table
    legacy.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    legacy.conn.execute("PRAGMA journal_mode=DELETE")
    legacy.conn.close()
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO following_users (user_id,name) VALUES ('12','synthetic author')")
        con.execute("INSERT INTO works (work_id,title,page_count,tags,user_id,is_deleted,last_backup,content_type,work_meta)"
                    " VALUES ('101','synthetic work',1,'[\"a\"]','12',0,'2026-10-22','illust','{}')")
        con.executemany("INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)",
                        [astuple(row) for row in build_failed_records_fixture()])
        payload = json.dumps([dict(tag="tagA", display="tagA", page=1, total=2, exhausted=True, works=[
            dict(id="901", title="carry 1", user_id="31", user_name="Synth author"),
            dict(id="902", title="carry 2", user_id="31", user_name="Synth author")])])
        con.execute("INSERT INTO recommend_section_carryover VALUES('certain',?,'2026-11-01T00:00:00')", (payload,))
    con.close()
    folder = lib / "fake-images" / "synthetic author(12)"
    folder.mkdir(parents=True)
    out = BytesIO()
    Image.new("RGB", (64, 48), (200, 0, 0)).save(out, "PNG")
    (folder / "synthetic work(101)_1.png").write_bytes(out.getvalue())
    return dict(db=db, images=lib / "fake-images")


# --------------------------------------------------------------------------- server
def make_stopped_reader(base, fake):
    """Use the product reader and durable stop with generated data only."""
    import hashlib
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate, _file_identity
    from pixivvault_nicegui.managed_network_stop_shared_scope import (
        ManagedUserNetworkStopScope, _OWNER_FILE, _OWNER_SCHEMA, _GATE_FILE, _file_identity_directory)
    root = base / 'generated-shared-stop'
    root.mkdir()
    gate = ManagedNetworkStopGate(root / _GATE_FILE)
    (root / _OWNER_FILE).write_text(json.dumps({
        'schema': _OWNER_SCHEMA, 'state': 'sealed',
        'root_identity': list(_file_identity_directory(root)),
        'gate_identity': list(_file_identity(gate.path)),
        'lock_identity': list(_file_identity(gate.lock_path)),
        'history_proof': hashlib.sha256(b'generated-only-no-real-historical-proof').hexdigest(),
    }), encoding='utf-8')
    scope = ManagedUserNetworkStopScope(root)
    scope.gate.trip(429)
    cookie = base / 'generated-cookie.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n'
                      '.pixiv.net\tTRUE\t/\tTRUE\t4102444800\tPHPSESSID\t55_generatedonly\n',
                      encoding='utf-8')
    def transport(*_args):
        fake._hit('transport')
        raise RuntimeError('unexpected synthetic transport')
    return scope.create_reader(cookie, transport=transport), scope


def serve(args):
    from tools.check_library_calendar_slider_owned import deny_external_connections
    network = deny_external_connections()
    from nicegui import app, ui
    from fastapi import Header, HTTPException
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding

    base = Path(args.root)
    fake = FakeClient()
    reader, scope = make_stopped_reader(base, fake)
    # The profile has no selected Cookie; the separate synthetic reader uses the
    # isolated shared STOP owner and generated Cookie created above.
    store = NativeProfileStore(base / "profile")
    profile = NativeProfileBinding(store, launch_snapshot=store.snapshot())
    profile.auth_stop_required = True
    kw = dict(live_client=reader, native_profile=profile, shared_network_stop_scope=scope,
              library_readonly_db=base / "lib" / "synthetic-legacy.sqlite",
              library_image_root=base / "lib" / "fake-images",
              pixiv_download_root=base / "managed",
              preferences_root=base / "prefs",
              recommendation_preferences_root=base / "prefs-reco",
              discovery_root=base / "discovery",
              external_vault_root=base / "vault",
              following_sync_root=base / "following",
              image_cache_root=base / "cache")
    for v in kw.values():
        if isinstance(v, Path) and not v.resolve().is_relative_to(base.resolve()):
            raise SystemExit("path outside isolated run folder")
    register_pages(**kw)
    stopping = False

    def authorized(value):
        if not secrets.compare_digest(value or "", args.nonce):
            raise HTTPException(403)

    @app.get("/_smoke/network", include_in_schema=False)
    def net(x_acceptance: str | None = Header(default=None)):
        authorized(x_acceptance)
        return {"calls": list(fake.calls), "sites": list(fake.sites), "denied": network["denied"],
                "missing_attributes": sorted(set(fake.__dict__.get("missing", [])))}

    @app.post("/_smoke/shutdown", include_in_schema=False)
    async def shutdown(x_acceptance: str | None = Header(default=None)):
        nonlocal stopping
        authorized(x_acceptance)
        stopping = True
        import faulthandler
        faulthandler.dump_traceback_later(12, exit=False)  # where a stuck shutdown waits -> server.log
        app.shutdown()
        return {"stopping": True}

    ui.run(host=HOST, port=args.port, show=False, reload=False, native=False, binding_refresh_interval=None)


# --------------------------------------------------------------------------- browser side
SNAPSHOT_JS = """() => {
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const ids = [...document.querySelectorAll('[data-testid]')].filter(shown).map(e => e.getAttribute('data-testid'));
  const de = document.documentElement;
  return {testids: [...new Set(ids)], text: document.body.innerText,
          scroll_width: Math.max(de.scrollWidth, document.body.scrollWidth),
          client_width: de.clientWidth,
          dark: document.body.classList.contains('body--dark')};
}"""


def port_free(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) != 0


def http(url, data=None, headers=None, timeout=5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=timeout)


def network_state(url, nonce):
    return json.loads(http(url + "/_smoke/network", headers={"X-Acceptance": nonce}).read())


def kill_tree(pid):
    # Only the process this run started (and its children).
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def safe_name(text):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)


def set_theme(page, theme):
    want_dark = theme == "dark"
    is_dark = page.evaluate("document.body.classList.contains('body--dark')")
    if is_dark != want_dark:
        page.get_by_test_id("theme-toggle").click()
        page.wait_for_function(
            f"document.body.classList.contains('body--dark') === {str(want_dark).lower()}", timeout=10000)
    return page.evaluate("document.body.classList.contains('body--dark')") == want_dark


def open_screen(page, name, spec, wait_ms):
    """Open one screen; returns once its first expected element is attached."""
    opener = spec.get("open") or f"tab-{name}"
    page.get_by_test_id(opener).click()
    ready = (spec.get("testids") or [None])[0]
    if ready and not ready.endswith("*"):
        page.get_by_test_id(ready).first.wait_for(state="attached", timeout=20000)
    page.wait_for_timeout(wait_ms)


def close_overlay(page):
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def run_matrix(pw, url, out: Path, expectations, screens, wait_ms, get_net, startup_wait_ms, strict=False):
    """The whole tour.  Returns (cells, foreign requests, testids per screen, call log).

    get_net() -> {"calls": [...], "denied": n} (cumulative, from the server).  The call log maps a
    phase ("startup", "load|<size>|<theme>", "<screen>|<size>|<theme>") to the calls / denied
    connection attempts that appeared during it.
    """
    gspec = expectations["global"]
    cells: dict[str, dict] = {}
    foreign: list[str] = []
    dumps: dict[str, list[str]] = {}
    call_log: dict[str, dict] = {}
    mark = {"calls": 0, "denied": 0}

    def lap(phase):
        state = get_net()
        call_log[phase] = dict(calls=state["calls"][mark["calls"]:], sites=state["sites"][mark["calls"]:],
                               denied=state["denied"] - mark["denied"])
        mark["calls"], mark["denied"] = len(state["calls"]), state["denied"]
        return call_log[phase]

    lap("startup")  # server up, no browser yet
    origin = f"{HOST}:{url.rsplit(':', 1)[1]}"
    browser = pw.chromium.launch(channel="msedge", headless=True)
    try:
        for size in H.SIZES:
            for theme in H.THEMES:
                console_errors: list[str] = []
                page_errors: list[str] = []
                ctx = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                          color_scheme=theme)

                def route(r):
                    from urllib.parse import urlsplit
                    u = urlsplit(r.request.url)
                    if u.scheme in ("http", "https") and u.netloc != origin:
                        foreign.append(r.request.url[:80])
                        r.abort()
                    else:
                        r.continue_()
                ctx.route("**/*", route)
                page = ctx.new_page()
                page.on("pageerror", lambda e: page_errors.append(str(e)[:300]))
                page.on("console", lambda m: console_errors.append(m.text[:300]) if m.type == "error" else None)
                page.on("response", lambda r: console_errors.append(f"HTTP {r.status} {r.url[:120]}")
                        if r.status >= 400 else None)
                page.goto(url, wait_until="domcontentloaded")
                page.get_by_test_id("tab-library").wait_for(timeout=30000)
                theme_ok = set_theme(page, theme)
                page.wait_for_timeout(startup_wait_ms)
                lap(f"load|{size[0]}x{size[1]}|{theme}")
                # What the user sees right after launch, before any click (initial tab).
                load_snap = page.evaluate(SNAPSHOT_JS)
                load_png = page.screenshot()
                load_errors = (list(console_errors), list(page_errors))
                for name in screens:
                    spec = expectations["screens"][name]
                    key = H.cell_key(name, size, theme)
                    startup = spec.get("kind") == "startup"
                    del console_errors[:], page_errors[:]
                    error = None
                    if startup:
                        snap = dict(load_snap)
                        console_errors.extend(load_errors[0])
                        page_errors.extend(load_errors[1])
                        seen = dict(calls=[], denied=0)  # judged by the startup/load network gate
                    else:
                        try:
                            open_screen(page, name, spec, wait_ms)
                        except Exception as e:  # missing opener / ready element: a FAIL, not a crash
                            error = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                        snap = page.evaluate(SNAPSHOT_JS)
                        seen = lap(key)
                    snap["console_errors"] = list(console_errors)
                    snap["page_errors"] = list(page_errors)
                    snap["theme_ok"] = theme_ok and snap.pop("dark") == (theme == "dark")
                    failures = H.check_snapshot(snap, gspec, spec)
                    failures += H.check_screen_network(seen["calls"], seen["denied"], spec.get("allowed_calls", []),
                                                       strict=strict)
                    if error:
                        failures.insert(0, "画面を開けない: " + error)
                    stem = out / "screens" / f"{safe_name(name)}_{size[0]}x{size[1]}_{theme}"
                    stem.parent.mkdir(parents=True, exist_ok=True)
                    if startup:
                        Path(str(stem) + ".png").write_bytes(load_png)
                    else:
                        page.screenshot(path=str(stem) + ".png")
                    Path(str(stem) + ".txt").write_text(snap["text"], encoding="utf-8")
                    dumps.setdefault(name, snap["testids"])
                    cells[key] = dict(failures=failures, testids=len(snap["testids"]),
                                      scroll=[snap["scroll_width"], snap["client_width"]])
                    if name == SETTINGS_SCREEN:
                        close_overlay(page)
                ctx.close()
    finally:
        browser.close()
    return cells, foreign, dumps, call_log


def run(args):
    expectations = H.load_expectations(args.expectations)
    screens = args.only or H.screen_order(expectations)
    unknown = [s for s in screens if s not in expectations["screens"]]
    if unknown:
        raise SystemExit(f"unknown screen(s): {unknown}")
    if args.port == 22386:
        raise SystemExit("refusing the real app's port")
    if not port_free(args.port):
        raise SystemExit(f"port {args.port} already in use")
    out = Path(args.out or VERIFY_ROOT / datetime.now().strftime("ui_smoke_%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    base = out / "run"
    base.mkdir()
    (base / "tmp").mkdir()
    seed_all(base)
    nonce = secrets.token_urlsafe(24)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               TEMP=str(base / "tmp"), TMP=str(base / "tmp"), NICEGUI_STORAGE_PATH=str(base / ".nicegui"))
    env.pop("PYTEST_CURRENT_TEST", None)
    log = (base / "server.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--serve",
                             "--root", str(base), "--port", str(args.port), f"--nonce={nonce}"],
                            cwd=str(base), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://{HOST}:{args.port}"
    harness_errors: list[str] = []
    cells, foreign, dumps, call_log = {}, [], {}, {}
    network = {"calls": [], "denied": 0, "missing_attributes": []}
    # Edge/Playwright temp files must stay on S: even when the caller did not export TEMP/TMP.
    os.environ["TEMP"] = os.environ["TMP"] = str(base / "tmp")
    try:
        deadline = time.time() + 60
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.returncode} (see {base / 'server.log'})")
            try:
                if http(url + "/healthz", timeout=1).status == 200:
                    break
            except Exception:
                if time.time() > deadline:
                    raise TimeoutError("server health timeout")
                time.sleep(0.3)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            cells, foreign, dumps, call_log = run_matrix(
                pw, url, out, expectations, screens, args.settle_ms,
                lambda: network_state(url, nonce), args.startup_wait_ms, strict=args.strict_network)
        network = network_state(url, nonce)
    except Exception as e:
        harness_errors.append(f"{type(e).__name__}: {str(e)[:600]}")
        try:
            network = network_state(url, nonce)
        except Exception:
            pass
    finally:
        try:
            http(url + "/_smoke/shutdown", data=b"", headers={"X-Acceptance": nonce}, timeout=5)
            proc.wait(timeout=30)
        except Exception as e:
            harness_errors.append(f"graceful shutdown failed ({type(e).__name__}); taskkill on own pid {proc.pid}")
            kill_tree(proc.pid)
        if proc.poll() is None:
            kill_tree(proc.pid)
        log.close()
        t0 = time.time()
        while not port_free(args.port) and time.time() - t0 < 10:
            time.sleep(0.3)
        if not port_free(args.port):
            harness_errors.append("port not released")
    server_log = (base / "server.log").read_text(encoding="utf-8", errors="replace")
    server_traces = H.significant_tracebacks(server_log)
    net_failures = H.check_startup_network(call_log, foreign,
                                           expectations["global"].get("startup_allowed_calls", []))
    allowed_seen = H.allowed_calls_seen(call_log, expectations, screens)
    if server_traces:
        net_failures.append(f"サーバーログに Traceback {len(server_traces)} 件 (run/server.log)")
    results = {k: v["failures"] for k, v in cells.items()}
    verdict = H.overall_verdict(results, net_failures, harness_errors)
    summary = dict(verdict=verdict, out=str(out), port=args.port,
                   sizes=[list(s) for s in H.SIZES], themes=list(H.THEMES), screens=screens,
                   strict_network=args.strict_network, cells=cells,
                   network=dict(calls=network["calls"], denied=network["denied"], call_log=call_log,
                                fake_client_missing_attributes=network.get("missing_attributes", []),
                                foreign_browser_requests=foreign, failures=net_failures,
                                declared_on_open_calls_seen=allowed_seen),
                   harness_errors=harness_errors, testids=dumps)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    labels = {k: v["label"] for k, v in expectations["screens"].items()}
    print(H.format_table(results, screens, labels))
    print()
    for key, fails in results.items():
        for f in fails:
            print(f"[FAIL] {key}: {f}")
    for f in net_failures:
        print(f"[FAIL] network: {f}")
    for f in harness_errors:
        print(f"[ERROR] {f}")
    print(f"\n偽 live client 呼び出し: {len(network['calls'])} 件 / 外部接続試行(遮断): {network['denied']} 件 / "
          f"ブラウザ外部リクエスト: {len(foreign)} 件")
    if network.get("missing_attributes"):
        print("  偽 live client に無かった属性（製品が要求。呼び出し列が途中で切れた可能性）: "
              + ", ".join(network["missing_attributes"]))
    startup_calls = sum(len(v["calls"]) for k, v in call_log.items() if k == "startup" or k.startswith("load|"))
    print(f"起動・読込直後の呼び出し: {startup_calls} 件（0 必須）")
    for phase, entry in call_log.items():
        for call, site in zip(entry["calls"], entry.get("sites", [])):
            print(f"  呼び出し元 [{phase}] {call} <- {site}")
    for screen, names in allowed_seen.items():
        print(f"  宣言済みのタブ初回取得 {screen}: {names}"
              + ("" if not args.strict_network else "  (--strict-network: 失敗扱い)"))
    print(f"結果: {verdict}   出力: {out}")
    if args.dump_testids:
        for name, ids in dumps.items():
            print(f"--- {name}: {sorted(ids)}")
    return 0 if verdict == "PASS" else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--root", help=argparse.SUPPRESS)
    ap.add_argument("--nonce", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=22488)
    ap.add_argument("--out", type=Path, help="output folder (default: verify/ui_smoke_<timestamp>)")
    ap.add_argument("--only", nargs="*", help="screen keys to run (default: all in the expectations file)")
    ap.add_argument("--expectations", type=Path, default=H.EXPECTATIONS_PATH)
    ap.add_argument("--settle-ms", type=int, default=1500, help="wait after opening each screen")
    ap.add_argument("--startup-wait-ms", type=int, default=4000, help="idle wait after page load, before any tab click")
    ap.add_argument("--strict-network", action="store_true",
                    help="ignore allowed_calls in the expectations: any fake-client call on any screen fails")
    ap.add_argument("--dump-testids", action="store_true", help="print every data-testid seen per screen")
    args = ap.parse_args()
    if args.serve:
        return serve(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
