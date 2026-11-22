"""Chromium acceptance for the real recommendation view with a fake transport."""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from test_recommendation_visual import _publish_fixture_model, _work
from test_gpuarb_remote_media import png


def test_recommendation_lazy_media_and_dismissals_with_actual_work7_settings(tmp_path):
    quiet = Path(r'S:\Work\_tmp\PV\sup\D6_QUIET.flag')
    if quiet.is_file():
        pytest.skip('D6 quiet window')
    pytest.importorskip('playwright.sync_api')
    # Run with a fresh NiceGUI singleton, independent of the other suites'
    # loopback server and application lifecycle.
    env = os.environ.copy()
    env.update(TEMP=str(tmp_path), TMP=str(tmp_path),
               NICEGUI_STORAGE_PATH=str(tmp_path / 'nicegui'), NICEGUI_SCREEN_TEST_PORT='0')
    result = subprocess.run([sys.executable, '-B', __file__, str(tmp_path)],
                            env=env, capture_output=True, text=True, errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def _check_browser(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.check_library_calendar_slider_owned import deny_external_connections

    deny_external_connections()
    from nicegui import app, ui
    from nicegui.server import Server
    from playwright import sync_api as playwright
    from pixivvault_nicegui.media_routes import register_media_routes
    from pixivvault_nicegui.pixiv_read_client import PublicImageReadClient
    from pixivvault_nicegui.recommendation_state import RecommendationController, RecommendationGroup, RecommendationPreferences, RecommendationSnapshot
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.recommend_view import RecommendView
    from pixivvault_nicegui.ui.theme import CSS

    sent = []
    image = png()

    def transport(request, *_):
        sent.append((request.get_method(), request.full_url))
        return image

    reader = PublicImageReadClient(transport=transport)
    reader._verified_identity = '101'
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'unused.onnx'
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    assert worker is not None
    worker._check_running = lambda **_: True
    works = (replace(_work(1), title='長い作品名' * 30), *(_work(n) for n in range(2, 7)))
    source = SimpleNamespace(load=lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=()))
    services.recommendations = RecommendationController(source, RecommendationPreferences(tmp_path / 'shelf'))
    routes = list(app.router.routes)
    register_media_routes(app, services.images)
    route = '/recommendation-visual-browser-acceptance'
    views = []

    def configure(enabled):
        services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1' if enabled else '0'))
        if enabled:
            _publish_fixture_model(worker.store, worker._model_sha)

    @ui.page(route)
    async def page():
        ui.add_css(CSS)
        view = RecommendView(services)
        views.append(view)
        view.active = True
        await view.refresh()

        async def reshow(enabled):
            await asyncio.to_thread(configure, enabled)
            await view.refresh()

        ui.button('ON', on_click=lambda: reshow(True)).props('data-testid=reco-test-on')
        ui.button('OFF', on_click=lambda: reshow(False)).props('data-testid=reco-test-off')

    previous_server = getattr(Server, 'instance', None)
    server = Thread(target=lambda: ui.run(host='127.0.0.1', port=0, show=False, reload=False,
                                          show_welcome_message=False))
    server.start()
    try:
        deadline = monotonic() + 10
        while server.is_alive():
            active_server = getattr(Server, 'instance', None)
            if active_server is not previous_server and active_server is not None and active_server.started:
                break
            assert monotonic() < deadline, 'test web server did not start'
            sleep(0.05)
        assert server.is_alive()
        port = Server.instance.servers[0].sockets[0].getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        with playwright.sync_playwright() as runtime:
            context = runtime.chromium.launch_persistent_context(
                str(tmp_path / 'browser'), channel='msedge', headless=True,
                args=['--disable-background-networking'], viewport={'width': 1280, 'height': 800})
            try:
                context.route('**/*', lambda request: request.continue_()
                              if request.request.url.startswith(origin + '/') else request.abort())
                browser = context.pages[0]
                browser.goto(origin + route)
                cards = browser.locator('[data-testid^="recommend-work-certain-"]')

                def wait_for_order(ids):
                    browser.wait_for_function("""ids => Array.from(
                        document.querySelectorAll('[data-testid^="recommend-work-certain-"]'),
                        card => card.dataset.testid.split('-').at(-1)).join('') === ids""", arg=ids)
                    for index in range(len(ids)):
                        thumbnail = cards.nth(index).locator('img')
                        playwright.expect(thumbnail).to_have_js_property('complete', True)
                        assert thumbnail.evaluate('img => img.naturalWidth') > 0

                wait_for_order('123456')
                original_requests = tuple(sent)
                assert len(original_requests) == 6
                browser.get_by_test_id('reco-test-on').click()
                wait_for_order('234561')
                projected = browser.get_by_test_id('recommend-thumb-certain-2')
                assert projected.evaluate('node => getComputedStyle(node).backgroundImage').startswith('url("data:image/png;base64,')
                old_image = projected.locator('img')
                assert old_image.evaluate('img => getComputedStyle(img).opacity') == '0'
                assert old_image.evaluate("img => img.dispatchEvent(new DragEvent('dragstart', {bubbles: true, cancelable: true}))") is False

                dialog = browser.get_by_test_id('recommend-hide-reason-dialog')
                hide = browser.get_by_test_id('recommend-work-certain-2').get_by_role('button', name='出さない', exact=True)
                for dismiss in (lambda: browser.keyboard.press('Escape'),
                                lambda: dialog.locator('.q-dialog__backdrop').click(position={'x': 10, 'y': 10}),
                                lambda: dialog.get_by_role('button', name='キャンセル').click()):
                    hide.click()
                    playwright.expect(dialog).to_be_visible()
                    dismiss()
                    playwright.expect(dialog).to_be_hidden()
                    deadline = monotonic() + 5
                    while views[-1]._pending_hide is not None:
                        assert monotonic() < deadline, 'the server did not clear the dismissed dialog'
                        sleep(0.01)

                browser.get_by_test_id('reco-test-off').click()
                wait_for_order('123456')
                assert tuple(sent) == original_requests
                assert browser.get_by_test_id('recommend-thumb-certain-2').evaluate(
                    'node => getComputedStyle(node).backgroundImage') == 'none'
            finally:
                context.close()
    finally:
        active_server = getattr(Server, 'instance', None)
        if active_server is not None and active_server is not previous_server:
            active_server.should_exit = True
        server.join(10)
        assert not server.is_alive()
        app.router.routes[:] = routes
        services.close()


if __name__ == '__main__':
    _check_browser(Path(sys.argv[1]))
