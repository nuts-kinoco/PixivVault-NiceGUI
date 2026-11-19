"""Compare the Flet and NiceGUI web UIs using the same isolated library and learning images."""

import argparse
import io
import json
import math
import os
import shutil
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGINAL = Path('S:/Projects/PixivVault')
ORIGINAL_PYTHON = Path('C:/Users/nat/miniconda3/envs/stenv/python.exe')
ORIGINAL_PORT, NICE_PORT = 22630, 22640
WORKS, AUTHORS, LEARNING, EXTRA_PAGES = 2000, 200, 60, 24
CASES = ('learning', 'library_display', 'library_scroll', 'library_search', 'viewer_next')


def color(number, page=0):
    return ((number * 37 + page * 17) % 180 + 50, (number * 13 + page * 83) % 180 + 50,
            (number * 53 + page * 31) % 180 + 50)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def original_copy(base):
    revision = git(ORIGINAL, 'rev-parse', 'HEAD').decode().strip()
    names = [name for name in git(ORIGINAL, 'ls-tree', '-r', '--name-only', 'HEAD').decode().splitlines()
             if name.startswith('assets/') or (name.endswith('.py') and
                (name.count('/') == 0 or name.startswith('tools/') and name.count('/') == 1))]
    raw = git(ORIGINAL, 'archive', '--format=zip', revision, '--', *names)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        archive.extractall(base)
    for name in ('main', 'pixiv_client'):
        patch = (ROOT / f'tools/original_run/patch_{name}.py.diff').read_text(encoding='utf-8')
        _, index, _, _, *hunks = patch.splitlines()
        old = index.split()[1].split('..')[0]
        relative = f'{name}.py'
        if not git(ORIGINAL, 'rev-parse', f'{revision}:{relative}').decode().startswith(old):
            raise RuntimeError(f'{name} upstream isolation patch requires review')
        (base / relative).write_bytes(git(ORIGINAL, 'show', f'{revision}:{relative}'))
        normalized = '\n'.join([f'diff --git a/{relative} b/{relative}', index,
                               f'--- a/{relative}', f'+++ b/{relative}', *hunks, ''])
        subprocess.run(['git', 'apply', '--no-index', '--whitespace=nowarn'], cwd=base,
                       input=normalized.encode(), check=True)
    return revision


def isolated_environment(base):
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / 'src')]))
    for name, folder in {'TEMP': 'temp', 'TMP': 'temp', 'TMPDIR': 'temp', 'USERPROFILE': 'home',
                         'HOME': 'home', 'APPDATA': 'roaming', 'LOCALAPPDATA': 'local'}.items():
        target = base / folder
        target.mkdir(exist_ok=True)
        env[name] = str(target)
    return env


def seed(base):
    from PIL import Image, ImageDraw
    from pixivvault_nicegui.legacy.database import Database

    library = base / 'library'
    library.mkdir()
    db_path = library / 'fixture.sqlite'
    database = Database(db_path, sandbox_root=library)
    rows, authors, learning = [], [], []

    def image(path, number, page=0):
        frame = Image.new('RGB', (480, 360), color(number, page))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((24, 24, 456, 336), outline=(245, 245, 245), width=8)
        draw.text((50, 60), f'{number:06d} / {page + 1}', fill=(255, 255, 255))
        frame.save(path)

    for index in range(AUTHORS):
        author_id = str(30000 + index)
        name = f'Bench Author {index:03d}'
        authors.append((author_id, name))
        folder = library / 'Images' / f'{name}({author_id})'
        folder.mkdir(parents=True)
        for offset in range(WORKS // AUTHORS):
            number = 100000 + index * (WORKS // AUTHORS) + offset
            title = f'Bench Work {number}'
            page_count = 1 + EXTRA_PAGES if number == 100000 else 1
            rows.append((str(number), title, page_count, '["bench"]', author_id, 'illust',
                         '2026-10-22', '2026-10-22', json.dumps({'urls': {
                             'small': f'https://synthetic.invalid/{number}-1.png'}})))
            for page in range(page_count):
                image(folder / f'{title}({number})_{page + 1}.png', number, page)
    with database.conn as db:
        db.executemany('INSERT INTO following_users(user_id,name) VALUES (?,?)', authors)
        db.executemany('INSERT INTO works(work_id,title,page_count,tags,user_id,content_type,'
                       'create_date,last_backup,work_meta) VALUES (?,?,?,?,?,?,?,?,?)', rows)
    database.set_setting('save_path', str(library / 'Images'))
    database.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    database.conn.execute('PRAGMA journal_mode=DELETE')
    database.conn.close()
    learning_root = base / 'learning'
    learning_root.mkdir()
    for number in range(800000, 800000 + LEARNING):
        learning.append(dict(work_id=str(number), title=f'Bench Learn {number}', author_id=str(90000 + number % 30),
                             author_name='Bench learning', tags=['bench'], source='ranking',
                             thumbnail_url=f'https://synthetic.invalid/{number}.png', x_restrict=0))
        image(learning_root / f'{number}.png', number)
    (base / 'learning.json').write_text(json.dumps(learning), encoding='utf-8')
    return db_path


def serve_nice(base):
    from tools.check_library_calendar_slider_owned import deny_external_connections
    network = deny_external_connections()
    from nicegui import app, ui
    from pixivvault_nicegui import learning_service, media, services
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.models import Artwork
    from pixivvault_nicegui.preferences import PreferencesStore

    rows = json.loads((base / 'learning.json').read_text(encoding='utf-8'))
    artworks = tuple(
        Artwork(f"demo-{row['work_id']}", row['title'], row['author_name'], aspect_ratio=4 / 3,
                tags=tuple(row['tags']), author_id=row['author_id']) for row in rows)
    services.generate_demo_artworks = learning_service.generate_demo_artworks = lambda _count: artworks
    media._png = lambda work_id, _page, _variant, _ratio: (
        base / 'learning' / f"{work_id.removeprefix('demo-')}.png").read_bytes()
    paths = dict(learning_sandbox=base / 'learning-ledger',
                 library_readonly_db=base / 'library' / 'fixture.sqlite',
                 library_image_root=base / 'library' / 'Images', preferences_root=base / 'preferences',
                 legacy_copy_root=base / 'legacy-snapshots')
    (base / 'legacy-snapshots').mkdir()
    (base / 'preferences').mkdir()
    prefs = PreferencesStore(base / 'preferences/preferences.sqlite3')
    try:
        prefs.set_setting('local_tagger_path', str(base / 'no-model'))
    finally:
        prefs.close()
    register_pages(**paths)

    @app.get('/__p6_network')
    def network_counts():
        return network

    ui.run(host='127.0.0.1', port=NICE_PORT, show=False, reload=False, native=False,
           binding_refresh_interval=None)


@contextmanager
def server(base, name, command, cwd, env, port):
    url = f'http://127.0.0.1:{port}/'
    with (base / f'{name}.log').open('wb') as log, socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    with (base / f'{name}.log').open('ab') as log, subprocess.Popen(
        command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW
    ) as process:
        (base / f'{name}-owned.json').write_text(json.dumps({'pid': process.pid, 'port': port}), encoding='utf-8')
        try:
            deadline = time.monotonic() + 35
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f'{name} did not start (exit={process.poll()})')
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.15)
            yield url
        finally:
            if process.poll() is None:
                process.terminate()  # only the Popen child owned by this run
                process.wait(timeout=10)


def browser_run(base, url, name, env, probe_only, repetitions, cases):
    from playwright.sync_api import sync_playwright
    # The bundled Chromium avoids machine Edge profile/import policies.
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path(os.environ['LOCALAPPDATA']) / 'ms-playwright'))
    os.environ.update({name: env[name] for name in ('TEMP', 'TMP', 'TMPDIR')})
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(base / f'{name}-browser'), headless=True, env=env, timeout=25000,
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking',
                  '--disable-component-update', '--no-first-run'],
            service_workers='block', viewport={'width': 1280, 'height': 800})
        out_of_origin = []
        def route(request):
            if request.request.url.startswith(url):
                request.continue_()
            else:
                out_of_origin.append(1)
                request.abort()
        context.route('**/*', route)
        try:
            page = context.pages[0]
            page.goto(url, timeout=25000)
            if name == 'original':
                page.locator('flt-semantics-placeholder').evaluate('(e) => e.click()', timeout=30000)
            if probe_only:
                return probe(page, base, name)
            return measure(page, base, name, repetitions, cases)
        finally:
            context.close()
            (base / f'{name}-browser-network.json').write_text(
                json.dumps({'nonlocal_requests_blocked': len(out_of_origin)}), encoding='utf-8')
            if name == 'nice':
                with urllib.request.urlopen(url + '__p6_network', timeout=5) as response:
                    (base / 'nice-server-network.json').write_bytes(response.read())


def probe(page, base, name):
    tab = (lambda label: page.locator('flt-semantics[role=button]').filter(has_text=label).first
           if name == 'original' else page.get_by_test_id(label))
    for section, flet in (('learning', '学習'), ('library', 'ライブラリ')):
        tab(flet if name == 'original' else f'tab-{section}').click()
        page.wait_for_timeout(2200)
        page.screenshot(path=base / f'{name}-{section}.png', timeout=15000)
        (base / f'{name}-{section}.txt').write_text(
            page.locator('flt-semantics').evaluate_all('els => els.map(e=>[e.id,e.getAttribute("role"),'
                'e.getAttribute("aria-label"),e.textContent].join(" | ")).join("\\n")')
            if name == 'original' else page.locator('body').inner_text(), encoding='utf-8')


def measure(page, base, name, repetitions, cases):
    from PIL import Image

    original = name == 'original'
    records = {}
    samples = base / f'{name}-samples.json'
    page.set_default_timeout(9000)

    def click(locator):
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError('expected control is absent')
        page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)

    def tab(section):
        if original:
            label = {'learning': '学習', 'library': 'ライブラリ'}[section]
            return page.get_by_role('button', name=label, exact=True).first
        return page.get_by_test_id(f'tab-{section}')

    def paint():
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def elapsed(operation, action, ready):
        started = time.perf_counter_ns()
        action()
        ready()
        paint()
        records.setdefault(operation, []).append((time.perf_counter_ns() - started) / 1_000_000)
        samples.write_text(json.dumps(records, indent=2), encoding='utf-8')

    def image_ready(number, page_no=0):
        expected = color(number, page_no)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            payload = page.screenshot(timeout=5000, animations='disabled')
            with Image.open(io.BytesIO(payload)) as frame:
                # Large, unique colors embedded in the same fixture PNG in both apps.
                frame = frame.crop((0, 250, frame.width, frame.height)).resize((160, 68), Image.Resampling.NEAREST)
                if any(count >= 90 and pixel == expected for count, pixel in
                       frame.convert('RGB').getcolors(frame.width * frame.height)):
                    return
        (base / f'{name}-image-timeout.png').write_bytes(payload)
        raise RuntimeError(f'{name}: expected painted fixture {number}, page {page_no + 1}')

    def author(number):
        label = f'Bench Author {number:03d}'
        if original:
            return page.locator(f'flt-semantics[aria-label^="{label}"]')
        return page.get_by_test_id('library-authors-scroll').get_by_role('button', name=label, exact=True)

    def ready_library():
        author(0).first.wait_for(state='visible', timeout=30000)
        page.get_by_text('200 人の作者中 50 人を表示 / 全 200 人' if original else
                         '50 / 200 作者表示（全200作者）', exact=True).first.wait_for(timeout=30000)

    def first_author():
        return page.evaluate('''({original}) => {
          const selector = original ? 'flt-semantics[aria-label^="Bench Author "]' : '[data-testid=library-author-open]';
          return [...document.querySelectorAll(selector)].map(e => {
            const r = e.getBoundingClientRect();
            return r.bottom > (original ? 398 : 302) && r.top < 760 && r.height ?
              ((e.getAttribute('aria-label') || '').match(/Bench Author [0-9]{3}/) || [])[0] : null;
          }).find(Boolean) || '';
        }''', {'original': original})

    def scroll_changed(previous, down):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = first_author()
            if current and (current > previous if down else current < previous):
                return
            paint()
        raise RuntimeError(f'{name}: wheel did not change the first visible author ({previous})')

    def learning():
        tab('learning').click()
        stage = page if original else page.locator('.pv-learning')
        picture = stage.get_by_text('Bench Learn 800000', exact=True).first
        picture.wait_for()
        image_ready(800000)
        title_selector = ('flt-semantics' if original else '.pv-learning .pv-view-title')
        seen = {'Bench Learn 800000'}
        for _ in range(repetitions):
            def next_card():
                handle = page.wait_for_function('''({seen, selector}) => [...document.querySelectorAll(selector)]
                    .map(e => e.textContent.trim()).find(t => /^Bench Learn [0-9]{6}$/.test(t) && !seen.includes(t))''',
                    arg={'seen': sorted(seen), 'selector': title_selector}, timeout=9000)
                title = handle.json_value()
                number = int(title.rsplit(' ', 1)[-1])
                image_ready(number)
                seen.add(title)
            elapsed('learning', lambda: page.keyboard.press('3'), next_card)

    def library_display():
        tab('library').click()
        ready_library()
        # Warm tab revisit. Leaving via the learning tab uses the normal shell handler.
        for _ in range(repetitions):
            tab('learning').click()
            author(0).first.wait_for(state='hidden')
            elapsed('library_display', lambda: tab('library').click(), ready_library)

    def library_scroll():
        tab('library').click()
        ready_library()
        page.mouse.move(700, 620)
        for index in range(repetitions):
            previous = first_author()
            down = index % 2 == 0
            elapsed('library_scroll', lambda: page.mouse.wheel(0, 630 if down else -630),
                    lambda: scroll_changed(previous, down))
            # Allow wheel animation to settle before changing directions.
            page.wait_for_timeout(350)

    def field():
        if original:
            # CanvasKit creates a native edit field only after focusing the Flutter field.
            page.mouse.click(330, 332)
            return page.locator('flt-semantics input:visible, flt-text-editing-host input:visible').first
        return page.get_by_placeholder('タイトル・作者・タグを検索', exact=True)

    def library_search():
        tab('library').click()
        ready_library()
        for index in range(repetitions):
            number = 150 + index
            label = f'Bench Author {number:03d}'
            search = field()
            search.wait_for()
            elapsed('library_search', lambda: search.fill(label), lambda: author(number).first.wait_for())

    def viewer_next():
        # Start from the unfiltered author grid even after the search samples.
        page.reload(wait_until='load')
        if original:
            page.locator('flt-semantics-placeholder').evaluate('(e) => e.click()', timeout=30000)
        tab('library').click()
        ready_library()
        wanted = author(0).first
        wanted.wait_for()
        if original:
            click(wanted.locator('flt-semantics[role=button]').first)
            card = page.locator('flt-semantics[role=button]').filter(
                has_text='クリックで p.1 から画集ビューアを開きます').first
        else:
            page.get_by_role('button', name='Bench Author 000', exact=True).click()
            card = page.locator('[data-work-id="100000"][data-page-no="0"]').first
        card.click()
        image_ready(100000)
        for index in range(1, repetitions + 1):
            expected = f'{index + 1} / {EXTRA_PAGES + 1}' if original else f'{index + 1}/{EXTRA_PAGES + 1}'
            def next_page():
                page.get_by_text(expected, exact=True).first.wait_for()
                image_ready(100000, index)
            elapsed('viewer_next', lambda: page.keyboard.press('ArrowRight'), next_page)

    failures = {}
    for case, run in zip(CASES, (learning, library_display, library_scroll, library_search, viewer_next)):
        if case in cases:
            try:
                run()
            except Exception as exc:
                failures[case] = type(exc).__name__
                page.screenshot(path=base / f'{name}-{case}-failed.png', timeout=5000)
                if original:
                    selectors = 'els => els.map(e=>[e.getAttribute("role"),e.getAttribute("aria-label"),e.textContent.slice(0,90)])'
                    diagnostics = page.locator('flt-semantics').evaluate_all(selectors)
                else:
                    diagnostics = page.locator('body').inner_text()
                (base / f'{name}-{case}-failed.json').write_text(
                    json.dumps({'error': str(exc), 'browser': diagnostics}, ensure_ascii=False), encoding='utf-8')
    summary = {key: {'n': len(values), 'median_ms': statistics.median(values),
                     'p95_ms': sorted(values)[math.ceil(len(values) * .95) - 1]}
               for key, values in records.items()}
    (base / f'{name}-summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--serve-nice', action='store_true')
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--only', choices=('original', 'nice'))
    parser.add_argument('--repetitions', type=int, default=20)
    parser.add_argument('--case', choices=CASES, action='append')
    args = parser.parse_args()
    base = args.out.resolve()
    if base.drive.upper() != 'S:':
        parser.error('output must stay on S:')
    if not 20 <= args.repetitions <= EXTRA_PAGES:
        parser.error(f'repetitions must be 20–{EXTRA_PAGES}')
    if args.serve_nice:
        serve_nice(base)
        return
    base.mkdir(parents=True)
    cases, failures = args.case or CASES, {}
    env = isolated_environment(base)
    db_path = seed(base)
    original = base / 'original'
    original.mkdir()
    revision = original_copy(original) if args.only != 'nice' else None
    with sqlite3.connect(db_path) as db:
        sizes = {table: db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                 for table in ('works', 'following_users', 'discovery_swipe_history')}
    (base / 'fixture.json').write_text(json.dumps({'original_revision': revision,
        'nice_revision': git(ROOT, 'rev-parse', 'HEAD').decode().strip(), 'db_rows_initial': sizes,
        'library_images': WORKS + EXTRA_PAGES, 'learning_candidates': LEARNING, 'learning_images': LEARNING},
        indent=2), encoding='utf-8')
    if args.only != 'nice':
        shutil.copyfile(db_path, original / 'fixture.sqlite')
        shutil.copyfile(base / 'learning.json', original / 'learning.json')
        shutil.copytree(base / 'learning', original / 'learning')
        flet_env = dict(env, PIXIVVAULT_DB_PATH=str(original / 'fixture.sqlite'), PV_WEB_PORT=str(ORIGINAL_PORT),
                        PV_LOCK_PORT=str(ORIGINAL_PORT + 1), PV_SERVER_PORT=str(ORIGINAL_PORT + 2),
                        PV_NO_BROWSER_COOKIES='1', PV_NO_TRAY='1', FLET_SERVER_IP='127.0.0.1')
        with server(base, 'original', [str(ORIGINAL_PYTHON), '-B', str(ROOT / 'tools/original_run/p6_original.py')],
                    original, flet_env, ORIGINAL_PORT) as url:
            failures['original'] = browser_run(base, url, 'original', env, args.probe, args.repetitions, cases)
    if args.only != 'original':
        with server(base, 'nice', [sys.executable, '-B', __file__, '--out', str(base), '--serve-nice'],
                    ROOT, env, NICE_PORT) as url:
            failures['nice'] = browser_run(base, url, 'nice', env, args.probe, args.repetitions, cases)
    (base / 'browser-result.json').write_text(json.dumps(failures, indent=2), encoding='utf-8')
    return any(failures.values())


if __name__ == '__main__':
    raise SystemExit(main())
