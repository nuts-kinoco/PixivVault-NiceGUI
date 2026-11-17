"""元 PixivVault (Flet) を隔離起動した Web 表示 (127.0.0.1:28550) の全タブを撮る。
使い方: S:\\Projects\\PixivVault-NiceGUI\\.venv\\Scripts\\python capture_original.py <出力ディレクトリ> [url]
セマンティクス有効化後の DOM からテキストも .txt に保存する。"""
import sys, os, json
from playwright.sync_api import sync_playwright
out = sys.argv[1]; url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:28550/"
os.makedirs(out, exist_ok=True)
TABS = [("download","ダウンロード"),("queue","キュー"),("failed","失敗リスト"),("dedup","重複整理"),
        ("learn","学習"),("ranking","ランキング"),("library","ライブラリ"),("recommend","おすすめ")]
JS_TREE = """()=>[...document.querySelectorAll('flt-semantics')].map(e=>{const r=e.getBoundingClientRect();
 return [e.getAttribute('role')||'',(e.getAttribute('aria-label')||e.textContent||'').replace(/\\s+/g,' ').slice(0,60),
 Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)].join('|')}).join('\\n')"""
def is_dark(png):
    from PIL import Image; import io
    im = Image.open(io.BytesIO(png)).convert("RGB"); r,g,b = im.getpixel((4, im.height//2)); return (r+g+b)/3 < 128
def header_icons(pg):
    xs = pg.evaluate("""()=>[...document.querySelectorAll('flt-semantics[role=button]')].map(e=>{const r=e.getBoundingClientRect();
      return [r.x,r.y,r.width,r.height]}).filter(a=>a[1]<130&&a[0]>window.innerWidth*0.6)""")
    return sorted(xs, key=lambda a: a[0])
def click_tab(pg, label):
    pg.locator("flt-semantics[role=button]").filter(has_text=label).first.click()
    pg.wait_for_timeout(1800)
with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    for (w,h) in [(1280,800),(1920,1080)]:
        pg = br.new_page(viewport={"width":w,"height":h}); pg.goto(url); pg.wait_for_timeout(7000)
        pg.locator("flt-semantics-placeholder").first.evaluate("e=>e.click()"); pg.wait_for_timeout(1500)
        for want_dark in (True, False):
            if is_dark(pg.screenshot()) != want_dark:
                ic = header_icons(pg); a = ic[3]   # 4番目=テーマ切替
                pg.mouse.click(a[0]+a[2]/2, a[1]+a[3]/2); pg.wait_for_timeout(1500)
            theme = "dark" if want_dark else "light"
            for key, label in TABS:
                click_tab(pg, label)
                base = f"{out}/{w}x{h}_{theme}_{key}"
                pg.screenshot(path=base+".png")
                open(base+".txt","w",encoding="utf-8").write(pg.evaluate(JS_TREE))
            click_tab(pg, "ダウンロード")
            ic = header_icons(pg); a = ic[-1]   # 末尾=設定
            pg.mouse.click(a[0]+a[2]/2, a[1]+a[3]/2); pg.wait_for_timeout(2000)
            base = f"{out}/{w}x{h}_{theme}_settings"
            pg.screenshot(path=base+".png"); open(base+".txt","w",encoding="utf-8").write(pg.evaluate(JS_TREE))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(800)
        pg.close()
    br.close()

