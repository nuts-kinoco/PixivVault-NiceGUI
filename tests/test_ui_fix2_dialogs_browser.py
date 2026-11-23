"""Exercise the real confirmation views with an offline browser fixture."""

import os
import socket
import subprocess
import sys
from contextlib import suppress
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

import pytest


ROUTE = '/ui-fix2-browser'


def serve(scratch, port):
    from nicegui import app, ui
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.ui.native_profile_view import NativeProfileView
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    from pixivvault_nicegui.ui.premium_gate_view import PremiumGateDialog
    from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
    from pixivvault_nicegui.ui.stop_migration_view import StopMigrationView
    from pixivvault_nicegui.ui.stop_recovery_view import StopRecoveryPanel
    from pixivvault_nicegui.ui.request_status_view import RequestStatusView
    from pixivvault_nicegui.ui.theme import CSS

    @app.post(ROUTE + '/shutdown')
    async def shutdown():
        app.shutdown()

    @ui.page(ROUTE)
    def view(theme: str = 'light', export_error: bool = False, initialized: bool = False,
             stops: str = ''):
        ui.add_css(CSS)
        ui.dark_mode(theme == 'dark')

        def export_metadata():
            if export_error:
                raise OSError('synthetic export failure')
            return SimpleNamespace(path=scratch / 'synthetic.zip', works=0)

        gate = SimpleNamespace(
            startup_suppressed=False, status=lambda: dict(enabled=False, remembered=False, retry_after=0),
            begin_prompt=lambda: 1, end_prompt=lambda *_args, **_kwargs: None,
            vault=SimpleNamespace(status=lambda: dict(initialized=initialized)),
        )
        premium = PremiumGateDialog(gate, default_uid_path=None)
        migration = StopMigrationView(None)
        profile = NativeProfileView(NativeProfileStore(scratch / 'profile'))
        if stops:
            important = dict(phase='stopped', status=500, attempt=3, limit=3, notice_id=1)
            if stops == 'pinned':
                important.update(status=401, stop_cause='durable_gate')
            if stops == 'failed':
                important.update(phase='failed', status=404)
            transient = [dict(important, phase='cancelled', notice_id=i) for i in range(2, 5)]
            records = [important]
            if stops in ('overflow', 'failed'):
                records.extend(transient)
            if stops == 'replacement':
                records.extend(dict(important, status=code, notice_id=i)
                               for i, code in enumerate((501, 502, 503), 2))
            policy = SimpleNamespace(progress_snapshot=lambda: records)
            RequestStatusView(policy)
            profile.request_retry_policy = policy
        preferences = PreferencesView(PreferencesStore(scratch / 'settings'),
                                      desktop=SimpleNamespace(protocol=object()),
                                      export_metadata=export_metadata,
                                      native_profile=profile if stops else None,
                                      enabled_keys={'meta_backfill_enabled', 'novel_save_format'})
        SharedGetRecoveryView(SimpleNamespace(preview_uncertain_get_recovery=lambda: object()))
        StopRecoveryPanel(SimpleNamespace(create_migration_view=lambda: migration))
        ui.button('借用プレミアム', on_click=premium.request_enable).props('data-testid=ui-fix2-open-premium')
        ui.button('通信結果', on_click=premium.open_recovery).props('data-testid=ui-fix2-open-communication')
        ui.button('正式移行', on_click=migration.open).props('data-testid=ui-fix2-open-migration')
        ui.button('起動と保存先', on_click=profile.open).props('data-testid=ui-fix2-open-profile')
        ui.button('設定', on_click=preferences.open).props('data-testid=ui-fix2-open-preferences')
        ui.context.client.on_delete(preferences.close)

    ui.run(host='127.0.0.1', port=port, show=False, reload=False, show_welcome_message=False,
           timeout_graceful_shutdown=5)


@pytest.fixture(scope='module')
def dialog_browser(tmp_path_factory):
    if Path(r'S:\Work\_tmp\PV\sup\D6_QUIET.flag').exists():
        pytest.skip('D6 quiet window')
    playwright = pytest.importorskip('playwright.sync_api')
    scratch = tmp_path_factory.mktemp('ui-fix2-browser')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    browser_env = os.environ | {'LOCALAPPDATA': str(scratch / 'local'), 'APPDATA': str(scratch / 'roaming')}
    server_env = os.environ | {'NICEGUI_STORAGE_PATH': str(scratch / 'storage'), 'NICEGUI_SCREEN_TEST_PORT': str(port),
                               'PYTHONUNBUFFERED': '1'}
    with (scratch / 'server.log').open('wb') as log:
        server = subprocess.Popen([sys.executable, '-B', __file__, str(scratch), str(port)], env=server_env,
                                  cwd=scratch, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = monotonic() + 60
        while True:
            assert server.poll() is None and monotonic() < deadline, 'offline dialog server did not start'
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.5):
                    break
            except OSError:
                sleep(.05)
        origin = f'http://127.0.0.1:{port}'
        with playwright.sync_playwright() as runtime:
            context = runtime.chromium.launch_persistent_context(
                str(scratch / 'chromium'), headless=True, env=browser_env,
                viewport={'width': 1280, 'height': 800}, service_workers='block',
                args=['--disable-background-networking', '--disable-crash-reporter', '--no-proxy-server',
                      '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
            )
            try:
                context.route('**/*', lambda request: request.continue_()
                              if request.request.url.startswith(origin + '/') else request.abort())
                yield playwright.expect, context, origin + ROUTE
            finally:
                context.close()
    finally:
        if server.poll() is None:
            connection = HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                with suppress(OSError):
                    connection.request('POST', ROUTE + '/shutdown')
                    with connection.getresponse() as response:
                        response.read()
            finally:
                connection.close()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.terminate()
            server.wait(timeout=15)
            raise


@pytest.fixture
def dialog_page(dialog_browser):
    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        yield page, expect
    finally:
        page.close()


def test_borrow_activation_focus_and_escape(dialog_page):
    page, expect = dialog_page
    opener = page.get_by_test_id('ui-fix2-open-premium')
    opener.press('Enter')
    password = page.get_by_test_id('premium-activation-password')
    expect(password).to_be_focused()
    modal = page.get_by_role('dialog', name='借用プレミアムをONにする')
    expect(modal).to_be_visible()
    expect(modal.get_by_test_id('premium-setup-guidance')).to_be_visible()
    expect(modal.get_by_label('Shaft 署名キー（初期設定）')).to_be_visible()
    page.keyboard.press('Escape')
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()
    opener.click()
    expect(password).to_be_focused()
    modal.get_by_role('button', name='キャンセル').click()
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()


def test_borrow_setup_names_the_uid_picker(dialog_page):
    page, expect = dialog_page
    page.get_by_test_id('ui-fix2-open-premium').press('Enter')
    upload = page.get_by_test_id('premium-uid-upload')
    picker = upload.get_by_role('button', name='UID一覧のテキストファイルを選ぶ')
    expect(picker).to_be_visible()
    with page.expect_file_chooser() as chooser:
        picker.press('Enter')
    chooser.value.set_files(dict(name='fixture.txt', mimeType='text/plain', buffer=b'\n'))
    expect(upload.get_by_text('fixture.txt', exact=True)).to_be_visible()
    expect(picker).to_be_visible()


@pytest.mark.parametrize('initialized', [False, True])
def test_borrow_missing_uid_status_matches_setup(dialog_browser, initialized):
    expect, context, url = dialog_browser
    with context.new_page() as page:
        page.goto(f'{url}?initialized={str(initialized).lower()}', wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        page.get_by_test_id('ui-fix2-open-premium').press('Enter')
        status = page.get_by_test_id('premium-uid-count')
        if initialized:
            expect(status).to_have_text('UID一覧: 未読み込み（変更しません）')
        else:
            expect(status).to_have_text('UID一覧: 未読み込み（初期設定に必要です）')


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_export_failure_remains_in_modal_and_closes_by_keyboard(dialog_browser, theme):
    expect, context, url = dialog_browser
    with context.new_page() as page:
        page.goto(f'{url}?theme={theme}&export_error=true', wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        page.get_by_test_id('ui-fix2-open-preferences').click()
        modal = page.get_by_test_id('preferences-dialog')
        export = modal.get_by_test_id('preferences-export-metadata')
        export.click()
        alert = modal.locator('.pv-dialog-alert').filter(has_text='エクスポートに失敗しました。')
        expect(alert.get_by_role('alert')).to_be_visible()
        # Wait beyond the previous Quasar notification timeout.
        page.wait_for_timeout(8200)
        expect(alert).to_be_visible()
        close = alert.get_by_role('button', name='閉じる')
        export.focus()
        for _ in range(8):
            page.keyboard.press('Tab')
            if close.evaluate('e => e === document.activeElement'):
                break
        expect(close).to_be_focused()
        page.keyboard.press('Enter')
        expect(alert).to_be_hidden()
        expect(modal).to_be_visible()
        expect(export).to_be_focused()
        expect(modal.get_by_test_id('preferences-export-status')).to_contain_text('[失敗]')


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize(('opener', 'control'), [
    ('ui-fix2-open-preferences', 'preference-meta_backfill_enabled'),
    ('ui-fix2-open-migration', 'migration-confirm'),
    ('shared-get-recovery-open', 'shared-get-recovery-ack'),
])
def test_settings_control_hit_and_rendered_ring(dialog_browser, theme, opener, control):
    from PIL import Image
    from tools.uiux_audit import contrast

    expect, context, url = dialog_browser
    with context.new_page() as page:
        page.goto(f'{url}?theme={theme}', wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        page.get_by_test_id(opener).press('Enter')
        target = page.get_by_test_id(control)
        expect(target).to_be_enabled()
        target.evaluate('async e => Promise.all(e.closest(".q-dialog").getAnimations({subtree:true})'
                        '.map(animation => animation.finished))')
        target.evaluate("e => e.scrollIntoView({block:'center'})")
        page.keyboard.press('Tab')
        target.focus()
        assert target.evaluate('e => e.matches(":focus-visible")')
        box = target.bounding_box()
        x, y = round(box['x'] + box['width'] / 2), box['y']
        bitmap = Image.open(BytesIO(page.screenshot(scale='css'))).convert('RGB')
        # The shared shadow draws a two-pixel ring outside a two-pixel inner gap.
        ring = bitmap.getpixel((x, int(y - 3)))
        gap = bitmap.getpixel((x, int(y - 1)))
        ratio = contrast(ring, gap)
        assert ratio >= 3, (ring, gap, target.evaluate('e => getComputedStyle(e).boxShadow'))
        overlaps = target.evaluate('''e => {
            const hitBox = control => {
                const r = control.getBoundingClientRect();
                const x = control.matches('.pv-hit28') ? Math.max(0, (28 - r.width) / 2) : 0;
                const y = control.matches('.pv-hit28') ? Math.max(0, (28 - r.height) / 2) : 0;
                return [r.left - x, r.top - y, r.right + x, r.bottom + y];
            };
            const [left, top, right, bottom] = hitBox(e);
            return [...e.closest('.q-dialog').querySelectorAll('button, [role=checkbox], [role=switch], [role=combobox]')]
                .filter(other => other !== e && !e.contains(other) && !other.contains(e)
                    && !other.matches(':disabled,.disabled,[aria-disabled=true]')
                    && other.checkVisibility({visibilityProperty:true, opacityProperty:true}))
                .filter(other => {
                    const [x1, y1, x2, y2] = hitBox(other);
                    return Math.min(right, x2) > Math.max(left, x1) && Math.min(bottom, y2) > Math.max(top, y1);
                }).map(other => other.getAttribute('data-testid') || other.getAttribute('role') || other.tagName);
        }''')
        assert overlaps == []
        before = target.get_attribute('aria-checked')
        for offset in (-13.5, 13.5):
            point = {'x': x, 'y': y + box['height'] / 2 + offset}
            assert target.evaluate('(e, p) => e.contains(document.elementFromPoint(p.x, p.y))', point)
        point = {'x': x, 'y': y + box['height'] / 2 - 13.5}
        page.mouse.click(point['x'], point['y'])
        expect(target).to_have_attribute('aria-checked', 'false' if before == 'true' else 'true')
        print(f'{control} {theme}: rendered ring {ratio:.3f}:1, adjacent overlaps 0, pointer activated')


def test_migration_focus_and_escape_without_executing(dialog_page):
    page, expect = dialog_page
    opener = page.get_by_test_id('ui-fix2-open-migration')
    opener.press('Enter')
    modal = page.get_by_test_id('stop-migration-dialog')
    expect(modal).to_have_accessible_name('停止履歴を引き継ぐ')
    expect(modal.get_by_role('heading', name='停止履歴を引き継ぐ')).to_be_focused()
    expect(modal.get_by_test_id('migration-execute')).to_be_disabled()
    page.keyboard.press('Escape')
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()


def test_migration_progress_label_is_readable(dialog_page):
    from test_visual_preference_browser import _CONTRAST

    page, _ = dialog_page
    page.get_by_test_id('ui-fix2-open-migration').click()
    label = page.get_by_role('progressbar').locator('.absolute-center')
    assert label.evaluate(_CONTRAST) >= 4.5


def test_settings_switch_accepts_pointer_in_28_pixel_target(dialog_page):
    page, expect = dialog_page
    page.get_by_test_id('ui-fix2-open-preferences').click()
    switch = page.get_by_test_id('preference-meta_backfill_enabled')
    switch.scroll_into_view_if_needed()
    box = switch.bounding_box()
    hit = {'x': box['x'] + 10, 'y': box['y'] + box['height'] / 2 - 13}
    assert switch.evaluate('(e, p) => e.contains(document.elementFromPoint(p.x, p.y))', hit)
    before = switch.get_attribute('aria-checked')
    page.mouse.click(hit['x'], hit['y'])
    expect(switch).to_have_attribute('aria-checked', 'false' if before == 'true' else 'true')


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_disabled_settings_text_is_readable(dialog_browser, theme):
    from tools.uiux_audit import contrast, scan

    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(f'{url}?theme={theme}', wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        page.get_by_test_id('ui-fix2-open-preferences').click()
        card = page.locator('.pv-preferences-card')
        expect(card).to_be_visible()
        card.evaluate('async e => Promise.all(e.closest(".q-dialog").getAnimations({ subtree: true })'
                      '.map(animation => animation.finished))')
        disabled_text = [node for node in scan(page, root='.pv-preferences-card')['nodes']
                         if node['state'] == 'disabled' and node['text'] and node.get('foreground')]
        assert len(disabled_text) > 10
        low_contrast = {node['selector']: round(ratio, 3) for node in disabled_text
                        if (ratio := contrast(node['foreground'], node['background'])) < 4.5}
        assert not low_contrast
    finally:
        page.close()


def test_settings_restores_pointer_opener_after_escape(dialog_page):
    page, expect = dialog_page
    opener = page.get_by_test_id('ui-fix2-open-preferences')
    opener.click()
    modal = page.get_by_role('dialog', name='設定')
    expect(modal.get_by_role('heading', name='設定', exact=True)).to_be_focused()
    page.keyboard.press('Tab')
    page.keyboard.press('Escape')
    expect(modal).to_be_hidden()
    expect(opener).to_be_focused()


def test_settings_export_success_notification_has_readable_text(dialog_page):
    from test_visual_preference_browser import _CONTRAST

    page, expect = dialog_page
    page.get_by_test_id('ui-fix2-open-preferences').click()
    page.get_by_test_id('preferences-export-metadata').click()
    notice = page.locator('.q-notification').get_by_text('エクスポートが完了しました。')
    expect(notice).to_be_visible()
    notice.evaluate('async e => Promise.all(e.closest(".q-notification").getAnimations({ subtree: true })'
                    '.map(animation => animation.finished))')
    assert notice.evaluate(_CONTRAST) >= 4.5


@pytest.mark.parametrize(('opener_id', 'dialog_id'), [
    ('ui-fix2-open-communication', 'premium-recovery-dialog'),
    ('ui-fix2-open-profile', 'native-profile-dialog'),
    ('shared-get-recovery-open', 'shared-get-recovery-dialog'),
    ('stop-recovery-open', 'stop-recovery-dialog'),
    ('stop-recovery-migration', 'stop-migration-dialog'),
    ('preferences-uri', 'preferences-uri-dialog'),
])
def test_confirmation_restores_pointer_opener(dialog_page, opener_id, dialog_id):
    page, expect = dialog_page
    if opener_id == 'stop-recovery-migration':
        page.get_by_test_id('stop-recovery-open').click()
    if opener_id == 'preferences-uri':
        page.get_by_test_id('ui-fix2-open-preferences').click()
        page.get_by_role('button', name='ブラウザ拡張機能').click()
    opener = page.get_by_test_id(opener_id)
    opener.click()
    dialog = page.get_by_test_id(dialog_id)
    expect(dialog).to_be_visible()
    expect(dialog.locator(':focus')).to_be_visible()
    expect(opener).to_be_enabled()
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


@pytest.mark.parametrize(('name', 'dialog_id', 'select_id'), [
    ('migration', 'stop-migration-dialog', 'migration-kind'),
    ('profile', 'native-profile-dialog', 'native-gpu-provider'),
    ('preferences', 'preferences-dialog', 'preference-novel_save_format'),
])
def test_escape_dismisses_select_before_its_dialog(dialog_page, name, dialog_id, select_id):
    page, expect = dialog_page
    opener = page.get_by_test_id(f'ui-fix2-open-{name}')
    opener.press('Enter')
    dialog = page.get_by_test_id(dialog_id)
    expect(dialog).to_be_visible()
    if name == 'profile':
        dialog.get_by_role('button', name='画像解析・GPU').click()
    choice = dialog.get_by_test_id(select_id).get_by_role('combobox')
    choice.press('ArrowDown')
    popup = page.get_by_role('listbox')
    expect(popup).to_be_visible()
    page.keyboard.press('Escape')
    expect(popup).to_be_hidden()
    expect(choice).to_be_focused()
    expect(dialog).to_be_visible()
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


@pytest.mark.parametrize('theme', ('light', 'dark'))
def test_settings_tab_cycles_and_escape_restores_opener(dialog_browser, theme):
    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(f'{url}?theme={theme}', wait_until='domcontentloaded')
        page.wait_for_function('window.did_handshake === true')
        opener = page.get_by_test_id('ui-fix2-open-preferences')
        opener.click()
        modal = page.get_by_test_id('preferences-dialog')
        heading = modal.get_by_role('heading', name='設定')
        expect(heading).to_be_focused()

        page.keyboard.press('Shift+Tab')
        assert modal.evaluate('e => e.contains(window.lastSettingsFocus = document.activeElement)'), (
            'Shift+Tab from the heading escaped the settings dialog')
        page.keyboard.press('Tab')
        assert modal.evaluate('e => e.contains(window.firstSettingsFocus = document.activeElement)')
        for _ in range(120):
            page.keyboard.press('Tab')
            assert modal.evaluate('e => e.contains(document.activeElement)'), 'Tab escaped the settings dialog'
            if page.evaluate('document.activeElement === window.firstSettingsFocus'):
                break
        else:
            pytest.fail('Tab never returned to the first settings control')

        page.keyboard.press('Shift+Tab')
        assert modal.evaluate('e => e.contains(document.activeElement)'), 'Shift+Tab escaped the settings dialog'
        assert page.evaluate('document.activeElement === window.lastSettingsFocus')
        # When an explanatory heading in the middle has focus, the next Tab
        # and Shift+Tab must select the modal endpoints, not its neighbours.
        probe = modal.get_by_role('button', name='ブラウザ拡張機能')
        probe.evaluate("e => { e.tabIndex = -1; e.focus() }")
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement === window.firstSettingsFocus')
        probe.focus()
        page.keyboard.press('Shift+Tab')
        assert page.evaluate('document.activeElement === window.lastSettingsFocus')
        page.keyboard.press('Tab')
        assert page.evaluate('document.activeElement === window.firstSettingsFocus')
        page.keyboard.press('Escape')
        expect(modal).to_be_hidden()
        expect(opener).to_be_focused()
    finally:
        page.close()


@pytest.mark.parametrize(('opener_id', 'dialog_id'), [
    ('ui-fix2-open-communication', 'premium-recovery-dialog'),
    ('ui-fix2-open-profile', 'native-profile-dialog'),
    ('shared-get-recovery-open', 'shared-get-recovery-dialog'),
    ('stop-recovery-open', 'stop-recovery-dialog'),
    ('ui-fix2-open-migration', 'stop-migration-dialog'),
    ('preferences-uri', 'preferences-uri-dialog'),
])
def test_confirmation_focus_cycles_from_initial_focus(dialog_page, opener_id, dialog_id):
    page, expect = dialog_page
    if opener_id == 'preferences-uri':
        page.get_by_test_id('ui-fix2-open-preferences').press('Enter')
        page.get_by_role('button', name='ブラウザ拡張機能').click()
    opener = page.get_by_test_id(opener_id)
    opener.click()
    dialog = page.get_by_test_id(dialog_id)
    expect(dialog).to_be_visible()
    expect(dialog.locator(':focus')).to_be_visible()
    assert dialog.evaluate('e => e.contains(window.initialDialogFocus = document.activeElement)')
    page.keyboard.press('Shift+Tab')
    assert dialog.evaluate('e => e.contains(window.lastDialogFocus = document.activeElement)')
    page.keyboard.press('Tab')
    assert dialog.evaluate('e => e.contains(window.firstDialogFocus = document.activeElement)')
    if dialog.evaluate('e => window.initialDialogFocus.tabIndex === -1'):
        assert page.evaluate('window.initialDialogFocus !== window.firstDialogFocus')
    page.keyboard.press('Shift+Tab')
    assert page.evaluate('document.activeElement === window.lastDialogFocus')
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


@pytest.mark.parametrize('location', ('header', 'profile', 'settings'))
@pytest.mark.parametrize('case', ('one', 'pinned'))
def test_request_notice_restores_focus(dialog_browser, location, case):
    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(url + '?stops=' + case)
        page.wait_for_function('window.did_handshake === true')
        opener = page.get_by_test_id('ui-fix2-open-profile')
        dialog = page.get_by_test_id('native-profile-dialog')
        if location == 'settings':
            opener = page.get_by_test_id('ui-fix2-open-preferences')
            dialog = page.get_by_test_id('preferences-dialog')
        if location != 'header':
            opener.press('Enter')
            expect(dialog).to_be_visible()
        scope = page if location == 'header' else dialog
        close = scope.get_by_role('button', name='取得の通知を閉じる').first
        expect(close).to_be_visible()
        close.press('Space' if case == 'pinned' else 'Enter')
        expect(close).to_be_hidden()
        if case == 'pinned':
            expect(scope.get_by_text('確認済み', exact=False).first).to_be_visible()
        if location != 'header':
            expect(dialog.locator(':focus')).to_be_visible()
        else:
            expect(page.locator('button:focus')).to_be_visible()
        assert page.evaluate('document.activeElement !== document.body')
    finally:
        page.close()


def test_request_notice_keeps_focused_close_for_next_rendered_stop(dialog_browser):
    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(url + '?stops=replacement')
        page.wait_for_function('window.did_handshake === true')
        close = page.get_by_test_id('request-retry-dismiss')
        for _ in range(15):
            page.keyboard.press('Tab')
            if close.evaluate('e => e === document.activeElement'):
                break
        expect(close).to_be_focused()
        page.keyboard.press('Enter')
        expect(page.get_by_test_id('request-retry-status')).to_contain_text('HTTP 500')
        expect(close).to_be_focused()
        page.keyboard.press('Space')
        expect(close).to_be_hidden()
        expect(page.locator('button:focus')).to_be_visible()
    finally:
        page.close()


@pytest.mark.parametrize(('case', 'expected'), [('overflow', 'HTTP 500'), ('failed', 'HTTP 404')])
def test_request_notice_priority_and_distinct_live_regions(dialog_browser, case, expected):
    expect, context, url = dialog_browser
    page = context.new_page()
    try:
        page.goto(url + '?stops=' + case)
        page.wait_for_function('window.did_handshake === true')
        page.get_by_test_id('ui-fix2-open-profile').press('Enter')
        expect(page.get_by_test_id('request-retry-status')).to_have_count(1)
        expect(page.get_by_test_id('native-request-retry-status')).to_have_count(1)
        for testid in ('request-retry-status', 'native-request-retry-status'):
            status = page.get_by_test_id(testid)
            expect(status).to_contain_text(expected)
            expect(status).to_have_attribute('role', 'alert')
    finally:
        page.close()


if __name__ == '__main__':
    serve(Path(sys.argv[1]), int(sys.argv[2]))
