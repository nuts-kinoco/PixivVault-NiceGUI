"""Compare A17 on the same isolated product fixture used for S4."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from time import monotonic
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Reuse the audited S4 fixture and navigation rather than duplicating them.
FIXTURE_REV = '739185b0e12d5d268351e98752fa910b98cfd2b2'
fixture_source = subprocess.check_output(
    ['git', 'show', f'{FIXTURE_REV}:verify/navimpl-20261123/r1/check_product.py'], cwd=ROOT)
fixture = {'__name__': 's4_fixture', '__file__': __file__}
exec(compile(fixture_source, 's4/check_product.py', 'exec'), fixture)

CARDS = ('ranking', 'personal', 'library', 'library-works', 'bookmarks', 'recommend', 'dedup')
SCREENS = (*CARDS, 'download', 'view-history', 'learning')
THRESHOLDS = {1280: .55, 1920: .60, 1024: .40}
READY = """({region,image}) => [...document.querySelectorAll(region)].some(host =>
    host.checkVisibility() && [...host.querySelectorAll(image)].some(e =>
        e.getClientRects().length && e.checkVisibility({opacityProperty:true, visibilityProperty:true}) &&
        (e.tagName === 'IMG' ? e.complete && e.naturalWidth > 0 :
        getComputedStyle(e).backgroundImage.startsWith('url('))))"""
RECOMMEND_LAYOUT = """() => [...document.querySelectorAll(
    '.pv-tone-recommend > *, .pv-recommend-shelves > *, .pv-recommend-shelf-header'
)].filter(e => e.checkVisibility()).slice(0, 16).map(e => {
    const {top,height} = e.getBoundingClientRect();
    return [e.dataset.testid || e.className, top, height];
})"""
DEDUP_LAYOUT = RECOMMEND_LAYOUT.replace(
    '.pv-tone-recommend > *, .pv-recommend-shelves > *, .pv-recommend-shelf-header',
    '.pv-tone-dedup > *, .pv-tone-dedup .pv-nav-fixed-tools > *, '
    '.pv-dedup-status > *, .pv-dedup-near > *').replace('slice(0, 16)', 'slice(0, 30)')


def run(scratch, port, result, *, baseline, narrow_pilot):
    p5, audit, area, smoke = (fixture[name] for name in ('p5', 'audit', 'area', 'smoke'))
    base, seed = scratch / 'product', scratch / 'seed'
    seed.mkdir()
    p5.seed_profile(base, seed, 'normal', tone_mode=True)
    fixture['fixture'].seed_area_profile(base, seed)
    (base / 'automation-state.txt').write_text('failed', encoding='ascii')
    nonce = secrets.token_urlsafe(24)
    owned = fixture['OwnedProcesses']()
    try:
        with p5.running_app('port', sys.executable, base, port, nonce, tone_mode=True,
                            personal_fixture=True, server_script=Path(__file__).resolve(),
                            startup_timeout=20, extra_env={
                                'PV_UIUX_STATE': 'normal', 'PV_UI_IMAGE_AREA': '1',
                                'PV_UIUX_RUN_SCRATCH': str(scratch),
                            }) as url, fixture['sync_playwright']() as pw:
            allowed = urlsplit(url).netloc

            def route_http(route):
                if urlsplit(route.request.url).netloc == allowed:
                    route.continue_()
                else:
                    result['foreign_requests'] += 1
                    route.abort()

            def route_ws(route):
                if urlsplit(route.url).netloc == allowed:
                    route.connect_to_server()
                else:
                    result['foreign_requests'] += 1
                    route.close()

            for zoom in ((100,) if baseline else (100, 200) if narrow_pilot else (100, 125, 200)):
                profile = audit.browser_profile(scratch, zoom)
                with pw.chromium.launch_persistent_context(
                        profile, channel='msedge', headless=True, viewport={'width':1280, 'height':800},
                        device_scale_factor=1, service_workers='block',
                        env=os.environ | {'LOCALAPPDATA': str(profile / 'local'), 'APPDATA': str(profile / 'roaming')},
                        args=['--disable-background-networking', '--disable-crash-reporter', '--no-first-run',
                              '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                              f'--disk-cache-dir={profile / "cache"}']) as ctx:
                    observed_zoom = audit.set_browser_zoom(ctx, zoom)
                    ctx.route('**/*', route_http)
                    ctx.route_web_socket('**/*', route_ws)
                    owned.observe()
                    page = ctx.pages[0]
                    page.set_default_timeout(4000)
                    page.on('pageerror', lambda _: result.update(browser_errors=result['browser_errors'] + 1))
                    for w,h,z in fixture['SIZES']:
                        if z != zoom or baseline and w not in (1280, 640):
                            continue
                        narrow = w * 100 / z <= 640
                        if narrow_pilot and not narrow:
                            continue
                        page.set_viewport_size({'width': w, 'height': h})
                        for theme in (('dark',) if baseline else ('dark', 'light')):
                            for variant in (('stress',) if narrow_pilot else ('normal', 'stress')):
                                mode = 'request-limit' if variant == 'stress' else 'normal'
                                page.goto(f'{url}/?_audit_theme={theme}&case={variant}&_tone_retry={mode}',
                                          wait_until='domcontentloaded', timeout=6000)
                                if (theme == 'light') == bool(page.locator('body.body--dark').count()):
                                    fixture['click'](page, 'theme-toggle')
                                target = fixture['target']
                                if variant == 'stress':
                                    fixture['expect'](target(page, 'automation-unresolved')).to_have_text('自動処理：未解消 2')
                                cell = dict(device=[w,h], zoom=z, browser_zoom=observed_zoom, theme=theme,
                                            variant=variant, header=page.evaluate(fixture['HEADER']), screens={})
                                result['cells'].append(cell)
                                if variant == 'stress':
                                    for key in ('warning', 'recovery', 'unresolved', 'breakdown'):
                                        if not cell['header'][key]:
                                            result['failures'].append([w,h,z,theme,variant,'header',key])
                                for screen in (('dedup',) if narrow_pilot else CARDS if baseline else SCREENS):
                                    try:
                                        fixture['open_screen'](page, screen)
                                        info = area.target_for(screen)
                                        if info['image']:
                                            page.wait_for_function(READY, arg={key: info[key] for key in ('region', 'image')})
                                        measure = area.measure_image_area(page, info)
                                        cell['screens'][screen] = measure
                                        if screen == 'recommend':
                                            measure['layout'] = page.evaluate(RECOMMEND_LAYOUT)
                                        elif screen == 'dedup':
                                            measure['layout'] = page.evaluate(DEDUP_LAYOUT)
                                        ratio = measure['area_ratio'] or 0
                                        limit = THRESHOLDS.get(w) if z == 100 and variant == 'normal' else None
                                        if (w, h, z, variant) == (1280, 800, 100, 'stress'):
                                            limit = .55
                                        if screen in CARDS and limit and ratio < limit:
                                            result['failures'].append([w,h,z,theme,variant,screen,'area',round(ratio,4)])
                                        if narrow and variant == 'stress' and screen in (*CARDS, 'view-history'):
                                            if not measure['first_image_complete'] or not measure['complete_images']:
                                                result['failures'].append([w,h,z,theme,variant,screen,'first image clipped'])
                                        if screen == 'learning' and (w,h,z) == (1280,800,100):
                                            iw, ih = measure['image_rect'][2:]
                                            if round(iw,1) < 618.6 or round(ih,1) < 464.0:
                                                result['failures'].append([w,h,z,theme,variant,screen,'image floor'])
                                        if screen == 'view-history':
                                            fixture['click'](page, 'view-history-close')
                                    except Exception as exc:
                                        result['failures'].append([w,h,z,theme,variant,screen,type(exc).__name__])
                                        if target(page, 'view-history-close').is_visible():
                                            fixture['click'](page, 'view-history-close')
                                check_desktop = (w, h, z) == (1280, 800, 100)
                                check_controls = theme == 'dark' and variant == 'stress' and (narrow or check_desktop)
                                if not baseline and not narrow_pilot and (check_desktop or check_controls):
                                    try:
                                        cell['judgments'] = fixture['judgments'](page)
                                        if not all(q['enabled'] and q['unobscured'] for q in cell['judgments']):
                                            result['failures'].append([w,h,z,theme,variant,'learning','judgment focus'])
                                        if check_controls:
                                            fixture['click'](page, 'db-warning-details')
                                            dialog = target(page, 'db-warning-dialog')
                                            fixture['expect'](dialog).to_contain_text('保存用フォルダーの長い名前' * 7)
                                            page.keyboard.press('Escape')
                                            fixture['expect'](target(page, 'db-warning-details')).to_be_focused()
                                            cell['db_focus_return'] = True
                                            for key, child in (('recommend', 'recommend-section-trend'),
                                                               ('dedup', 'duplicate-bulk-low')):
                                                fixture['navigate'](page, key)
                                                toggle = target(page, f'toolbar-toggle-{key}')
                                                fixture['expect'](target(page, child)).to_be_hidden()
                                                toggle.press('Enter')
                                                fixture['expect'](target(page, child)).to_be_visible()
                                                page.keyboard.press('Escape')
                                                fixture['expect'](toggle).to_be_focused()
                                                fixture['expect'](target(page, child)).to_be_hidden()
                                            cell['toolbar_focus_return'] = True
                                    except Exception as exc:
                                        result['failures'].append([w,h,z,theme,variant,'keyboard',type(exc).__name__])
            result['server_network_denied'] = smoke.network_state(url, nonce)['denied']
    finally:
        lifecycle = owned.result(scratch, (port,))
        result['owned_remaining_count'] = len(lifecycle['remaining'])
        result['owned_ports_closed'] = all(lifecycle['owned_ports_closed'].values())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve-port', type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--port', type=int, default=22631)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--narrow-pilot', action='store_true')
    args = parser.parse_args()
    if args.serve_port:
        fixture['serve'](args.serve_port, args.nonce)
    else:
        start = monotonic()
        result = dict(cells=[], failures=[], browser_errors=0, foreign_requests=0)
        try:
            with tempfile.TemporaryDirectory(prefix='a17-', dir=os.environ['TEMP']) as folder:
                run(Path(folder), args.port, result, baseline=args.baseline, narrow_pilot=args.narrow_pilot)
        finally:
            result['seconds'] = round(monotonic() - start, 2)
            fixture['p5'].write_json(args.out, result)
        raise SystemExit(int(bool(result['failures'] or result['browser_errors'] or
                                  result['foreign_requests'] or result['server_network_denied'] or
                                  result['owned_remaining_count'] or not result['owned_ports_closed'])))
