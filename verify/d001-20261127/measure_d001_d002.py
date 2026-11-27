"""Isolated browser measurement for D-001 (640x400 & 1280x800@200% stress) and D-002 (A8 font-size)."""
from __future__ import annotations

import argparse
import json
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

FIXTURE_REV = '739185b0e12d5d268351e98752fa910b98cfd2b2'
fixture_source = subprocess.check_output(
    ['git', 'show', f'{FIXTURE_REV}:verify/navimpl-20261123/r1/check_product.py'], cwd=ROOT)
fixture = {'__name__': 's4_fixture', '__file__': __file__}
exec(compile(fixture_source, 's4/check_product.py', 'exec'), fixture)

CARDS = ('ranking', 'personal', 'library', 'library-works', 'bookmarks', 'recommend', 'dedup')
READY = """({region,image}) => [...document.querySelectorAll(region)].some(host =>
    host.checkVisibility() && [...host.querySelectorAll(image)].some(e =>
        e.getClientRects().length && e.checkVisibility({opacityProperty:true, visibilityProperty:true}) &&
        (e.tagName === 'IMG' ? e.complete && e.naturalWidth > 0 :
        getComputedStyle(e).backgroundImage.startsWith('url('))))"""

SAFETY_PROBE = """() => {
    const rect = sel => {
        const el = [...document.querySelectorAll(sel)].find(e => e.checkVisibility({visibilityProperty:true, opacityProperty:true}));
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                scrollH: el.scrollHeight, clientH: el.clientHeight,
                overflowY: getComputedStyle(el).overflowY, maxHeight: getComputedStyle(el).maxHeight};
    };
    return {
        db_warning: rect('[data-testid=db-status-banner]'),
        db_details_btn: rect('[data-testid=db-warning-details]'),
        recovery: rect('[data-testid=shared-get-recovery-open]'),
        retry_status: rect('[data-testid=request-retry-status]'),
        stop_details_toggle: rect('[data-testid=request-retry-stop-details-toggle]'),
        retry_dismiss: rect('[data-testid=request-retry-dismiss]'),
        unresolved: rect('[data-testid=automation-unresolved]'),
        breakdown: rect('[data-testid=automation-breakdown]'),
        horizontal_overflow: document.documentElement.scrollWidth > window.innerWidth,
    };
}"""


def run_d001(scratch: Path, port: int, out_dir: Path, *, include_desktop: bool, check_keyboard: bool):
    p5, audit, area, smoke = (fixture[name] for name in ('p5', 'audit', 'area', 'smoke'))
    base, seed = scratch / 'product', scratch / 'seed'
    seed.mkdir(parents=True, exist_ok=True)
    p5.seed_profile(base, seed, 'normal', tone_mode=True)
    fixture['fixture'].seed_area_profile(base, seed)
    (base / 'automation-state.txt').write_text('failed', encoding='ascii')
    nonce = secrets.token_urlsafe(24)
    owned = fixture['OwnedProcesses']()
    png_dir = out_dir / 'png'
    png_dir.mkdir(parents=True, exist_ok=True)
    result = dict(cells=[], failures=[], browser_errors=0, foreign_requests=0, keyboard={})
    try:
        with p5.running_app('port', sys.executable, base, port, nonce, tone_mode=True,
                            personal_fixture=True, server_script=Path(__file__).resolve(),
                            startup_timeout=25, extra_env={
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

            zoom_specs = [
                (100, [(640, 400, 100)] + ([(1280, 800, 100)] if include_desktop else [])),
                (200, [(1280, 800, 200)]),
            ]
            for zoom, size_list in zoom_specs:
                profile = audit.browser_profile(scratch, zoom)
                with pw.chromium.launch_persistent_context(
                        profile, channel='msedge', headless=True, viewport={'width': 1280, 'height': 800},
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
                    page.set_default_timeout(6000)
                    page.on('pageerror', lambda _: result.update(browser_errors=result['browser_errors'] + 1))
                    for w, h, z in size_list:
                        narrow = w * 100 / z <= 640
                        page.set_viewport_size({'width': w, 'height': h})
                        variants = ('stress',) if narrow else ('normal', 'stress')
                        for theme in ('dark', 'light'):
                            for variant in variants:
                                mode = 'request-limit' if variant == 'stress' else 'normal'
                                page.goto(f'{url}/?_audit_theme={theme}&case={variant}&_tone_retry={mode}',
                                          wait_until='domcontentloaded', timeout=8000)
                                if (theme == 'light') == bool(page.locator('body.body--dark').count()):
                                    fixture['click'](page, 'theme-toggle')
                                # Dismiss any non-safety startup hint toast so it does not cover the narrow nav dropdown.
                                for close_btn in page.locator('.q-notification .q-btn').filter(visible=True).all():
                                    try:
                                        close_btn.click(timeout=500)
                                    except Exception:
                                        pass
                                target = fixture['target']
                                if variant == 'stress':
                                    fixture['expect'](target(page, 'automation-unresolved')).to_have_text('自動処理：未解消 2')
                                    fixture['expect'](target(page, 'request-retry-status')).to_contain_text('試行 3/3')
                                    fixture['expect'](target(page, 'request-retry-status')).to_contain_text('試行 2/3')
                                cell = dict(device=[w, h], zoom=z, browser_zoom=observed_zoom, theme=theme,
                                            variant=variant, safety=page.evaluate(SAFETY_PROBE), screens={})
                                result['cells'].append(cell)
                                for screen in CARDS:
                                    try:
                                        fixture['open_screen'](page, screen)
                                        info = area.target_for(screen)
                                        if info['image']:
                                            page.wait_for_function(READY, arg={key: info[key] for key in ('region', 'image')}, timeout=8000)
                                        measure = area.measure_image_area(page, info)
                                        png_name = f'{w}x{h}-{z}-{theme}-{variant}-{screen}.png'
                                        if narrow or (variant == 'stress' and screen == 'ranking'):
                                            page.screenshot(path=str(png_dir / png_name), animations='disabled')
                                            measure['png'] = f'png/{png_name}'
                                        cell['screens'][screen] = measure
                                        if narrow and variant == 'stress':
                                            if not measure['first_image_complete'] or not measure['complete_images']:
                                                result['failures'].append([w, h, z, theme, variant, screen,
                                                                           f"complete={measure['complete_images']}"])
                                        elif not narrow and variant == 'normal':
                                            ratio = measure['area_ratio'] or 0
                                            if ratio < 0.55:
                                                result['failures'].append([w, h, z, theme, variant, screen,
                                                                           f'area={ratio:.4f}'])
                                    except Exception as exc:
                                        result['failures'].append([w, h, z, theme, variant, screen,
                                                                   f'{type(exc).__name__}: {exc}'])
                                if check_keyboard and narrow and z == 100 and theme == 'light' and variant == 'stress':
                                    status_loc = target(page, 'request-retry-status')
                                    status_loc.focus()
                                    for _ in range(8):
                                        page.keyboard.press('PageDown')
                                        page.wait_for_timeout(50)
                                    scrolled = status_loc.evaluate(
                                        'el => ({top: el.scrollTop, max: el.scrollHeight - el.clientHeight, focused: document.activeElement === el})')
                                    toggle = target(page, 'request-retry-stop-details-toggle')
                                    toggle.focus()
                                    page.keyboard.press('Enter')
                                    details_vis = target(page, 'request-retry-stop-details-text').is_visible()
                                    toggle.focus()
                                    page.keyboard.press('Enter')
                                    # DB warning full text + Escape return
                                    db_btn = target(page, 'db-warning-details')
                                    db_btn.focus()
                                    page.keyboard.press('Enter')
                                    dialog = target(page, 'db-warning-dialog')
                                    fixture['expect'](dialog).to_contain_text('保存用フォルダーの長い名前' * 7)
                                    page.keyboard.press('Escape')
                                    fixture['expect'](db_btn).to_be_focused(timeout=2000)
                                    db_focus_back = db_btn.evaluate('el => document.activeElement === el')
                                    result['keyboard'] = dict(
                                        rate_stop_scroll=scrolled,
                                        stop_details_keyboard_open=details_vis,
                                        db_warning_focus_return=db_focus_back,
                                    )
            result['server_network_denied'] = smoke.network_state(url, nonce)['denied']
    finally:
        lifecycle = owned.result(scratch, (port,))
        result['owned_remaining_count'] = len(lifecycle['remaining'])
        result['owned_ports_closed'] = all(lifecycle['owned_ports_closed'].values())
    return result


def run_d002(scratch: Path, port: int):
    """Measure real computed font-size on synthetic long & normal profiles for all D-002 targets."""
    p5, audit, smoke = (fixture[name] for name in ('p5', 'audit', 'smoke'))
    base, seed = scratch / 'd002-long', scratch / 'd002-seed'
    seed.mkdir(parents=True, exist_ok=True)
    p5.seed_profile(base, seed, 'long', tone_mode=True)
    nonce = secrets.token_urlsafe(24)
    owned = fixture['OwnedProcesses']()
    result = dict(samples=[], browser_errors=0, foreign_requests=0)
    try:
        with p5.running_app('port', sys.executable, base, port, nonce, tone_mode=True,
                            personal_fixture=True, server_script=Path(__file__).resolve(),
                            startup_timeout=25, extra_env={
                                'PV_UIUX_STATE': 'long',
                                'PV_UIUX_RUN_SCRATCH': str(scratch),
                            }) as url, fixture['sync_playwright']() as pw:
            allowed = urlsplit(url).netloc
            profile = audit.browser_profile(scratch, 100)
            with pw.chromium.launch_persistent_context(
                    profile, channel='msedge', headless=True, viewport={'width': 1280, 'height': 800},
                    device_scale_factor=1, service_workers='block',
                    env=os.environ | {'LOCALAPPDATA': str(profile / 'local'), 'APPDATA': str(profile / 'roaming')},
                    args=['--disable-background-networking', '--disable-crash-reporter', '--no-first-run',
                          '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                          f'--disk-cache-dir={profile / "cache"}']) as ctx:
                ctx.route('**/*', lambda r: r.continue_() if urlsplit(r.request.url).netloc == allowed else (result.update(foreign_requests=result['foreign_requests']+1), r.abort()))
                ctx.route_web_socket('**/*', lambda r: r.connect_to_server() if urlsplit(r.url).netloc == allowed else (result.update(foreign_requests=result['foreign_requests']+1), r.close()))
                owned.observe()
                page = ctx.pages[0]
                page.set_default_timeout(6000)
                page.on('pageerror', lambda _: result.update(browser_errors=result['browser_errors'] + 1))

                def probe_sel(screen, name, sel, role, min_px, frozen):
                    info = page.evaluate('''(sel) => {
                        const el = [...document.querySelectorAll(sel)].find(e => e.checkVisibility({visibilityProperty:true, opacityProperty:true}));
                        if (!el) return null;
                        const cs = getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        return {font_px: parseFloat(cs.fontSize), weight: cs.fontWeight,
                                text: (el.innerText || '').trim().slice(0, 30),
                                w: +r.width.toFixed(1), h: +r.height.toFixed(1),
                                scrollW: el.scrollWidth, clientW: el.clientWidth};
                    }''', sel)
                    result['samples'].append(dict(screen=screen, name=name, selector=sel, role=role,
                                                  min_px=min_px, frozen=frozen, info=info))

                for theme in ('dark', 'light'):
                    page.goto(f'{url}/?_audit_theme={theme}&case=normal', wait_until='domcontentloaded', timeout=8000)
                    if (theme == 'light') == bool(page.locator('body.body--dark').count()):
                        fixture['click'](page, 'theme-toggle')

                    # 1. download
                    fixture['navigate'](page, 'download')
                    page.locator('.pv-ud-list .pv-ud-c-name').filter(visible=True).first.wait_for(timeout=6000)
                    probe_sel(f'download/{theme}', 'author-name', '.pv-ud-list .pv-ud-c-name', 'body', 13, False)

                    # 2. queue
                    fixture['navigate'](page, 'queue')
                    fixture['target'](page, 'queue-list').wait_for(timeout=5000)
                    probe_sel(f'queue/{theme}', 'job-title', '[data-testid=queue-list] .pv-job-title', 'body', 13, False)
                    probe_sel(f'queue/{theme}', 'heading-hint', '[data-testid=queue-heading] > div:nth-of-type(2)', 'aux', 11, True)

                    # 3. failed
                    fixture['navigate'](page, 'failed')
                    fixture['target'](page, 'failed-rows').wait_for(timeout=5000)
                    probe_sel(f'failed/{theme}', 'failure-title', '[data-testid=failed-rows] .pv-failure-title', 'body', 13, False)
                    probe_sel(f'failed/{theme}', 'failure-reason', '[data-testid=failed-rows] .pv-failure-reason', 'body', 13, False)

                    # 4. bookmarks
                    fixture['navigate'](page, 'bookmarks')
                    fixture['target'](page, 'bookmark-card').wait_for(timeout=5000)
                    probe_sel(f'bookmarks/{theme}', 'card-title', '[data-testid=bookmark-card] .pv-card-title', 'body', 13, False)
                    probe_sel(f'bookmarks/{theme}', 'card-author', '[data-testid=bookmark-card] .pv-card-author', 'aux', 11, True)

                    # 5. personal
                    fixture['navigate'](page, 'personal')
                    fixture['click'](page, 'personal-fetch')
                    page.locator('[data-testid=personal-ranking-view] .pv-card .pv-card-title').filter(visible=True).first.wait_for(timeout=6000)
                    probe_sel(f'personal/{theme}', 'card-title', '[data-testid=personal-ranking-view] .pv-card .pv-card-title', 'body', 13, False)
                    probe_sel(f'personal/{theme}', 'card-author', '[data-testid=personal-ranking-view] .pv-card .pv-card-author', 'aux', 11, True)

                    # 6. recommend
                    fixture['navigate'](page, 'recommend')
                    page.locator('[data-testid=recommend-section-certain] .pv-card .pv-card-title').filter(visible=True).first.wait_for(timeout=6000)
                    probe_sel(f'recommend/{theme}', 'card-title', '[data-testid=recommend-section-certain] .pv-card .pv-card-title', 'body', 13, False)
                    probe_sel(f'recommend/{theme}', 'card-author', '[data-testid=recommend-section-certain] .pv-card .pv-card-author', 'aux', 11, True)

                    # 7. library
                    fixture['navigate'](page, 'library')
                    page.locator('[data-testid=library-authors-scroll] .pv-library-card-info').filter(visible=True).first.wait_for(timeout=6000)
                    probe_sel(f'library/{theme}', 'author-info', '[data-testid=library-authors-scroll] .pv-library-card-info > div', 'aux', 11, True)

                    # 8. history (backup)
                    fixture['click'](page, 'tab-history')
                    fixture['target'](page, 'backup-size-large').wait_for(timeout=5000)
                    probe_sel(f'history/{theme}', 'size-large', '[data-testid=backup-size-large] > span:nth-of-type(2) > span:nth-of-type(1)', 'aux', 11, True)

            result['server_network_denied'] = smoke.network_state(url, nonce)['denied']
    finally:
        lifecycle = owned.result(scratch, (port,))
        result['owned_remaining_count'] = len(lifecycle['remaining'])
        result['owned_ports_closed'] = all(lifecycle['owned_ports_closed'].values())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve-port', type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--port', type=int, default=22641)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--keyboard', action='store_true')
    parser.add_argument('--d002', action='store_true')
    args = parser.parse_args()
    if args.serve_port:
        fixture['serve'](args.serve_port, args.nonce)
    else:
        start = monotonic()
        args.out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='d001-', dir=os.environ['TEMP']) as folder:
            d001 = run_d001(Path(folder) / 'd001', args.port, args.out,
                            include_desktop=args.desktop, check_keyboard=args.keyboard)
            d001['seconds'] = round(monotonic() - start, 2)
            (args.out / 'D001.json').write_text(json.dumps(d001, ensure_ascii=False, indent=2) + '\n',
                                                encoding='utf-8', newline='\n')
            if args.d002:
                t2 = monotonic()
                d002 = run_d002(Path(folder) / 'd002', args.port + 1)
                d002['seconds'] = round(monotonic() - t2, 2)
                (args.out / 'D002.json').write_text(json.dumps(d002, ensure_ascii=False, indent=2) + '\n',
                                                    encoding='utf-8', newline='\n')
        print(f"DONE in {monotonic() - start:.1f}s -> {args.out}")
        print(f"D001 failures ({len(d001['failures'])}):", d001['failures'][:10])
