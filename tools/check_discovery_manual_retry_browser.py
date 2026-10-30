"""D01 bounded synthetic GUI acceptance: stopped acquisition -> failed click -> retry.

Owns a disposable fixture and one loopback server. No real Cookie, network GET,
legacy source or existing data roots are opened. This is *partial* D01 evidence:
the exhausted 20-round state is injected; elapsed 30-second intervals are not tested.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import traceback
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools.run_browser_checks import child_environment, free_port, port_released, source_hashes, wait_for_health


def serve(port: int, fixture: Path, nonce: str) -> None:
    from fastapi import Header, HTTPException, Response
    from nicegui import app, ui
    from PIL import Image
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.learning_view import LearningView

    fixture = fixture.resolve(strict=True)
    if not fixture.is_relative_to(ROOT / 'artifacts') or not 1024 <= port <= 65535:
        raise ValueError('candidate-local fixture and high loopback port required')

    def only_local(event, args):
        if event == 'socket.connect':
            address = args[1]
            host = address[0] if isinstance(address, tuple) else None
            if host not in ('127.0.0.1', '::1', 'localhost'):
                raise RuntimeError('external socket prohibited')
        if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1', '::1', 'localhost', None):
            raise RuntimeError('external DNS prohibited')
    sys.addaudithook(only_local)

    register_pages()
    class Reader:
        allow = False
        ranking_calls = 0

        def require_identity(self):
            return None

        def get_ranking(self, **_):
            self.ranking_calls += 1
            if not self.allow:
                raise RuntimeError('injected offline ranking failure')
            return [dict(illust_id=str(index), title=f'Generated {index}', user_id='99',
                         user_name='Generated author', url=f'https://i.pximg.net/generated/{index}.jpg',
                         tags=['generated']) for index in range(1, 5)]

        def get_recommended_users(self, *_args, **_kwargs):
            return {'users': [], 'works': []}

        def search_recommendation_illusts(self, *_args, **_kwargs):
            return {'works': []}

    reader = Reader()
    services = ApplicationServices(count=4)
    services.discovery = DiscoveryLearningService(fixture / 'synthetic-discovery', reader)
    live = {'view': None}
    png = BytesIO()
    Image.new('RGB', (320, 240), (40, 80, 120)).save(png, 'PNG')

    class Pin:
        def url(self, work_id, page_no=0, variant='view'):
            return f'/fixture-art/{work_id}/{page_no}/{variant}.png'

        def close(self):
            return None

    services.acquire_ranking_images = lambda records: Pin()

    @app.get('/fixture-art/{work_id}/{page_no}/{variant}.png')
    def generated_image(work_id: str, page_no: int, variant: str):
        return Response(png.getvalue(), media_type='image/png')

    @app.get('/_retry/state')
    def state():
        view = live['view']
        return dict(calls=reader.ranking_calls, allow=reader.allow,
                    initial_done=bool(view and view._initial_done),
                    ready=bool(view and view._storage_ready),
                    candidates=len(view.state.artworks) if view else 0,
                    gave_up=bool(view and view._refill.gave_up))

    @app.post('/_retry/stop')
    def stop(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        view = live['view']
        if view is None or not view._initial_done or not view._storage_ready:
            raise HTTPException(409, 'initial acquisition not settled')
        view._stop_candidate_refill()
        view._refill.armed = True
        view._refill.active = True
        view._refill.gave_up = True
        view._refill.failed_rounds = 20
        view._render_refill_status()
        return {'gave_up': True}

    @app.post('/_retry/allow')
    def allow(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        reader.allow = True
        return {'allow': True}

    @app.post('/_retry/shutdown')
    def shutdown(x_acceptance: str | None = Header(default=None)):
        if not secrets.compare_digest(x_acceptance or '', nonce):
            raise HTTPException(403)
        app.shutdown()
        return {'stopping': True}

    @ui.page('/manual-retry')
    def retry_page():
        view = LearningView(services)
        live['view'] = view
        view.set_active(True)

    app.on_shutdown(services.discovery.close)
    app.on_shutdown(services.close)
    ui.run(host='127.0.0.1', port=port, show=False, reload=False,
           binding_refresh_interval=None)


def run(output: Path, channel: str) -> int:
    from playwright.sync_api import expect, sync_playwright

    output = output.resolve()
    allowed = (ROOT / 'artifacts').resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        raise ValueError('fresh dedicated candidate output required')
    output.mkdir(parents=True)
    before = source_hashes()
    checker_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    port = free_port()
    nonce = secrets.token_hex(24)
    origin = f'http://127.0.0.1:{port}'
    report = dict(success=False, synthetic_only=True, initial_zero=False,
                  explicit_failure=False, explicit_recovered=False,
                  channel=channel, checker_sha256=checker_hash,
                  source_before=before, external_requests=[], page_errors=[],
                  console_errors=[], forced_cleanup=False, normal_exit=False,
                  port_released=False, server_pid=None)
    process = None
    opener = build_opener(ProxyHandler({}))
    try:
        with (output / 'server.log').open('xb') as stream:
            process = subprocess.Popen(
                [sys.executable, '-B', str(Path(__file__).resolve()), '--serve',
                 '--port', str(port), '--fixture', str(output), '--nonce', nonce],
                cwd=ROOT, env=child_environment(), stdin=subprocess.DEVNULL,
                stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            report['server_pid'] = process.pid
        wait_for_health(origin, process)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=None if channel == 'chromium' else channel)
            context = browser.new_context(viewport=dict(width=1280, height=720))
            def guard(route):
                parsed = urlsplit(route.request.url)
                if (parsed.scheme, parsed.netloc) != ('http', f'127.0.0.1:{port}'):
                    report['external_requests'].append('blocked')
                    route.abort()
                else:
                    route.continue_()
            context.route('**/*', guard)
            page = context.new_page()
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.on('console', lambda message: report['console_errors'].append(message.text)
                    if message.type == 'error' else None)
            try:
                page.goto(origin + '/manual-retry', wait_until='domcontentloaded')
                button = page.get_by_test_id('discovery-fetch')
                status = page.get_by_test_id('discovery-refill-status')
                expect(button).to_be_visible()
                page.wait_for_function("""async origin => {
                    const r = await fetch(origin + '/_retry/state');
                    const state = await r.json();
                    return state.initial_done && state.ready && state.calls >= 1;
                }""", arg=origin, timeout=20000)
                def read_state():
                    return page.request.get(origin + '/_retry/state').json()
                def post(path):
                    response = page.request.post(origin + path,
                        headers={'X-Acceptance': nonce})
                    assert response.status == 200, (path, response.status)
                    return response.json()
                post('/_retry/stop')
                expect(status).to_contain_text('自動補充は停止しています')
                assert read_state()['candidates'] == 0
                report['initial_zero'] = True
                before_fail = read_state()['calls']
                button.click()
                expect(page.get_by_text('候補を取得できませんでした。再試行できます。')).to_be_visible(timeout=15000)
                assert read_state()['calls'] > before_fail
                report['explicit_failure'] = True
                post('/_retry/stop')
                post('/_retry/allow')
                before_retry = read_state()['calls']
                button.click()
                # The successful source merge replaces the empty-state notice;
                # assert the durable accepted candidate rather than a fleeting label.
                page.wait_for_function("""async origin => {
                    const r = await fetch(origin + '/_retry/state');
                    const state = await r.json();
                    return state.candidates > 0;
                }""", arg=origin, timeout=20000)
                page.wait_for_function("""() => {
                    const img = document.querySelector('.pv-view-art img');
                    return img && img.complete && img.naturalWidth > 0;
                }""", timeout=20000)
                for choice in ('見送る', '合わない', 'いいね', 'SuperLike'):
                    expect(page.get_by_role('button', name=choice, exact=True)).to_be_enabled()
                end = read_state()
                assert end['candidates'] >= 1 and end['calls'] > before_retry
                report['explicit_recovered'] = True
                report['ranking_calls'] = end['calls']
                page.screenshot(path=str(output / 'recovered-synthetic.png'), animations='disabled')
            finally:
                context.close()
                browser.close()
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()[-4000:]
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    request = Request(origin + '/_retry/shutdown', data=b'',
                                      headers={'X-Acceptance': nonce}, method='POST')
                    with opener.open(request, timeout=5) as response:
                        report['shutdown_status'] = response.status
                except Exception as error:
                    report['shutdown_error'] = type(error).__name__
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    report['forced_cleanup'] = True
                    process.terminate()
                    process.wait(timeout=5)
            report['normal_exit'] = process.returncode == 0 and not report['forced_cleanup']
        report['port_released'] = port_released(port)
        report['source_after'] = source_hashes()
        report['source_changed'] = report['source_before'] != report['source_after']
        report['success'] = bool(report['initial_zero'] and report['explicit_failure']
                                 and report['explicit_recovered'] and report['normal_exit']
                                 and report['port_released'] and not report['external_requests']
                                 and not report['page_errors'] and not report['console_errors']
                                 and not report['source_changed'])
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in (
        'success', 'initial_zero', 'explicit_failure', 'explicit_recovered',
        'normal_exit', 'port_released', 'source_changed')}, ensure_ascii=False))
    return 0 if report['success'] else 1


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--serve', action='store_true')
    cli.add_argument('--port', type=int)
    cli.add_argument('--fixture', type=Path)
    cli.add_argument('--nonce')
    cli.add_argument('--output', type=Path)
    cli.add_argument('--channel', choices=('msedge', 'chromium'), default='chromium')
    args = cli.parse_args()
    if args.serve:
        if args.port is None or args.fixture is None or not args.nonce:
            cli.error('--serve needs --port --fixture --nonce')
        serve(args.port, args.fixture, args.nonce)
        return 0
    if args.output is None:
        cli.error('--output required')
    return run(args.output, args.channel)


if __name__ == '__main__':
    raise SystemExit(main())
