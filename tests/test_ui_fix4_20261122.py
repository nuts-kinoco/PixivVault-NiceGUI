"""Chromium checks for the real library and viewer with an isolated synthetic vault."""
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def library_browser(tmp_path_factory):
    from tools import p5_design_evidence as p5, uiux_audit as audit, uiux_fixture

    if os.name != 'nt':
        pytest.skip('This isolated browser fixture uses Windows Edge on S:')
    sync_api = pytest.importorskip('playwright.sync_api')
    audit.allow_browser()
    base = tmp_path_factory.mktemp('ui-fix4-browser')
    assert base.drive.upper() == 'S:'
    source = base / 'original'
    source.mkdir()
    product = base / 'port'
    p5.seed_profile(product, source, 'long', tone_mode=True)
    with sqlite3.connect(product / 'lib/synthetic-legacy.sqlite') as database:
        database.execute("UPDATE works SET page_count=2 WHERE work_id='101'")
        database.execute(
            "INSERT INTO works (work_id,title,page_count,tags,user_id,is_deleted,last_backup,content_type,work_meta) "
            "SELECT '102','short title',1,tags,user_id,is_deleted,last_backup,content_type,work_meta "
            "FROM works WHERE work_id='101'"
        )
    database.close()
    first = next((product / 'lib/fake-images').rglob('synthetic work(101)_1.png'))
    shutil.copyfile(first, first.with_name('synthetic work(101)_2.png'))
    shutil.copyfile(first, first.with_name('short title(102)_1.png'))
    with socket.socket() as reservations:
        reservations.bind(('127.0.0.1', 0))
        port = reservations.getsockname()[1]
    nonce = secrets.token_urlsafe(24)
    with p5.running_app('port', sys.executable, product, port, nonce,
                        tone_mode=True, personal_fixture=True, server_script=Path(__file__).resolve(),
                        extra_env={'PV_UIUX_STATE': 'long'}) as url, sync_api.sync_playwright() as runtime:
        audit.smoke.network_state(url, nonce)
        browser_env = os.environ | {'LOCALAPPDATA': str(base / 'browser-local'),
                                    'APPDATA': str(base / 'browser-roaming')}
        with runtime.chromium.launch_persistent_context(
            str(base / 'browser'), channel='msedge', headless=True, env=browser_env,
            viewport={'width': 1280, 'height': 800}, service_workers='block',
            args=['--disable-gpu', '--disable-background-networking', '--no-first-run',
                  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
        ) as browser:
            browser.route('**/*', lambda route: route.continue_() if route.request.url.startswith(url + '/') else route.abort())
            browser.route_web_socket('**/*', lambda route: route.connect_to_server()
                                     if route.url.startswith(url.replace('http:', 'ws:') + '/') else route.close())
            yield browser, url, uiux_fixture


@pytest.fixture
def library_page(library_browser):
    browser, url, fixture = library_browser
    page = browser.new_page()
    try:
        page.goto(url, wait_until='domcontentloaded')
        fixture.ready(page, 'tab-library')
        yield page
    finally:
        page.close()


def test_bookmark_card_preserves_ellipsis_and_exposes_complete_title(library_page):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    page.get_by_test_id('library-subview-bookmarks').click()
    title = page.locator('[data-testid=bookmark-card] .pv-card-title').filter(has_text='長文の表示').first
    expect(title).to_be_visible()
    full = title.inner_text()
    assert title.evaluate('el => el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === "ellipsis"')
    expect(title).to_have_attribute('title', full)
    opener = title.locator('..').get_by_test_id('bookmark-open')
    expect(opener).to_have_attribute('aria-label', f'ブックマークを開く：{full}…')
    assert page.get_by_test_id('bookmark-filter-toggle').evaluate('''el => {
        const rect = el.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const middle = rect.top + rect.height / 2;
        return rect.height < 28 && [middle - 13.5, middle + 13.5].every(
            y => el.contains(document.elementFromPoint(x, y)));
    }''')
    page.get_by_test_id('bookmark-search').press('Tab')
    opener.focus()
    preview = page.locator('.pv-bookmark-title-preview:popover-open')
    expect(preview).to_have_text(full)
    assert preview.evaluate('el => el.scrollHeight <= el.clientHeight + 1')
    page.keyboard.press('Escape')
    expect(preview).to_have_count(0)
    expect(opener).to_be_focused()
    page.keyboard.press('Tab')
    opener.focus()
    expect(preview).to_have_count(1)
    # Chromium does not send focusout when the focused element is removed.
    opener.evaluate('el => el.closest(".pv-bookmark-card").remove()')
    expect(preview).to_have_count(0)


def test_bookmark_scroll_keeps_usable_height_in_small_content_panel(library_page):
    from playwright.sync_api import expect
    from tools import uiux_audit as audit

    page = library_page
    try:
        audit.set_browser_zoom(page.context, 200)
        page.reload(wait_until='domcontentloaded')
        assert page.evaluate('() => [innerWidth, innerHeight, devicePixelRatio]') == [640, 400, 2]
        page.get_by_test_id('tab-library').click()
        page.get_by_test_id('library-subview-bookmarks').click()
        scroll = page.get_by_test_id('bookmark-scroll')
        expect(scroll).to_be_visible()
        layout = scroll.evaluate('''el => {
            const shell = el.closest('.pv-shell');
            const sizes = selector => [...shell.querySelectorAll(selector)]
                .filter(node => node.getClientRects().length)
                .map(node => [node.dataset.testid || node.className, node.clientHeight]);
            return {height: el.clientHeight, width: el.clientWidth, scrollWidth: el.scrollWidth,
                    shellHeight: shell.clientHeight, shellScrollHeight: shell.scrollHeight,
                    sizes: sizes('.pv-shell > *, .pv-library > *, .pv-bookmarks > *')};
        }''')
        assert layout['height'] >= 144 and layout['scrollWidth'] <= layout['width'] + 1, str(layout)
        assert layout['shellScrollHeight'] == layout['shellHeight'], layout
        assert scroll.evaluate('''el => {
            const library = el.closest('.pv-library');
            return getComputedStyle(library).overflowY === 'hidden' ||
                   library.scrollHeight <= library.clientHeight + 1;
        }''')
        card = scroll.get_by_test_id('bookmark-card').first
        card.scroll_into_view_if_needed()
        expect(card).to_be_in_viewport()
    finally:
        audit.set_browser_zoom(page.context, 100)


def test_history_dialog_moves_focus_and_escape_restores_opener(library_page):
    from playwright.sync_api import expect

    opener = library_page.get_by_test_id('view-history-open')
    opener.press('Enter')
    expect(library_page.get_by_test_id('view-history-close')).to_be_focused()
    dialog = library_page.get_by_test_id('view-history-dialog')
    for direction in ('Tab', 'Shift+Tab'):
        for _ in range(6):
            library_page.keyboard.press(direction)
            assert dialog.evaluate('el => el.contains(document.activeElement)')
    library_page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()


def test_calendar_dialog_focus_and_close_return_to_the_calendar_button(library_page):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    page.get_by_test_id('library-filter-toggle').click()
    opener = page.get_by_test_id('library-date-from-calendar')
    expect(opener).to_be_visible()
    opener.focus()
    expect(opener).to_be_focused()
    page.keyboard.press('Enter')
    cancel = page.get_by_test_id('library-date-cancel')
    expect(cancel).to_be_focused()
    assert cancel.evaluate('el => getComputedStyle(el).boxShadow !== "none"')
    dialog = page.get_by_test_id('library-date-dialog')
    for direction in ('Tab', 'Shift+Tab'):
        for _ in range(8):
            page.keyboard.press(direction)
            assert dialog.evaluate('el => el.contains(document.activeElement)')
    page.keyboard.press('Escape')
    expect(page.get_by_test_id('library-date-dialog')).to_have_count(0)
    expect(opener).to_be_focused()
    opener.press('Enter')
    cancel = page.get_by_test_id('library-date-cancel')
    expect(cancel).to_be_focused()
    cancel.press('Enter')
    expect(page.get_by_test_id('library-date-dialog')).to_have_count(0)
    expect(opener).to_be_focused()


@pytest.mark.parametrize('check_ring', [True, False])
def test_bookmark_viewer_accepts_tab_and_restores_origin_on_escape(library_page, check_ring):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    page.get_by_test_id('library-subview-bookmarks').click()
    opener = page.get_by_test_id('bookmark-card').filter(has_text='長文の表示').locator(
        '[data-testid=bookmark-open][tabindex="0"]').first
    opener.press('Enter')
    viewer = page.get_by_role('dialog', name='画集ビューア').filter(visible=True)
    close = viewer.get_by_role('button', name='閉じる', exact=True)
    expect(close).to_be_focused()
    if check_ring:
        assert close.evaluate(r'''el => {
            const style = getComputedStyle(el);
            const colors = style.boxShadow.match(/rgb\([^)]*\)/g);
            if (!colors || colors.length !== 2 || !style.boxShadow.includes('0px 0px 0px 4px')) return false;
            const luminance = rgb => {
                const values = rgb.match(/\d+/g).map(n => n / 255).map(
                    n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4);
                return .2126 * values[0] + .7152 * values[1] + .0722 * values[2] + .05;
            };
            const [a, b] = colors.map(luminance);
            return Math.max(a, b) / Math.min(a, b) >= 3;
        }''')
        return
    author_content = viewer.locator('.pv-viewer-info .q-btn.pv-subtitle .q-btn__content')
    expect(author_content).to_be_visible()
    assert author_content.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    expect(viewer).to_have_attribute('data-zoom-ready', 'true')
    expect(viewer.get_by_label('ページ 1 / 2')).to_be_visible()
    details = viewer.get_by_role('button', name='詳細', exact=True)
    expect(details).to_have_attribute('aria-expanded', 'false')
    page.keyboard.press('Tab')
    expect(details).to_have_attribute('aria-expanded', 'true')
    expect(close).to_be_focused()
    page.keyboard.press('i')
    expect(details).to_have_attribute('aria-expanded', 'false')
    page.keyboard.press('Shift+Tab')
    assert not close.evaluate('el => el === document.activeElement')
    page.keyboard.press('Shift+ArrowRight')
    expect(close).to_be_focused()
    for key in ('Shift+ArrowRight', 'Shift+Tab'):
        visited = {close.evaluate('el => el.id')}
        count = viewer.evaluate('''el => [...el.querySelectorAll('button, a[href], [tabindex]')]
            .filter(n => n.tabIndex >= 0 && !n.matches(':disabled, .disabled, [aria-disabled=true]') &&
                         !n.closest('[inert], [aria-hidden=true]') && n.getClientRects().length).length''')
        for _ in range(count):
            page.keyboard.press(key)
            assert viewer.evaluate('el => el.contains(document.activeElement)')
            visited.add(page.evaluate('document.activeElement.id'))
        assert len(visited) == count
        expect(close).to_be_focused()
    page.keyboard.press('Shift+ArrowRight')
    next_page = viewer.get_by_role('button', name='次のページ')
    previous_page = viewer.get_by_role('button', name='前のページ')
    expect(next_page).to_be_focused()
    page.keyboard.press('ArrowRight')
    expect(viewer.get_by_label('ページ 2 / 2')).to_be_visible()
    expect(previous_page).to_be_focused()
    page.keyboard.press('ArrowLeft')
    expect(viewer.get_by_label('ページ 1 / 2')).to_be_visible()
    expect(next_page).to_be_focused()
    expect(viewer).to_have_attribute('data-zoom-ready', 'true')
    page.keyboard.press('+')
    expect(viewer.get_by_test_id('viewer-zoom-value')).not_to_have_text('100%')
    page.keyboard.press('0')
    expect(viewer.get_by_test_id('viewer-zoom-value')).to_have_text('100%')
    expect(details).to_have_attribute('aria-expanded', 'false')
    page.keyboard.press('i')
    expect(details).to_have_attribute('aria-expanded', 'true')
    page.keyboard.press('Escape')
    expect(details).to_have_attribute('aria-expanded', 'false')
    expect(viewer).to_be_visible()
    page.keyboard.press('Escape')
    expect(viewer).to_be_hidden()
    expect(opener).to_be_focused()


def hold_viewer_restore_frame(page):
    # Hold only the real ArtworkViewer post-close frame. Normal browser
    # updates, including the observer, run while the next update is sent.
    page.evaluate("""() => {
        const schedule = window.requestAnimationFrame;
        const held = [];
        window.requestAnimationFrame = callback =>
            String(callback).includes('const opener =') && String(callback).includes('__pvFocusGeneration')
                ? (held.push(callback), 0) : schedule(callback);
        window.r5ProjectionFrames = held;
        window.r5ReleaseProjection = () => {
            window.requestAnimationFrame = schedule;
            for (const callback of held) schedule(callback);
            return new Promise(resolve => schedule(resolve));
        };
    }""")


@pytest.mark.parametrize('order', ('list-first', 'viewer-first', 'together'))
@pytest.mark.parametrize('recreated', (True, False), ids=('new-card', 'missing-card'))
@pytest.mark.parametrize('user_moved', (False, True), ids=('automatic', 'user'))
def test_r5_viewer_recovery_after_projection(library_browser, order, recreated, user_moved):
    from playwright.sync_api import expect

    browser, origin, fixture = library_browser
    page = browser.new_page()
    try:
        page.goto(origin + '/?r5_projection=1', wait_until='domcontentloaded')
        fixture.ready(page, 'tab-library').click()
        page.get_by_test_id('library-subview-bookmarks').click()
        opener = page.get_by_test_id('bookmark-card').filter(has_text='長文の表示').get_by_test_id('bookmark-open').first
        expect(opener).to_be_visible()
        original = opener.element_handle()
        opener.press('Enter')
        viewer = page.get_by_role('dialog', name='画集ビューア').filter(visible=True)
        expect(viewer.get_by_test_id('viewer-close')).to_be_focused()

        def act(action):
            page.get_by_test_id(f'r5-projection-{action}').evaluate('node => node.click()')

        def redraw(action):
            act(action)
            expect(opener).to_have_count(0 if action == 'drop' else 1)

        act('arm')
        expect(page.get_by_test_id('r5-projection-status')).to_have_text('pending')

        hold_viewer_restore_frame(page)
        if order == 'list-first':
            redraw('drop')
            if recreated:
                redraw('return')
        if order == 'together':
            act('drop-close')
            expect(opener).to_have_count(0)
        else:
            page.keyboard.press('Escape')
        expect(viewer).to_be_hidden()
        page.wait_for_function('window.r5ProjectionFrames.length === 1', timeout=5000)
        if order == 'viewer-first':
            redraw('drop')
        if recreated and order != 'list-first':
            redraw('return')
        if recreated:
            assert original.evaluate('node => !node.isConnected')
        else:
            expect(opener).to_have_count(0)

        page.wait_for_function("""() => {
            const node = document.activeElement;
            return node !== document.body && node.isConnected && node.getClientRects().length &&
                !node.closest('[hidden],[inert],[aria-hidden=true],[aria-disabled=true],:disabled');
        }""", timeout=3000)
        if user_moved:
            before_key = page.evaluate_handle('document.activeElement')
            page.keyboard.press('Tab')
            assert before_key.evaluate('node => node !== document.activeElement')
            if recreated:
                assert not opener.evaluate('node => node === document.activeElement')
        fallback = page.evaluate_handle('document.activeElement')
        page.evaluate('window.r5ReleaseProjection()')

        if recreated and not user_moved:
            expect(opener).to_be_focused()
        else:
            assert fallback.evaluate('node => node === document.activeElement')
    finally:
        page.close()


CENTER_AND_SCROLL = """node => {
    const panel = node.closest('[data-testid="bookmark-scroll"], [data-testid="library-pages-scroll"]');
    const rect = node.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const panelRect = panel.getBoundingClientRect();
    return {x, y, center: x >= 0 && x < innerWidth && y >= 0 && y < innerHeight && node.contains(hit),
            scroll: panel.scrollTop, pane: [panelRect.top, panelRect.bottom],
            hit: hit?.getAttribute('data-testid') || hit?.className || hit?.tagName};
}"""


@pytest.mark.parametrize('tab', ('downloaded', 'bookmarks'))
@pytest.mark.parametrize('case', ('offscreen', 'covered', 'visible', 'wheel'))
def test_r6_recreated_card_is_visible_when_focus_returns(library_browser, tab, case):
    from playwright.sync_api import expect

    browser, origin, fixture = library_browser
    page = browser.new_page()
    try:
        page.goto(origin + '/?r5_projection=1', wait_until='domcontentloaded')
        fixture.ready(page, 'tab-library').click()
        if tab == 'bookmarks':
            page.get_by_test_id('library-subview-bookmarks').click()
            opener = page.get_by_test_id('bookmark-card').filter(has_text='長文の表示').get_by_test_id('bookmark-open').first
            panel = page.get_by_test_id('bookmark-scroll')
        else:
            author = page.locator('.pv-library-author-card').filter(has_text='synthetic author')
            author.get_by_test_id('library-author-open').first.click()
            opener = page.locator('.pv-library-page-card').filter(has_text='short title')
            panel = page.get_by_test_id('library-pages-scroll')

        expect(opener).to_be_visible()
        original = opener.element_handle()
        opener.press('Enter')
        viewer = page.get_by_role('dialog', name='画集ビューア').filter(visible=True)
        expect(viewer.get_by_test_id('viewer-close')).to_be_focused()

        def update(action):
            page.get_by_test_id(f'r5-projection-{action}').evaluate('node => node.click()')

        update('arm')
        expect(page.get_by_test_id('r5-projection-status')).to_have_text('pending')
        hold_viewer_restore_frame(page)
        page.keyboard.press('Escape')
        expect(viewer).to_be_hidden()
        page.wait_for_function('window.r5ProjectionFrames.length === 1', timeout=5000)
        update('drop')
        expect(opener).to_have_count(0)
        update('return')
        expect(opener).to_have_count(1)
        assert original.evaluate('node => !node.isConnected')

        if case == 'visible':
            # Establish the visible starting point even when a real stop
            # notice in the header leaves a shorter library scrollport.
            opener.evaluate("node => node.scrollIntoView({block: 'center', behavior: 'instant'})")
        else:
            # Reorder this real regenerated card far below the scrollport,
            # with enough grid space below it to permit centering.
            opener.evaluate("""(node, mode) => {
                const card = node.closest('[data-testid=bookmark-card], .pv-library-page-card');
                const grid = card.parentElement;
                grid.style.gridAutoRows = '140px';
                card.style.gridRow = '16';
                grid.appendChild(Object.assign(document.createElement('div'), {style: 'grid-row: 28'}));
                const panel = node.closest('[data-testid=bookmark-scroll], [data-testid=library-pages-scroll]');
                panel.scrollTop = 0;
                if (mode !== 'covered') return;
                const panelBox = panel.getBoundingClientRect();
                const cardBox = node.getBoundingClientRect();
                // Cover the initial center while leaving room to scroll
                // the center out from under the synthetic toolbar.
                const veilHeight = Math.min(72, Math.floor(panelBox.height / 2));
                panel.scrollTop = cardBox.top + cardBox.height / 2 - panelBox.top - veilHeight / 2;
                const veil = document.createElement('div');
                veil.dataset.testid = 'r6-test-toolbar';
                Object.assign(veil.style, {position: 'fixed', top: panelBox.top + 'px',
                    left: panelBox.left + 'px', width: panelBox.width + 'px', height: veilHeight + 'px',
                    zIndex: '900', background: 'var(--pv-surface)'});
                document.body.appendChild(veil);
            }""", case)
        before = opener.evaluate(CENTER_AND_SCROLL)
        assert before['center'] is (case == 'visible'), (case, before)
        if case == 'covered':
            assert 0 <= before['y'] < page.evaluate('innerHeight'), before
            assert before['hit'] == 'r6-test-toolbar', before

        fallback = page.evaluate_handle('document.activeElement')
        if case == 'wheel':
            box = panel.bounding_box()
            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
            page.mouse.wheel(0, 180)
            page.wait_for_function("panel => panel.scrollTop > 0", arg=panel.element_handle())
            before = opener.evaluate(CENTER_AND_SCROLL)

        page.evaluate('window.r5ReleaseProjection()')
        after = opener.evaluate(CENTER_AND_SCROLL)
        if case == 'wheel':
            assert fallback.evaluate('node => node === document.activeElement')
            assert after['scroll'] == before['scroll'], (before, after)
        else:
            assert opener.evaluate('node => node === document.activeElement'), (case, before, after)
            assert after['center'], (case, before, after)
            if case == 'visible':
                assert after['scroll'] == before['scroll'], (before, after)
    finally:
        page.close()


def test_short_viewer_title_and_author_stay_together(library_page):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    author = page.locator('.pv-library-author-card').filter(has_text='synthetic author')
    author.get_by_test_id('library-author-open').first.click()
    page.locator('.pv-library-page-card').filter(has_text='short title').first.click()
    viewer = page.get_by_role('dialog', name='画集ビューア').filter(visible=True)
    title = viewer.locator('.pv-view-title')
    expect(title).to_have_text('short title')
    assert title.evaluate('''el => {
        const text = document.createRange();
        text.selectNodeContents(el);
        const author = el.parentElement.querySelector('[data-testid=viewer-author-open]');
        return author.getBoundingClientRect().left - text.getBoundingClientRect().right < 48;
    }''')


@pytest.mark.parametrize('control', ['size', 'profile', 'caption'])
def test_library_dark_selected_size_and_author_card_keep_original_style(library_page, control):
    from playwright.sync_api import expect

    page = library_page
    if not page.locator('body').evaluate("el => el.classList.contains('body--dark')"):
        page.get_by_test_id('theme-toggle').click()
    page.get_by_test_id('tab-library').click()
    if control == 'size':
        selected = page.locator('.pv-library .pv-size-selected')
        expect(selected).to_be_visible()
        assert selected.evaluate('el => getComputedStyle(el).backgroundColor') == 'rgba(33, 150, 243, 0.12)'
    elif control == 'profile':
        profile = page.get_by_test_id('library-author-profile').first
        expect(profile).to_be_visible()
        assert profile.evaluate('el => [el.offsetWidth, el.offsetHeight]') == [22, 22]
        assert profile.evaluate("el => getComputedStyle(el, '::after').width") == '28px'
    else:
        info = page.locator('.pv-library-author-card .pv-library-card-info.row').first
        assert info.evaluate('el => new Set([...el.children].map(n => getComputedStyle(n).fontSize)).size') == 1


def test_library_author_action_name_survives_menu_and_grid_resize(library_page):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    author = page.locator('.pv-library-author-card').filter(has_text='synthetic author')
    opener = author.get_by_test_id('library-author-open').first
    expect(opener).to_have_accessible_name('作者を開く：synthetic author…')
    opener.click(button='right')
    expect(page.get_by_role('menu').filter(visible=True)).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('menu').filter(visible=True)).to_have_count(0)
    page.set_viewport_size({'width': 1024, 'height': 768})
    page.get_by_test_id('library-size-small').click()
    expect(opener).to_have_accessible_name('作者を開く：synthetic author…')
    expect(author.locator('.pv-library-card-title').first).to_have_attribute('title', 'synthetic author')


def test_bookmark_button_hit_area_uses_common_center_and_omits_disabled_actions(library_page):
    from playwright.sync_api import expect

    page = library_page
    page.get_by_test_id('tab-library').click()
    page.get_by_test_id('library-subview-bookmarks').click()
    toggle = page.get_by_test_id('bookmark-filter-toggle')
    expect(toggle).to_be_visible()
    expect(toggle).to_be_enabled()
    area = toggle.evaluate('''el => {
        const area = getComputedStyle(el, '::after');
        const top = area.top.endsWith('%') ? parseFloat(area.top) * el.offsetHeight / 100 : parseFloat(area.top);
        const transform = new DOMMatrixReadOnly(area.transform);
        return {centerOffset: top - el.offsetHeight / 2, x: transform.e, y: transform.f,
                height: parseFloat(area.height), width: parseFloat(area.width)};
    }''')
    assert abs(area['centerOffset']) < 1, area
    assert area['y'] == pytest.approx(-area['height'] / 2, abs=1), area
    assert area['x'] == pytest.approx(-area['width'] / 2, abs=1), area
    download = page.get_by_test_id('bookmark-download')
    expect(download).to_be_disabled()
    assert download.evaluate("el => getComputedStyle(el, '::after').content === 'none'")


if __name__ == '__main__':
    # The existing audit launches the real app with a synthetic profile and fake clients.
    if not Path('fixture.json').is_file() or os.environ.get('PV_UIUX_STATE') != 'long':
        raise SystemExit('An isolated audit fixture is required')
    sys.path.insert(0, str(ROOT))
    from unittest.mock import patch

    from nicegui import ui
    from pixivvault_nicegui.ui.library_view import LibraryView
    from tools.uiux_fixture import serve_port

    initialize_library = LibraryView.__init__

    def library_with_projection_controls(view, *args, **kwargs):
        initialize_library(view, *args, **kwargs)
        if ui.context.client.request.query_params.get('r5_projection') != '1':
            return

        def filter_cards(query):
            if view.subview == 'bookmarks':
                view.bookmark_state.set_query(query)
                view.bookmarks.refresh()
            elif query:
                view.page_grid.publish([])
            else:
                view.render_pages()

        def arm():
            if view.subview == 'bookmarks':
                view._refresh_bookmark_projection()
                pending = view._bookmark_projection_pending
            else:
                view.reload(after_viewer=True)
                pending = view._reload_after_viewer
            status.set_text('pending' if pending else 'not pending')

        def drop_and_close():
            filter_cards('no matching synthetic bookmark')
            view.close_viewer()

        with view.root:
            status = ui.label().props('data-testid=r5-projection-status')
            ui.button('保留', on_click=arm).props('data-testid=r5-projection-arm')
            ui.button('一覧から外す', on_click=lambda: filter_cards('no matching synthetic bookmark')).props(
                'data-testid=r5-projection-drop')
            ui.button('一覧に戻す', on_click=lambda: filter_cards('')).props('data-testid=r5-projection-return')
            ui.button('一覧から外して閉じる', on_click=drop_and_close).props('data-testid=r5-projection-drop-close')

    with patch.object(LibraryView, '__init__', library_with_projection_controls):
        serve_port(int(sys.argv[2]), sys.argv[3].removeprefix('--nonce='))
