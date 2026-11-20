from pathlib import Path

import nicegui
import pytest

from pixivvault_nicegui.ui import verified_image


def test_verified_image_runs_once_per_request_and_reports_only_current_decode(tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    vue = Path(nicegui.__file__).with_name('static') / 'vue.esm-browser.prod.js'
    component = Path(verified_image.__file__).with_suffix('.js')
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip('Playwright Chromium is unavailable')
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / 'chromium'), headless=True,
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking'],
        )
        try:
            context.route('**/*', lambda route: route.abort())
            page = context.pages[0]
            page.set_content('<div id="mount"></div>')
            page.evaluate('''async source => {
                const url = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
                try { window.pvMountVue = await import(url); }
                finally { URL.revokeObjectURL(url); }
            }''', vue.read_text(encoding='utf8'))
            page.add_script_tag(content=component.read_text(encoding='utf8').replace(
                'export default {', 'window.pvVerifiedImage = {', 1,
            ))
            result = page.evaluate('''async () => {
                const Vue = window.pvMountVue;
                const image = window.pvVerifiedImage;
                const svg = color => 'data:image/svg+xml,' + encodeURIComponent(
                    `<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2">` +
                    `<rect width="2" height="2" fill="${color}"/></svg>`);
                const props = Vue.reactive({src: '', request_id: 0, retain_previous: false});
                const verified = image.methods.verify;
                const calls = [], events = [], errors = [], pending = [];
                image.methods.verify = function() {
                    calls.push(this.request_id);
                    return verified.call(this);
                };
                const decode = HTMLImageElement.prototype.decode;
                HTMLImageElement.prototype.decode = function() {
                    const ready = decode.call(this);
                    ready.catch(() => {});
                    return new Promise((resolve, reject) => {
                        pending.push(() => ready.then(resolve, reject));
                    });
                };
                const app = Vue.createApp({render: () => Vue.h(image, {...props,
                    onLoad: source => events.push(source), onError: source => errors.push(source)})});
                app.mount('#mount');
                const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
                const update = async propsUpdate => {
                    Object.assign(props, propsUpdate);
                    await Vue.nextTick();
                    await Vue.nextTick();
                };
                const releaseDecodes = async () => {
                    for (const release of pending.splice(0).reverse()) await release();
                    await frame();
                    await frame();
                };
                const displayed = () => document.querySelector('[data-pv-displayed=true]');

                await update({src: svg('red'), request_id: 1, event_source: 'first'});
                await update({src: svg('blue'), request_id: 2, event_source: 'second', retain_previous: true});
                await releaseDecodes();
                const blue = displayed();
                const currentDisplayed = blue?.src === props.src;

                await update({request_id: 3, event_source: 'same source'});
                const retainedDuringRetry = displayed() === blue;
                await releaseDecodes();
                const retried = displayed();

                await update({src: 'data:image/png;base64,AA==', request_id: 4, event_source: 'broken'});
                await releaseDecodes();
                const retainedAfterError = displayed() === retried;

                await update({src: svg('green'), request_id: 5, event_source: 'unmounted'});
                app.unmount();
                await releaseDecodes();
                return {calls, events, errors, currentDisplayed, retainedDuringRetry,
                    retainedAfterError, unmounted: displayed() === null};
            }''')
        finally:
            context.close()
    assert result == {
        'calls': [0, 1, 2, 3, 4, 5],
        'events': ['second', 'same source'],
        'errors': ['broken'],
        'currentDisplayed': True,
        'retainedDuringRetry': True,
        'retainedAfterError': True,
        'unmounted': True,
    }


def test_local_preload_promotes_the_decoded_node_only_for_an_adjacent_request(tmp_path):
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
    vue = Path(nicegui.__file__).with_name('static') / 'vue.esm-browser.prod.js'
    component = Path(verified_image.__file__).with_suffix('.js')
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip('Playwright Chromium is unavailable')
        context = playwright.chromium.launch_persistent_context(
            str(tmp_path / 'chromium'), headless=True,
            args=['--proxy-server=http://127.0.0.1:1', '--disable-background-networking'],
        )
        try:
            requested = []

            def route(request):
                url = request.request.url
                if url not in {'http://127.0.0.1/media/p2', 'http://127.0.0.1/media/p3'}:
                    request.abort()
                    return
                requested.append(url)
                headers = {'Cache-Control': 'no-store'}
                if url.endswith('/p3') and requested.count(url) == 1:
                    request.fulfill(status=404, headers=headers, body='')
                else:
                    request.fulfill(content_type='image/svg+xml', headers=headers,
                                    body='<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>')

            context.route('**/*', route)
            page = context.pages[0]
            page.set_content('<base href="http://127.0.0.1/"><div id="mount"></div>')
            page.evaluate('''async source => {
                const url = URL.createObjectURL(new Blob([source], {type: 'text/javascript'}));
                try { window.pvMountVue = await import(url); }
                finally { URL.revokeObjectURL(url); }
            }''', vue.read_text(encoding='utf8'))
            page.add_script_tag(content=component.read_text(encoding='utf8').replace(
                'export default {', 'window.pvVerifiedImage = {', 1,
            ))
            page.evaluate('''() => {
                const Vue = window.pvMountVue;
                window.events = [];
                window.decoded = [];
                window.decodeErrors = 0;
                const decode = HTMLImageElement.prototype.decode;
                HTMLImageElement.prototype.decode = function() {
                    return decode.call(this).then(() => window.decoded.push(this), error => {
                        window.decodeErrors++;
                        throw error;
                    });
                };
                window.imageProps = Vue.reactive({src:'', request_id:0, preload_src:'/media/p2'});
                Vue.createApp({render: () => Vue.h(window.pvVerifiedImage, {
                    ...window.imageProps, onLoad: source => window.events.push(source),
                    onError: () => window.events.push('error'),
                })}).mount('#mount');
            }''')
            page.wait_for_function('window.decoded.length === 1', timeout=3000)
            assert page.evaluate('window.events') == []
            assert page.evaluate("window.decoded[0].dataset.pvDisplayed") == 'false'
            page.evaluate('''() => Object.assign(window.imageProps, {
                src:'/media/p2#request-1', preload_src:'', use_preload:true, request_id:1
            })''')
            page.wait_for_function('window.events.length === 1')
            assert page.evaluate('window.events') == ['/media/p2#request-1']
            assert page.evaluate('window.decoded.length') == 1
            assert page.evaluate("window.decoded[0] === document.querySelector('[data-pv-displayed=true]')")
            assert requested == ['http://127.0.0.1/media/p2']

            page.evaluate('''() => Object.assign(window.imageProps, {
                src:'/media/p2#request-2', use_preload:false, request_id:2
            })''')
            page.wait_for_function('window.events.length === 2')
            assert page.evaluate('window.events')[-1] == '/media/p2#request-2'
            assert page.evaluate("window.decoded[0] !== document.querySelector('[data-pv-displayed=true]')")

            page.evaluate("() => { window.imageProps.preload_src = '/media/p3'; }")
            page.wait_for_function('window.decodeErrors === 1')
            assert page.evaluate('window.events.length') == 2
            page.evaluate('''() => Object.assign(window.imageProps, {
                src:'/media/p3#request-3', preload_src:'', use_preload:true, request_id:3
            })''')
            page.wait_for_function('window.events.length === 3')
            assert page.evaluate('window.events')[-1] == '/media/p3#request-3'
            assert requested.count('http://127.0.0.1/media/p3') == 2
        finally:
            context.close()
