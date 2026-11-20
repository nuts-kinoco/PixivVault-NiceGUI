"""Capture the original Flet and port browser UIs with isolated, synthetic data."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import shell_measure, ui_smoke_browser as smoke, ui_smoke_helpers as helpers
from tools.original_run import capture_original as original

ORIGINAL_REPO = Path("S:/Projects/PixivVault")
ORIGINAL_PYTHON = Path("C:/Users/nat/miniconda3/envs/stenv/python.exe")
ORIGINAL_JAPANESE_FONT = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/NotoSansJP-VF.ttf"
PROFILES = ("normal", "empty", "long")
SCREENS = {
    "queue": "queue", "failed": "failed", "dedup": "dedup", "learning": "learning",
    "ranking": "ranking", "library": "library", "bookmarks": "library/マイブックマーク",
    "recommend": "recommend", "history": "history", "settings": "settings", "personal": "ranking/自分",
    "scheduler": None, "metadata": None, "download": "download",
}
SENTINEL = "P5合成データ"
LONG = "長文の表示と折り返しを検証する合成文字列" * 20
SYNTHETIC_ACCOUNT = "12"
LEARNING_FIXTURE_URL = "https://i.pximg.net/p5-synthetic/301_p0.png"
RANKING_FIXTURE_URL = "https://i.pximg.net/p5-synthetic/101_p0.png"
TAB_ICONS = {
    "download": "download", "queue": "queue", "failed": "report_problem", "dedup": "content_copy",
    "learning": "explore", "ranking": "leaderboard", "library": "photo_library", "recommend": "recommend",
}
DARK_TOKENS = {
    "bg-vault": "#0b0b10", "bg-surface": "#15161c", "bg-card": "#1e202d", "border-vault": "#2a2b38",
    "pixiv-blue": "#0096fa", "text-main": "#e8e8ef", "text-muted": "#8a8a99", "text-elsewhere": "#b9b9c4",
}
LIGHT_TOKENS = {
    "bg-vault": "#f3f5fb", "bg-surface": "#ffffff", "bg-card": "#eef1f8", "border-vault": "#c7cdd7",
    "pixiv-blue": "#0096fa", "text-main": "#191c20", "text-muted": "#4b5058", "text-elsewhere": "#3a3f47",
}


def selected_screens(only, *, include_download):
    if only:
        for key in only:
            if key not in SCREENS:
                raise ValueError(f"unknown screen: {key}")
            if key == "download" and not include_download:
                raise ValueError("download requires --include-download after integration")
        return only
    return [key for key in SCREENS if key != "download" or include_download]


def compose(left, right, output, size):
    with Image.open(left) as a, Image.open(right) as b:
        if a.size != size or b.size != size:
            raise ValueError(f"image sizes {a.size}, {b.size}; expected {size}")
        width, height = size
        combined = Image.new("RGB", (2 * width, height))
        combined.paste(a, (0, 0))
        combined.paste(b, (width, 0))
        combined.save(output)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def copy_original(revision, destination):
    paths = git(ORIGINAL_REPO, "ls-tree", "-r", "--name-only", "-z", revision).decode().split("\0")
    files = []
    for name in filter(None, paths):
        path = PurePosixPath(name)
        if (len(path.parts) == 1 and path.suffix == ".py" or
                len(path.parts) == 2 and path.parts[0] == "tools" and path.suffix == ".py" or
                name in {"assets/icon.ico", "assets/icon.png", "assets/icon.jpg"}):
            files.append(name)
    with zipfile.ZipFile(BytesIO(git(ORIGINAL_REPO, "archive", "--format=zip", revision, "--", *files))) as archive:
        archive.extractall(destination)
    for name in ("main.py", "pixiv_client.py"):
        patch = (ROOT / "tools/original_run" / f"patch_{name}.diff").read_bytes().splitlines()
        first_hunk = next(index for index, line in enumerate(patch) if line.startswith(b"@@"))
        headers = [f"diff --git a/{name} b/{name}", f"--- a/{name}", f"+++ b/{name}"]
        data = b"\n".join([*(line.encode() for line in headers), *patch[first_hunk:]]) + b"\n"
        subprocess.run(["git", "apply", "--no-index", "--whitespace=nowarn", "-"],
                       cwd=destination, input=data, check=True)


def download_authors(profile):
    if profile == "empty":
        return ()
    first = SENTINEL + (" " + LONG[:160] if profile == "long" else "")
    return (("2001", first, False), ("12", "synthetic author", True),
            ("2003", "synthetic author C", False), ("2004", "synthetic author D", False))


def seed_download_following(base, authors):
    from pixivvault_nicegui.following_sync import FollowingSyncStore

    class FollowingFixture:
        def require_identity(self):
            return SYNTHETIC_ACCOUNT

        def get_following_users_detailed(self, account_id, visibility):
            if visibility == "hide":
                return ()
            return tuple(dict(user_id=uid, name=name, account="", profile_img="")
                         for uid, name, _ in authors)

    store = FollowingSyncStore(base / "following")
    try:
        store.sync(FollowingFixture())
        for uid, _, starred in authors:
            if starred:
                store.set_favorite(SYNTHETIC_ACCOUNT, uid, True)
    finally:
        store.close()


def seed_profile(base, copy, profile):
    paths = smoke.seed_all(base)
    label = SENTINEL + (" " + LONG if profile == "long" else "")
    with sqlite3.connect(paths["db"]) as con:
        if profile == "empty":
            for table in ("works", "following_users", "failed_queue", "recommend_section_carryover"):
                con.execute(f"DELETE FROM {table}")
        else:
            con.execute("UPDATE works SET title=?, is_bookmarked=1", (label,))
            con.execute("UPDATE failed_queue SET title=?, error_reason=? || error_reason WHERE rowid=("
                        "SELECT rowid FROM failed_queue ORDER BY failed_at DESC LIMIT 1)",
                        (label, label + " "))
            con.execute(
                "INSERT INTO bookmark_remote_cache "
                "(work_id,is_novel,title,user_id,user_name,tags,create_date,rest_type,x_restrict) "
                "VALUES ('101',0,?,'12','synthetic author','[\"a\"]','2026-10-22','show',0)",
                (label,),
            )
            if profile == "long":
                rows = con.execute("SELECT section, payload_json FROM recommend_section_carryover").fetchall()
                for section, payload in rows:
                    groups = json.loads(payload)
                    for group in groups:
                        for item in group["works"]:
                            item["title"] = label
                    con.execute("UPDATE recommend_section_carryover SET payload_json=? WHERE section=?",
                                (json.dumps(groups), section))
    if profile == "empty":
        for image in paths["images"].rglob("*.png"):
            image.unlink()
    else:
        # Distinct files with closely matching image content for each real scanner.
        folder = paths["images"] / "dedup author(2001)"
        folder.mkdir()
        for work_id, colour in ((201, "#557ce3"), (202, "#567de4")):
            name = f"{label} {work_id}" if profile == "normal" else f"{label[:140]} {work_id}"
            Image.new("RGB", (96, 72), colour).save(folder / f"synthetic pair({work_id})_1.png")
            with sqlite3.connect(paths["db"]) as con:
                con.execute("INSERT INTO works "
                            "(work_id,title,page_count,tags,user_id,is_deleted,last_backup,content_type,work_meta) "
                            "VALUES (?,?,1,'[]','2001',0,'2026-10-21','illust','{}')",
                            (str(work_id), name))
    original_db = copy / "p5-synthetic.sqlite"
    shutil.copy2(paths["db"], original_db)
    shutil.copytree(paths["images"], copy / "Images")
    authors = download_authors(profile)
    seed_download_following(base, authors)
    for folder in (base, copy):
        write_json(folder / "fixture.json", {
            "profile": profile, "title": label, "account_id": SYNTHETIC_ACCOUNT,
            "download_authors": authors,
        })
    with sqlite3.connect(original_db) as con:
        con.execute("DELETE FROM following_users")
        con.executemany(
            "INSERT INTO following_users (user_id,name,follow_order,is_favorite) VALUES (?,?,?,?)",
            ((uid, name, index, starred) for index, (uid, name, starred) in enumerate(authors)),
        )
        con.executemany("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", [
            ("save_path", str(copy / "Images")), ("check_on_startup", "0"),
            ("meta_backfill_enabled", "0"), ("web_bridge_enabled", "0"),
        ])


class P5Fixture:
    """Supply the same synthetic inputs to the original and port views."""
    def __init__(self, folder):
        self.data = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
        bitmap = BytesIO()
        Image.new("RGB", (64, 48), "#965ce3").save(bitmap, "PNG")
        self.image = bitmap.getvalue()

    def require_identity(self):
        return SYNTHETIC_ACCOUNT

    def learning_candidates(self):
        if self.data["profile"] == "empty":
            return []
        return [dict(work_id="301", title=self.data["title"], author_id="2001",
                     author_name=self.data["download_authors"][0][1], tags=["a"], source="following",
                     thumbnail_url=LEARNING_FIXTURE_URL, description=self.data["title"])]

    def queue_rows(self):
        if self.data["profile"] == "empty":
            return ()
        return (dict(jobId="p5job1", kind="illustration", id="401", state="done",
                     author_name=self.data["title"][:220], author_id="2001", done=1, total=1, message=""),
                dict(jobId="p5job2", kind="illustration", id="402", state="error",
                     author_name="P5 synthetic failure", author_id="2001", done=0, total=2,
                     message="P5 synthetic failure"))

    def get_ranking(self, mode, content, page, date=None, lane=None):
        if self.data["profile"] == "empty" or page != 1:
            return []
        return [{
            "illust_id": 101, "user_id": 12, "title": self.data["title"], "user_name": "synthetic author",
            "tags": ["a"], "illust_type": {"illust": 0, "manga": 1, "ugoira": 2}.get(content, 0),
            "illust_page_count": 1, "rank": 1, "yes_rank": 2, "rating_count": 12, "view_count": 24,
            "ai_type": 1, "url": RANKING_FIXTURE_URL,
        }]

    def get_image_bytes(self, url):
        if url not in {RANKING_FIXTURE_URL, LEARNING_FIXTURE_URL}:
            raise ValueError("unexpected fixture image")
        return self.image

    def thumbnail_data_uri(self, url):
        return "data:image/png;base64," + base64.b64encode(self.get_image_bytes(url)).decode("ascii")


def serve_original(port):
    from tools.check_library_calendar_slider_owned import deny_external_connections

    # Keep app code on the archived copy's import path. No browser/profile, bridge,
    # or real Pixiv transport is involved in this session.
    sys.path.insert(0, os.getcwd())
    deny_external_connections()
    if os.environ.get("PV_NO_BROWSER_COOKIES") != "1":
        raise RuntimeError("original browser cookie extraction must be disabled")
    sys.argv = ["main.py", "--tab", "queue"]
    import discovery_engine
    import flet
    import main
    import server
    from pixiv_client import PixivClient

    server.start_server = lambda *_: None  # no URI/extension/web token service in a visual fixture
    ranking = P5Fixture(Path.cwd())
    server.list_web_jobs_snapshot = ranking.queue_rows
    discovery_engine.get_next_batch = lambda *, exclude_ids, **_: SimpleNamespace(
        items=[row for row in ranking.learning_candidates() if row["work_id"] not in exclude_ids],
        nothing_and_failed=False)
    original_detail = PixivClient.get_discovery_detail
    PixivClient.get_discovery_detail = lambda self, wid, *args, **kw: (
        {"image_url": LEARNING_FIXTURE_URL, "description": ranking.data["title"]} if str(wid) == "301"
        else original_detail(self, wid, *args, **kw))
    original_memory_image = PixivClient.download_thumbnail_to_memory
    PixivClient.download_thumbnail_to_memory = lambda self, url, *args, **kw: (
        ranking.get_image_bytes(url) if url == LEARNING_FIXTURE_URL
        else original_memory_image(self, url, *args, **kw))
    PixivClient.get_ranking = lambda _, *args, **kw: ranking.get_ranking(*args, **kw)
    original_thumbnail = PixivClient.download_thumbnail_as_data_uri
    PixivClient.download_thumbnail_as_data_uri = lambda self, url, *args, **kw: (
        ranking.thumbnail_data_uri(url) if url == RANKING_FIXTURE_URL
        else original_thumbnail(self, url, *args, **kw))
    run = flet.run
    flet.run = lambda target, **kw: run(target, **kw, host="127.0.0.1", no_cdn=True)
    main.main()


def serve_port(port, nonce):
    from pixivvault_nicegui import bootstrap
    from pixivvault_nicegui import services
    from pixivvault_nicegui.bookmark_cache_store import BookmarkCacheStore
    from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
    from pixivvault_nicegui.job_service import JobSnapshot
    from pixivvault_nicegui.ranking_source import RankingSource
    from pixivvault_nicegui.remote_media import RemoteBookmarkImages

    register_pages = bootstrap.register_pages
    get_services = services.get_services
    ranking = P5Fixture(Path.cwd())
    stopped_reader = smoke.make_stopped_reader
    record = RemoteBookmark("101", "illustration", "show", ranking.data["title"], "12",
                            "synthetic author", ("a",), RANKING_FIXTURE_URL, "2026-10-22", 1, 0, 0)
    records = () if ranking.data["profile"] == "empty" else (record,)

    def register_fixture_pages(**kw):
        register_pages(**kw, startup_tab="queue")

    def make_fixture_reader(base, fake):
        reader, scope = stopped_reader(base, fake)
        # The isolated reader already knows its synthetic account. The production
        # cache validation/restoration path still loads through its real store.
        reader._verified_identity = "12"
        prefs = base / "prefs"
        prefs.mkdir()
        store = BookmarkCacheStore(prefs)
        try:
            store.write("12", "2026-10-22T00:00:00+00:00", records, expected_revision=store.revision())
        finally:
            store.close()
        return reader, scope

    def get_fixture_services(*args, **kw):
        instance = get_services(*args, **kw)
        if getattr(instance, "_p5_visual_fixture", False):
            return instance
        instance._p5_visual_fixture = True
        instance.jobs.snapshot = lambda: tuple(
            JobSnapshot(row["jobId"], "pixiv-illust", row["id"], row["author_name"],
                        state=row["state"], done=row["done"], total=row["total"])
            for row in ranking.queue_rows())
        discovery = instance.discovery
        if discovery is not None:
            def seed_candidates():
                db = discovery._database()
                with db._transaction() as conn:
                    for candidate in ranking.learning_candidates():
                        conn.execute(
                            "INSERT OR IGNORE INTO managed_discovery_candidate VALUES (?,?)",
                            (candidate["work_id"], json.dumps(candidate, ensure_ascii=False)))
                        discovery._accept_metadata(candidate)

            discovery._submit(seed_candidates).result(timeout=10)
        # The archive reads saved counts from its isolated legacy DB. The port
        # reads managed receipts. Present the same synthetic count in both views.
        instance.saved_author_counts = lambda: {SYNTHETIC_ACCOUNT: len(records), "2001": 2 * len(records)}
        if instance.ranking_source is not None:
            instance.ranking_source = RankingSource(ranking)
            instance.acquire_ranking_images = lambda records: instance.images.acquire_catalog(
                RemoteBookmarkImages(ranking, tuple(records)))
        return instance

    services.get_services = get_fixture_services
    bootstrap.register_pages = register_fixture_pages
    smoke.make_stopped_reader = make_fixture_reader
    smoke.serve(SimpleNamespace(root=os.getcwd(), port=port, nonce=nonce))


def separate_environment(base):
    tmp = base / "tmp"
    tmp.mkdir()
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(TEMP=str(tmp), TMP=str(tmp), PYTHONUTF8="1", PYTHONUNBUFFERED="1",
               PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in ("src", "tests")))
    return env


@contextmanager
def running_app(kind, python, base, port, nonce=None):
    if not smoke.port_free(port):
        raise RuntimeError(f"port in use: {port}")
    env = separate_environment(base)
    env.update(NICEGUI_STORAGE_PATH=str(base / ".nicegui"))
    if kind == "original":
        # Do not inherit Flet's host/port or open the machine's default browser.
        env.update(PV_WEB_PORT=str(port), PV_LOCK_PORT=str(port + 1), PV_SERVER_PORT=str(port + 2),
                   PIXIVVAULT_DB_PATH=str(base / "p5-synthetic.sqlite"),
                   PV_NO_BROWSER_COOKIES="1", PV_NO_TRAY="1",
                   FLET_SERVER_PORT=str(port), FLET_SERVER_IP="127.0.0.1", FLET_DISPLAY_URL_PREFIX="P5",
                   FLET_WEB_NO_CDN="1")
        if not smoke.port_free(port + 1):
            raise RuntimeError(f"original lock port in use: {port + 1}")
    args = [str(python), "-B", str(Path(__file__).resolve()), f"--serve-{kind}", str(port)]
    if nonce:
        args.append(f"--nonce={nonce}")
    url = f"http://127.0.0.1:{port}"
    log_path = base.parent / (kind + ".log")
    with log_path.open("wb") as log:
        proc = subprocess.Popen(args, cwd=base, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            health = url + ("/healthz" if kind == "port" else "/")
            deadline = time.monotonic() + 60
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"{kind} exited {proc.returncode}. See {log_path}")
                try:
                    with smoke.http(health, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"{kind} did not start. See {log_path}")
                time.sleep(0.25)
            yield url
        finally:
            if nonce and proc.poll() is None:
                try:
                    with smoke.http(url + "/_smoke/shutdown", data=b"",
                                    headers={"X-Acceptance": nonce}, timeout=2):
                        pass
                    proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=10)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def navigate_port(page, key, specs):
    if key == "bookmarks":
        smoke.open_screen(page, "library", specs["library"], 0)
        page.get_by_test_id("library-subview-bookmarks").click(timeout=5000)
        page.get_by_test_id("bookmark-view").wait_for(state="visible", timeout=5000)
    else:
        opener = specs[key].get("open", f"tab-{key}")
        page.get_by_test_id(opener).click(timeout=5000)
        page.get_by_test_id(specs[key]["testids"][0]).first.wait_for(state="visible", timeout=5000)


DOM_AUDIT = r"""() => {
  const visible = e => e.checkVisibility({visibilityProperty:true, opacityProperty:true});
  const rect = r => [r.x,r.y,r.width,r.height].map(n => +n.toFixed(2));
  const text = [], clipped = [], ellipses = [], scroll = [], icons = [], styles = [];
  const name = e => e.dataset.testid || e.id || e.tagName.toLowerCase();
  const captureStyle = e => {
    const s = getComputedStyle(e);
    return {font:s.fontFamily, background:s.backgroundColor, color:s.color, radius:s.borderTopLeftRadius};
  };
  const boundary = (e, box) => {
    for (let p = e; p; p = p.parentElement) {
      const s = getComputedStyle(p), r = p.getBoundingClientRect();
      const x = s.overflowX, y = s.overflowY;
      const beyondX = box.left < r.left - 2 || box.right > r.right + 2;
      const beyondY = box.top < r.top - 2 || box.bottom > r.bottom + 2;
      if (beyondY && /auto|scroll/.test(y) && p.scrollHeight > p.clientHeight+1) return 'scroll';
      if (beyondX && /auto|scroll/.test(x) && p.scrollWidth > p.clientWidth+1) return 'scroll';
      if (beyondX && /hidden|clip/.test(x)) return s.textOverflow === 'ellipsis' ? 'ellipsis' : name(p);
      if (beyondY && /hidden|clip/.test(y)) return name(p);
    }
    if (box.right > innerWidth+2 || box.left < -2 || box.bottom > innerHeight+2 || box.top < -2) {
      return 'viewport';
    }
    return null;
  };
  for (const e of document.querySelectorAll('body *')) {
    if (!visible(e) || /^(SCRIPT|STYLE|NOSCRIPT|LINK|META)$/.test(e.tagName)) continue;
    const s = getComputedStyle(e);
    if (e.matches('button, [role=dialog], .pv-card, .q-card, .pv-dialog-title, .pv-failure-heading')) {
      styles.push({id:name(e), rect:rect(e.getBoundingClientRect()), ...captureStyle(e)});
    }
    if (e.matches('.q-icon')) {
      icons.push({glyph:e.textContent.trim(), font:s.fontFamily, rect:rect(e.getBoundingClientRect())});
      continue;
    }
    for (const n of e.childNodes) {
      if (n.nodeType !== Node.TEXT_NODE || !n.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (!r.width || !r.height) continue;
        const problem = boundary(e, r);
        const item = {id:name(e), text:n.textContent.trim().slice(0,160), rect:rect(r), font:s.fontFamily,
                      serif:!!e.closest('.pv-serif-heading, .pv-dialog-title, .pv-failure-heading, [data-testid=queue-log-heading] .pv-library-heading')};
        if (problem === 'scroll') scroll.push(item);
        else if (problem === 'ellipsis') ellipses.push(item);
        else if (problem) clipped.push({...item, by:problem});
        else text.push(item);
      }
    }
  }
  const style = getComputedStyle(document.body);
  const tokens = Object.fromEntries(['page','bg-vault','bg-surface','bg-card','border-vault','pixiv-blue',
    'text-main','text-muted','text-elsewhere'].map(key => [key, style.getPropertyValue('--pv-'+key).trim()]));
  return {width:[innerWidth, document.documentElement.scrollWidth], text, clipped, ellipses,
    scrolled_text_count:scroll.length, icons, styles, tokens, body_font:style.fontFamily};
}"""


def audit_port(page):
    data = page.evaluate(DOM_AUDIT)
    data["shell"] = page.evaluate(shell_measure.MEASURE_JS)
    data["display_contract"] = page.evaluate("""() => {
      const rect = selector => document.querySelector(selector)?.getBoundingClientRect();
      const search = rect('[data-testid=failed-search]');
      const category = rect('[data-testid=failed-category-all]');
      const empty = rect('[data-testid=queue-view] .pv-empty');
      const download = rect('[data-testid=unified-author-list]');
      const heading = rect('[data-testid=unified-thead]');
      const band = rect('[data-testid=unified-band]');
      const jobList = rect('[data-testid=queue-list]');
      const cards = [...document.querySelectorAll(
        '.pv-bookmarks .pv-card-title, [data-testid=recommend-view] .pv-card-title'
      )].filter(el => el.getClientRects().length);
      return {
        failure_categories_below_search: search?.height && category?.height ? search.bottom <= category.top : null,
        single_line_cards: cards.length ? cards.every(el => getComputedStyle(el).whiteSpace === 'nowrap'
          || getComputedStyle(el).webkitLineClamp === '1') : null,
        download_table_clear_of_band: band?.height && heading?.height
          ? heading.bottom <= band.top && download.height >= 34 : null,
        download_list_height: download?.height || 0,
        download_header_to_band: band && heading ? band.top - heading.bottom : null,
        queue_list_height: jobList?.height || 0,
        queue_empty_alignment: empty?.height
          ? getComputedStyle(document.querySelector('[data-testid=queue-view] .pv-empty')).textAlign : null,
      };
    }""")
    # CDP exposes the glyph families used by the browser, including Japanese fallbacks.
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        doc = cdp.send("DOM.getDocument")["root"]["nodeId"]
        data["rendered_fonts"] = {}
        for selector in (".pv-title", ".pv-tab .q-icon", ".pv-tab .q-btn__content span.block",
                         ".pv-dialog-title", ".pv-serif-heading", ".pv-failure-heading", "[data-testid=failed-reason]",
                         "[data-testid=queue-log-heading] .pv-library-heading"):
            nodes = cdp.send("DOM.querySelectorAll", {"nodeId": doc, "selector": selector})["nodeIds"]
            if nodes:
                data["rendered_fonts"][selector] = cdp.send("CSS.getPlatformFontsForNode", {"nodeId": nodes[0]})["fonts"]
    finally:
        cdp.detach()
    return data


def audit_original(page):
    outline = page.evaluate(original.OUTLINE_JS)
    # Flutter exposes accessibility bounds, while the actual glyphs are on a canvas.
    # Offscreen semantics descendants may be intentionally reachable by scrolling.
    return {"semantics": outline,
            "outside_viewport": [e for e in outline if e["text"] and
                                 (e["rect"][0] < -2 or e["rect"][1] < -2 or
                                  e["rect"][0]+e["rect"][2] > page.viewport_size["width"]+2 or
                                  e["rect"][1]+e["rect"][3] > page.viewport_size["height"]+2)]}


def new_page(browser, url, size, theme, foreign, japanese_font):
    scope = urlsplit(url).netloc
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  color_scheme=theme, device_scale_factor=1, service_workers="block")

    def route(request):
        target = urlsplit(request.request.url)
        if target.scheme in ("http", "https") and target.netloc != scope:
            foreign.add(target.netloc + target.path)
            request.abort()
        elif japanese_font and target.path.startswith("/assets/fonts/notosansjp/"):
            request.fulfill(status=200, content_type="font/ttf", body=japanese_font)
        else:
            request.continue_()

    def route_ws(websocket):
        if urlsplit(websocket.url).netloc == scope:
            websocket.connect_to_server()
        else:
            foreign.add(urlsplit(websocket.url).hostname)
            websocket.close()

    context.route("**/*", route)
    context.route_web_socket("**/*", route_ws)
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return page


def capture_view(page, target, kind, path, specs, profile):
    if kind == "original":
        original.open_screen(page, target)
    else:
        navigate_port(page, target, specs)
    if target == "dedup" and profile != "empty":
        if kind == "original":
            original.click_label(page, "スキャンして更新")
            page.wait_for_function(
                f"() => ({original.OUTLINE_JS})().some(e => e.text.startsWith('\u5b8c\u4e86:'))",
                timeout=8000)
            choices = page.evaluate(original.OUTLINE_JS)
            label = "近似重複の候補"
            boxes = [row["rect"] for row in choices
                     if row["text"].startswith(label) and 0 < row["rect"][2] <= 190]
            if not boxes:
                raise ValueError("original near-duplicate filter unavailable after scan")
            x, y, w, h = boxes[0]
            page.mouse.click(x + w / 2, y + h / 2)
        else:
            page.get_by_test_id("duplicate-scan").click(timeout=5000)
            page.wait_for_function(
                "() => document.querySelector('[data-testid=duplicate-status]')"
                "?.textContent.includes('\u679a\u3092\u78ba\u8a8d')",
                timeout=8000)
            page.get_by_test_id("duplicate-mode-near").click(timeout=5000)
    page.mouse.move(0, 0)
    page.wait_for_timeout(1400 if target in {"ranking", "learning"} else 500)
    page.evaluate("document.fonts.ready")
    audit = audit_original(page) if kind == "original" else audit_port(page)
    page.screenshot(path=path, timeout=10000)
    audit["background_pixel"] = shell_measure.pixel(path, 4, 4)
    if kind == "port":
        for tab in audit["shell"]["tabs"]:
            if tab["active"] and target != "settings":
                audit["active_tab_pixel"] = shell_measure.pixel(path, int(tab["x"]+tab["w"]/2), int(tab["y"]+2))
                break
    write_json(path.with_suffix(".json"), audit)
    return audit


def mechanics(audits, theme, key):
    port = audits.get("port")
    if not port:
        return []
    flags = []
    expected_tokens = DARK_TOKENS if theme == "dark" else LIGHT_TOKENS
    for name, value in expected_tokens.items():
        if port["tokens"].get(name, "").lower() != value:
            flags.append(f"--pv-{name}")
    if port["width"][1] - port["width"][0] > 1:
        flags.append("document overflow")
    contract = port["display_contract"]
    if key == "failed" and contract["failure_categories_below_search"] is False:
        flags.append("failure search and categories share a row")
    if key in {"bookmarks", "recommend"} and contract["single_line_cards"] is False:
        flags.append("card title exceeds original one-line limit")
    if key == "queue" and contract["queue_list_height"] < 36:
        flags.append("queue list shorter than one row")
    if key == "queue" and contract["queue_empty_alignment"] not in {None, "start", "left"}:
        flags.append("queue empty message not left-aligned")
    if key == "download" and contract["download_table_clear_of_band"] is False:
        flags.append("download table hidden or overlapping the progress band")
    if port["clipped"]:
        flags.append("clipped text")
    for tab in port["shell"]["tabs"]:
        if tab["key"] in TAB_ICONS and tab["icon"] != TAB_ICONS[tab["key"]]:
            flags.append(f"tab icon {tab['key']}")
    for style in port["styles"]:
        if style["id"].startswith("failed-category-") and style["radius"] != "8px":
            flags.append(f"filter radius {style['id']}")
    if any(("Yu Mincho" not in e["font"] if e.get("serif") else "Noto Sans JP" not in e["font"]) and not (
        key == "download" and e["font"] == "Consolas, monospace"
        and re.fullmatch(r"[\d /,\u4eba\u2014\uff0d:-]+", e["text"])
    ) for e in port["text"]):
        flags.append("text font")
    for selector, fonts in port["rendered_fonts"].items():
        if not fonts or selector == ".pv-tab .q-icon":
            continue
        serif_expected = selector in {".pv-dialog-title", ".pv-serif-heading", ".pv-failure-heading",
                                      "[data-testid=queue-log-heading] .pv-library-heading"}
        has_serif = any("Mincho" in font["familyName"] for font in fonts)
        if has_serif != serif_expected:
            flags.append(f"rendered font mismatch {selector}")
    if key != "settings":
        for kind, audit in audits.items():
            if audit["background_pixel"] != shell_measure.ORIG["bg"][theme]:
                flags.append(f"page pixel {kind}")
        if port.get("active_tab_pixel") not in {None, shell_measure.ORIG["selected_tab_bg"][theme]}:
            flags.append("selected tab pixel")
    return sorted(set(flags))


def tour(browser, urls, output, specs, size, theme, screens, foreign, japanese_font, profile):
    from playwright.sync_api import Error

    pages = {}
    startup_errors = {}
    try:
        for kind, url in urls.items():
            try:
                page = new_page(browser, url, size, theme, foreign[kind], japanese_font if kind == "original" else None)
                pages[kind] = page
                if kind == "original":
                    original.enable_semantics(page)
                    original.set_theme(page, theme)
                else:
                    page.get_by_test_id("tab-library").wait_for(timeout=20000)
                    if not smoke.set_theme(page, theme):
                        raise ValueError("theme switch failed")
            except (Error, ValueError) as exc:
                startup_errors[kind] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"

        result = {}
        for key in screens:
            folder = output / key
            folder.mkdir()
            cell = result[key] = {"errors": {}}
            audits = {}
            for kind, target in (("original", SCREENS[key]), ("port", key)):
                if target is None:
                    cell[kind] = None
                    continue
                if kind in startup_errors:
                    cell["errors"][kind] = startup_errors[kind]
                    continue
                page = pages[kind]
                try:
                    audit = capture_view(page, target, kind, folder / f"{kind}.png", specs, profile)
                    audits[kind] = audit
                    rows = audit["semantics"] if kind == "original" else audit["text"]
                    shown = "\n".join(e["text"] for e in rows)
                    displayed = shown
                    if kind == "port":
                        displayed += "\n" + "\n".join(e["text"] for e in audit["ellipses"])
                    cell[kind] = {
                        "image": f"{key}/{kind}.png", "audit": f"{key}/{kind}.json",
                        "background_pixel": audit["background_pixel"],
                        "fixture_text_visible": SENTINEL in shown,
                        "long_text_visible": LONG[:20] in shown,
                        "fixture_text_in_visible_or_ellipsized_element": SENTINEL in displayed,
                    }
                    if kind == "port":
                        cell[kind].update(clipped=len(audit["clipped"]), ellipses=len(audit["ellipses"]),
                                          width=audit["width"], tokens=audit["tokens"],
                                          active_tab_pixel=audit.get("active_tab_pixel"),
                                          text_fonts=sorted({e["font"] for e in audit["text"]}))
                except (Error, ValueError) as exc:
                    cell["errors"][kind] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                    if kind == "original":
                        write_json(folder / "original-incomplete.json", audit_original(page))
                finally:
                    if key == "settings":
                        # Close via the UI for subsequent tab navigation in this session.
                        if kind == "port":
                            close = page.get_by_test_id("preferences-close-top")
                            if close.is_visible():
                                close.click(timeout=3000)
                        else:
                            page.mouse.click(5, 5)
                        page.wait_for_timeout(250)
            if cell.get("original") and cell.get("port"):
                compose(folder / "original.png", folder / "port.png", folder / "paired.png", size)
                cell["paired"] = f"{key}/paired.png"
            cell["mechanical_flags"] = mechanics(audits, theme, key)
        return result
    finally:
        for page in pages.values():
            page.context.close()


def capture(args):
    from playwright.sync_api import sync_playwright
    import hashlib

    screens = selected_screens(args.only, include_download=args.include_download)
    japanese_font = ORIGINAL_JAPANESE_FONT.read_bytes()
    specs = helpers.load_expectations()["screens"]
    revision = git(ORIGINAL_REPO, "rev-parse", "HEAD").decode().strip()
    port_revision = git(ROOT, "rev-parse", "HEAD").decode().strip()
    out = smoke.VERIFY_ROOT / datetime.now().strftime("p5_design_%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True)
    temp = out / "tmp"
    temp.mkdir()
    os.environ.update(TEMP=str(temp), TMP=str(temp), PYTHONDONTWRITEBYTECODE="1")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(temp / "browsers")
    profiles = args.profile or PROFILES
    foreign = {"original": set(), "port": set()}
    summary = {"original_revision": revision, "port_revision": port_revision,
               "port_dirty": bool(git(ROOT, "status", "--porcelain")), "screens": screens,
               "profiles": profiles, "matrix": {}, "network": {},
               "original_local_font": {"path": str(ORIGINAL_JAPANESE_FONT),
                                       "sha256": hashlib.sha256(japanese_font).hexdigest()}}
    print(out, flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True,
            args=["--disable-background-networking", "--no-proxy-server",
                  "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"])
        try:
            for profile in profiles:
                base = out / profile
                source = base / "original_copy"
                product = base / "port_data"
                source.mkdir(parents=True)
                copy_original(revision, source)
                seed_profile(product, source, profile)
                nonce = secrets.token_urlsafe(24)
                with running_app("original", args.original_python, source, args.original_port) as old_url:
                    with running_app("port", sys.executable, product, args.port, nonce) as new_url:
                        urls = {"original": old_url, "port": new_url}
                        for size in helpers.SIZES:
                            for theme in helpers.THEMES:
                                key = f"{profile}/{size[0]}x{size[1]}_{theme}"
                                folder = out / key
                                folder.mkdir()
                                cells = tour(browser, urls, folder, specs, size, theme, screens, foreign, japanese_font, profile)
                                summary["matrix"][key] = cells
                                summary["foreign_blocked"] = {k: sorted(v) for k, v in foreign.items()}
                                write_json(out / "summary.json", summary)
                                print(f"{key}: {sum('paired' in v for v in cells.values())} pairs, "
                                      f"{sum(bool(v['errors']) for v in cells.values())} incomplete", flush=True)
                        network = smoke.network_state(new_url, nonce)
                        summary["network"][profile] = network
        finally:
            browser.close()
            write_json(out / "summary.json", summary)
    return int(any(cell["errors"] for row in summary["matrix"].values() for cell in row.values()) or
               any(net["denied"] or net["calls"] for net in summary["network"].values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--include-download", action="store_true")
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument("--original-port", type=int, default=22610, choices=range(22610, 22618))
    parser.add_argument("--port", type=int, default=22620, choices=range(22620, 22630))
    parser.add_argument("--original-python", type=Path, default=ORIGINAL_PYTHON)
    parser.add_argument("--serve-original", type=int, choices=range(22610, 22618), help=argparse.SUPPRESS)
    parser.add_argument("--serve-port", type=int, choices=range(22620, 22630), help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_original or args.serve_port:
        if not (Path.cwd().resolve().is_relative_to(smoke.VERIFY_ROOT.resolve())
                and (Path.cwd() / "fixture.json").is_file()):
            parser.error("server entry points require an isolated P5 run folder")
    if args.serve_original:
        serve_original(args.serve_original)
    elif args.serve_port:
        serve_port(args.serve_port, args.nonce)
    else:
        return capture(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
