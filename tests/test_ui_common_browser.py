"""Loopback browser checks and measurement probe for the shared UI."""
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys

import pytest
from PIL import Image

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import p5_design_evidence as p5, uiux_audit as audit, uiux_fixture
from tools.ui_tone_evidence import navigate_extra
from tools.uiux_rules import contrast


THEMES = ('light', 'dark')
TOAST_TYPES = ('negative', 'warning', 'positive', 'info')
SIZES = ((1024, 768), (1280, 800), (1920, 1080))


def control(name):
    return f'[data-testid="{name}"]'


# Product order for these fixtures. Date pickers and the embedded download
# form contain their own variable lists of stops. Keep their place fixed
# between surrounding commands, and verify their stops in both directions.
PRODUCT_TAB_ORDER = {
    'ranking-date': (control('ranking-date-cancel'), (control('ranking-date-picker') + ' :is(button,[tabindex])',),
                     control('ranking-date-today')),
    'view-history': (control('view-history-close'), (control('view-history-row') + ' :is(button,[tabindex])',),
                     control('view-history-danger') + ' [role=button]', control('view-history-export')),
    'download-details': (control('unified-details-close'), ('.q-card :is(input,button,[role=button],[role=combobox],a[href])',)),
    'dedup-trash': (control('duplicate-trash-close'), control('duplicate-trash-details') + ' [role=button]'),
    'queue-delete-confirm': ('button:has-text("キャンセル")', control('queue-bulk-delete-confirm')),
    'failed-delete-confirm': (control('failed-remove-cancel'), control('failed-remove-confirm')),
    'library-date-from': (control('library-date-cancel'), (control('library-date-picker') + ' :is(button,[tabindex])',)),
    'library-date-to': (control('library-date-cancel'), (control('library-date-picker') + ' :is(button,[tabindex])',)),
    'hidden-ranking': (control('ranking-hidden-close'),),
}

COMPONENT_TAB_ORDER = {
    'learning-reset': (control('learning-reset-confirm'), control('learning-reset-export'),
                       control('learning-reset-close')),
    'similar': tuple(map(control, ('similar-close', 'similar-retry', 'similar-analyze'))),
    'novel': tuple(map(control, ('novel-reader-next', 'novel-reader-close'))),
    'ugoira': tuple(map(control, ('ugoira-play-toggle', 'ugoira-player-close'))),
    'unfollowed': ('button:has-text("閉じる")', '[role=combobox][aria-label="媒体"]',
                   '[role=combobox][aria-label="ブックマーク"]', control('unfollowed-fetch')),
    'account-switch': tuple(map(control, ('account-switch-cancel', 'account-switch-confirm'))),
}
RACE_TAB_ORDER = tuple(map(control, ('race-first', 'race-save', 'race-close')))


def test_shared_log_audit_does_not_pair_with_a_queue_log():
    cells = audit.plan(['shared-log', 'shared-log-collapsed'], ['normal'])
    assert cells and all(cell.original is None for cell in cells)


@pytest.fixture(scope='module')
def browser_fixture(tmp_path_factory):
    if audit.QUIET.is_file():
        pytest.skip('D6 quiet window')
    playwright = pytest.importorskip('playwright.sync_api')
    work = tmp_path_factory.mktemp('common-ui-browser')
    original = work / 'original-fixture'
    original.mkdir()
    profile = work / 'port'
    p5.seed_profile(profile, original, 'normal', tone_mode=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    nonce = secrets.token_urlsafe(24)
    with p5.running_app('port', sys.executable, profile, port, nonce, tone_mode=True,
                        personal_fixture=True, server_script=Path(__file__).resolve(),
                        extra_env={'PV_UIUX_STATE': 'normal'}):
        with playwright.sync_playwright() as runner:
            yield runner, f'http://127.0.0.1:{port}'


@pytest.fixture(scope='module')
def zoom_page(browser_fixture, request, tmp_path_factory):
    runner, origin = browser_fixture
    if audit.QUIET.is_file():
        pytest.skip('D6 quiet window')
    zoom = getattr(request, 'param', 100)
    work = tmp_path_factory.mktemp('common-ui-edge')
    context = runner.chromium.launch_persistent_context(
        audit.browser_profile(work, zoom), channel='msedge', headless=True,
        accept_downloads=False, service_workers='block', reduced_motion='reduce',
        env=os.environ | {'LOCALAPPDATA': str(work / 'local'), 'APPDATA': str(work / 'roaming')},
        args=['--disable-gpu', '--disable-background-networking', '--no-proxy-server',
              '--disable-crash-reporter', '--no-first-run',
              '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
    )
    try:
        context.route('**/*', lambda route: route.continue_()
                      if route.request.url.startswith(origin + '/') else route.abort())
        context.route_web_socket('**/*', lambda route: route.connect_to_server()
                                 if route.url.startswith('ws' + origin[4:] + '/') else route.close())
        if zoom == 200:
            audit.set_browser_zoom(context, zoom)
        yield context.pages[0], origin, zoom
    finally:
        context.close()


def record(tmp_path, name, data):
    (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def screenshot(page, tmp_path, name):
    if os.environ.get('PV_COMMON_UI_CAPTURE') == '1':
        page.screenshot(path=tmp_path / (name + '.png'), animations='disabled')


def load(page, origin, path):
    page.goto(origin + path, wait_until='load')
    page.wait_for_function('window.socket?.connected && window.did_handshake')


def focus_with_tab(page, button, limit=12):
    order = []
    for _ in range(limit):
        page.keyboard.press('Tab')
        order.append(page.evaluate("document.activeElement?.getAttribute('data-testid')"))
        if button.count() and button.evaluate('e => document.activeElement === e'):
            return True, order
    return False, order


def measure_close(button):
    data = button.evaluate('''element => {
        const style = getComputedStyle(element);
        const toast = element.closest('.pv-toast');
        return {text: style.color, ring: style.outlineColor, ringWidth: parseFloat(style.outlineWidth),
                ringStyle: style.outlineStyle, shadow: style.boxShadow,
                focusVisible: element.matches(':focus-visible'),
                background: getComputedStyle(toast).backgroundColor};
    }''')
    def rgb(color):
        return [float(channel) for channel in re.findall(r'[\d.]+', color)][:3]

    shadow = re.fullmatch(r'(rgb\([^)]+\)) 0px 0px 0px ([\d.]+)px inset', data['shadow'])
    if shadow:
        data.update(ring=shadow[1], ringWidth=float(shadow[2]), ringStyle='inset')
    # Sample the painted button, where Quasar's focus helper can overlay the toast background.
    with Image.open(BytesIO(button.screenshot(animations='disabled'))) as screenshot:
        x = screenshot.width // 2
        background = screenshot.getpixel((x, 5))[:3]
        ring = max((screenshot.getpixel((x, y))[:3] for y in (0, 1)),
                   key=lambda pixel: contrast(pixel, background))
    data['buttonBackground'] = background
    data['ringPixel'] = ring
    data['textContrast'] = contrast(rgb(data['text']), background)
    data['ringContrast'] = contrast(ring, background)
    return data


def test_toast_close_contrast(zoom_page, tmp_path):
    page, origin, _ = zoom_page
    results = {}
    for theme in THEMES:
        load(page, origin, f'/_common-notice/{theme}')
        for kind in TOAST_TYPES:
            trigger = page.get_by_test_id(f'notify-{kind}')
            trigger.press('Enter')
            toast = page.locator(f'.q-notification.pv-toast-{kind}')
            close = toast.get_by_role('button', name='閉じる')
            close.wait_for(state='visible')
            reached, _ = focus_with_tab(page, close)
            result = measure_close(close)
            result['tabReached'] = reached
            results[f'{theme}-{kind}'] = result
            screenshot(page, tmp_path, f'toast-{theme}-{kind}')
            if reached:
                page.keyboard.press('Enter')
            else:
                close.click()
            toast.wait_for(state='hidden')
            result['focusReturned'] = trigger.evaluate('e => e === document.activeElement')
    record(tmp_path, 'toast-close.json', results)
    assert all(r['textContrast'] >= 4.5 and r['ringContrast'] >= 3
               and r['ringStyle'] in {'solid', 'inset'} and r['ringWidth'] >= 2
               and r['tabReached'] and r['focusVisible'] and r['focusReturned'] for r in results.values()), results


@pytest.mark.parametrize(('mode', 'destination'), (
    ('explicit', 'modal-error'), ('implicit', 'modal-error'),
    ('disabled', 'modal-first'), ('explicit-disabled', 'modal-first'), ('no-controls', 'dialog'),
))
def test_modal_notice_close_by_keyboard(zoom_page, tmp_path, mode, destination):
    page, origin, _ = zoom_page
    load(page, origin, f'/_common-notice/light?focus={mode}')
    opener = page.get_by_test_id('modal-open')
    opener.press('Enter')
    dialog = page.get_by_role('dialog')
    trigger = dialog.get_by_test_id('modal-error')
    trigger.wait_for()
    page.wait_for_function('''() => !document.querySelector('[role="dialog"] .q-dialog__inner')
        .getAnimations().some(animation => animation.playState === 'running')''')
    trigger.focus()
    trigger.press('Enter')
    close = page.get_by_role('button', name='閉じる', exact=True)
    close.wait_for(state='visible')
    reached, order = focus_with_tab(page, close)
    inline = dialog.get_by_role('alert').count() == 1
    screenshot(page, tmp_path, 'modal-notice')
    result = dict(tab_reached=reached, notice_inside_dialog=inline, tab_order=order,
                  expected_focus=destination)
    if reached:
        page.keyboard.press('Enter')
        close.wait_for(state='hidden')
        result['dialog_still_open'] = dialog.is_visible()
        target = dialog.locator('.q-dialog__inner') if destination == 'dialog' else dialog.get_by_test_id(destination)
        for _ in range(30):
            if target.evaluate('e => document.activeElement === e'):
                break
            page.wait_for_timeout(20)
        result['focus_returned'] = target.evaluate('e => document.activeElement === e')
        result['actual_focus'] = page.evaluate('''() => {
            const target = document.activeElement;
            if (target?.matches('.q-dialog__inner')) return 'dialog';
            return target?.dataset.testid || target?.getAttribute('role') || target?.tagName;
        }''')
        page.keyboard.press('Escape')
        dialog.wait_for(state='hidden')
        for _ in range(30):
            if opener.evaluate('e => e === document.activeElement'):
                break
            page.wait_for_timeout(20)
        result['focus_returned_to_opener'] = opener.evaluate('e => e === document.activeElement')
    record(tmp_path, 'modal-notice.json', result)
    assert reached and inline, result
    assert result['dialog_still_open'] and result['focus_returned'] and result['focus_returned_to_opener'], result


@pytest.mark.parametrize('theme', ('light', 'dark'))
def test_startup_learning_notice_restores_focus(zoom_page, theme):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_common-start-notice/{theme}')
    message = page.get_by_test_id('learning-toast-message')
    expect(message).to_have_attribute('role', 'alert')
    expect(message).to_contain_text('初回の候補を取得できませんでした')
    close = page.get_by_test_id('learning-toast-close')
    close.focus()
    page.keyboard.press('Enter')
    expect(message).to_be_hidden()
    expect(page.locator('.pv-learning')).to_be_focused()


@pytest.mark.parametrize('mode', ('disabled', 'hidden', 'preconnect'))
def test_nonmodal_notice_has_a_safe_fallback(zoom_page, mode):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_common-fallback-notice/{mode}')
    if mode != 'preconnect':
        page.get_by_test_id('notice-origin').press('Enter')
    close = page.get_by_role('button', name='閉じる', exact=True)
    expect(close).to_be_visible()
    close.press('Space')
    expect(close).to_be_hidden()
    expect(page.get_by_test_id('notice-fallback')).to_be_focused()


@pytest.mark.parametrize(('screen', 'dialog_id', 'close_id'), (
    ('ranking-date', 'ranking-date-dialog', 'ranking-date-cancel'),
    ('view-history', 'view-history-dialog', 'view-history-close'),
    ('download-details', 'unified-details-dialog', 'unified-details-close'),
    ('dedup-trash', 'duplicate-trash-dialog', 'duplicate-trash-close'),
    ('queue-delete-confirm', None, None),
    ('failed-delete-confirm', None, 'failed-remove-cancel'),
    ('library-date-from', 'library-date-dialog', 'library-date-cancel'),
    ('library-date-to', 'library-date-dialog', 'library-date-cancel'),
    ('hidden-ranking', 'ranking-hidden-dialog', 'ranking-hidden-close'),
))
def test_product_dialog_cycles_and_returns_focus(zoom_page, screen, dialog_id, close_id):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    cell = audit.Cell(screen, 'normal', 'light', 1280, 800, 100, None)
    audit.load_page(page, origin, cell)
    specs = p5.helpers.load_expectations()['screens']
    if screen.startswith('library-date-'):
        p5.navigate_port(page, 'library', specs, tone_mode=True)
        page.get_by_test_id('library-filter-toggle').click()
        field = screen.rsplit('-', 1)[-1]
        opener = page.get_by_test_id(f'library-date-{field}-calendar')
        expect(opener).to_be_visible()
        opener.focus()
        page.keyboard.press('Enter')
    elif screen == 'hidden-ranking':
        p5.navigate_port(page, 'ranking', specs, tone_mode=True)
        # Navigation queues focus on the selected tab for the next frame.
        # Complete that transition before starting the independent menu action.
        expect(page.get_by_test_id('tab-ranking')).to_have_attribute('aria-current', 'page')
        page.evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        opener = page.get_by_test_id('ranking-hide-manage')
        opener.focus()
        expect(opener).to_be_focused()
        page.keyboard.press('Enter')
        menu = page.get_by_test_id('ranking-hide-manage-menu')
        expect(menu).to_be_visible()
        menu.get_by_test_id('ranking-hide-manage-open').press('Enter')
    else:
        proven, opener = uiux_fixture.open_port(page, cell, specs)
        assert proven
    dialog = page.get_by_test_id(dialog_id) if dialog_id else page.get_by_role('dialog').filter(visible=True)
    expect(dialog).to_be_visible()
    expect(dialog.locator(':focus')).to_be_visible()
    walk_focus(page, dialog, PRODUCT_TAB_ORDER[screen])
    if close_id:
        dialog.get_by_test_id(close_id).press('Space')
    else:
        page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    try:
        expect(opener).to_be_focused()
    except AssertionError as failure:
        active = page.evaluate('document.activeElement.outerHTML.slice(0, 500)')
        raise AssertionError(f'Dialog focus returned to {active}') from failure


@pytest.mark.parametrize(('kind', 'opener_id', 'dialog_id', 'close_id'), (
    ('learning-reset', 'learning-reset-open', 'learning-reset-dialog', 'learning-reset-close'),
    ('similar', 'surface-opener', None, 'similar-close'),
    ('novel', 'surface-opener', 'novel-reader', 'novel-reader-close'),
    ('ugoira', 'surface-opener', 'ugoira-player', 'ugoira-player-close'),
    ('unfollowed', 'surface-opener', 'unfollowed-dialog', None),
    ('account-switch', 'surface-opener', 'account-switch-dialog', 'account-switch-cancel'),
))
def test_component_dialog_cycles_and_returns_focus(zoom_page, kind, opener_id, dialog_id, close_id):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_focus-surface/{kind}')
    if kind == 'novel':
        opener = page.get_by_role('button', name='読む', exact=True)
    elif kind == 'ugoira':
        opener = page.get_by_role('button', name='再生', exact=True)
    else:
        opener = page.get_by_test_id(opener_id)
    expect(opener).to_be_enabled()
    opener.press('Enter')
    dialog = page.get_by_test_id(dialog_id) if dialog_id else page.get_by_role('dialog').filter(visible=True)
    if kind in {'novel', 'ugoira'}:
        release = page.get_by_test_id('finish-fixture-read')
        expect(release).to_be_enabled()
        expect(dialog).to_be_hidden()
        release.focus()
        expect(release).to_be_focused()
        page.keyboard.press('Enter')
    expect(dialog).to_be_visible()
    page.wait_for_function('document.activeElement.closest(".q-dialog__inner") !== null')
    if kind == 'novel':
        expect(dialog.get_by_test_id('novel-reader-text')).to_contain_text('合成の本文')
        expect(dialog.get_by_test_id('novel-reader-next')).to_be_enabled()
    if kind == 'ugoira':
        expect(dialog.get_by_test_id('ugoira-canvas')).to_have_attribute('data-frame', '0')
    walk_focus(page, dialog, COMPONENT_TAB_ORDER[kind])
    close = dialog.get_by_test_id(close_id) if close_id else dialog.get_by_role('button', name='閉じる')
    close.press('Space')
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


def walk_focus(page, surface, expected_order):
    selectors = [entry[0] if isinstance(entry, tuple) else entry for entry in expected_order]
    variable = {index for index, entry in enumerate(expected_order) if isinstance(entry, tuple)}
    # Resolve ordinary controls once using the product expectation, while
    # grouped widgets may supply a different number of keyboard stops.
    controls = [None if index in variable else surface.locator(selector).element_handle(timeout=3000)
                for index, selector in enumerate(selectors)]
    path = surface.evaluate_handle("""(root, {controls, groups}) => {
        const nodes = new Map();
        return {
            current() {
                const node = document.activeElement;
                const index = controls.findIndex(control => control === node);
                const slot = index >= 0 ? index : groups.findIndex(selector => selector && node.matches(selector));
                if (!nodes.has(node)) nodes.set(node, nodes.size);
                return [nodes.get(node), slot, root.contains(node), node.tagName, node.dataset.testid, node.className];
            }
        };
    }""", {'controls': controls,
           'groups': [selector if index in variable else None for index, selector in enumerate(selectors)]})
    try:
        controls[0].focus()
        start, first, inside, *_ = path.evaluate('path => path.current()')
        assert inside and first == 0
        forward = [start]
        order = []
        for _ in range(256):
            page.keyboard.press('Tab')
            node, slot, inside, *label = path.evaluate('path => path.current()')
            assert inside and slot >= 0, ('Tab stopped outside the specified controls', label)
            if node == start:
                assert order == list(range(1, len(expected_order))), ('Tab order', order, selectors)
                break
            assert node not in forward, ('Tab repeated before completing a lap', label)
            forward.append(node)
            if not (slot in variable and order and order[-1] == slot):
                order.append(slot)
        else:
            pytest.fail('Tab did not return to the first control')

        for expected in [*reversed(forward[1:]), start]:
            page.keyboard.press('Shift+Tab')
            node, slot, inside, *label = path.evaluate('path => path.current()')
            assert inside and node == expected, ('Shift+Tab reversed a different path', slot, label)
    finally:
        path.dispose()
        for control_handle in controls:
            if control_handle:
                control_handle.dispose()


@pytest.mark.parametrize(('screen', 'opener_id', 'menu_id'), (
    ('download', 'unified-sort', 'unified-sort-menu'),
    ('failed', 'failed-menu', None),
    ('ranking', 'ranking-hide-manage', 'ranking-hide-manage-menu'),
    ('download', 'saved-folder-open', 'saved-folder-menu'),
))
def test_product_menu_keyboard_return(zoom_page, screen, opener_id, menu_id):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    cell = audit.Cell(screen, 'normal', 'light', 1280, 800, 100, None)
    if menu_id == 'saved-folder-menu':
        load(page, origin, '/_focus-surface/saved-folder')
    else:
        audit.load_page(page, origin, cell)
        p5.navigate_port(page, screen, p5.helpers.load_expectations()['screens'], tone_mode=True)
    opener = page.get_by_test_id(opener_id)
    expect(opener).to_be_visible()
    expect(opener).to_be_enabled()
    opener.press('Enter')
    menu = page.get_by_test_id(menu_id) if menu_id else page.locator('.q-menu').filter(visible=True)
    expect(menu).to_be_visible()
    page.wait_for_function('document.activeElement?.closest(".q-menu") !== null')
    page.keyboard.press('Tab')
    assert menu.evaluate('e => e.contains(document.activeElement)')
    page.keyboard.press('Shift+Tab')
    assert menu.evaluate('e => e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(menu).to_be_hidden()
    expect(opener).to_be_focused()




@pytest.mark.parametrize('keyboard_context', [False, True], ids=['external-link', 'context-menu'])
def test_viewer_popup_and_close_return_focus(zoom_page, keyboard_context):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    cell = audit.Cell('viewer', 'normal', 'dark', 1280, 800, 100, None)
    audit.load_page(page, origin, cell)
    specs = p5.helpers.load_expectations()['screens']
    p5.navigate_port(page, 'ranking', specs, tone_mode=True)
    image = page.locator('[data-testid=ranking-view] .pv-card img').filter(visible=True).first
    image.click()
    viewer = page.get_by_role('dialog', name='画集ビューア').filter(visible=True)
    close = viewer.get_by_test_id('viewer-close')
    expect(close).to_be_focused()
    if keyboard_context:
        close.press('Shift+F10')
        popup = page.get_by_test_id('viewer-context-menu').filter(visible=True)
        opener = close
    else:
        opener = viewer.get_by_role('button', name='作品メニュー')
        opener.press('Enter')
        popup = page.locator('.q-menu').filter(visible=True)
    expect(popup).to_be_visible()
    page.wait_for_function('document.activeElement.closest(".q-menu") !== null')
    for key in ('Tab', 'Shift+Tab'):
        page.keyboard.press(key)
        assert popup.evaluate('e => e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(popup).to_be_hidden()
    expect(opener).to_be_focused()
    close.press('Space')
    expect(viewer).to_be_hidden()
    assert page.evaluate("document.activeElement !== document.body")


@pytest.mark.parametrize('mode', ('close', 'count', 'mixed', 'cancel-last'))
def test_shell_request_focus_and_live_roles_r3(zoom_page, mode):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_request-recovery/{mode}')
    opener = page.get_by_test_id('fixture-main-action')
    expect(opener).to_be_visible()
    opener.press('Enter')
    banner = page.locator('.pv-header')
    if mode == 'close':
        close = banner.get_by_test_id('request-retry-dismiss')
        expect(close).to_be_visible()
        close.press('Enter')
        expect(close).to_be_hidden()
        expect(opener).to_be_focused()
    elif mode in ('count', 'cancel-last'):
        target = banner.get_by_test_id('request-retry-more' if mode == 'count' else 'request-retry-dismiss')
        expect(target).to_be_visible()
        target.focus()
        page.get_by_test_id('fixture-update').evaluate('node => node.click()')
        expect(target).to_be_hidden()
        expect(opener).to_be_focused()
    else:
        stop = banner.get_by_role('alert').filter(has_text='通信を止めています')
        wait = banner.get_by_role('status').filter(has_text='残り約8秒')
        expect(stop).to_be_visible()
        expect(wait).to_be_visible()
        expect(stop).not_to_contain_text('再試行待機中')
        banner.get_by_test_id('request-retry-dismiss').press('Enter')
        stop = banner.get_by_role('alert').filter(has_text='確認済み')
        expect(stop).to_be_visible()
        expect(stop).not_to_contain_text('再試行待機中')
        announce = banner.get_by_test_id('request-retry-progress-announcement')
        before = announce.inner_text()
        assert '再試行' in before and '残り約' not in before
        for region in (announce, stop):
            region.evaluate('''node => {
                node.fixtureUpdates = 0;
                new MutationObserver(changes => node.fixtureUpdates += changes.length)
                    .observe(node, {childList: true, subtree: true, characterData: true});
            }''')
        expect(wait).to_have_attribute('aria-live', 'off')
        page.get_by_test_id('fixture-update').evaluate('node => node.click()')
        expect(banner.get_by_role('status').filter(has_text='残り約7秒')).to_be_visible()
        expect(announce).to_have_text(before)
        expect(stop).not_to_contain_text('再試行待機中')
        assert all(region.evaluate('node => node.fixtureUpdates') == 0 for region in (announce, stop))


@pytest.mark.parametrize('replaceable', (False, True))
def test_routine_completion_is_a_status_r3(zoom_page, replaceable):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, '/_common-notice/light')
    suffix = 'replaced' if replaceable else 'ordinary'
    page.get_by_test_id(f'completion-{suffix}').press('Enter')
    toast = page.locator('.q-notification').filter(has_text=f'保存完了 {suffix}')
    expect(toast).to_be_visible()
    expect(toast).to_have_attribute('role', 'status')
    expect(toast.get_by_role('button')).to_have_count(0)
    expect(toast).to_be_hidden(timeout=9000)


def test_focus_walk_rejects_a_stalled_tab_r3(zoom_page):
    page, origin, _ = zoom_page
    load(page, origin, '/_common-notice/light')
    opener = page.get_by_test_id('modal-open')
    opener.press('Enter')
    dialog = page.get_by_role('dialog')
    dialog.get_by_test_id('modal-first').focus()
    dialog.evaluate('e => e.addEventListener("keydown", event => event.preventDefault(), true)')
    with pytest.raises(AssertionError):
        walk_focus(page, dialog, tuple(map(control, ('modal-first', 'modal-error'))))


@pytest.mark.parametrize('mode', ('expiry', 'disabled', 'replacement', 'moved', 'heading'))
def test_application_focus_loss_fallback_r3(zoom_page, mode):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_focus-loss/{mode}')
    source = page.get_by_test_id('loss-source')
    mutate = page.get_by_test_id('loss-mutate')
    if mode == 'expiry':
        source.press('Enter')
        undo = page.get_by_test_id('learning-toast-undo')
        expect(undo).to_be_visible()
        undo.focus()
        expect(undo).to_be_hidden(timeout=9000)
        expect(source).to_be_focused()
        return

    source.focus()
    if mode == 'moved':
        page.get_by_test_id('loss-neighbour').focus()
    mutate.evaluate('node => node.click()')
    if mode == 'disabled':
        expect(source).to_be_disabled()
    else:
        expect(source).to_have_count(0)
    expected = {'replacement': 'loss-fresh', 'heading': 'loss-heading'}.get(mode, 'loss-neighbour')
    expect(page.get_by_test_id(expected)).to_be_focused()


def test_background_focus_loss_recovers_on_reactivation_r4(zoom_page):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, '/_focus-loss/moved')
    session = page.context.new_cdp_session(page)
    session.send('Emulation.setFocusEmulationEnabled', {'enabled': False})
    page.bring_to_front()
    source = page.get_by_test_id('loss-source')
    source.focus()
    page.wait_for_function('document.hasFocus()')
    background = page.context.new_page()
    try:
        background.bring_to_front()
        page.wait_for_function('!document.hasFocus()', polling=50, timeout=5000)
        state = source.evaluate('''async node => {
            node.remove();
            await Promise.resolve();
            return [document.hasFocus(), document.activeElement === document.body];
        }''')
        assert state == [False, True]
        page.bring_to_front()
        page.wait_for_function('document.hasFocus()')
        expect(page.get_by_test_id('loss-neighbour')).to_be_focused()
    finally:
        background.close()
        session.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        session.detach()


@pytest.mark.parametrize('mode', ('dialog-hide', 'post-update'))
def test_delayed_recovery_keeps_new_keyboard_focus_r4(zoom_page, mode):
    from playwright.sync_api import expect

    page, origin, _ = zoom_page
    load(page, origin, f'/_focus-race/{mode}')
    opener = page.get_by_test_id('race-open')
    opener.press('Enter')
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_test_id('race-first')).to_be_focused()
    # Hold the application's recovery frame while allowing Quasar to finish
    # closing. Advance it after the user makes another keyboard movement.
    page.evaluate("""() => {
        const schedule = window.requestAnimationFrame;
        const held = [];
        window.requestAnimationFrame = callback =>
            String(callback).includes('pvFocus') ? (held.push(callback), 0) : schedule(callback);
        window.pvReleaseFrame = () => {
            window.requestAnimationFrame = schedule;
            for (const callback of held) schedule(callback);
            return new Promise(resolve => schedule(resolve));
        };
        window.pvHeldFrames = held;
    }""")
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    page.wait_for_function('window.pvHeldFrames.length > 0')
    opener.focus()
    page.keyboard.press('Tab')
    destination = page.get_by_test_id('race-other')
    expect(destination).to_be_focused()
    page.evaluate('window.pvReleaseFrame()')
    expect(destination).to_be_focused()


def test_focus_walk_rejects_a_reordered_control_r4(zoom_page):
    page, origin, _ = zoom_page
    load(page, origin, '/_focus-race/dialog-hide')
    page.get_by_test_id('race-open').press('Enter')
    dialog = page.get_by_role('dialog')
    walk_focus(page, dialog, RACE_TAB_ORDER)
    dialog.get_by_test_id('race-close').evaluate("""node => {
        node.parentElement.insertBefore(node, node.parentElement.querySelector('[data-testid=race-save]'));
    }""")
    dialog.get_by_test_id('race-first').focus()
    with pytest.raises(AssertionError, match='Tab order'):
        walk_focus(page, dialog, RACE_TAB_ORDER)


def test_modal_notice_replaces_retry(zoom_page, tmp_path):
    page, origin, _ = zoom_page
    load(page, origin, '/_common-notice/light?focus=implicit')
    opener = page.get_by_test_id('modal-open')
    opener.press('Enter')
    dialog = page.get_by_role('dialog')
    for attempt in range(1, 4):
        dialog.get_by_test_id('modal-error').press('Enter')
        dialog.get_by_text(f'保存できませんでした。 {attempt}', exact=True).wait_for()
    alerts = dialog.get_by_role('alert')
    after_retries = alerts.count()
    page.keyboard.press('Escape')
    dialog.wait_for(state='hidden')
    opener.press('Enter')
    dialog.wait_for()
    after_reopen = alerts.count()
    record(tmp_path, 'modal-retry.json', dict(after_retries=after_retries, after_reopen=after_reopen))
    assert (after_retries, after_reopen) == (1, 1)


@pytest.mark.parametrize('zoom_page', (100, 200), indirect=True)
def test_shell_zoom_matrix(zoom_page, tmp_path):
    page, origin, zoom = zoom_page
    specs = p5.helpers.load_expectations()['screens']
    results = {}
    for width, height in SIZES:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in THEMES:
            name = f'{width}x{height}-{theme}-{zoom}'
            load(page, origin, '/')
            assert p5.smoke.set_theme(page, theme)
            assert page.evaluate('[innerWidth, innerHeight, devicePixelRatio]') == [
                width * 100 // zoom, height * 100 // zoom, zoom / 100]
            navigate_extra(page, 'shared-log', 'port')
            shared = shell_layout(page)
            screenshot(page, tmp_path, 'shared-log-' + name)
            p5.navigate_port(page, 'bookmarks', specs, tone_mode=True)
            gallery = page.get_by_test_id('bookmark-scroll')
            gallery.wait_for(state='attached')
            bookmarks = shell_layout(page)
            if bookmarks['galleryHeight'] > 0:
                card = gallery.locator('.pv-card').first
                card.scroll_into_view_if_needed(timeout=5000)
                bookmarks['cardInGallery'] = card.evaluate('''card => {
                    const r = card.getBoundingClientRect();
                    const g = card.closest('[data-testid=bookmark-scroll]').getBoundingClientRect();
                    return Math.min(r.bottom,g.bottom,innerHeight) > Math.max(r.top,g.top,0);
                }''')
            screenshot(page, tmp_path, 'bookmarks-' + name)
            results[name] = dict(shared_log=shared, bookmarks=bookmarks)
    record(tmp_path, f'shell-{zoom}.json', results)
    assert all(r['scrollHeight'] == r['height'] and r['scrollWidth'] == r['width']
               for screens in results.values() for r in screens.values()), results
    assert all(r['shared_log']['logBottom'] <= r['shared_log']['height']
               and r['bookmarks'].get('cardInGallery') for r in results.values()), results


def shell_layout(page):
    return page.evaluate('''() => {
        const shell = document.querySelector('.pv-shell');
        const content = shell.querySelector('.pv-content');
        const chrome = shell.querySelector('.pv-shell-chrome');
        const banner = shell.querySelector('[data-testid=db-status-banner]');
        const log = shell.querySelector('[data-testid=shared-log]');
        const gallery = shell.querySelector('[data-testid=bookmark-scroll]');
        return {height:shell.clientHeight, scrollHeight:shell.scrollHeight,
                width:shell.clientWidth, scrollWidth:shell.scrollWidth,
                contentHeight:content.clientHeight, galleryHeight:gallery?.clientHeight ?? 0,
                chromeHeight:chrome?.clientHeight ?? 0, bannerHeight:banner?.clientHeight ?? 0,
                logBottom:log?.getBoundingClientRect().bottom ?? 0};
    }''')


def serve(port, nonce):
    from nicegui import ui
    from pixivvault_nicegui.ui.action_controls import notify_action
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.learning_view import LearningView
    from pixivvault_nicegui.ui.theme import CSS, TOKENS

    @ui.page('/_common-notice/{theme}')
    def notifications(theme: str):
        ui.add_css(CSS)
        ui.colors(primary=TOKENS['accent'])
        ui.dark_mode(theme == 'dark')
        for kind in TOAST_TYPES:
            ui.button(kind, on_click=lambda _, kind=kind: notify_action(
                '確認が必要です。', type=kind, important=True)).props(f'data-testid=notify-{kind}')
        for replaceable, suffix in ((False, 'ordinary'), (True, 'replaced')):
            ui.button('保存完了', on_click=lambda _, r=replaceable, name=suffix:
                      notify_action(f'保存完了 {name}', type='positive', replaceable=r)).props(
                          f'data-testid=completion-{suffix}')
        mode = ui.context.client.request.query_params.get('focus', 'explicit')
        attempt = 0
        with ui.dialog() as dialog, ui.card() as content:
            first = ui.button('設定を閉じる', on_click=dialog.close).props('data-testid=modal-first')

            def notify_inside():
                nonlocal attempt
                attempt += 1
                if mode in {'disabled', 'explicit-disabled', 'no-controls'}:
                    trigger.disable()
                if mode == 'no-controls':
                    first.disable()
                options = {'return_focus': trigger} if mode in {'explicit', 'explicit-disabled'} else {}
                notify_action(f'保存できませんでした。 {attempt}', type='negative',
                              within_dialog=content, **options)

            trigger = ui.button('保存する', on_click=notify_inside).props('data-testid=modal-error')
        ui.button('設定を開く', on_click=dialog.open).props('data-testid=modal-open')

    @ui.page('/_request-recovery/{mode}')
    def request_recovery(mode: str):
        from types import SimpleNamespace
        from pixivvault_nicegui.ui.shell import DemoShell

        records = []
        services = ApplicationServices(count=3)
        services._request_retry = SimpleNamespace(progress_snapshot=lambda: list(records))
        ui.context.client.on_delete(services.close)
        shell = DemoShell(services)
        with shell.content:
            def begin():
                base = dict(phase='stopped', status=401, attempt=1, limit=3,
                            notice_id=1, stop_cause='durable_gate')
                if mode == 'count':
                    records[:] = [dict(base, phase='cancelled', stop_cause=None, notice_id=i)
                                  for i in range(1, 5)]
                elif mode == 'mixed':
                    records[:] = [base, dict(base, phase='waiting', stop_cause=None, status=500,
                                             notice_id=2, remaining_seconds=8, wait_seconds=10)]
                else:
                    records[:] = [base]

            def update():
                if mode == 'mixed':
                    records[-1]['remaining_seconds'] = 7
                else:
                    records.pop(0)

            ui.button('取得操作', on_click=begin).props('data-testid=fixture-main-action')
            ui.button('合成状態を更新', on_click=update).props('tabindex=-1 data-testid=fixture-update')

    @ui.page('/_common-start-notice/{theme}')
    def startup_notice(theme: str):
        ui.add_css(CSS)
        ui.dark_mode(theme == 'dark')
        from uuid import uuid4
        from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
        from z_learning_support_20261118 import FakeReader, FakeServices, candidate, seed_candidates

        service = DiscoveryLearningService(Path.cwd() / f'startup-{uuid4().hex}', FakeReader(),
                                           following=(), saved_ids=())
        seed_candidates(service, [candidate(1)])
        async def fail_initialization(*_args, **_kwargs):
            raise OSError('synthetic discovery startup failure')
        service.initialize_once = fail_initialization
        view = LearningView(FakeServices(service))
        ui.context.client.on_delete(service.close)
        view.set_active(True)

    @ui.page('/_common-fallback-notice/{mode}')
    def fallback_notice(mode: str):
        ui.button('戻る').props('data-testid=notice-fallback')
        def show():
            notify_action('保存できませんでした。', type='negative', within_dialog=content,
                          return_focus=None if mode == 'preconnect' else opener)
            if mode == 'hidden':
                opener.set_visibility(False)
            else:
                opener.disable()
        opener = ui.button('保存する', on_click=show).props('data-testid=notice-origin')
        content = ui.column()
        if mode == 'preconnect':
            show()

    @ui.page('/_focus-loss/{mode}')
    def focus_loss(mode: str):
        from pixivvault_nicegui.ui.learning_toast import LearningToast
        from pixivvault_nicegui.ui.management_dialog import focus_region

        ui.add_css(CSS)
        with ui.column().classes('pv-shell'):
            ui.label('作業画面').props('role=heading aria-level=1 data-testid=loss-heading')
            with ui.column() as region:
                focus_region(region)
                source = ui.button('選択').props('data-testid=loss-source')
                if mode == 'expiry':
                    toast = LearningToast(return_focus=source)
                    source.on_click(lambda: toast.show_undo(None, lambda _: None, ''))
                if mode in {'disabled', 'moved'}:
                    ui.button('次の操作').props('data-testid=loss-neighbour')

            def update():
                if mode == 'disabled':
                    source.disable()
                elif mode == 'heading':
                    region.delete()
                elif mode == 'moved':
                    source.delete()
                else:
                    region.clear()
                    with region:
                        ui.button('新しい行').props('data-testid=loss-fresh')

            ui.button('更新', on_click=update).props('tabindex=-1 data-testid=loss-mutate')

    @ui.page('/_focus-race/{mode}')
    def focus_race(mode: str):
        from pixivvault_nicegui.ui.management_dialog import focus_after_dialog, managed_dialog

        opener = ui.button('設定を開く').props('data-testid=race-open')
        ui.button('次の操作').props('data-testid=race-other')
        dialog = managed_dialog(return_focus=opener if mode == 'dialog-hide' else None,
                                focus_after_update=mode == 'post-update')
        with dialog, ui.card():
            ui.button('表示').props('autofocus data-testid=race-first')
            ui.button('保存').props('data-testid=race-save')
            ui.button('閉じる', on_click=dialog.close).props('data-testid=race-close')
        opener.on_click(dialog.open)
        if mode == 'post-update':
            dialog.on('hide', lambda: focus_after_dialog(dialog.client, '[data-testid=race-open]'))

    @ui.page('/_focus-surface/{kind}')
    def focus_surface(kind: str):
        from types import SimpleNamespace
        from pixivvault_nicegui.managed_novel_jobs import FixtureNovelSource
        from pixivvault_nicegui.ui.learning_reset import LearningResetView
        from pixivvault_nicegui.ui.novel_save_view import NovelLibraryView
        from pixivvault_nicegui.ui.saved_folder_button import SavedFolderButton
        from pixivvault_nicegui.ui.shell import DemoShell
        from pixivvault_nicegui.ui.similar_view import SimilarView
        from pixivvault_nicegui.ui.unfollowed_authors_view import UnfollowedAuthorsView
        from pixivvault_nicegui.ui.ugoira_view import UgoiraLibraryView

        ui.add_css(CSS)
        stub = SimpleNamespace(closing=False, closed=False)
        if kind == 'saved-folder':
            stub.saved_folder_choices = lambda: [SimpleNamespace(key='first', label='合成の保存先 A'),
                                                 SimpleNamespace(key='second', label='合成の保存先 B')]
            SavedFolderButton(stub)
            return
        if kind == 'learning-reset':
            async def count():
                return 1
            view = LearningResetView(SimpleNamespace(
                active=True, state=SimpleNamespace(closed=False, saving=False), services=stub,
                _storage_ready=True, _storage_busy=False, _review_busy=False),
                SimpleNamespace(judged_count=count))
            return
        if kind == 'similar':
            view = SimilarView(None, lambda *_: None)
            open_view = view.dialog.open
        elif kind in {'novel', 'ugoira'}:
            from base64 import b64encode
            from threading import Event
            from uuid import uuid4

            started, ready = Event(), Event()
            def wait_for_browser():
                started.set()
                if not ready.wait(timeout=12):
                    raise TimeoutError('browser did not release the synthetic read')

            if kind == 'novel':
                store = Path.cwd() / f'novel-reader-{uuid4().hex}'
                folder = store / 'saved'
                folder.mkdir(parents=True)
                (folder / 'novel.txt').write_text('合成の題名\n合成の作者\n' + '合成の本文。' * 4800,
                                                   encoding='utf-8')
                saved = SimpleNamespace(work_id='901', output_format='txt', metadata=None,
                                        relative_receipt=Path('saved/receipt.json'), output_names=('novel.txt',))
                def current(*_args, **_kwargs):
                    wait_for_browser()
                    return saved
                stub.novel_jobs = SimpleNamespace(saved_entries=lambda: [saved], current=current,
                                                  store=SimpleNamespace(root=store))
                stub.novel_fixture_source = FixtureNovelSource({})
                view = NovelLibraryView(stub)
            else:
                frame = BytesIO()
                Image.new('RGB', (2, 2), 'blue').save(frame, format='PNG')
                image = 'data:image/png;base64,' + b64encode(frame.getvalue()).decode('ascii')
                entry = {'spec': {'title': '合成アニメーション', 'work_id': '902', 'output': 'gif'},
                         'actual_output': 'gif'}
                def playback(_entry):
                    wait_for_browser()
                    return {'width': 2, 'height': 2, 'duration_ms': 100,
                            'frames': [{'src': image, 'delay': 100}]}
                stub.ugoira = SimpleNamespace(library=lambda: [entry], playback=playback)
                view = UgoiraLibraryView(stub)
            release = ui.button('読込を再開', on_click=ready.set).props('data-testid=finish-fixture-read')
            release.disable()
            ui.timer(.03, lambda: release.set_enabled(started.is_set()))
            ui.context.client.on_delete(ready.set)
            view.set_active(True)
            return
        elif kind == 'unfollowed':
            view = UnfollowedAuthorsView(stub)
            open_view = view.show
        elif kind == 'account-switch':
            shell = DemoShell.__new__(DemoShell)
            shell.account_switch_dialog = None
            shell._followup = SimpleNamespace(cancel_account_switch=shell._close_account_switch,
                                               confirm_account_switch=lambda: None)
            open_view = lambda: shell._show_account_switch(SimpleNamespace(new_account='fixture'))
        else:
            raise ValueError('unknown test surface')
        ui.button('開く', on_click=open_view).props('data-testid=surface-opener')


    uiux_fixture.serve_port(port, nonce)


if __name__ == '__main__':
    assert len(sys.argv) == 4 and sys.argv[1] == '--serve-port'
    assert os.environ.get('PV_UIUX_STATE') == 'normal' and Path('fixture.json').is_file()
    serve(int(sys.argv[2]), sys.argv[3].split('=', 1)[1])
