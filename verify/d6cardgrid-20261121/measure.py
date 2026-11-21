"""Loopback ranking and library measurements against the product views."""
import argparse
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
PORT = 22764
URL = f"http://127.0.0.1:{PORT}"
CARD = '[data-testid=ranking-view] .pv-card'
AUTHOR = '[data-testid=library-authors-scroll] [data-author-id]'


def serve(root):
    from PIL import Image
    from nicegui import app, ui
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.ranking_source import RankingSource
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    from pixivvault_nicegui.ui.ranking_view import RankingView
    from pixivvault_nicegui.ui.theme import CSS
    from tools.check_library_calendar_slider_owned import deny_external_connections

    deny_external_connections()
    image = io.BytesIO()
    Image.new("RGB", (140, 120), (30, 120, 170)).save(image, format="PNG")
    image_bytes = image.getvalue()
    events = root / "server.jsonl"

    def record(row):
        with events.open("a", encoding="ascii") as output:
            output.write(json.dumps(row) + "\n")

    class FakeReader:
        def require_identity(self):
            return '41'

        def get_ranking(self, *, mode, **kwargs):
            start = time.perf_counter()
            offset = 100 if mode == "weekly" else 0
            rows = [dict(illust_id=40000 + offset + index, user_id=41, user_name="作者",
                         title=f"作品 {index + 1:02}", tags=["猫"] if index % 2 else [],
                         url=f"https://i.pximg.net/fixture-{offset + index}.png",
                         rank=index + 1, view_count=index + 1) for index in range(50)]
            record(dict(kind="api", start=start, end=time.perf_counter()))
            return rows

        def get_image_bytes(self, *args, **kwargs):
            return image_bytes

    def instrument(owner, method, kind):
        original = getattr(owner, method)

        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                record(dict(kind=kind, start=start, end=time.perf_counter()))

        setattr(owner, method, measured)

    services = ApplicationServices(1)
    register_media_routes(app, services.images)

    @ui.page("/")
    def ranking():
        ui.add_css(CSS)
        reader = FakeReader()
        services.ranking_source = RankingSource(reader)
        # Install the fixture provider after making the visible view active so
        # the first click measures an empty result grid.
        services.live_client = None
        view = RankingView(services)
        view.set_active(True)
        services.live_client = reader
        services.preferred_ranking_tags = lambda: frozenset({'猫'})
        view._preferred = frozenset({"猫"})
        view._preferred_built = True
        instrument(view, "render", "render")
        method = "_card_data" if hasattr(view.gallery, "_card_data") else "_make_card"
        instrument(view.gallery, method, "card")

        ui.button("bench sort", on_click=lambda: view.sort.set_value(
            "view_count" if view.sort.value == "rank" else "rank"))

    @ui.page("/library")
    def library():
        ui.add_css(CSS)
        from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork

        services = ApplicationServices(50)
        services.library = LibrarySnapshot(tuple(
            LibraryWork(art, f'fixture-{n:02}', author_name=f'Fixture {n:02}')
            for n, art in enumerate(services.artworks)))
        view = LibraryView(services)
        view.set_active(True)
        instrument(view, "render_authors", "render")
        instrument(view, "_make_author_card", "card")
        first_name = view.state.visible_authors[0].name
        view.search(first_name)
        view.search_input.value = first_name

    ui.run(host="127.0.0.1", port=PORT, show=False, reload=False, access_log=False)


def run(root, repetitions):
    from playwright.sync_api import expect, sync_playwright

    root.mkdir()
    env = dict(os.environ, USERPROFILE=str(root), APPDATA=str(root), LOCALAPPDATA=str(root))
    rows = []
    with (root / "server.log").open("wb") as log:
        server = subprocess.Popen([sys.executable, "-B", __file__, str(root), "--serve"],
                                  env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True, env=env, args=[
                    "--proxy-server=http://127.0.0.1:1", "--disable-background-networking"])
                context = browser.new_context(viewport={"width": 1280, "height": 800},
                                              service_workers="block")
                context.route("**/*", lambda route: route.continue_()
                              if route.request.url.startswith(URL + "/") else route.abort())
                page = context.new_page()
                page.set_default_timeout(12000)
                import socket
                for _ in range(75):
                    if server.poll() is not None:
                        raise RuntimeError("isolated server exited")
                    try:
                        with socket.create_connection(('127.0.0.1', PORT), timeout=.25):
                            break
                    except OSError:
                        time.sleep(.15)

                def ready(path=""):
                    page.goto(URL + path, wait_until="domcontentloaded")
                    page.wait_for_function("window.did_handshake === true")
                    page.evaluate("""() => {
                        window.bench = {};
                        document.addEventListener('click', () => {
                          window.bench = {begin: performance.now()};
                        }, true);
                        document.addEventListener('input', () => {
                          window.bench = {begin: performance.now()};
                        }, true);
                    }""")

                def observe(label, action, selector, total, *, first=None, screenshot=False, image=True):
                    previous = page.locator(selector).first.element_handle() if page.locator(selector).count() else None
                    start = time.perf_counter()
                    action()
                    browser_times = page.evaluate("""async ([selector, total, first, image, transition]) => {
                      const state = window.bench;
                      const notice = document.querySelector('[data-testid=ranking-notice]');
                      let list, pixels;
                      const deadline = performance.now() + 12000;
                      while (performance.now() <= deadline) {
                        const cards = document.querySelectorAll(selector);
                        const img = cards[0]?.querySelector('img');
                        const done = !transition || notice?.textContent.includes('50 件');
                        if (done && cards.length === total && (!first || cards[0]?.dataset.workId === first)) {
                          list ??= performance.now();
                          if (!image || (img?.complete && img.naturalWidth > 0)) {
                            pixels = performance.now();
                            break;
                          }
                        }
                        await new Promise(requestAnimationFrame);
                      }
                      if (pixels == null) throw Error(JSON.stringify({selector, status: notice?.textContent,
                        cards: document.querySelectorAll(selector).length,
                        first: document.querySelector(selector)?.dataset.workId,
                        image: document.querySelector(selector + ' img')?.naturalWidth}));
                      await new Promise(requestAnimationFrame);
                      return [list - state.begin, pixels - list, performance.now() - state.begin];
                    }""", [selector, total, first, image, label in ("initial", "refresh", "mode")])
                    end = time.perf_counter()
                    click_shot = None
                    if screenshot:
                        page.screenshot(path=root / f"{label}.png")
                        click_shot = (time.perf_counter() - start) * 1000
                    data = [json.loads(line) for line in (root / "server.jsonl").read_text().splitlines()]
                    current = [e for e in data if start <= e["start"] <= e["end"] <= end]
                    stage = {kind: sum((e["end"] - e["start"]) * 1000 for e in current if e["kind"] == kind)
                             for kind in ("card", "render", "api")}
                    same = previous is not None and previous.evaluate(
                        "(node, selector) => node === document.querySelector(selector)", selector)
                    rows.append(dict(case=label, list_ms=browser_times[0], image_ms=browser_times[1],
                                     paint_ms=browser_times[2], screenshot_ms=click_shot, same_first=same,
                                     residual_ms=browser_times[2] - stage["api"], **stage))
                    (root / "raw.json").write_text(json.dumps(rows, indent=2))

                for index in range(repetitions):
                    ready()
                    fetch = page.get_by_test_id("ranking-fetch")
                    observe("initial", fetch.click, CARD, 50, first="40000", screenshot=index == 0)
                    # A refresh with identical IDs must finish a new request.
                    def refresh():
                        fetch.click()
                        expect(fetch).to_be_enabled(timeout=12000)
                    observe("refresh", refresh, CARD, 50, first="40000", screenshot=index == 0)
                    weekly = page.get_by_test_id("ranking-mode").get_by_text("週間", exact=True)
                    observe("mode", weekly.click, CARD, 50, first="40100", screenshot=index == 0)
                    observe("sort", page.get_by_text("bench sort", exact=True).click,
                            CARD, 50, first="40149", screenshot=index == 0)
                    ready("/library")
                    expect(page.locator(AUTHOR)).to_have_count(1)
                    search = page.get_by_test_id("library-search")
                    observe("library", lambda: search.fill(""), AUTHOR, 50,
                            screenshot=index == 0, image=False)
                browser.close()
        finally:
            server.terminate()
            code = server.wait(timeout=15)
            (root / "owned-server.json").write_text(json.dumps({"pid": server.pid, "exit": code}))
    metrics = ("list_ms", "image_ms", "paint_ms", "screenshot_ms", "card", "render", "api", "residual_ms")
    summary = {}
    for case in dict.fromkeys(r["case"] for r in rows):
        subset = [r for r in rows if r["case"] == case]
        summary[case] = {m: round(statistics.median(r[m] for r in subset if r[m] is not None), 2)
                         for m in metrics}
        summary[case]["n"] = len(subset)
    (root / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if args.serve:
        serve(args.root)
    else:
        run(args.root, args.repetitions)
