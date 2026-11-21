"""Isolated browser exercises for the viewer's real controls and media route."""

import re

from playwright.sync_api import expect


def install_fixture(app, services, base):
    from pixivvault_nicegui.desktop_integration import DesktopIntegration
    from types import SimpleNamespace

    image_root = base / 'library' / 'Images'
    actions = {'downloads': [], 'folders': []}

    def opened(folder):
        actions['folders'].append(folder.relative_to(image_root).as_posix())

    async def save(work, page, pages, author, *, is_current, **kwargs):
        assert is_current()
        actions['downloads'].append([work, page, pages, author])
        return SimpleNamespace(export_error=False)

    services.desktop = DesktopIntegration(allowed_roots=(image_root,),
                                          launcher=SimpleNamespace(open_default=opened))
    services.save_viewer_page = save

    @app.get('/__d6_viewer_actions')
    def read_actions():
        return actions


def exercise(page, base, open_viewer, image_ready, extra_pages):
    outcome = []
    failed_page = re.compile(r'/media/100000/2/view\.png(?:\?|$)')
    fail = True
    blocked = []

    def fail_image_once_enabled(route):
        if fail:
            blocked.append(1)
            route.abort()
        else:
            route.fallback()

    page.route(failed_page, fail_image_once_enabled)
    opener = open_viewer()
    viewer = page.get_by_test_id('library-viewer')
    image = page.get_by_test_id('library-viewer-image')
    counter = viewer.locator('.pv-counter')
    quality = viewer.get_by_test_id('viewer-quality-toggle')
    download = viewer.get_by_test_id('viewer-download')
    zoom = viewer.get_by_test_id('viewer-zoom-value')
    details = viewer.get_by_test_id('viewer-detail-panel')
    retry = viewer.get_by_role('button', name='画像を再読込')

    def at(index, *, variant='view'):
        expect(counter).to_have_text(f'{index + 1}/{extra_pages + 1}')
        expect(image).to_have_attribute('aria-busy', 'false')
        expect(image.locator('img[data-pv-displayed=true]')).to_have_attribute(
            'src', re.compile(rf'/media/100000/{index}/{variant}\.png(?:[?#]|$)'))

    def actions():
        return page.evaluate("async () => (await fetch('/__d6_viewer_actions')).json()")

    at(0)
    expect(viewer.get_by_role('button', name='前のページ')).to_be_disabled()
    initial_size = page.viewport_size
    for size in [(1280, 800), (1920, 1080)]:
        page.set_viewport_size(dict(zip(('width', 'height'), size)))
        page.screenshot(path=base / f'viewer-{size[0]}x{size[1]}.png', animations='disabled')
    page.set_viewport_size(initial_size)
    viewer.get_by_test_id('viewer-zoom-in').click()
    expect(zoom).to_have_text('125%')
    viewer.get_by_role('button', name='次のページ').click()
    at(1)
    expect(zoom).to_have_text('100%')
    viewer.get_by_role('button', name='前のページ').click()
    at(0)
    outcome.append('forward, back, zoom reset')

    viewer.evaluate("""el => {
        el.focus();
        for (let count = 0; count < 2; count++)
            el.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
    }""")
    expect(counter).to_have_text(f'3/{extra_pages + 1}')
    expect(image).to_have_attribute('aria-busy', 'false')
    expect(retry).to_be_visible()
    expect(download).to_be_disabled()
    assert blocked

    fail = False
    retry.click()
    at(2)
    image_ready(100000, 2)
    expect(retry).to_be_hidden()
    outcome.append('rapid forward, failed page, retry, verified pixels')

    quality.click()
    at(2, variant='original')
    expect(viewer).to_have_attribute('data-viewer-quality', 'original')
    quality.click()
    at(2)
    expect(viewer).to_have_attribute('data-viewer-quality', 'view')
    download.click()
    expect(download).to_have_text(re.compile('保存済'))
    assert actions()['downloads'] == [['100000', 2, extra_pages + 1, '30000']]
    outcome.append('normal/original exact page, explicit download exact page')

    viewer.focus()
    page.keyboard.press('Shift+F10')
    page.get_by_test_id('viewer-context-folder').click()
    page.wait_for_function("async () => (await (await fetch('/__d6_viewer_actions')).json()).folders.length === 1")
    assert actions()['folders'] == ['Bench Author 000(30000)']
    outcome.append('keyboard context menu and saved folder')

    viewer.focus()
    page.keyboard.press('i')
    expect(details).to_be_visible()
    page.keyboard.press('Escape')
    expect(details).to_be_hidden()
    expect(viewer).to_be_visible()
    page.keyboard.press('Escape')
    expect(viewer).to_be_hidden()
    expect(opener).to_be_focused()
    outcome.append('two-step Esc and focus restoration')
    return outcome
