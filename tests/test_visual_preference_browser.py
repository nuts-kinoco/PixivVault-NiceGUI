"""Keyboard, focus and contrast checks on the real settings panel."""
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace

import pytest


QUIET = Path(r'S:\Work\_tmp\PV\sup\D6_QUIET.flag')


class QuietWindow(Exception):
    pass


def test_visual_settings_dialog_keyboard_and_contrast(tmp_path):
    if QUIET.is_file():
        pytest.skip('D6 quiet window')
    pytest.importorskip('playwright.sync_api')
    # The assembled UI test owns a NiceGUI app, event loop and routing table.
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).resolve()), str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
        env=os.environ | {'NICEGUI_SCREEN_TEST_PORT': '0', 'PYTHONUTF8': '1',
                          'NICEGUI_STORAGE_PATH': str(tmp_path / 'nicegui'),
                          'TEMP': str(tmp_path), 'TMP': str(tmp_path)},
    )
    if result.returncode == 75:
        pytest.skip('D6 quiet window')
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_visual_settings(tmp_path):
    from playwright import sync_api as playwright
    from nicegui import app, ui
    from nicegui.server import Server
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    from pixivvault_nicegui.ui.theme import CSS

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    worker.ranking_finalized(lambda: [{'work_id': 'before'}])
    worker.flush_events()
    route = '/visual-preference-browser-acceptance'

    @ui.page(route)
    def settings():
        ui.add_css(CSS)
        theme = ui.dark_mode(True)
        view = PreferencesView(services.preferences, enabled_keys=services.preference_consumers(),
                               visual_service=services)
        ui.button('設定', on_click=view.open).props('data-testid=open-settings')
        ui.button('ライト', on_click=theme.disable).props('data-testid=light-theme')
        ui.context.client.on_delete(view.close)

    previous_server = getattr(Server, 'instance', None)
    server = Thread(target=lambda: ui.run(host='127.0.0.1', port=0, show=False, reload=False,
                                          show_welcome_message=False))
    started = False
    try:
        if QUIET.is_file():
            raise QuietWindow
        server.start()
        started = True
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
            if QUIET.is_file():
                raise QuietWindow
            context = runtime.chromium.launch_persistent_context(
                str(tmp_path / 'browser'), channel='msedge', headless=True,
                args=['--disable-background-networking', '--no-proxy-server',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
                viewport={'width': 1280, 'height': 800})
            try:
                context.route('**/*', lambda req: req.continue_() if req.request.url.startswith(origin + '/') else req.abort())
                context.route_web_socket('**/*', lambda route: route.connect_to_server()
                                         if route.url.startswith('ws' + origin[4:] + '/') else route.close())
                page = context.pages[0]
                page.goto(origin + route)
                for mode in ('dark', 'light'):
                    if mode == 'light':
                        page.get_by_test_id('light-theme').click()
                    page.get_by_test_id('open-settings').click()
                    status = page.get_by_test_id('preferences-visual-status')
                    clear = page.get_by_test_id('preferences-visual-clear')
                    playwright.expect(status).to_have_attribute('role', 'status')
                    playwright.expect(status).to_contain_text('追加 GET 0 件')
                    start = page.get_by_test_id('preferences-visual-start')
                    playwright.expect(start).to_be_enabled()
                    for element in (status, start, clear):
                        assert element.evaluate(_CONTRAST) >= 4.5
                    clear.focus()
                    page.keyboard.press('Enter')
                    modal = page.get_by_test_id('preferences-visual-clear-dialog')
                    playwright.expect(modal).to_be_visible()
                    cancel = page.get_by_test_id('preferences-visual-clear-cancel')
                    playwright.expect(cancel).to_be_focused()
                    page.keyboard.press('Shift+Tab')
                    playwright.expect(page.get_by_test_id('preferences-visual-clear-confirm')).to_be_focused()
                    page.keyboard.press('Tab')
                    playwright.expect(cancel).to_be_focused()
                    page.keyboard.press('Escape')
                    playwright.expect(modal).to_be_hidden()
                    playwright.expect(clear).to_be_focused()
                    assert worker.store.events()
                    if mode == 'dark':
                        page.get_by_test_id('preferences-close-top').click()
                    else:
                        clear.click()
                        page.get_by_test_id('preferences-visual-clear-confirm').click()
                        playwright.expect(status).to_contain_text('一時停止中')
                        assert services.visual_preference_worker.store.events() == ()
            finally:
                context.close()
    finally:
        active_server = getattr(Server, 'instance', None)
        if started and active_server is not None and active_server is not previous_server:
            active_server.should_exit = True
            server.join(10)
            assert not server.is_alive()
        app.remove_route(route)
        services.close()


_CONTRAST = r"""element => {
    const channels = value => value.match(/[\d.]+/g).map(Number);
    const style = getComputedStyle(element);
    const foreground = channels(style.color);
    const layers = [];
    for (let node = element; node; node = node.parentElement) {
        const rgba = channels(getComputedStyle(node).backgroundColor);
        layers.push(rgba);
        if ((rgba[3] ?? 1) === 1) break;
    }
    const background = layers.reverse().reduce((under, [r, g, b, a = 1]) =>
        [r, g, b].map((value, i) => value * a + under[i] * (1 - a)), [255, 255, 255]);
    const luminance = color => color.slice(0, 3).map(value => value / 255)
        .map(x => x <= .04045 ? x / 12.92 : ((x + .055) / 1.055) ** 2.4)
        .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
    const values = [luminance(foreground), luminance(background)];
    return (Math.max(...values) + .05) / (Math.min(...values) + .05);
}"""


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.check_library_calendar_slider_owned import deny_external_connections

    deny_external_connections()
    try:
        _exercise_visual_settings(Path(sys.argv[1]))
    except QuietWindow:
        sys.exit(75)
