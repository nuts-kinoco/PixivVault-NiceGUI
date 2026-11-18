"""WU-7: capture the restored download screen with isolated data and compare it with the original.

Starts the product's own register_pages()/ui.run() on 127.0.0.1:<port> (default 22489) with an isolated
data root, a network-free fake live client and a seeded following ledger (20 authors, some with a star,
some stamped with a last-downloaded date; the 保存済み counts are synthetic numbers patched into
`saved_author_counts` of this process only), drives it with Playwright (Edge) and writes
  <out>/comparison.md      geometry (x/y/w/h relative to the left card) and colours vs the original capture
  <out>/*.png              our captures + original|ours side-by-side images
The original captures (verify/original_20261117_225445) only exist for an empty list; rows, chips and the run
state are checked against the original code's constants (docs in comparison.md).

Run: PYTHONPATH=<wt>/src PYTHONUTF8=1 <venv>/python.exe -B tools/download_screen_capture.py [--out DIR] [--port N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ui_smoke_browser as S

ORIGINAL = Path("S:/Projects/PV-DEV-20261117/verify/original_20261117_225445")
SIZES = ((1280, 800), (1920, 1080))
THEMES = ("dark", "light")
NAMES = ["青葉", "朝霧", "石蕗", "海老原", "小野寺", "霞", "雲雀", "桜庭", "汐見", "鈴木", "月城", "冬馬", "夏目",
         "野々宮", "葉月", "緋色", "藤堂", "真白", "水無月", "夕凪"]
FAVOURITES = {1, 4, 9, 14}
STAMPED = {0: "2026-11-17T21:34:08+00:00", 2: "2026-11-16T09:02:41+00:00", 5: "2026-11-10T00:15:00+00:00"}
COUNTS = {0: 128, 1: 7, 2: 1520, 3: 0, 4: 36, 5: 912, 6: 3, 7: 45, 8: 210}


# --------------------------------------------------------------------------- server (isolated)
def serve(args):
    from tools.check_library_calendar_slider_owned import deny_external_connections
    deny_external_connections()
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.following_sync import FollowingSyncStore
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.services import ApplicationServices

    base = Path(args.root)
    fake = S.FakeClient()
    store = NativeProfileStore(base / "profile")
    profile = NativeProfileBinding(store, launch_snapshot=store.snapshot())
    profile.auth_stop_required = True

    ids = [str(2001 + i) for i in range(args.authors)]
    follow = FollowingSyncStore(base / "following")

    class Reader:
        def require_identity(self):
            return "99"

        def get_following_users_detailed(self, uid, rest):
            if rest == "hide":
                return ()
            return tuple(dict(user_id=u, name=NAMES[i % len(NAMES)] + ("" if i < len(NAMES) else str(i)),
                              account="", profile_img="") for i, u in enumerate(ids))
    follow.sync(Reader())
    for i in FAVOURITES:
        follow.set_favorite("99", ids[i], True)
    for i, stamp in STAMPED.items():
        follow.record_downloaded("99", ids[i], stamp)
    follow.close()
    counts = {ids[i]: n for i, n in COUNTS.items()}
    ApplicationServices.saved_author_counts = lambda self: dict(counts)   # synthetic numbers, this process only

    kw = dict(live_client=fake, native_profile=profile,
              library_readonly_db=base / "lib" / "synthetic-legacy.sqlite",
              library_image_root=base / "lib" / "fake-images",
              pixiv_download_root=base / "managed", preferences_root=base / "prefs",
              recommendation_preferences_root=base / "prefs-reco", discovery_root=base / "discovery",
              external_vault_root=base / "vault", following_sync_root=base / "following",
              image_cache_root=base / "cache", save_export_root=base / "exports", bookmark_account_id="99")
    for v in kw.values():
        if isinstance(v, Path) and not v.resolve().is_relative_to(base.resolve()):
            raise SystemExit("path outside isolated run folder")
    register_pages(**kw)
    stopping = False

    @app.get("/_cap/network", include_in_schema=False)
    def net():
        return {"calls": list(fake.calls)}

    @app.post("/_cap/shutdown", include_in_schema=False)
    def shutdown():
        nonlocal stopping
        stopping = True
        app.shutdown()
        return {"stopping": True}

    ui.run(host=S.HOST, port=args.port, show=False, reload=False, native=False, binding_refresh_interval=None)


# --------------------------------------------------------------------------- measuring
MEASURE_JS = """() => {
  const q = s => document.querySelector(s);
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
  const text = el => { if (!el) return null; const g = document.createRange(); g.selectNodeContents(el);
    const r = g.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
  const tid = id => q(`[data-testid=${id}]`);
  const lbl = (parent, s) => q(`${parent} ${s}`);
  const heads = [...document.querySelectorAll('[data-testid=unified-thead] > div')];
  const nth = (sel, i) => document.querySelectorAll(sel)[i] || null;
  return {
    left_card: box(tid('unified-left')), target_card: box(tid('unified-target-card')),
    fav_card: box(tid('unified-favorite-card')), band: box(tid('unified-band')),
    title: text(q('.pv-ud-title')), sub: text(q('.pv-ud-header .pv-ud-sub')),
    mode_followed: box(tid('unified-mode-followed')), mode_bookmark: box(tid('unified-mode-bookmark')),
    search: box(q('.pv-ud-search .q-field__control')),
    select_all: box(tid('unified-select-all')), deselect_all: box(tid('unified-deselect-all')),
    star_only: box(tid('unified-star-only')), sort_asc: box(tid('unified-sort-asc')),
    sort_desc: box(tid('unified-sort-desc')), sort: text(tid('unified-sort')),
    count_text: text(tid('unified-count')),
    target_title: text(q('.pv-ud-cardhead .a')), target_count: text(tid('unified-target-count')),
    target_empty: text(tid('unified-targets-empty')),
    header_check: box(tid('unified-header-check')),
    th_name: text(heads[2]), th_id: text(heads[3]), th_saved: text(heads[4]), th_last: text(heads[5]),
    fav_label: text(q('.pv-ud-fav .lab')), fav_count: text(tid('unified-favorite-count')),
    fav_button: box(tid('unified-favorite-add')),
    type: box(tid('unified-type')), run: box(tid('unified-run')), pause: box(tid('unified-pause')),
    stop: box(tid('unified-stop')),
    band_status: text(tid('unified-band-status')), band_hint: box(q('.pv-ud-band-hint')),
    row0: box(nth('[data-testid=unified-author-row]', 0)),
    row0_check: box(nth('[data-testid=unified-row-check]', 0)), row0_star: box(nth('[data-testid=unified-row-star]', 0)),
    row0_name: text(nth('.pv-ud-row .pv-ud-c-name', 0)), row0_id: text(nth('.pv-ud-row .pv-ud-c-id', 0)),
    row0_count: text(nth('.pv-ud-row .pv-ud-c-count', 0)), row0_last: text(nth('.pv-ud-row .pv-ud-c-last', 0)),
    row1: box(nth('[data-testid=unified-author-row]', 1)),
    chip0: box(nth('[data-testid=unified-chip]', 0)), chip1: box(nth('[data-testid=unified-chip]', 1)),
    rows: document.querySelectorAll('[data-testid=unified-author-row]').length,
    scroll: [Math.max(document.documentElement.scrollWidth, document.body.scrollWidth), document.documentElement.clientWidth],
  };
}"""

# our key -> (name in the original *_download.txt, which occurrence)
ORIG_MAP = {
    "title": ("ダウンロード", 1), "sub": ("作者を選ぶか、IDを入れて実行します", 0),
    "mode_followed": ("フォロー中の作者", 0), "mode_bookmark": ("マイブックマーク", 0),
    "select_all": ("表示中を全部選ぶ", 0), "deselect_all": ("選択を全部外す", 0), "star_only": ("☆のみ", 0),
    "sort_asc": ("昇順", 0), "sort_desc": ("降順", 0), "sort": ("Show menu フォロー登録順", 0),
    "count_text": ("0 / 0 人", 0), "target_title": ("この回の対象", 0), "target_count": ("0 人", 0),
    "target_empty": ("左の一覧でチェックすると、ここに積まれます", 1),
    "th_name": ("作者名", 0), "th_id": ("ID", 0), "th_saved": ("保存済み", 0), "th_last": ("最後に取得した日", 0),
    "fav_label": ("自動監視", 0), "fav_count": ("0 人", 1), "fav_button": ("☆を対象にする", 0),
    "type": ("イラスト・漫画+小説（両方）", 0), "run": ("左の一覧で作者を選ぶと押せます", 0), "pause": ("一時停止", 0),
    "stop": ("停止", 0), "band_status": ("待機中", 0),
}
# elements that sit at the bottom of their column: compared with the left card's bottom edge as origin,
# because the shell above differs (banner / two-row tabs) and the body height with it
BOTTOM = {"fav_label", "fav_count", "fav_button", "type", "run", "pause", "stop", "band_status", "fav_card", "band"}
RIGHT_ALIGNED = {"th_saved", "th_last"}          # compared by their right edge
FONT_X = {"sub", "select_all", "deselect_all", "star_only", "sort", "th_saved", "th_last", "fav_button",
          "count_text", "target_title", "target_count", "target_empty", "title", "th_name", "th_id", "fav_label",
          "fav_count", "band_status"}
# fixed numbers read from the original images (left card, right card, favourite card, band) at 1280x800
ORIG_BOXES = {"left_card": (17, 375, 776, 313), "target_card": (803, 375, 460, 199),
              "fav_card": (803, 586, 460, 46), "band": (17, 696, 1246, 84),
              "search": (34, 444, 300, 42)}
ORIG_BOXES_1920 = {"left_card": (337, 375, 776, 593), "target_card": (1123, 375, 460, 479),
                   "fav_card": (1123, 866, 460, 46), "band": (337, 976, 1246, 84), "search": (354, 444, 300, 42)}
# the original code's row constants (unified_download_tab.py _render_authors): row 34px, padding 8, gap 8,
# check 16, star 22, name 210, id 96, count 86 (right), last-downloaded expands
ROW_EXPECT = {"row0": ("h", 34), "row0_check": ("wh", (16, 16)), "row0_star": ("wh", (22, 22)),
              "row0_name": ("w<=", 210), "row0_id": ("w<=", 96), "chip0": ("h", 44), "chip1": ("h", 44)}


def parse_orig(name, size, theme):
    rows = {}
    for line in (ORIGINAL / f"{size[0]}x{size[1]}_{theme}_download.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) >= 6:
            try:
                rect = tuple(int(v) for v in parts[-4:])
            except ValueError:
                continue
            rows.setdefault(parts[1], []).append(rect)
    return rows


def sample_colours(png: Path, origin, points, height):
    from PIL import Image
    im = Image.open(png).convert("RGB")
    ox, oy = origin
    out = {}
    for key, (anchor, dx, dy) in points.items():
        x, y = int(ox + dx), int(oy + dy + (height if anchor == "b" else 0))
        out[key] = im.getpixel((x, y)) if 0 <= x < im.width and 0 <= y < im.height else None
    return out


# points relative to the left card's top-left corner (ours and original alike)
COLOUR_POINTS = {"left card face": ("t", 8, 150), "mode selected": ("t", 30, 35), "mode unselected": ("t", 500, 35),
                 "right card face": ("t", 30 + 786, 100), "run button (disabled)": ("b", 60 + 786 + 8, -20),
                 "type button": ("b", 3 + 786, -20), "pause button": ("b", 360 + 786 + 8, -20),
                 "page background": ("t", -8, 150)}


def parity(ours, orig, key, tol):
    if ours is None or orig is None:
        return None
    return [round(a - b, 1) for a, b in zip(ours, orig)]


# --------------------------------------------------------------------------- driver
def drive(args, out: Path, url):
    from playwright.sync_api import sync_playwright
    from PIL import Image
    report = []
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            for size in SIZES:
                for theme in THEMES:
                    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=theme)
                    foreign = []
                    ctx.route("**/*", lambda r: (foreign.append(r.request.url[:80]), r.abort())
                              if r.request.url.startswith("http") and not r.request.url.startswith(url) else r.continue_())
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
                    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
                    page.goto(url, wait_until="domcontentloaded")
                    page.get_by_test_id("unified-download-view").wait_for(state="attached", timeout=30000)
                    S.set_theme(page, theme)
                    page.wait_for_selector("[data-testid=unified-author-row]", timeout=15000)
                    page.wait_for_timeout(2500)
                    tag = f"{size[0]}x{size[1]}_{theme}"
                    page.screenshot(path=str(out / f"ours_{tag}_idle.png"))
                    results[(size, theme, "idle")] = page.evaluate(MEASURE_JS)
                    results[(size, theme, "errors")] = list(errors)
                    # pick three authors from the list (a click on a row), look at chips
                    rows = page.locator("[data-testid=unified-author-row]")
                    for i in (0, 2, 3):
                        rows.nth(i).click()
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(out / f"ours_{tag}_selected.png"))
                    results[(size, theme, "selected")] = page.evaluate(MEASURE_JS)
                    if size == SIZES[0] and theme == "dark":
                        # sort menu open + the pager state with a second page (after adding authors elsewhere is not
                        # needed: 20 authors = one page; the menu is what we want to see)
                        page.get_by_test_id("unified-sort").click()
                        page.wait_for_timeout(300)
                        page.screenshot(path=str(out / f"ours_{tag}_sortmenu.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                        page.get_by_test_id("unified-details-open").click()
                        page.wait_for_timeout(1200)
                        page.screenshot(path=str(out / f"ours_{tag}_details.png"))
                        results[(size, theme, "details_testids")] = sorted(set(page.evaluate(
                            "[...document.querySelectorAll('[data-testid^=managed-download-]')]"
                            ".filter(e => e.getClientRects().length).map(e => e.getAttribute('data-testid'))")))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(500)
                    # run: the fake client lists nothing, so the engine finishes each author at once
                    page.get_by_test_id("unified-run").click()
                    deadline = time.time() + 40
                    while time.time() < deadline:
                        if page.get_by_test_id("unified-band-status").inner_text().strip() in ("完了しました。", "失敗しました。") \
                                or page.get_by_test_id("unified-band-status").inner_text().startswith("失敗"):
                            break
                        page.wait_for_timeout(300)
                    page.wait_for_timeout(1500)
                    page.screenshot(path=str(out / f"ours_{tag}_run_end.png"))
                    results[(size, theme, "run_end")] = page.evaluate(MEASURE_JS)
                    results[(size, theme, "run_end_status")] = page.get_by_test_id("unified-band-status").inner_text()
                    results[(size, theme, "run_end_last")] = page.evaluate(
                        "[...document.querySelectorAll('[data-testid=unified-row-last]')].slice(0,6).map(e=>e.textContent)")
                    results[(size, theme, "foreign")] = list(foreign)
                    ctx.close()
        finally:
            browser.close()
    return results


def table(rows):
    out = ["| 項目 | 元 (x,y,w,h) | 移植版 (x,y,w,h) | 差 | 判定 |", "|---|---|---|---|---|"]
    out.extend(rows)
    return "\n".join(out)


def judge(delta, key=None, tol_pos=2, tol_size=3):
    """一致 / フォント幅差（縦が合い、横幅だけ文字の幅で変わる）/ 残差。"""
    if delta is None:
        return "-"
    if key in RIGHT_ALIGNED:
        delta = [delta[0] + delta[2], delta[1], 0, delta[3]]
    exact = (abs(delta[0]) <= tol_pos and abs(delta[1]) <= tol_pos and abs(delta[2]) <= tol_size
             and abs(delta[3]) <= tol_size)
    if exact:
        return "一致"
    if key in FONT_X and abs(delta[1]) <= tol_pos and abs(delta[3]) <= tol_size + 3:
        return "フォント幅差"
    return "残差"


def fmt(rect):
    return "-" if rect is None else "(" + ", ".join(f"{v:.0f}" for v in rect) + ")"


def build_report(out: Path, results):
    from PIL import Image
    lines = ["# ダウンロード画面の並置比較（WU-7）", "",
             f"- 日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             "- 元: `verify/original_20261117_225445/*_download.{png,txt}`（作者 0 人の状態のみ）。移植版: 隔離データ（作者 20 人・☆ 4 人・取得日あり 3 人・保存済み件数は試験用の合成値）。",
             "- 位置は左カラム（モード切替のあるカード）の左上を原点にした相対値。共通シェル（DB バナー・上部の 2 段タブ）の違いで画面全体の y は元とずれるため、カード自体の絶対位置は別表にした。",
             "- テキストは文字の外接矩形、ボタン・カードは要素の矩形。文字の幅はフォント（元: Flet の Roboto 系 / 移植版: Edge の sans）で数 px 変わるので、幅の差は許容 3px 超でも文字なら参考値。",
             "- 本人の裁定による意図した差: ライトテーマのカードは明るい面（元は暗色のまま）。フォントは sans（見出しも明朝にしない）。これらは「残差」に数えない。", ""]
    total_res = 0
    total_cmp = 0
    for size in SIZES:
        for theme in THEMES:
            tag = f"{size[0]}x{size[1]}_{theme}"
            m = results[(size, theme, "idle")]
            orig = parse_orig(None, size, theme)
            lc = m["left_card"]
            ocard = ORIG_BOXES if size == SIZES[0] else ORIG_BOXES_1920
            ox, oy = ocard["left_card"][:2]
            lines += [f"## {size[0]}x{size[1]} / {theme}", ""]
            lines += ["### カードの絶対位置（共通シェルの違いを含む）", ""]
            rows = []
            for key in ("left_card", "target_card", "fav_card", "band"):
                d = parity(m[key], ocard[key], key, 2)
                same_x_w = abs(d[0]) <= 2 and abs(d[2]) <= 2
                rows.append(f"| {key} | {fmt(ocard[key])} | {fmt(m[key])} | {fmt(d)} | "
                            f"{'x・幅は一致 / y・高さはシェル差' if same_x_w else '残差'} |")
                total_cmp += 1
                total_res += not same_x_w
            lines += [table(rows), ""]
            lines += ["### 左カラム原点からの相対位置（要素ごと）", ""]
            rows = []
            for key, (name, nth) in ORIG_MAP.items():
                cands = orig.get(name) or []
                if nth >= len(cands):
                    # the "0 人" texts can be named differently in the txt; skip when absent
                    continue
                orect = cands[nth]
                ours = m.get(key)
                if ours is None:
                    rows.append(f"| {key} | {fmt(orect)} | 無し | - | 残差 |")
                    total_res += 1
                    total_cmp += 1
                    continue
                if key in BOTTOM:
                    # relative to the left card's bottom edge (negative = above it)
                    o_rel = (orect[0] - ox, orect[1] - (oy + ocard["left_card"][3]), orect[2], orect[3])
                    n_rel = (ours[0] - lc[0], ours[1] - (lc[1] + lc[3]), ours[2], ours[3])
                else:
                    o_rel = (orect[0] - ox, orect[1] - oy, orect[2], orect[3])
                    n_rel = (ours[0] - lc[0], ours[1] - lc[1], ours[2], ours[3])
                d = [round(a - b, 1) for a, b in zip(n_rel, o_rel)]
                verdict = judge(d, key)
                total_cmp += 1
                total_res += verdict == "残差"
                rows.append(f"| {key}{'（下端基準）' if key in BOTTOM else ''} | {fmt(o_rel)} | {fmt(n_rel)} | {fmt(d)} | {verdict} |")
            for key in ("search",):
                o = ocard[key]
                o_rel = (o[0] - ox, o[1] - oy, o[2], o[3])
                n = m[key]
                n_rel = (n[0] - lc[0], n[1] - lc[1], n[2], n[3])
                d = [round(a - b, 1) for a, b in zip(n_rel, o_rel)]
                verdict = judge(d, "search")
                total_cmp += 1
                total_res += verdict == "残差"
                rows.append(f"| search（入力欄） | {fmt(o_rel)} | {fmt(n_rel)} | {fmt(d)} | {verdict} |")
            lines += [table(rows), ""]
            # the data-dependent elements are checked against the original code's constants
            sel = results[(size, theme, "selected")]
            lines += ["### データありの要素（元コードの定数と比較）", "", "| 項目 | 元コードの値 | 移植版 | 判定 |", "|---|---|---|---|"]
            for key, (kind, want) in ROW_EXPECT.items():
                src = sel if key.startswith("chip") else m
                v = src.get(key)
                if v is None:
                    lines.append(f"| {key} | {want} | 無し | 残差 |")
                    total_res += 1
                    total_cmp += 1
                    continue
                if kind == "h":
                    ok = abs(v[3] - want) <= 2
                    got = f"h={v[3]:.0f}"
                elif kind == "wh":
                    ok = abs(v[2] - want[0]) <= 2 and abs(v[3] - want[1]) <= 2
                    got = f"{v[2]:.0f}x{v[3]:.0f}"
                else:
                    ok = v[2] <= want + 1
                    got = f"w={v[2]:.0f}"
                total_cmp += 1
                total_res += not ok
                lines.append(f"| {key} | {kind} {want} | {got} | {'一致' if ok else '残差'} |")
            # column x inside the row (relative to the row's left edge) vs the original's 8/8 padding layout
            r0 = m["row0"]
            exp = {"row0_check": 8, "row0_star": 8 + 16 + 8, "row0_name": 8 + 16 + 8 + 22 + 8,
                   "row0_id": 8 + 16 + 8 + 22 + 8 + 210 + 8}
            for key, want in exp.items():
                v = m[key]
                got = v[0] - r0[0]
                ok = abs(got - want) <= 2
                total_cmp += 1
                total_res += not ok
                lines.append(f"| {key} の行内 x | {want} | {got:.0f} | {'一致' if ok else '残差'} |")
            lines += ["", f"- 行の内容(先頭): 名前 {fmt(m['row0_name'])} / 保存済み列の右端 {m['row0_count'][0] + m['row0_count'][2] - r0[0]:.0f} (期待 = 行幅 - …)",
                      f"- 横スクロール: scrollWidth={m['scroll'][0]} / clientWidth={m['scroll'][1]}",
                      f"- 行数 {m['rows']}（20 人 = 1 ページ）。コンソールエラー: {results[(size, theme, 'errors')] or 'なし'}",
                      f"- 実行後の帯: 「{results[(size, theme, 'run_end_status')]}」、先頭 6 行の最後に取得した日: {results[(size, theme, 'run_end_last')]}",
                      f"- 外部リクエスト（遮断）: {len(results[(size, theme, 'foreign')])} 件", ""]
            # colours
            png = out / f"ours_{tag}_idle.png"
            orig_png = ORIGINAL / f"{tag}_download.png"
            if size == SIZES[0]:
                ours_c = sample_colours(png, (lc[0], lc[1]), COLOUR_POINTS, lc[3])
                orig_c = sample_colours(orig_png, (ox, oy), COLOUR_POINTS, ocard["left_card"][3])
                lines += ["### 色（左カラム原点からの相対点を標本）", "", "| 場所 | 元 RGB | 移植版 RGB | 備考 |", "|---|---|---|---|"]
                for key in COLOUR_POINTS:
                    note = ""
                    if theme == "light" and key in ("left card face", "right card face", "run button (disabled)", "pause button"):
                        note = "ライトは明るいカード（本人の裁定）"
                    lines.append(f"| {key} | {orig_c[key]} | {ours_c[key]} | {note} |")
                lines.append("")
            # side by side
            a = Image.open(orig_png).convert("RGB")
            b = Image.open(png).convert("RGB")
            sheet = Image.new("RGB", (a.width + b.width + 10, max(a.height, b.height)), (255, 0, 255))
            sheet.paste(a, (0, 0))
            sheet.paste(b, (a.width + 10, 0))
            sheet.save(out / f"side_by_side_{tag}.png")
    details = results.get((SIZES[0], "dark", "details_testids"))
    lines += ["## 詳細ダイアログ（作品単位で選ぶ）", "",
              f"- 開いたときに見えた既存の data-testid（managed-download-*）: {len(details or [])} 件", f"- 一覧: {details}", ""]
    lines += ["## 集計", "", f"- 比較した項目: {total_cmp}、残差: {total_res}", ""]
    (out / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return total_cmp, total_res


def run(args):
    if args.port == 22386:
        raise SystemExit("refusing the real app's port")
    if not S.port_free(args.port):
        raise SystemExit(f"port {args.port} already in use")
    out = Path(args.out or S.VERIFY_ROOT / datetime.now().strftime("download_screen_%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=False)
    base = out / "run"
    base.mkdir()
    (base / "tmp").mkdir()
    S.seed_all(base)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               TEMP=str(base / "tmp"), TMP=str(base / "tmp"), NICEGUI_STORAGE_PATH=str(base / ".nicegui"))
    env.pop("PYTEST_CURRENT_TEST", None)
    os.environ["TEMP"] = os.environ["TMP"] = str(base / "tmp")
    log = (base / "server.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--serve", "--root", str(base),
                             "--port", str(args.port), "--authors", str(args.authors)],
                            cwd=str(base), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://{S.HOST}:{args.port}"
    try:
        deadline = time.time() + 60
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early (see {base / 'server.log'})")
            try:
                if S.http(url + "/healthz", timeout=1).status == 200:
                    break
            except Exception:
                if time.time() > deadline:
                    raise TimeoutError("server health timeout")
                time.sleep(0.3)
        results = drive(args, out, url)
        cmp_n, res_n = build_report(out, results)
        calls = json.loads(S.http(url + "/_cap/network").read())["calls"]
        (out / "network_calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"compared {cmp_n} items, residuals {res_n}; fake client calls {len(calls)}; report: {out / 'comparison.md'}")
    finally:
        try:
            S.http(url + "/_cap/shutdown", data=b"", timeout=5)
            proc.wait(timeout=30)
        except Exception:
            S.kill_tree(proc.pid)
        if proc.poll() is None:
            S.kill_tree(proc.pid)
        log.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--root", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=22489)
    ap.add_argument("--authors", type=int, default=20)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.serve:
        return serve(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
