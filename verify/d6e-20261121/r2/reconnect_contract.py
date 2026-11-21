"""Run the real gallery and Socket.IO client against synthetic loopback media."""

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure

PORT = 22742


def serve():
    from nicegui import app, json as nice_json, ui
    from nicegui.outbox import Outbox
    from PIL import Image
    from pixivvault_nicegui.media import DemoImageCatalog
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.models import Artwork
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.gallery import GalleryView
    from pixivvault_nicegui.ui.theme import CSS
    from pixivvault_nicegui.viewer_gif import DisplayedGif
    from tools.check_library_calendar_slider_owned import deny_external_connections

    network = deny_external_connections()
    works = (
        Artwork('41001', 'First work', 'First author', page_count=3, tags=('first',), author_id='321'),
        Artwork('41002', 'Second work', 'Second author', page_count=2, tags=('second',), author_id='322'),
        Artwork('41003', 'Animated work', 'Third author', tags=('animated',), author_id='323'),
    )
    rng = random.Random(1)
    frames = [Image.frombytes('P', (512, 512), rng.randbytes(512 * 512)) for _ in range(4)]
    grayscale = [value for value in range(256) for _ in range(3)]
    for frame in frames:
        frame.putpalette(grayscale)
    output = BytesIO()
    frames[0].save(output, format='GIF', save_all=True, append_images=frames[1:],
                   duration=100, loop=0, optimize=False)
    data = output.getvalue()
    assert len(data) > 1024 * 1024
    display = DisplayedGif(SimpleNamespace(remote_id='41003', author_id='323',
                           kind='illustration', illustration_type=2,
                           title='Animated work', author_name='Third author'),
                           data, hashlib.sha256(data).hexdigest())
    display.verified()

    class Images(DemoImageCatalog):
        def is_animation(self, work_id):
            return work_id == '41003'

        def display_animation(self, work_id):
            assert self.is_animation(work_id)
            return display

    services = ApplicationServices(3)
    services.artworks = works
    services.images = Images(works)
    register_media_routes(app, services.images)
    app.on_shutdown(services.close)
    stats = {'rewinds': [], 'viewer_messages': [], 'gif_bytes': len(data), 'network': network}
    gallery = None

    original_rewind = Outbox.try_rewind
    original_emit = Outbox._emit

    def rewind(self, target):
        if target < self.next_message_id:
            stats['rewinds'].append(self.next_message_id - target)
        return original_rewind(self, target)

    async def emit(self, message):
        await original_emit(self, message)
        if gallery is not None:
            _, kind, payload = message
            if kind == 'run_javascript' or (
                kind == 'update' and any(i in payload or str(i) in payload for i in
                                         (gallery.viewer.root.id, gallery.viewer.image.id, gallery.viewer.tags.id))
            ):
                stats['viewer_messages'].append([kind, len(nice_json.dumps(payload).encode('utf8'))])

    Outbox.try_rewind = rewind
    Outbox._emit = emit

    @ui.page('/', reconnect_timeout=30)
    def mount():
        nonlocal gallery
        ui.add_css(CSS)
        ui.add_head_html('<meta name="referrer" content="no-referrer">')
        gallery = GalleryView(services)

    @app.get('/__viewer_state')
    async def state():
        return {'connected': gallery.client.has_socket_connection,
                'title': gallery.viewer.title.text, **stats}

    @app.post('/__viewer_step/{name}')
    async def step(name: str):
        with gallery.client:
            if name == 'open':
                assert not gallery.client.has_socket_connection
                gallery.open('41001')
                gallery.viewer.open()
            elif name == 'next-work':
                gallery.move_work(1)
            elif name == 'tags':
                assert not gallery.client.has_socket_connection
                gallery.viewer.set_detail_values('Latest detail', ('latest-tag', '<literal>'))
            elif name == 'open-details':
                gallery.detail_open = True
                gallery.viewer.set_details_open(True)
            elif name == 'gif':
                stats['viewer_messages'].clear()
                gallery.move_work(1)
            else:
                raise ValueError(name)
        return {}

    ui.run(host='127.0.0.1', port=PORT, show=False, reload=False, native=False,
           binding_refresh_interval=None)


def exercise(page, url):
    from playwright.sync_api import expect

    def server_state():
        with urlopen(url + '__viewer_state', timeout=5) as response:
            return json.load(response)

    def step(name):
        with urlopen(Request(url + '__viewer_step/' + name, method='POST'), timeout=5) as response:
            assert response.status == 200

    def disconnect():
        page.evaluate('window.socket.disconnect()')
        page.wait_for_function(
            "async () => !(await (await fetch('/__viewer_state')).json()).connected")

    def reconnect():
        page.evaluate('''() => {
            window.socket.io.opts.query.next_message_id = window.nextMessageId;
            window.socket.connect();
        }''')
        page.wait_for_function('window.socket.connected && window.did_handshake')

    page.goto(url)
    page.wait_for_function('window.did_handshake')
    opener = page.locator('[data-work-id="41001"]')
    opener.focus()
    viewer = page.get_by_test_id('gallery-viewer')
    viewport = viewer.get_by_test_id('viewer-zoom-viewport')
    title = viewer.locator('.pv-view-title')
    counter = viewer.locator('.pv-counter')
    image = viewer.get_by_test_id('viewer-image')
    tags = viewer.get_by_test_id('viewer-detail-tags')
    results = {}

    disconnect()
    step('open')
    reconnect()
    expect(viewer).to_be_visible()
    expect(image).to_have_attribute('aria-busy', 'false')
    results['zoom_installed_after_offline_open'] = viewport.evaluate('el => !!el.__pvZoomController')
    results['viewer_focused_after_offline_open'] = viewer.evaluate('el => document.activeElement === el')
    viewer.focus()
    page.keyboard.press('ArrowRight')
    expect(counter).to_have_text('2/3')
    expect(image).to_have_attribute('aria-busy', 'false')

    page.evaluate('''() => {
        const socket = window.socket;
        const receive = socket.onevent.bind(socket);
        socket.onevent = packet => {
            if (window.__holdUpdates && packet.data?.[1]?._id !== undefined) {
                window.__missedUpdate = true;
                return;
            }
            receive(packet);
        };
        window.__holdUpdates = true;
    }''')
    step('next-work')
    page.wait_for_function('window.__missedUpdate')
    disconnect()
    step('tags')
    page.evaluate('window.__holdUpdates = false')
    reconnect()
    expect(title).to_have_text('Second work')
    expect(counter).to_have_text('1/2')
    expect(image).to_have_attribute('aria-busy', 'false')
    rows = tags.locator('div').all_text_contents()
    results['latest_tags_after_rewind'] = rows == ['latest-tag', '<literal>']
    results['rewinds'] = server_state()['rewinds']
    results['same_document'] = page.evaluate('window.__missedUpdate === true')

    step('open-details')
    details = viewer.get_by_test_id('viewer-detail-panel')
    expect(details).to_be_visible()
    viewer.focus()
    page.keyboard.press('Escape')
    expect(details).to_be_hidden()
    expect(viewer).to_be_visible()

    step('gif')
    expect(title).to_have_text('Animated work', timeout=10000)
    expect(image).to_have_attribute('aria-busy', 'false', timeout=10000)
    displayed = image.locator('img[data-pv-displayed=true]')
    expect(displayed).to_be_visible()
    source = displayed.get_attribute('src')
    gif = re.search(r'/media/viewer-gif/[0-9a-f]+\.gif$', source)
    results['gif_local_http'] = bool(gif)
    results['gif_wire'] = server_state()['viewer_messages']
    results['gif_bytes'] = server_state()['gif_bytes']
    if gif:
        response = page.request.get(url + source.lstrip('/'))
        results['gif_response'] = (response.status, response.headers['content-type'], len(response.body()))

    viewer.focus()
    page.keyboard.press('Escape')
    expect(viewer).to_be_hidden()
    results['opener_focus_restored'] = opener.evaluate('el => document.activeElement === el')
    if gif:
        results['retired_gif_status'] = page.request.get(url + source.lstrip('/')).status
    results['server_network'] = server_state()['network']
    return results


def run(base):
    from playwright.sync_api import sync_playwright

    base.mkdir(parents=True)
    env = measure.isolated_environment(base)
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path(os.environ['LOCALAPPDATA']) / 'ms-playwright'))
    with measure.server(base, 'reconnect', [sys.executable, '-B', __file__, '--serve'],
                        measure.ROOT, env, PORT) as url, sync_playwright() as playwright:
        driver = playwright._impl_obj._connection._transport._proc
        context = playwright.chromium.launch_persistent_context(
            str(base / 'profile'), headless=True, env=env, viewport={'width': 1280, 'height': 800},
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking',
                  '--disable-component-update', '--no-first-run'], service_workers='block')
        blocked = []

        def restrict(route):
            if route.request.url.startswith(url):
                route.continue_()
            else:
                blocked.append(1)
                route.abort()

        context.route('**/*', restrict)
        page = context.pages[0]
        errors = []
        page.on('pageerror', lambda error: errors.append(error.name))
        try:
            measure.save_browser_pids(base, 'reconnect', driver.pid, 'startup')
            results = exercise(page, url)
        finally:
            try:
                measure.save_browser_pids(base, 'reconnect', driver.pid, 'shutdown')
            finally:
                context.close()
    results['browser_errors'] = errors
    results['browser_network'] = len(blocked)
    (base / 'reconnect.json').write_text(json.dumps(results, indent=2), encoding='utf8')
    assert results['zoom_installed_after_offline_open']
    assert results['viewer_focused_after_offline_open']
    assert results['latest_tags_after_rewind'] and results['same_document'] and results['rewinds']
    assert results['gif_local_http'] and results['gif_response'] == (200, 'image/gif', results['gif_bytes'])
    assert max(size for _, size in results['gif_wire']) < 16 * 1024
    assert results['opener_focus_restored'] and results['retired_gif_status'] == 404
    assert not errors and not blocked


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        run(args.out.resolve())
