"""共通シェル（ヘッダー・タブバー・配色）の実測と、元(Flet 版)の画面との比較表づくり（P2）。

隔離起動（tools/ui_smoke_browser.py の --serve をそのまま使う。ポートは試験用）した製品を
Playwright(Edge) で 2 サイズ x ダーク/ライトに撮り、DOM/CSS の計算値と画素を測る。
元の値は docs/ORIGINAL_SHELL_SPEC.md と verify/original_20261117_225445/ の画面から取った。

  PYTHONPATH=src PYTHONUTF8=1 <venv>/python.exe -B tools/shell_measure.py --out <verify/shell_日時> [--port 22489]

出力: <out>/<幅>x<高さ>_<theme>.png（製品）、<out>/orig_<幅>x<高さ>_<theme>.png（元のコピー）、
measurements.json、comparison.md。DB バナーは実機設定だと赤い帯になり元（灰色の 1 行）と高さが違うので、
測定のときだけ非本番表示（灰色 11px）と同じ見た目にして縦位置をそろえる。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ui_smoke_browser as S

ORIGINAL_DIR = Path("S:/Projects/PV-DEV-20261117/verify/original_20261117_225445")
SIZES = ((1280, 800), (1920, 1080))
THEMES = ("dark", "light")
# 元の実測（1280 幅。仕様書 3 章・画像）。
ORIG_TABS = {"download": (10, 139), "queue": (159, 96), "failed": (265, 124), "dedup": (399, 110),
             "learning": (530, 82), "ranking": (622, 124), "library": (756, 124), "recommend": (891, 110)}
ORIG = {
    "title_x": 10, "title_y": 38, "title_h": 46, "auth_y": 94, "conn_y": 135,
    "icon_centers_x": [1025, 1070, 1115, 1160, 1205, 1250], "icon_center_y": 100, "icon_pitch": 45,
    "banner_y": 175, "banner_h": 83, "banner_x": 10, "tab_y": 269, "tab_h": 32,
    "bg": {"dark": "#111418", "light": "#f8f9ff"},
    "selected_tab_bg": {"dark": "#2d3844", "light": "#d3dde9"},
    "selected_tab_text": {"dark": "#9ec7f9", "light": "#3d6692"},
    "tab_text": {"dark": "#e1e2e8", "light": "#191c20"},
    "icon": {"dark": "#c3c6cf", "light": "#43474e"},
    "banner_bg": {"dark": "#32353a", "light": "#e1e2e8"},
}

MEASURE_JS = r"""() => {
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: +r.left.toFixed(1), y: +r.top.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1)}; };
  const css = (e, p) => e ? getComputedStyle(e)[p] : null;
  const q = s => document.querySelector(s);
  const t = id => q(`[data-testid="${id}"]`);
  const banner = t('db-status-banner');
  const base = banner ? banner.getBoundingClientRect().bottom : 0;
  const tabs = [...document.querySelectorAll('.pv-tab')].map(e => ({key: e.dataset.testid.slice(4), ...box(e),
      color: css(e, 'color'), bg: css(e, 'backgroundColor'), font: css(e, 'fontFamily').slice(0, 40),
      icon: (e.querySelector('.q-icon') || {}).textContent, active: e.classList.contains('pv-tab-active')}));
  const ids = ['tab-history', 'view-history-open', 'saved-folder-open', 'theme-toggle', 'pixiv-home-link', 'preferences-open'];
  const icons = ids.map(i => ({id: i, ...box(t(i)), color: css(t(i), 'color')}));
  const nav = q('.pv-nav');
  return {
    base, title: {...box(q('.pv-title')), color: css(q('.pv-title'), 'color'), size: css(q('.pv-title'), 'fontSize'),
                  weight: css(q('.pv-title'), 'fontWeight'), font: css(q('.pv-title'), 'fontFamily').slice(0, 40)},
    auth: {...box(t('auth-status')), color: css(t('auth-status'), 'color'), size: css(t('auth-status'), 'fontSize')},
    conn: {...box(q('.pv-conn-row')), items: [...q('.pv-conn-row').querySelectorAll('[data-testid]')].map(e => e.dataset.testid)},
    icons, tabs,
    banner: {...box(t('cookie-banner')), bg: css(t('cookie-banner'), 'backgroundColor'),
             border: css(t('cookie-banner'), 'borderTopColor')},
    nav: {...box(nav), scrollWidth: nav.scrollWidth, clientWidth: nav.clientWidth},
    page: {bg: css(document.body, 'backgroundColor'), scrollWidth: document.documentElement.scrollWidth,
           clientWidth: document.documentElement.clientWidth},
    content_top: box(q('.pv-content')).y,
  };
}"""

NONPROD_BANNER_JS = """() => { const b = document.querySelector('[data-testid="db-status-banner"]');
  b.textContent = 'DB: S:\\\\Projects\\\\PV-DEV-20261117\\\\original_copy\\\\data\\\\pv_iso.db';
  b.style.cssText = 'font-size:11px;color:#9e9e9e;padding:2px 0 0 8px'; }"""


def hexcolor(css_color):
    import re
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", css_color or "")
    if not m:
        return css_color
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = m.group(4)
    return "#%02x%02x%02x" % (r, g, b) + (f"@{a}" if a is not None and float(a) != 1 else "")


def pixel(png: Path, x: int, y: int) -> str:
    from PIL import Image
    r, g, b = Image.open(png).convert("RGB").getpixel((x, y))
    return "#%02x%02x%02x" % (r, g, b)


def capture(args, out: Path):
    base = out / "run"
    base.mkdir(parents=True)
    (base / "tmp").mkdir()
    S.seed_all(base)
    nonce = secrets.token_urlsafe(24)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1", TEMP=str(base / "tmp"),
               TMP=str(base / "tmp"), NICEGUI_STORAGE_PATH=str(base / ".nicegui"))
    env.pop("PYTEST_CURRENT_TEST", None)
    log = (base / "server.log").open("wb")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(S.__file__).resolve()), "--serve", "--root", str(base),
                             "--port", str(args.port), "--nonce", nonce], cwd=str(base), env=env,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://{S.HOST}:{args.port}"
    os.environ["TEMP"] = os.environ["TMP"] = str(base / "tmp")
    results = {}
    try:
        deadline = time.time() + 60
        while True:
            if proc.poll() is not None:
                raise RuntimeError("server exited early")
            try:
                if S.http(url + "/healthz", timeout=1).status == 200:
                    break
            except Exception:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            for size in SIZES:
                for theme in THEMES:
                    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=theme)
                    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(url) else r.abort())
                    page = ctx.new_page()
                    page.goto(url, wait_until="domcontentloaded")
                    page.get_by_test_id("tab-library").wait_for(timeout=30000)
                    S.set_theme(page, theme)
                    page.wait_for_timeout(2500)
                    page.evaluate(NONPROD_BANNER_JS)
                    page.wait_for_timeout(300)
                    key = f"{size[0]}x{size[1]}_{theme}"
                    png = out / f"{key}.png"
                    page.screenshot(path=str(png))
                    data = page.evaluate(MEASURE_JS)
                    data["pixels"] = {"bg": pixel(png, 5, size[1] - 5)}
                    sel = next(t for t in data["tabs"] if t["active"])
                    data["pixels"]["selected_tab_bg"] = pixel(png, int(sel["x"]) + 6, int(sel["y"] + sel["h"] / 2))
                    results[key] = data
                    if key == "1280x800_dark":
                        results["functional"] = functional(page)
                    orig = ORIGINAL_DIR / f"{key}_download.png"
                    if orig.exists():
                        shutil.copy(orig, out / f"orig_{key}.png")
                    ctx.close()
            browser.close()
    finally:
        try:
            S.http(url + "/_smoke/shutdown", data=b"", headers={"X-Acceptance": nonce}, timeout=5)
            proc.wait(timeout=20)
        except Exception:
            S.kill_tree(proc.pid)  # この実行が起動した PID だけ
        if proc.poll() is None:
            S.kill_tree(proc.pid)
        log.close()
    return results


def functional(page):
    """ヘッダー右端のボタンが実際に動くか（元の配置へ移した機能）。"""
    out = {}
    page.get_by_test_id("tab-history").click()
    page.get_by_test_id("backup-history").first.wait_for(state="visible", timeout=20000)
    out["バックアップ(history ボタン)で画面が開く"] = True
    out["history ボタンが選択表示(aria-current)"] = page.get_by_test_id("tab-history").get_attribute("aria-current") == "page"
    page.get_by_test_id("view-history-open").click()
    page.get_by_test_id("view-history-dialog").wait_for(state="visible", timeout=20000)
    out["閲覧履歴ボタンで学習タブ上に履歴ダイアログが開く"] = page.get_by_test_id("tab-learning").get_attribute("aria-current") == "page"
    page.get_by_test_id("view-history-close").click()
    page.wait_for_timeout(500)
    link = page.get_by_test_id("pixiv-home-link")
    out["Pixiv を開くリンク"] = (link.get_attribute("href"), link.get_attribute("target"), link.get_attribute("rel"))
    page.get_by_test_id("preferences-open").click()
    page.get_by_test_id("preferences-dialog").wait_for(state="visible", timeout=20000)
    out["設定ボタンで設定ダイアログが開く"] = True
    out["設定ダイアログ題名のフォント"] = page.evaluate(
        "getComputedStyle(document.querySelector('[data-testid=preferences-dialog] .pv-dialog-title')).fontFamily")
    return out


def table(rows, head):
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def compare(results, out: Path):
    md = ["# 共通シェル 元(Flet)との比較（P2）", "",
          "測定: tools/shell_measure.py。座標は画面左上基準の px。y は「DB バナー下端」からの相対（元は DB バナーが灰色 1 行、"
          "製品は測定時だけ同じ見た目にそろえた）。元の値は仕様書と元の画像の実測。", ""]
    func = results.pop("functional", None)
    for key, d in results.items():
        w = int(key.split("x")[0])
        theme = key.split("_")[1]
        o_base = 28  # 元の DB 行（11px、y=12 付近）の下端
        md += [f"## {key}", ""]
        rows = [
            ("ロゴ x", ORIG["title_x"], d["title"]["x"]),
            ("ロゴ y(相対)", ORIG["title_y"] - o_base, round(d["title"]["y"] - d["base"], 1)),
            ("ロゴ 高さ", ORIG["title_h"], d["title"]["h"]),
            ("ロゴ サイズ/太さ", "32 / 700", f"{d['title']['size']} / {d['title']['weight']}"),
            ("認証ラベル y(相対)", ORIG["auth_y"] - o_base, round(d["auth"]["y"] - d["base"], 1)),
            ("接続行 y(相対)", ORIG["conn_y"] - o_base - 6, round(d["conn"]["y"] - d["base"], 1)),
            ("Cookie 帯 y(相対)", ORIG["banner_y"] - o_base, round(d["banner"]["y"] - d["base"], 1)),
            ("Cookie 帯 高さ", ORIG["banner_h"], d["banner"]["h"]),
            ("Cookie 帯 x/幅", f"10 / {w - 20}", f"{d['banner']['x']} / {d['banner']['w']}"),
            ("タブ y(相対)", ORIG["tab_y"] - o_base, round(d["tabs"][0]["y"] - d["base"], 1)),
            ("タブ 高さ", ORIG["tab_h"], d["tabs"][0]["h"]),
        ]
        md += [table(rows, ["項目", "元", "製品"]), ""]
        icon_rows = []
        for i, ic in enumerate(d["icons"]):
            cx = round(ic["x"] + ic["w"] / 2, 1)
            ocx = ORIG["icon_centers_x"][i] + (w - 1280)
            icon_rows.append((ic["id"], ocx, cx, round(ic["y"] + ic["h"] / 2 - d["base"], 1), ORIG["icon_center_y"] - o_base,
                              hexcolor(ic["color"])))
        md += ["ヘッダー右端のアイコン（中心 x、中心 y(相対)）", "",
               table(icon_rows, ["要素", "元 中心x", "製品 中心x", "製品 中心y(相対)", "元 中心y(相対)", "製品 色"]), ""]
        tab_rows = []
        for t in d["tabs"]:
            o = ORIG_TABS.get(t["key"])
            tab_rows.append((t["key"], t["icon"], f"{o[0]}/{o[1]}" if o else "元に無い", f"{t['x']}/{t['w']}",
                             hexcolor(t["color"]), hexcolor(t["bg"])))
        nav = d["nav"]
        md += ["タブ（x/幅）", "", table(tab_rows, ["タブ", "アイコン", "元 x/幅", "製品 x/幅", "文字色", "地"]), "",
               f"タブバー: 幅 {nav['clientWidth']}px に対し内容 {nav['scrollWidth']}px"
               + ("（バー内スクロール。画面外のタブ "
                  f"{sum(1 for t in d['tabs'] if t['x'] + t['w'] > nav['x'] + nav['clientWidth'] + 1)} 個）"
                  if nav['scrollWidth'] > nav['clientWidth'] else "（1 行に収まる）"),
               f"ページ横スクロール: scrollWidth {d['page']['scrollWidth']} / clientWidth {d['page']['clientWidth']}", ""]
        sel = next(t for t in d["tabs"] if t["active"])
        unsel = next(t for t in d["tabs"] if not t["active"])
        crow = [
            ("ページ背景(計算値)", ORIG["bg"][theme], hexcolor(d["page"]["bg"])),
            ("ページ背景(画素)", ORIG["bg"][theme], d["pixels"]["bg"]),
            ("選択タブ 文字", ORIG["selected_tab_text"][theme], hexcolor(sel["color"])),
            ("選択タブ 地(計算値)", ORIG["selected_tab_bg"][theme] + "（元は約 19% 相当）", hexcolor(sel["bg"])),
            ("選択タブ 地(画素)", ORIG["selected_tab_bg"][theme], d["pixels"]["selected_tab_bg"]),
            ("非選択タブ 文字", ORIG["tab_text"][theme], hexcolor(unsel["color"])),
            ("ヘッダーアイコン 色", ORIG["icon"][theme], hexcolor(d["icons"][0]["color"])),
            ("Cookie 帯 地", ORIG["banner_bg"][theme], hexcolor(d["banner"]["bg"])),
            ("Cookie 帯 枠", "#42a5f5", hexcolor(d["banner"]["border"])),
            ("ロゴ 色", "#e1e2e8" if theme == "dark" else "#191c20", hexcolor(d["title"]["color"])),
            ("認証ラベル 色", "#ef5350（元はライトでも同じ）", hexcolor(d["auth"]["color"])),
        ]
        md += ["色", "", table(crow, ["項目", "元", "製品"]), ""]
        md += [f"接続行の要素: {', '.join(d['conn']['items'])}", "",
               f"![製品](./{key}.png) ![元](./orig_{key}.png)", ""]
    if func:
        md += ["## 動作確認（1280x800 ダーク）", "", table([(k, v) for k, v in func.items()], ["項目", "結果"]), ""]
    (out / "comparison.md").write_text("\n".join(md), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--port", type=int, default=22489)
    args = ap.parse_args()
    if args.port == 22386 or not S.port_free(args.port):
        raise SystemExit("port refused / in use")
    args.out.mkdir(parents=True, exist_ok=True)
    results = capture(args, args.out)
    (args.out / "measurements.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    compare(results, args.out)
    print("done", args.out)


if __name__ == "__main__":
    main()
