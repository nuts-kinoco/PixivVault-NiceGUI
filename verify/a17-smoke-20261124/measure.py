"""A17 M1: measure archived product revisions against the same offline fixture."""
from contextlib import nullcontext
from io import BytesIO
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import tempfile
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import p5_design_evidence as p5, ui_smoke_browser as smoke
from tools import ui_tone_evidence as tone, uiux_audit as audit, uiux_image_area as area
from tools import uiux_fixture as fixture

OLD = 'fedf4ad5cf6d1701d8064e12e9e5e5d6e5541634'
FLET = '3ac3aa0d47a64785251593bcb09bb2d5f6df374b'
BASE = '0e5327b688d63a33900883d444ef04fa1c9f9a89'
SCREENS = ('download', 'ranking', 'personal', 'library', 'library-works',
           'bookmarks', 'recommend', 'dedup', 'view-history', 'learning')
TOOLBARS = {'ranking', 'personal', 'library', 'library-works',
            'bookmarks', 'recommend', 'dedup'}
SIZE = (1024, 640)
HISTORY_SIZES = ((1280, 800), (1920, 1080), (1024, 768), (640, 400))
if '--serve-original' not in sys.argv:
    path = ROOT / 'verify/navimpl2-20261124/check_area.py'
    nav = {'__file__': str(path), '__name__': 'nav'}
    exec(compile(path.read_bytes(), str(path), 'exec'), nav)
    controls = nav['fixture']


def case_is_stress():
    from nicegui import ui
    try:
        return ui.context.client.request.query_params.get('case') == 'stress'
    except (RuntimeError, AttributeError):
        return False


def serve_old(port, nonce):
    from pixivvault_nicegui.ui import shell
    from pixivvault_nicegui.ui.request_status_view import RequestStatusView

    status = shell.db_status
    initialize = RequestStatusView.__init__

    def db_status(_):
        root = Path.cwd() / '保存先'
        if case_is_stress():
            root /= ('保存用フォルダーの長い名前' * 7) + '/' + ('作品と履歴の保存' * 8)
        return status(SimpleNamespace(pixiv_download_root=root))

    def retry_status(view, policy, **kwargs):
        if not case_is_stress():
            policy = SimpleNamespace(progress_snapshot=lambda: ())
        return initialize(view, policy, **kwargs)

    with patch.object(shell, 'db_status', db_status), patch.object(RequestStatusView, '__init__', retry_status):
        fixture.serve_port(port, nonce)


def serve_flet(port):
    # Flet's real banner takes the production branch with the isolated database path.
    from tools.check_library_calendar_slider_owned import deny_external_connections
    sys.path.insert(0, str(Path.cwd()))
    network = deny_external_connections()
    import gui
    from threading import Thread
    from time import sleep

    def report_network():
        while True:
            sleep(.3)
            if Path('network-probe').exists():
                Path('network-count').write_text(str(network['denied']), encoding='ascii')

    Thread(target=report_network, daemon=True).start()
    with patch.object(gui, 'is_production_db_path', lambda *_: True):
        fixture.serve_original(port)


def old_open(page, screen):
    if screen == 'view-history':
        return controls['open_screen'](page, screen)
    if screen in ('library-works', 'bookmarks'):
        controls['click'](page, 'tab-library')
        controls['click'](page, 'library-author-open' if screen == 'library-works'
                          else 'library-subview-bookmarks')
    else:
        controls['click'](page, f'tab-{screen}')
    for key, opener in (('ranking', 'ranking-fetch'), ('personal', 'personal-fetch')):
        if screen == key:
            button = controls['target'](page, opener)
            button.wait_for()
            if button.is_enabled():
                button.click()
    if screen == 'dedup':
        controls['click'](page, 'duplicate-scan')
        controls['expect'](controls['target'](page, 'duplicate-status')).to_contain_text('枚を確認')
        if controls['target'](page, 'duplicate-mode-near').get_attribute('aria-pressed') != 'true':
            controls['click'](page, 'duplicate-mode-near')


def settle_history(page):
    dialog = controls['target'](page, 'view-history-dialog')
    page.wait_for_timeout(350)
    page.evaluate('''() => Promise.allSettled(document.getAnimations().filter(
        a => a.effect?.target?.closest('.q-dialog') && a.effect.getTiming().iterations !== Infinity
    ).map(a => a.finished))''')
    card = dialog.locator('.pv-tone-b-history-card')
    first = card.bounding_box()
    page.wait_for_timeout(80)
    second = card.bounding_box()
    if first is None or second is None or any(abs(first[key] - second[key]) > .1 for key in first):
        raise ValueError('history dialog rectangle is still changing')
    return second


def nice_metrics(page, screen):
    info = area.target_for(screen)
    # Use the same region, including the recommendation first-card boundary, in both revisions.
    if screen == 'bookmarks' and not page.locator(info['region']).count():
        info['region'] = '[data-testid=library-bookmarks-scroll]'
    if info['image']:
        page.wait_for_function(nav['READY'], arg={key: info[key] for key in ('region', 'image')}, timeout=4500)
    page.locator(info['region']).filter(visible=True).first.evaluate('(e) => {e.scrollTop = 0; e.scrollLeft = 0}')
    page.evaluate('document.fonts.ready')
    dialog = settle_history(page) if screen == 'view-history' else None
    if info['image']:
        # Let additional thumbnails in the first viewport finish painting after
        # the fixture's first-image readiness signal. Offscreen images can be lazy.
        page.wait_for_timeout(250)
    measured = area.measure_image_area(page, info)
    measured['complete_cards'] = page.evaluate('''({region,item}) => {
      const host = [...document.querySelectorAll(region)].find(e => e.checkVisibility());
      const bounds = host.getBoundingClientRect();
      return [...host.querySelectorAll(item)].filter(e => {
        const b = e.getBoundingClientRect();
        return b.width && b.height && b.left >= Math.max(0,bounds.left) - .5 &&
          b.top >= Math.max(0,bounds.top) - .5 && b.right <= Math.min(innerWidth,bounds.right) + .5 &&
          b.bottom <= Math.min(innerHeight,bounds.bottom) + .5;
      }).length;
    }''', {key: info[key] for key in ('region', 'item')})
    if dialog is not None:
        measured['settled_dialog'] = dialog
    if screen == 'learning':
        judgments = []
        for button in page.locator('.pv-judgment').all():
            button.scroll_into_view_if_needed()
            button.focus()
            hit = button.evaluate('''e => {
              const r=e.getBoundingClientRect();
              const center=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
              return e===document.activeElement && !!center && e.contains(center);
            }''')
            judgments.append(dict(focused_and_hittable=hit, enabled=button.is_enabled()))
        measured['judgments'] = judgments
    return measured


def flet_metrics(page, screen):
    """Record exact visible artwork runs on the Flutter canvas and semantic controls."""
    from PIL import Image
    picture = Image.open(BytesIO(page.screenshot(scale='css'))).convert('RGB')
    # The colour is the field of the existing synthetic 1200x900 artwork.
    # Use the artwork's top edge to identify complete fixture images.
    colours = {(56, 83, 129), (248, 200, 105), (154, 217, 199)}
    pixels = picture.load()
    runs = []
    for y in range(picture.height):
        x = 0
        while x < picture.width:
            if pixels[x, y] not in colours:
                x += 1
                continue
            start = x
            while x < picture.width and pixels[x, y] in colours:
                x += 1
            if x - start >= 20:
                runs.append((start, y, x))
    groups = []
    for x, y, right in runs:
        existing = next((g for g in reversed(groups)
                         if 0 < y - g[3] <= 2 and x < g[2] + 2 and right > g[0] - 2), None)
        if existing is None:
            groups.append([x, y, right, y])
        else:
            existing[:] = [min(x, existing[0]), existing[1], max(right, existing[2]), y]
    rectangles = [[x, y, r-x, b-y+1] for x,y,r,b in groups if b-y >= 12]
    rows = page.evaluate(tone.FLET_DOM)
    # General Flutter semantics are useful diagnostics but don't identify the
    # actual scroll clip in every screen. Leave unsupported ratios unset.
    result = dict(viewport=[picture.width, picture.height], area_ratio=None,
                  visible_rect=None, image_rect=None, first_image_y=None,
                  complete_images=None, complete_cards=None)
    if rectangles and screen in {'learning','ranking','personal','library','library-works','bookmarks','recommend','view-history'}:
        result['fixture_colour_rectangles'] = rectangles
        image = max(rectangles, key=lambda r: r[2]*r[3]) if screen == 'learning' else min(rectangles, key=lambda r: (r[1],r[0]))
        result.update(image_rect=image, first_image_y=image[1])
        if screen == 'learning' and abs(image[3] - image[2] * .75) <= 2:
            result.update(complete_images=1, area_ratio=image[2]*image[3]/(picture.width*picture.height))
    result['scroll_semantics'] = [
        {'role':r['role'], 'rect':r['rect'], 'scroll_height':r['scroll_height'], 'client_height':r['client_height']}
        for r in rows if r['scroll_height'] > r['client_height'] + 1 or r['role'] == 'scrollbar']
    result['artwork_semantics'] = [
        {'role':r['role'], 'rect':r['rect']} for r in rows if r['role'] == 'img' or
        r['label'] in ('クリックで画集ビューアを開く', 'synthetic author', '合成作品')]
    if screen == 'view-history':
        result['history_alert_dialog'] = next((r['rect'] for r in rows if r['role'] == 'alertdialog'), None)
    if screen == 'learning':
        judgments = []
        for label in ('見送る', '合わない', 'いいね', 'SuperLike'):
            # Flet exposes the tooltip rather than a plain name on the actionable container.
            hint = next((r for r in rows if r['label'].startswith(label) and r['role'] == 'button'), None)
            control = page.get_by_role('button', name=label, exact=False).first
            rect = hint['rect'] if hint else None
            if not control.count() or rect is None:
                judgments.append(dict(pointer_reachable=False, enabled=False))
                continue
            x,y,w,h = rect
            clickable = True
            try:
                control.click(trial=True, timeout=1400)
            except Exception:
                clickable = False
            judgments.append(dict(pointer_reachable=clickable and
                                  0 <= x+w/2 < picture.width and 0 <= y+h/2 < picture.height,
                                  enabled=control.is_enabled()))
        result['judgments'] = judgments
    return result


def learning_excerpt(page, measured, output):
    """Keep only the synthetic artwork pixels at their displayed scale."""
    from PIL import Image

    box = measured.get('image_rect')
    if measured.get('artwork_semantics'):
        box = measured['artwork_semantics'][0]['rect']
    if box is None:
        raise ValueError('learning artwork rectangle missing')
    x, y, w, h = box
    painted = Image.open(BytesIO(page.screenshot(scale='css'))).convert('RGB')
    artwork = painted.crop((round(x), round(y), round(x+w), round(y+h)))
    palette = {(56, 83, 129), (248, 200, 105), (154, 217, 199)}
    artwork.putdata([pixel if pixel in palette else (240, 240, 240) for pixel in artwork.get_flattened_data()])
    artwork.save(output)


def warm_baseline(browser, url, foreign, result):
    """Observe the once-per-profile hint before measuring settled screens."""
    with p5.new_page(browser, url, SIZE, 'dark', foreign, None).context as context:
        page = context.pages[0]
        hint = page.locator('.q-notification.pv-toast').filter(has_text='「自動処理」に移りました')
        from playwright.sync_api import TimeoutError

        try:
            hint.wait_for(timeout=3500)
        except TimeoutError:
            result['startup_hint_observed'] = False
            return
        result['startup_hint_observed'] = True
        result['startup_hint_rect'] = hint.bounding_box()
        hint.get_by_role('button', name='閉じる').click(timeout=3500)
        hint.wait_for(state='hidden', timeout=3500)


def sweep(args, scratch, result, owned):
    from playwright.sync_api import sync_playwright

    is_old, is_flet = args.version == 'old', args.version == 'flet'
    origin, product = scratch / 'original', scratch / 'product'
    origin.mkdir()
    for name in ('local', 'roaming', 'browser-tmp'):
        (scratch / name).mkdir()
    if is_flet:
        p5.copy_original(FLET, origin)
    env = {'PV_UIUX_STATE': 'normal', 'PV_UI_IMAGE_AREA': '1',
           'PV_UIUX_RUN_SCRATCH': str(scratch), 'LOCALAPPDATA': str(scratch / 'local'),
           'APPDATA': str(scratch / 'roaming')}
    if is_old:
        with ZipFile(BytesIO(p5.git(ROOT, 'archive', '--format=zip', OLD, 'src'))) as archive:
            archive.extractall(scratch / 'old')
        env['PYTHONPATH'] = os.pathsep.join(str(p) for p in (scratch / 'old/src', ROOT / 'tests'))
    p5.seed_profile(product, origin, 'normal', tone_mode=True)
    fixture.seed_area_profile(product, origin)
    (product / 'automation-state.txt').write_text('failed', encoding='ascii')
    nonce = None if is_flet else secrets.token_urlsafe(24)
    font = p5.ORIGINAL_JAPANESE_FONT.read_bytes()
    result['flet_local_font_sha256'] = hashlib.sha256(font).hexdigest()
    if audit.QUIET.exists():
        raise RuntimeError('D6_QUIET')
    with p5.running_app('original' if is_flet else 'port', p5.ORIGINAL_PYTHON if is_flet else sys.executable,
                        origin if is_flet else product, args.port, nonce, tone_mode=True, personal_fixture=True,
                        server_script=Path(__file__), extra_env=env | {'A17_VERSION': args.version}) as url, sync_playwright() as pw:
        temp_env = {key: str(scratch / 'browser-tmp') for key in ('TEMP','TMP')}
        os.environ.update(temp_env)
        browser = pw.chromium.launch(channel='msedge', headless=True, env=os.environ | env,
                                     args=['--disable-background-networking', '--disable-crash-reporter',
                                           '--no-proxy-server', '--disable-gpu',
                                           '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
        owned.observe()
        result['edge_version'] = browser.version
        foreign = set()
        with browser:
            if args.version == 'baseline':
                warm_baseline(browser, url+'/?case=normal', foreign, result)
            for size in (HISTORY_SIZES if args.history else (SIZE,)):
                for theme in (('dark',) if args.learning_excerpt else ('light', 'dark')):
                    for variant in (('normal',) if is_flet or args.learning_excerpt else ('normal','stress')):
                        if audit.QUIET.exists():
                            raise RuntimeError('D6_QUIET')
                        row = dict(size=size, theme=theme, variant=variant, screens={})
                        result['cells'].append(row)
                        view_url = url if is_flet else (
                            f'{url}/?_audit_theme={theme}&case={variant}&_tone_retry='
                            + ('normal' if variant == 'normal' else 'request-limit'))
                        with p5.new_page(browser, view_url, size, theme, foreign, font if is_flet else None).context as ctx:
                            page = ctx.pages[0]
                            page.set_default_timeout(4500)
                            page.on('pageerror', lambda _: result.update(browser_errors=result['browser_errors']+1))
                            row['viewport'] = page.evaluate('''() => ({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
                               zoom:visualViewport.scale,font:getComputedStyle(document.body).fontFamily})''')
                            if is_flet:
                                p5.original.enable_semantics(page)
                                p5.original.set_theme(page, theme)
                            else:
                                smoke.set_theme(page, theme)
                                row['theme_confirmed'] = page.locator('body').evaluate(
                                    "e => e.classList.contains('body--dark')") == (theme == 'dark')
                                row['header'] = {}
                                for key in ('db-status-banner', 'shared-get-recovery-open', 'automation-unresolved',
                                            'automation-breakdown', 'request-retry-status'):
                                    item = controls['target'](page, key)
                                    row['header'][key] = item.bounding_box() if item.count() else None
                                if variant == 'stress' and not is_old:
                                    controls['expect'](controls['target'](page, 'automation-unresolved')).to_have_text('自動処理：未解消 2')
                            screens = ('learning',) if args.learning_excerpt else ('view-history',) if args.history else SCREENS
                            for screen in screens:
                                try:
                                    if is_flet:
                                        cell = audit.Cell(screen, 'normal', theme, *size, 100, audit.ORIGINALS.get(screen))
                                        if screen == 'library-works':
                                            cell = audit.Cell(screen, 'normal', theme, *size, 100, 'library')
                                        opened = fixture.open_area_original(page, cell, 'normal')
                                        page.wait_for_timeout(1400 if screen == 'learning' else 750)
                                        measured = flet_metrics(page, screen)
                                        measured['fixture_route_ready'] = opened
                                    else:
                                        (old_open if is_old else controls['open_screen'])(page, screen)
                                        measured = nice_metrics(page, screen)
                                    if args.learning_excerpt:
                                        learning_excerpt(page, measured, args.learning_excerpt)
                                    row['screens'][screen] = {'initial' if is_flet or is_old else 'collapsed': measured}
                                    if screen == 'view-history':
                                        if is_flet:
                                            page.mouse.click(5, 5)
                                        else:
                                            controls['click'](page, 'view-history-close')
                                            controls['expect'](controls['target'](page, 'view-history-dialog')).to_be_hidden()
                                    if not (is_flet or is_old) and screen in TOOLBARS:
                                        key = 'library' if screen == 'library-works' else screen
                                        controls['set_toolbar'](page, key, True)
                                        page.wait_for_timeout(180)
                                        row['screens'][screen]['expanded'] = nice_metrics(page, screen)
                                        controls['set_toolbar'](page, key, False)
                                except Exception as exc:
                                    if args.learning_excerpt:
                                        raise
                                    row['screens'].setdefault(screen, {})['measurement_issue'] = audit.safe_error(exc)
                            print(args.version, size, theme, variant,
                                  [(key, value.get('measurement_issue')) for key,value in row['screens'].items()],
                                  flush=True)
        if is_flet:
            (origin / 'network-probe').touch()
            from time import sleep
            for _ in range(20):
                if (origin / 'network-count').exists():
                    break
                sleep(.1)
            result['server_network_denied'] = int((origin / 'network-count').read_text())
        else:
            result['server_network_denied'] = smoke.network_state(url, nonce)['denied']
        result['foreign_browser_destinations_blocked'] = len(foreign)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve-port', type=int)
    parser.add_argument('--serve-original', type=int)
    parser.add_argument('--nonce')
    parser.add_argument('--version', choices=('flet','old','baseline'))
    parser.add_argument('--port', type=int, default=22745)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--learning-excerpt', type=Path)
    args = parser.parse_args()
    if args.serve_port:
        return (serve_old if os.environ['A17_VERSION'] == 'old' else controls['serve'])(args.serve_port, args.nonce)
    if args.serve_original:
        return serve_flet(args.serve_original)
    if not args.version or any(not 22742 <= port <= 22760 for port in (args.port, args.port+1)):
        parser.error('select a revision and an isolated port')
    product_revision = p5.git(ROOT, 'rev-parse', 'HEAD:src').decode().strip()
    if product_revision != p5.git(ROOT, 'rev-parse', f'{BASE}:src').decode().strip():
        parser.error('worktree source revision does not match this evidence run')
    if p5.git(ROOT, 'status', '--porcelain', '--', 'src').strip():
        parser.error('worktree source has unstaged changes')
    result = dict(revision={'flet':FLET,'old':OLD,'baseline':BASE}[args.version], cells=[], browser_errors=0,
                  emulation='1024×640 CSS viewport at 100% browser zoom, DPR 1 for CSS layout equivalence',
                  host=platform.platform())
    owned = tone.OwnedProcesses()
    started = monotonic()
    ports = (args.port, args.port+1) if args.version == 'flet' else (args.port,)
    scratch_root = Path(os.environ['TEMP']).resolve()
    if not scratch_root.is_relative_to(Path('S:/Work/_tmp/PV/a17-smoke-20261124')):
        parser.error('TEMP must be the authorized A17 scratch directory')
    if args.learning_excerpt and not args.learning_excerpt.resolve().is_relative_to(scratch_root):
        parser.error('the single artwork excerpt must be in A17 scratch')
    try:
        with tempfile.TemporaryDirectory(prefix='measure-', dir=scratch_root) as folder:
            scratch = Path(folder)
            try:
                sweep(args, scratch, result, owned)
            except Exception as exc:
                result['run_issue'] = audit.safe_error(exc)
                print(result['run_issue'], file=sys.stderr)
            finally:
                gc.collect()
                lifecycle = owned.result(scratch, ports)
                result['owned_remaining'] = len(lifecycle['remaining'])
                result['owned_ports_closed'] = all(lifecycle['owned_ports_closed'].values())
                files = [path for path in scratch.rglob('*') if path.is_file()]
                result['scratch_cleaned'] = dict(files=len(files), bytes=sum(p.stat().st_size for p in files))
        result['scratch_remaining'] = int(scratch.exists())
    finally:
        result['seconds'] = round(monotonic()-started, 2)
        if not args.learning_excerpt:
            p5.write_json(Path(__file__).with_name(('history' if args.history else args.version) + '.json'), result)
        else:
            print(json.dumps({key: result.get(key) for key in
                  ('revision', 'startup_hint_observed', 'startup_hint_rect', 'owned_remaining',
                   'owned_ports_closed', 'scratch_cleaned', 'scratch_remaining')}), flush=True)
    return int('run_issue' in result)


if __name__ == '__main__':
    raise SystemExit(main())
