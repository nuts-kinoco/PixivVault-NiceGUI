"""Separate image retries, preference rebuild, grid payload and image observation."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import socket
from statistics import median
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure

FIRST = '[data-testid=ranking-view] [data-work-id="40000"] img'
FRAMES = '() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))'


def serve(root, port, retry):
    from nicegui import app, json as nicejson
    from fastapi.responses import Response
    from pixivvault_nicegui.ui.card_grid import CardGrid
    from pixivvault_nicegui.ui.ranking_view import RankingView

    measure.PORT = port

    @app.middleware('http')
    async def fixture_failures(request, call_next):
        if request.url.path.startswith('/media/400') and not request.url.path.startswith('/media/40000/'):
            return Response(status_code=503, headers={'Cache-Control': 'no-store'})
        return await call_next(request)

    def log(kind, start, **values):
        with (root / 'probe.jsonl').open('a', encoding='ascii') as stream:
            stream.write(json.dumps(dict(kind=kind, start=start, ms=(time.perf_counter() - start) * 1000,
                                         **values)) + '\n')

    rebuild = RankingView._rebuild_preferred

    def timed_preferences(self):
        start = time.perf_counter()
        try:
            return rebuild(self)
        finally:
            log('preferences', start)

    RankingView._rebuild_preferred = timed_preferences
    to_dict = CardGrid._to_dict

    def timed_payload(self):
        value = to_dict(self)
        if value.get('props', {}).get('cards'):
            # Sample the production serializer and the actual emitted element.
            start = time.perf_counter()
            encoded = nicejson.dumps(value)
            log('grid', start, bytes=len(encoded.encode('utf-8')))
        return value

    CardGrid._to_dict = timed_payload
    if not retry:
        CardGrid.retry_failed_images = lambda self: None
    measure.serve(root)


def observe_roundtrips(page, extra):
    start = time.perf_counter()
    page.wait_for_function('s => {const i=document.querySelector(s);return i?.complete && i.naturalWidth>0}', arg=FIRST)
    if extra:
        page.evaluate('s => document.querySelector(s).decode()', FIRST)
        page.evaluate(FRAMES)
    else:
        page.evaluate('async s => {await document.querySelector(s).decode(); await (' + FRAMES + ')()}', FIRST)
    return (time.perf_counter() - start) * 1000


def run(root, retry):
    from playwright.sync_api import sync_playwright

    root.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    profile = Path(os.environ['TEMP']) / ('r2-profile-' + root.name)
    profile.mkdir()
    env = dict(os.environ, USERPROFILE=str(profile), APPDATA=str(profile), LOCALAPPDATA=str(profile),
               NICEGUI_STORAGE_PATH=str(profile / '.nicegui'), PYTHONDONTWRITEBYTECODE='1')
    with (root / 'server.log').open('wb') as output:
        args = [sys.executable, '-B', __file__, str(root), '--serve', str(port)]
        if not retry:
            args.append('--no-retry')
        proc = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, env=env)
        try:
            deadline = time.monotonic() + 20
            while True:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('isolated server unavailable')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as runner:
                browser = runner.chromium.launch_persistent_context(
                    str(profile / 'chrome'), viewport={'width': 1280, 'height': 800},
                    headless=True, env=env, args=['--disable-background-networking',
                        '--proxy-server=http://127.0.0.1:1'], service_workers='block')
                try:
                    browser.route('**/*', lambda route: route.continue_()
                                  if route.request.url.startswith(url + '/') else route.abort())
                    page = browser.pages[0]
                    page.goto(url + '/', wait_until='domcontentloaded')
                    page.wait_for_function('window.did_handshake === true')
                    button = page.get_by_test_id('ranking-fetch')
                    page.evaluate('''() => {
                        const notice = document.querySelector('[data-testid=ranking-notice]');
                        const mark = () => {
                            if (notice.textContent.includes('取得中')) window.loading = true;
                            if (window.loading && notice.textContent.includes('50 件')) window.finished = true;
                        };
                        new MutationObserver(mark).observe(notice, {subtree:true, childList:true, characterData:true});
                    }''')
                    responses = Counter()
                    page.on('response', lambda response: responses.update([response.status])
                            if urlsplit(response.url).path.startswith('/media/') else None)
                    rows = []
                    for index in range(6):
                        page.evaluate('() => {window.finished = window.loading = false}')
                        responses.clear()
                        start = time.perf_counter()
                        button.click()
                        page.wait_for_function('window.finished === true')
                        page.evaluate(FRAMES)
                        end = time.perf_counter()
                        times = {}
                        for extra in ((True, False) if index % 2 else (False, True)):
                            times['three_rtt_ms' if extra else 'two_rtt_ms'] = observe_roundtrips(page, extra)
                        page.wait_for_timeout(375)
                        trace = [json.loads(line) for line in (root / 'probe.jsonl').read_text(encoding='ascii').splitlines()]
                        events = [event for event in trace if start <= event['start'] <= time.perf_counter()]
                        if index:
                            rows.append(dict(total_ms=(end - start) * 1000, retries_503=responses[503],
                                preference_ms=sum(e['ms'] for e in events if e['kind'] == 'preferences'),
                                preference_calls=sum(e['kind'] == 'preferences' for e in events),
                                grid_bytes=sum(e['bytes'] for e in events if e['kind'] == 'grid'),
                                grid_serialization_ms=sum(e['ms'] for e in events if e['kind'] == 'grid'), **times))
                    summary = {key: round(median(row[key] for row in rows), 3) for key in rows[0]}
                    (root / 'result.json').write_text(json.dumps(dict(samples=rows, median=summary), indent=2),
                                                       encoding='ascii')
                finally:
                    browser.close()
        finally:
            proc.terminate()
            code = proc.wait(timeout=15)
            (root / 'owned-server.json').write_text(json.dumps(dict(pid=proc.pid, exit=code, port=port)),
                                                       encoding='ascii')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--serve', type=int)
    parser.add_argument('--no-retry', action='store_true')
    options = parser.parse_args()
    if options.serve:
        serve(options.root, options.serve, not options.no_retry)
    else:
        run(options.root, not options.no_retry)
