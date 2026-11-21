"""Exercise the shipped CardGrid template with NiceGUI's Vue and Quasar in Chromium."""
from pathlib import Path

import nicegui
import pytest

from pixivvault_nicegui.ui import card_grid

STATIC = Path(nicegui.__file__).with_name('static')
GRID = Path(card_grid.__file__).with_suffix('.js')
ORIGIN = 'http://127.0.0.1:22766'


@pytest.fixture
def page(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as runner:
        if not Path(runner.chromium.executable_path).is_file():
            pytest.skip('Playwright Chromium is unavailable')
        browser = runner.chromium.launch_persistent_context(
            str(tmp_path / 'browser'), viewport={'width': 1280, 'height': 800}, headless=True,
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking'])
        try:
            browser.route(ORIGIN + '/', lambda route: route.fulfill(body='<div id="app"></div>', content_type='text/html'))
            browser.route(ORIGIN + '/media/**', lambda route: route.fulfill(status=503))
            browser.route('**/*', lambda route: route.fallback() if route.request.url.startswith(ORIGIN + '/') else route.abort())
            page = browser.pages[0]
            page.goto(ORIGIN + '/')
            page.add_style_tag(content='.pv-card{height:180px}.grid{display:grid;grid-template-columns:repeat(6,1fr)}')
            page.evaluate('''async source => {
                const url = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
                try { window.Vue = await import(url); } finally { URL.revokeObjectURL(url); }
            }''', (STATIC / 'vue.esm-browser.prod.js').read_text(encoding='utf-8'))
            page.add_script_tag(path=STATIC / 'quasar.umd.prod.js')
            page.add_script_tag(content=GRID.read_text(encoding='utf-8').replace(
                'export default {', 'window.CardGrid = {', 1))
            page.evaluate("""() => {
                window.path_prefix = '';
                window.packets = [];
                const cards = Array.from({length: 24}, (_, index) => ({
                    work_id: String(index), title: 'fixture', rank: '#' + index,
                    heart: index === 0, src: index === 1 ? 'https://blocked.invalid/x' : '/media/' + index,
                    artwork_id: String(index), page_no: 0, disabled: index === 1, external: index === 0,
                    similar: index === 0, profile: '/explicit-profile', checked: false, count: null,
                    menu_id: 'menu-' + index,
                    menu: [{key:'link', enabled:true, label:'Link', testid:'link-' + index, href: '/explicit'}],
                }));
                window.props = Vue.reactive({cards, grid_active:true, render_epoch:1});
                const app = Vue.createApp({render: () => Vue.h(CardGrid, {
                    ...props, class:'grid', ref:'grid', onCardAction: item => packets.push(item)
                })});
                app.use(Quasar);
                window.host = app.mount('#app');
                window.readyAtMount = host.$refs.grid.ready;
            }""")
            yield page
        finally:
            browser.close()


def test_heart_visible_rows_and_invalid_source(page):
    from playwright.sync_api import expect

    assert page.evaluate('window.readyAtMount')
    expect(page.locator('[data-work-id="0"] .pv-card-head [data-testid=ranking-heart-0]')).to_be_visible()
    expect(page.locator('[data-work-id="6"] img')).to_have_attribute('loading', 'eager')
    expect(page.locator('[data-work-id="12"] img')).to_have_attribute('loading', 'eager')
    expect(page.locator('[data-work-id="18"] img')).to_have_attribute('loading', 'lazy')
    expect(page.locator('[data-work-id="1"] [data-testid=thumbnail-unavailable]')).to_be_visible()
    page.evaluate("props.layout = 'library-page'; props.library_placeholder = 'image'")
    expect(page.locator('[data-testid=library-image-placeholder][role=img][aria-label="画像未表示"]').nth(1)).to_be_visible()


def test_shared_menu_survives_delayed_previous_hide_and_keeps_focus(page):
    from playwright.sync_api import expect

    first = page.locator('[data-work-id="0"]')
    second = page.locator('[data-work-id="1"]')
    first.click(button='right')
    expect(page.get_by_test_id('link-0')).to_have_attribute('target', '_blank')
    expect(page.get_by_test_id('link-0')).to_have_attribute('rel', 'noopener noreferrer')
    page.evaluate("""() => {
        const menu = host.$refs.grid.$refs.menu;
        const instance = Array.isArray(menu) ? menu[0] : menu;
        window.deliverOldHide = instance.$.vnode.props.onHide;
    }""")
    second.click(button='right')
    expect(page.get_by_test_id('link-1')).to_be_visible()
    page.evaluate('deliverOldHide()')
    expect(page.get_by_test_id('link-1')).to_be_visible()
    page.evaluate("props.cards = props.cards.map(card => ({...card, heart:false}))")
    expect(page.get_by_test_id('link-1')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_test_id('link-1')).to_have_count(0)
    expect(second).to_be_focused()

    for key in ('Shift+F10', 'ContextMenu'):
        first.press(key)
        expect(page.get_by_test_id('link-0')).to_be_visible()
        page.keyboard.press('Escape')
        expect(first).to_be_focused()
    first.press('Space')
    assert page.evaluate('packets.length') == 0
    first.press('Enter')
    assert page.evaluate('packets.map(p => p.action)') == ['open']
    first.press('Shift+F10')
    expect(page.get_by_test_id('link-0')).to_be_visible()
    page.evaluate('props.grid_active = false')
    expect(page.locator('[role=menuitem][href]')).to_have_count(0)
    expect(first).to_have_attribute('tabindex', '-1')
    page.evaluate('props.grid_active = true')
    first.press('Shift+F10')
    expect(page.get_by_test_id('link-0')).to_be_visible()
    page.evaluate('props.render_epoch += 1')
    expect(page.get_by_test_id('link-0')).to_have_count(0)



def test_inactive_links_and_disabled_bookmark_focus(page):
    from playwright.sync_api import expect

    page.evaluate("props.layout = 'bookmark'")
    first, second = page.get_by_test_id('bookmark-open').nth(0), page.get_by_test_id('bookmark-open').nth(1)
    first.focus()
    second.wait_for()
    bounds = second.bounding_box()
    page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2, button='right')
    expect(page.get_by_test_id('link-1')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_test_id('link-1')).to_have_count(0)
    expect(first).to_be_focused()

    page.evaluate("props.layout = 'library-author'")
    profile = page.get_by_test_id('library-author-profile').first
    expect(profile).to_have_attribute('href', '/explicit-profile')
    page.evaluate('props.grid_active = false')
    expect(page.locator('[data-testid=library-author-profile][href]')).to_have_count(0)
    page.evaluate("props.layout = 'library-page'")
    expect(page.get_by_test_id('library-external-open')).to_be_disabled()
    expect(page.get_by_test_id('library-similar-open')).to_be_disabled()


def test_disabled_bookmark_replacement_restores_the_previous_card_opener(page):
    from playwright.sync_api import expect

    page.evaluate("props.layout = 'bookmark'")
    first, disabled = page.get_by_test_id('bookmark-open').nth(0), page.get_by_test_id('bookmark-open').nth(1)
    first.click(button='right')
    entry = page.get_by_test_id('link-0')
    expect(entry).to_be_visible()
    entry.focus()
    expect(entry).to_be_focused()
    bounds = disabled.bounding_box()
    page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2, button='right')
    expect(page.get_by_test_id('link-1')).to_be_visible()
    page.keyboard.press('Escape')
    expect(first).to_be_focused()


def test_disjoint_replacement_inserts_first_thumbnail_before_eager_siblings(page):
    from playwright.sync_api import expect

    dispatched = []
    page.context.on('request', lambda request: dispatched.append(request.url) if '/media/shift-' in request.url else None)
    page.evaluate('''() => {
        props.cards = props.cards.map((card, index) => ({
            ...card, work_id: 'shift-' + index, src: '/media/shift-' + index
        }));
    }''')
    expect(page.locator('[data-work-id="shift-0"] [role=status]')).to_be_visible()
    expect(page.locator('[data-work-id="shift-12"] img')).to_have_attribute('loading', 'eager')
    expect(page.locator('[data-work-id="shift-12"] [role=status]')).to_be_visible()
    assert dispatched and dispatched[0].endswith('/media/shift-0'), dispatched[:6]

def test_retry_reloads_only_failed_thumbnail(page):
    from playwright.sync_api import expect

    phase = {'recover': False}
    requests = []
    svg = "<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>"

    def image(route):
        failed = route.request.url.endswith('/failing')
        requests.append(failed)
        if failed and not phase['recover']:
            route.fulfill(status=503)
        else:
            route.fulfill(body=svg, content_type='image/svg+xml')

    page.route(ORIGIN + '/media/retry/**', image)
    page.evaluate("""
        props.cards = [
            {...props.cards[0], src: '/media/retry/failing'},
            {...props.cards[1], src: '/media/retry/loaded'},
        ];
    """)
    first, second = (page.locator(f'[data-work-id="{index}"]') for index in range(2))
    expect(first.get_by_role('status')).to_be_visible()
    page.wait_for_function("document.querySelector('[data-work-id=\"1\"] img')?.naturalWidth === 8")
    image_before = second.locator('img').element_handle()
    phase['recover'] = True
    page.evaluate('props.retry_epoch = 1')
    expect(first.get_by_role('status')).to_have_count(0)
    page.wait_for_function("document.querySelector('[data-work-id=\"0\"] img')?.naturalWidth === 8")
    assert image_before.evaluate('node => node.isConnected')
    assert requests.count(True) == 2 and requests.count(False) == 1


def test_partial_overlap_prioritizes_first_new_thumbnail_without_reloading_survivor(page):
    from playwright.sync_api import expect

    survivor = page.locator('[data-work-id="2"] img')
    expect(page.locator('[data-work-id="2"] [role=status]')).to_be_visible()
    old_image = survivor.element_handle()
    requests = []

    def record(request):
        path = request.url.rsplit('/', 1)[-1]
        if path == '2' or path.startswith('partial-'):
            requests.append(path)

    page.context.on('request', record)
    page.evaluate('''() => {
        const old = props.cards[2];
        props.cards = props.cards.map((card, index) => index === 20 ? old : ({
            ...card, work_id: 'partial-' + index, src: '/media/partial-' + index
        }));
    }''')
    expect(page.locator('[data-work-id="partial-0"] [role=status]')).to_be_visible()
    expect(page.locator('[data-work-id="partial-12"] img')).to_have_attribute('loading', 'eager')
    expect(page.locator('[data-work-id="partial-12"] [role=status]')).to_be_visible()
    assert requests and requests[0] == 'partial-0', requests[:6]
    assert '2' not in requests
    assert old_image.evaluate('node => node === document.querySelector(\'[data-work-id="2"] img\')')
