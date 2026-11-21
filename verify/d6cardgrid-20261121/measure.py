"""Loopback ranking and library measurements against the product views."""
import argparse
import io
import json
import os
import socket
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
            time.sleep(.150)  # Keep loading observable while capture or pytest shares the host.
            offset = 100 if mode == "weekly" else 0
            rows = [dict(illust_id=40000 + offset + index, user_id=41, user_name="作者",
                         title=f"作品 {index + 1:02}", tags=["猫"] if index % 2 else [],
                         url=f"https://i.pximg.net/fixture-{offset + index}.png",
                         rank=index + 1, view_count=index + 1, page_count=1) for index in range(50)]
            if mode == 'weekly' and os.environ.get('PV_D6_FIXTURE_OVERLAP') == '1':
                rows[20].update(illust_id=40002, url='https://i.pximg.net/fixture-2.png')
            record(dict(kind="api", start=start, end=time.perf_counter()))
            return rows

        def get_artwork_pages(self, work_id):
            return (f'https://i.pximg.net/fixture-{work_id}.png',)

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

    services = ApplicationServices(50)
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

    @ui.page('/bookmarks')
    def bookmarks():
        from pixivvault_nicegui.bookmark_state import BookmarkState
        from pixivvault_nicegui.ui.bookmark_view import BookmarkView
        ui.add_css(CSS)
        items = services.artworks[:2]
        status = ui.label('未開封').props('data-testid=bench-opened')
        BookmarkView(BookmarkState(local=items[:1], cached_remote=items),
                     on_open=status.set_text, image_url=lambda wid: services.images.url(wid, 0, 'thumb')).render()

    ui.run(host="127.0.0.1", port=PORT, show=False, reload=False, access_log=False)


def check_browser_contract(page, root):
    from playwright.sync_api import expect

    cards = page.locator(CARD)
    expect(cards).to_have_count(50)
    first, second = cards.nth(0), cards.nth(1)
    expect(first.locator('.pv-card-rank')).to_have_text('#1')
    expect(first.locator('.pv-card-head')).to_have_count(0)
    expect(second.locator('.pv-card-head [data-testid=ranking-heart-40001]')).to_be_visible()
    menu = page.get_by_role('menu')
    origin = second.bounding_box()
    second.click(button='right', position={'x': 28, 'y': 34})
    expect(menu).to_have_count(1)
    expect(menu).to_have_attribute('data-testid', 'ranking-ctx-40001')
    popup = menu.bounding_box()
    assert abs(popup['x'] - origin['x'] - 28) <= 4
    assert abs(popup['y'] - origin['y'] - 34) <= 4
    expect(menu.get_by_test_id('ranking-ctx-40001-bookmark')).to_have_attribute('aria-disabled', 'true')
    link = menu.get_by_test_id('ranking-ctx-40001-pixiv')
    expect(link).to_have_attribute('href', 'https://www.pixiv.net/artworks/40001')
    expect(link).to_have_attribute('target', '_blank')
    expect(link).to_have_attribute('rel', 'noopener noreferrer')
    page.keyboard.press('Escape')
    expect(menu).to_have_count(0)
    expect(second).to_be_focused()
    for key in ('Shift+F10', 'ContextMenu'):
        first.press(key)
        expect(menu).to_have_attribute('data-testid', 'ranking-ctx-40000')
        page.keyboard.press('Escape')
        expect(menu).to_have_count(0)
        expect(first).to_be_focused()

    first.press('Enter')
    close = page.get_by_role('button', name='閉じる', exact=True)
    expect(close).to_be_visible()
    close.click()
    expect(first).to_be_focused()

    first.press('Shift+F10')
    expect(menu).to_have_attribute('data-testid', 'ranking-ctx-40000')
    page.evaluate('window.socket.disconnect()')
    page.evaluate("""() => {
      window.socket.io.opts.query.next_message_id = window.nextMessageId;
      window.socket.connect();
    }""")
    page.wait_for_function('window.socket.connected && window.did_handshake')
    expect(menu).to_have_attribute('data-testid', 'ranking-ctx-40000')
    page.keyboard.press('Escape')
    expect(first).to_be_focused()
    grid = page.locator('[data-testid=ranking-view] [data-render-epoch]')
    epoch = grid.get_attribute('data-render-epoch')
    original_card = first.element_handle()
    page.get_by_test_id('ranking-fetch').click()
    page.wait_for_function('''() => window.bench.loading &&
        document.querySelector('[data-testid=ranking-notice]').textContent.includes('50 件')''')
    expect(grid).to_have_attribute('data-render-epoch', epoch)
    expect(cards).to_have_count(50)
    assert original_card.evaluate('(node, selector) => node === document.querySelector(selector)', CARD)
    first.press('Shift+F10')
    expect(menu).to_have_attribute('data-testid', 'ranking-ctx-40000')
    page.keyboard.press('Escape')
    expect(first).to_be_focused()
    page.screenshot(path=root / 'contract.png')
    page.get_by_test_id('ranking-mode').get_by_text('週間', exact=True).click()
    expect(first).to_have_attribute('data-work-id', '40100')
    expect(grid).not_to_have_attribute('data-render-epoch', epoch)
    (root / 'contract.json').write_text(json.dumps({
        'count': 50, 'heart': True, 'right_click': True, 'pointer_position': True,
        'keyboard_menu': True, 'reconnect_refresh_same_dom': True,
        'reconnect_keeps_open_menu': True, 'identical_refresh_keeps_epoch': True,
        'replaced_results_advance_epoch': True,
        'menu_focus': True, 'viewer_focus': True, 'reconnect': True,
    }, indent=2))


def check_library_contract(page, root):
    from playwright.sync_api import expect

    author = page.locator(AUTHOR).first
    opener = author.get_by_test_id('library-author-open')
    key = author.get_attribute('data-author-id')
    opener.press('Enter')
    detail = page.locator('[data-testid=library-pages-scroll]')
    expect(detail).to_be_visible()
    detail_card = detail.locator('.pv-library-page-card').first
    detail_card.press('Enter')
    close = page.get_by_role('button', name='閉じる', exact=True)
    expect(close).to_be_visible()
    close.click()
    expect(detail_card).to_be_focused()
    page.get_by_test_id('library-back').click()
    target = page.locator(f'{AUTHOR}[data-author-id="{key}"]').get_by_test_id('library-author-open')
    expect(target).to_be_focused()
    page.route('**/media/**', lambda route: route.fulfill(status=404))
    page.reload(wait_until='domcontentloaded')
    expect(page.get_by_test_id('library-image-placeholder').first).to_have_attribute('aria-label', '画像未表示')
    (root / 'library-contract.json').write_text(json.dumps({
        'focus_by_author_id': True, 'focus_by_page': True, 'image_failure_accessible': True}))



def check_bookmark_contract(page, root):
    from playwright.sync_api import expect

    cards = page.get_by_test_id('bookmark-card')
    expect(cards).to_have_count(2)
    first = cards.first
    choice = first.get_by_test_id('bookmark-select')
    expect(first.get_by_test_id('bookmark-downloaded')).to_be_visible()
    choice.click()
    expect(choice).to_have_attribute('aria-checked', 'true')
    expect(page.get_by_test_id('bookmark-selection')).to_have_text('選択中: 1件')
    opened = page.get_by_test_id('bench-opened')
    expect(opened).to_have_text('未開封')
    first.get_by_test_id('bookmark-open').press('Enter')
    expect(opened).to_have_text(first.get_attribute('data-work-id'))
    choice.press('Space')
    expect(choice).to_have_attribute('aria-checked', 'false')
    (root / 'bookmark-contract.json').write_text(json.dumps({
        'selection_keeps_open_separate': True, 'keyboard_toggle': True, 'downloaded_badge': True}))


def stable_shot(page, path):
    page.mouse.move(0, 0)
    page.wait_for_function("""selector => {
        const cards = [...document.querySelectorAll(selector)];
        return cards.length === 50 && cards.filter(card => {
            const rect = card.getBoundingClientRect();
            return rect.bottom > 0 && rect.top < window.innerHeight;
        }).every(card => {
            const img = card.querySelector('img');
            return img?.complete && img.naturalWidth > 0;
        });
    }""", arg=CARD)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    page.screenshot(path=path, animations='disabled')


def run(root, repetitions, width, contract, visual_only, all_screenshots, trace_images=False,
        partial_overlap=False):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', PORT))
    root.mkdir()
    env = dict(os.environ, USERPROFILE=str(root), APPDATA=str(root), LOCALAPPDATA=str(root),
               PV_D6_FIXTURE_OVERLAP='1' if partial_overlap else '0')
    rows = []
    image_traces = []
    with (root / "server.log").open("wb") as log:
        server = subprocess.Popen([sys.executable, "-B", __file__, str(root), "--serve"],
                                  env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True, env=env, args=[
                    "--proxy-server=http://127.0.0.1:1", "--disable-background-networking"])
                context = browser.new_context(viewport={'width': width, 'height': 1080 if width == 1920 else 800},
                                              service_workers="block")
                context.route("**/*", lambda route: route.continue_()
                              if route.request.url.startswith(URL + "/") else route.abort())
                image_requests = []

                def trace_image(request):
                    if request.resource_type == 'image' and request.url.startswith(URL + '/media/'):
                        work_id = request.url.split('/')[4]
                        if partial_overlap and work_id == '40002':
                            image_requests.append((21, time.perf_counter()))
                        elif work_id.isascii() and work_id.isdecimal() and 40100 <= int(work_id) < 40150:
                            image_requests.append((int(work_id) - 40099, time.perf_counter()))

                if trace_images:
                    context.on('request', trace_image)
                page = context.new_page()
                page.set_default_timeout(12000)
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
                        const notice = document.querySelector('[data-testid=ranking-notice]');
                        if (notice) new MutationObserver(records => {
                          if (notice.textContent.trim() === '取得中…' || records.some(record =>
                            record.oldValue?.trim() === '取得中…' ||
                            [...record.addedNodes, ...record.removedNodes].some(
                              node => node.textContent.trim() === '取得中…'))) window.bench.loading = true;
                        }).observe(notice, {childList: true, subtree: true, characterData: true,
                                           characterDataOldValue: true});
                        document.addEventListener('click', () => {
                          window.bench = {begin: performance.now()};
                        }, true);
                        document.addEventListener('input', () => {
                          window.bench = {begin: performance.now()};
                        }, true);
                    }""")

                def observe(label, action, selector, total, *, first=None, screenshot=False, image=True):
                    previous = page.locator(selector).first.element_handle() if page.locator(selector).count() else None
                    if trace_images and label == 'mode':
                        image_requests.clear()
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
                        const done = !transition || (state.loading && notice?.textContent.includes('50 件'));
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
                    if trace_images and label == 'mode':
                        image_traces.append([
                            dict(rank=rank, request_ms=round((start_time - start) * 1000, 2))
                            for rank, start_time in image_requests
                        ])
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

                if visual_only:
                    ready()
                    page.get_by_test_id('ranking-fetch').click()
                    stable_shot(page, root / 'stable.png')
                for index in range(0 if visual_only else repetitions):
                    snap = all_screenshots or index == 0
                    ready()
                    fetch = page.get_by_test_id("ranking-fetch")
                    observe("initial", fetch.click, CARD, 50, first="40000", screenshot=snap)
                    observe("refresh", fetch.click, CARD, 50, first="40000", screenshot=snap)
                    weekly = page.get_by_test_id("ranking-mode").get_by_text("週間", exact=True)
                    survivor_selector = CARD + '[data-work-id="40002"] img'
                    if partial_overlap:
                        page.wait_for_function(
                            'selector => document.querySelector(selector)?.naturalWidth > 0',
                            arg=survivor_selector)
                        survivor = page.locator(survivor_selector).element_handle()
                    observe("mode", weekly.click, CARD, 50, first="40100", screenshot=snap)
                    if partial_overlap:
                        rows[-1]['overlap_kept_image_node'] = survivor.evaluate(
                            '(image, selector) => image === document.querySelector(selector)', survivor_selector)
                        (root / 'raw.json').write_text(json.dumps(rows, indent=2))
                    observe("sort", page.get_by_text("bench sort", exact=True).click,
                            CARD, 50, first="40149", screenshot=snap)
                    ready("/library")
                    expect(page.locator(AUTHOR)).to_have_count(1)
                    search = page.get_by_test_id("library-search")
                    observe("library", lambda: search.fill(""), AUTHOR, 50,
                            screenshot=snap)
                if contract:
                    ready()
                    page.get_by_test_id('ranking-fetch').click()
                    check_browser_contract(page, root)
                    if page.locator('[data-render-epoch]').count():
                        ready('/library')
                        if page.locator('[data-testid=library-authors-scroll] [data-render-epoch]').count():
                            check_library_contract(page, root)
                            page.unroute('**/media/**')
                            ready('/bookmarks')
                            check_bookmark_contract(page, root)
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
    if trace_images:
        (root / 'image-trace.json').write_text(json.dumps(image_traces, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument('--width', type=int, choices=(1280, 1920), default=1280)
    parser.add_argument('--contract', action='store_true')
    parser.add_argument('--visual-only', action='store_true')
    parser.add_argument('--all-screenshots', action='store_true')
    parser.add_argument('--trace-images', action='store_true')
    parser.add_argument('--partial-overlap', action='store_true')
    args = parser.parse_args()
    if args.serve:
        serve(args.root)
    else:
        run(args.root, args.repetitions, args.width, args.contract, args.visual_only,
            args.all_screenshots, args.trace_images, args.partial_overlap)
