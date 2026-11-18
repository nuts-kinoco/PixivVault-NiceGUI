"""Browser keyboard regression on a loopback-only, synthetic download screen."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright


def serve(port, root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.check_library_calendar_slider_owned import deny_external_connections
    deny_external_connections()
    from nicegui import ui
    from pixivvault_nicegui.ui.theme import CSS as theme_css
    from pixivvault_nicegui.ui.unified_download_view import UnifiedDownloadView
    from test_w_dl_screen_20261118 import FakeServices, author_rows

    services = FakeServices(Path(root), author_rows(3))
    @ui.page('/')
    async def page():
        ui.add_css(theme_css)
        view = UnifiedDownloadView(services)
        await view.reload()
    ui.run(host='127.0.0.1', port=port, show=False, reload=False, binding_refresh_interval=None)


def check_keyboard(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.check_library_calendar_slider_owned import deny_external_connections
    deny_external_connections()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(TEMP=str(tmp_path), TMP=str(tmp_path), NICEGUI_STORAGE_PATH=str(tmp_path / '.nicegui'),
               PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTEST_CURRENT_TEST', None)
    with (tmp_path / 'server.log').open('wb') as log:
        proc = subprocess.Popen([sys.executable, '-B', __file__, str(port), str(tmp_path)],
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            url = f'http://127.0.0.1:{port}/'
            deadline = time.monotonic() + 40
            while True:
                if proc.poll() is not None:
                    raise AssertionError('synthetic UI server exited; see server.log')
                try:
                    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=1).close()
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.2)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page()
                page.goto(url)
                row = page.locator('[data-testid=unified-author-row]').first
                header = page.locator('[data-testid=unified-header-check]')
                star = row.locator('[data-testid=unified-row-star]')
                row.wait_for()
                for element in (header, row, star):
                    assert element.get_attribute('tabindex') == '0'
                header.focus()
                page.keyboard.press('Tab')
                expect(row).to_be_focused()
                assert row.evaluate("e => getComputedStyle(e).outlineStyle") != 'none'
                page.keyboard.press('Space')
                expect(row).to_have_attribute('data-picked', 'true')
                page.keyboard.press('Enter')
                expect(row).to_have_attribute('data-picked', 'false')
                page.keyboard.press('Tab')
                expect(star).to_be_focused()
                before = star.get_attribute('data-on')
                page.keyboard.press('Space')
                expect(star).to_have_attribute('data-on', 'false' if before == 'true' else 'true')
                assert row.get_attribute('data-picked') == 'false'
                page.keyboard.press('Enter')
                expect(star).to_have_attribute('data-on', before)
                header.focus()
                page.keyboard.press('Space')
                expect(header).to_have_attribute('data-state', 'all')
                page.keyboard.press('Enter')
                expect(header).to_have_attribute('data-state', 'empty')
                browser.close()
        finally:
            proc.terminate()  # only the server spawned by this test
            proc.wait(timeout=20)


def test_keyboard_selection_and_star(tmp_path):
    env = os.environ.copy()
    env.update(TEMP=str(tmp_path), TMP=str(tmp_path), PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTEST_CURRENT_TEST', None)
    result = subprocess.run([sys.executable, '-B', __file__, '--check', str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == '__main__':
    if sys.argv[1] == '--check':
        check_keyboard(Path(sys.argv[2]))
    else:
        serve(int(sys.argv[1]), sys.argv[2])
