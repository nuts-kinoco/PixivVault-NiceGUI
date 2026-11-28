"""Capture and measure the isolated 2026-11-22 UI/UX audit matrix."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
from urllib.parse import urlsplit
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from tools import p5_design_evidence as p5, ui_smoke_browser as smoke, ui_tone_evidence as tone
from tools import uiux_fixture as fixture
from tools import uiux_image_area as image_area
from tools.uiux_image_area import measure_image_area
from tools.uiux_report import proven, write_report
from tools.uiux_rules import contrast, finding, inspect


QUIET = Path('S:/Work/_tmp/PV/sup/D6_QUIET.flag')
SCRATCH_ROOT = Path('S:/Work/_tmp/PV')
TEMP = SCRATCH_ROOT / 'codex-ui-audit/r2'
OUTPUT = ROOT / 'verify/uiux-audit-20261122/r2'
AREA_TEMP = SCRATCH_ROOT / 'codex-ui-img'
AREA_OUTPUT = ROOT / 'verify/uiimg-20261122/r1'
DOM = Path(__file__).with_name('uiux_dom.js').read_text(encoding='utf-8')
STATES = ('normal', 'empty', 'long', 'error', 'loading')
SIZES = ((1024, 768, 100), (1280, 800, 100), (1920, 1080, 100), (1280, 800, 200))
FINAL_ZOOM_SCREENS = {'download', 'learning', 'personal', 'bookmarks', 'settings', 'scheduler',
                      'metadata', 'stop-recovery', 'queue-delete-confirm'}
SCREENS = {
    **{key: 'normal empty long' for key in p5.SCREENS},
    'ranking': 'normal empty long error loading',
    'personal': 'normal empty long error loading',
    'dedup': 'normal empty long error loading',
    'scheduler': 'normal empty',
    'metadata': 'normal empty',
    'recommend': 'normal empty long error loading',
    'history': 'normal empty long error loading',
    'settings': 'normal error',
    'view-history': 'normal empty error',
    'startup-and-storage': 'normal',
    'startup-reference': 'error',
    'startup-cookie-rejected': 'error',
    'startup-lock-busy': 'error',
    'startup-legacy-uncertain': 'error',
    'startup-legacy-copy-error': 'error',
    'startup-stop-migration': 'error',
    'stop-history-guide': 'error',
    'stop-migration-dialog': 'normal',
    'stop-recovery': 'error',
    'stop-uncertain': 'error',
    'borrow-on': 'normal',
    'premium-recovery': 'normal',
    'communication-result': 'normal',
    'request-wait': 'loading',
    'request-limit': 'error',
    'shared-log': 'normal empty',
    'shared-log-collapsed': 'normal empty',
    'metadata-ledger': 'normal empty',
    'extension': 'normal empty error',
    'extension-confirm': 'normal',
    'toast-success': 'normal',
    'toast-error': 'error',
    'ranking-date': 'normal',
    'personal-filters': 'normal',
    'dedup-trash': 'normal empty',
    'learning-settings': 'normal',
    'learning-review': 'normal empty',
    'learning-reset': 'normal',
    'download-details': 'normal empty',
    'viewer': 'normal long',
    'queue-delete-confirm': 'normal',
    'failed-delete-confirm': 'normal',
    'view-history-delete': 'normal',
}
# Every shipped Flet tab hides the common log. The queue log is a separate panel.
ORIGINALS = {**p5.SCREENS, 'view-history': 'view-history', 'startup-and-storage': 'settings',
             'extension': 'settings', 'extension-confirm': 'settings', 'learning-reset': 'settings',
             'view-history-delete': 'view-history'}
STATE_REASON = {
    'normal': '専用の異常・待機・通知で開く画面。',
    'empty': 'この画面の空状態を作る独立した入力が未定義。',
    'long': 'この画面に長い作品名・説明を渡す合成入力が未定義。',
    'error': 'この画面の異常を再現する合成境界が未定義。',
    'loading': 'この画面の読み込みを保持する合成境界が未定義。',
}


@dataclass(frozen=True)
class Cell:
    screen: str
    state: str
    theme: str
    width: int
    height: int
    zoom: int
    original: str | None
    unsupported: str = ''

    @property
    def condition(self):
        return f'{self.width}x{self.height}/{self.theme}/{self.state}/{self.zoom}%'

    @property
    def key(self):
        return f'{self.state}/{self.width}x{self.height}-{self.theme}-{self.zoom}/{self.screen}'

    def to_dict(self):
        return dict(asdict(self), condition=self.condition, key=self.key)


def plan(screens=None, states=None):
    return [Cell(key, state, theme, width, height, zoom, ORIGINALS.get(key),
                 '' if state in SCREENS[key].split() else STATE_REASON[state])
            for state in states or STATES for width, height, zoom in SIZES
            for theme in ('light', 'dark') for key in screens or SCREENS]


def plan_image_area(screens=None):
    return [Cell(key, 'normal', theme, width, height, zoom,
                 ORIGINALS['library'] if key == 'library-works' else ORIGINALS.get(key))
            for width, height, zoom in image_area.SIZES for theme in ('light', 'dark')
            for key in screens or image_area.LABELS]


def plan_final(screens=None, states=None):
    return [replace(cell, original=None) for cell in plan(screens, states or ('normal', 'error', 'loading'))
            if not cell.unsupported and (states or cell.state == SCREENS[cell.screen].split()[0])
            and (cell.zoom == 100 or cell.screen in FINAL_ZOOM_SCREENS)]


class QuietPeriod(RuntimeError):
    pass


def allow_browser():
    if QUIET.exists():
        raise QuietPeriod('D6_QUIET.flag が存在します。Chromium 撮影・計測は保留。')


def scan(page, **options):
    return page.evaluate(DOM, options)


def keyboard(page, baseline, opener):
    results = []
    positions = []
    visited = set()
    modal = baseline['modal']
    focal = baseline['focus']
    if modal and (not focal or not focal['inside']):
        results.append(finding('A13', modal, '初期フォーカスが外側', '開いたときにフォーカスを移動'))
    count = sum(bool(n['control']) and n['state'] != 'disabled' for n in baseline['nodes'])
    bound = min(count + 12, 140)
    wrapped = False
    for _ in range(bound):
        page.keyboard.press('Tab')
        focused = scan(page, focusOnly=True)
        if not focused:
            continue
        selector = focused['selector']
        if selector in visited:
            wrapped = True
            break
        positions.append([selector, focused['rect']])
        visited.add(selector)
        if not focused['inside']:
            results.append(finding('A12', selector, 'モーダル外にフォーカス', 'モーダル内を巡回'))
            continue
        ring = focused.get('outline')
        if ring and not ring['uncertainColor']:
            ratio = contrast(ring['foreground'], ring['background'])
            if ratio < 3:
                results.append(finding('A12', selector, round(ratio, 3), 'フォーカス輪 3:1',
                                       state='focus', status='review' if focused['shadow'] else 'violation'))
        elif not ring and not (focused['shadow'] or focused['focusOverlay']):
            results.append(finding('A12', selector, 'outline・shadow・既知の疑似輪なし',
                                   '可視フォーカス輪 3:1', state='focus', status='review'))
        if len(visited) == 1:
            sample = scan(page, target=selector)
            if sample is None:
                results.append(finding('A1', selector, 'フォーカス切替中のため測定できない',
                                       '安定した表示で再測定', state='focus', status='review'))
            else:
                results.extend(row for row in inspect(sample, zoom=100) if row['item'] == 'A1')
    if wrapped:
        for node in baseline['nodes']:
            if (node['control'] and node['tabIndex'] >= 0 and node['state'] != 'disabled' and
                    not node['nestedControl'] and not node['hiddenHit'] and node['selector'] not in visited):
                results.append(finding('A12', node['selector'], 'Tab 一巡で未到達', 'キーボードで到達',
                                       status='review'))
    for (left, first), (right, second) in zip(positions, positions[1:]):
        if abs(first[1] - second[1]) <= 3 and first[0] > second[0] + 20:
            results.append(finding('A12', right, {'previous': left, 'x': [first[0], second[0]]},
                                   '見た目の順に移動', status='review'))

    if modal:
        from playwright.sync_api import TimeoutError

        page.keyboard.press('Escape')
        try:
            page.locator(modal).filter(visible=True).wait_for(state='hidden', timeout=900)
        except TimeoutError:
            pass
        after = scan(page, focusOnly=True)
        remains = page.locator(modal).filter(visible=True).count()
        if remains:
            results.append(finding('A12', modal, 'Escape 後も開いている', 'Escape で閉じる'))
        elif opener is not None and opener.is_visible():
            if not after or not opener.evaluate('e => e === document.activeElement || e.contains(document.activeElement)'):
                results.append(finding('A13', modal, '起点にフォーカスが復帰しない', '閉じたときに起点へ復帰'))
    return results, positions, wrapped


def hover(page, baseline):
    visited = set()
    findings = []
    for item in baseline['nodes']:
        x, y, w, h = item['rect']
        if (not item['control'] or item['hiddenHit'] or
                not (0 < x + w / 2 < baseline['viewport'][0] and 0 < y + h / 2 < baseline['viewport'][1])):
            continue
        group = item['cluster'], item['tag'], item['filled'], item['state']
        if group in visited:
            continue
        if len(visited) == 8:
            break
        visited.add(group)
        page.mouse.move(x + w / 2, y + h / 2)
        sample = scan(page, target=item['selector'])
        if sample is None:
            findings.append(finding('A1', item['selector'], 'ホバー切替中のため測定できない',
                                    '安定した表示で再測定', state='hover', status='review'))
            continue
        results = inspect(sample, zoom=100)
        findings.extend(row for row in results if row['item'] == 'A1' and row['state'] == 'hover')
    page.mouse.move(0, 0)
    return findings, len(visited)


def motion_findings(page, baseline, *, toast):
    page.emulate_media(reduced_motion='reduce')
    try:
        page.wait_for_timeout(160)
        reduced = scan(page, motion=True)
    finally:
        page.emulate_media(reduced_motion='no-preference')
    motion = reduced['motion'] if reduced is not None else []
    results = [finding('A14', item['selector'], 'reduce で反復アニメーションが稼働', '自動・反復を停止')
               for item in motion if item['repeated'] and item['animation']]
    results.extend(finding('A14', item['selector'], dict(ms=item['duration']),
                           'reduce で自動アニメーションを止める', status='review')
                   for item in motion if item['animation'] and not item['repeated']
                   and isinstance(item['duration'], (int, float)) and item['duration'] > 160)
    if reduced is None:
        results.append(finding('A14', 'document', '切り替え中のため縮小モードを測定できない',
                               '安定した表示で再測定', status='review'))
    timing = []
    if toast:
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            timing = scan(page, toastOnly=True) or []
            if timing and all(row['duration'] is not None for row in timing):
                break
            page.wait_for_timeout(150)
        for row in timing:
            if row['important'] and row['duration'] is not None:
                results.append(finding('A14', row['selector'], dict(ms=row['duration'], content=row['content']),
                                       '重要な通知は明示操作で閉じる', status='review'))
    return results, dict(normal=len(baseline['motion']), reduced=len(motion) if reduced is not None else None,
                         toasts=timing)


def output_path(out, path):
    return path.relative_to(out).as_posix()


def loaded_module_manifest(root, paths):
    root = Path(root).resolve()
    result = {}
    for filename in paths:
        if not filename:
            continue
        path = Path(filename).resolve()
        if path.is_relative_to(root) and path.is_file() and path.suffix == '.py':
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(result.items()))


def capture_state(state, fixture_proven, visible, busy):
    return bool(fixture_proven and visible and (state == 'loading' or not busy))


def state_marker(screen, state):
    if state == 'normal':
        return {
            'download': '[data-testid=unified-author-list] > div',
            'queue': '[data-testid=queue-view] [data-testid=job-row]',
            'failed': '[data-testid=failed-rows] > div',
            'dedup': '[data-testid=duplicate-near-group]',
            'learning': '[data-testid=learning-candidate-author]',
            'ranking': '[data-testid=ranking-view] .pv-card',
            'personal': '[data-testid=personal-ranking-view] .pv-card',
            'library': '[data-testid=library-authors-scroll] > div',
            'bookmarks': '[data-testid=bookmark-card]',
            'recommend': '[data-testid=recommend-view] .pv-grid .pv-card',
        }.get(screen)
    if state == 'empty' and screen == 'queue':
        return '[data-testid=queue-view] .pv-empty'
    if state in {'error', 'loading'}:
        return {'dedup': '[data-testid=duplicate-status]',
                'ranking': '[data-testid=ranking-notice]',
                'personal': '[data-testid=personal-notice]',
                'recommend': '[data-testid=recommend-notice]' if state == 'error' else
                             '[data-testid=recommend-busy-status]'}.get(screen)
    return None


def screenshot(page, path, cell, *, redact=False):
    style = '[data-testid=scheduler-account] { color: transparent !important; }' if redact else None
    pixels = page.screenshot(timeout=10000, scale='device', style=style)
    with Image.open(BytesIO(pixels)) as image:
        if image.size != (cell.width, cell.height):
            raise ValueError('physical screenshot dimensions not confirmed')
        width, height = image.size
    path.write_bytes(pixels)
    return dict(width=width, height=height, sha256=hashlib.sha256(pixels).hexdigest())


def load_page(page, url, cell, *, original=False):
    page.bring_to_front()
    suffix = '' if original else f'?_audit_theme={cell.theme}&_audit_screen={cell.screen}'
    page.goto(url + '/' + suffix, wait_until='domcontentloaded', timeout=25000)
    if original:
        p5.original.enable_semantics(page)
        p5.original.set_theme(page, cell.theme)
    else:
        page.wait_for_function('window.did_handshake === true', timeout=10000)
        page.get_by_test_id('tab-library').wait_for(state='attached', timeout=5500)
        if not smoke.set_theme(page, cell.theme):
            raise ValueError('theme not confirmed')


def capture_settings_groups(page, out, folder, record):
    from playwright.sync_api import Error

    try:
        groups = tone.capture_settings(page, folder, write_metrics=False)
    except Error as error:
        record['settings_groups_issue'] = safe_error(error)
        return
    record['group_images'] = [output_path(out, folder / f) for f in
                              ('port-all-groups.png', *(group['image'] for group in groups))]
    record['group_titles'] = {group['image']: group['title'] for group in groups}


def capture_port(page, url, cell, out, folder, specs, record, *, prime_zoom=False, checkpoint=None,
                 final=False):
    from playwright.sync_api import Error

    record['port_step'] = 'load'
    load_page(page, url, cell)
    if cell.screen.startswith('toast'):
        scan(page, startToastWatch=True)
    record['port_step'] = 'open'
    state_proven, opener = fixture.open_port(page, replace(cell, zoom=100) if prime_zoom else cell, specs)
    opened_dialog = False
    if prime_zoom:
        opened_dialog = page.locator('.q-dialog__inner').filter(visible=True).count() > 0
        set_browser_zoom(page.context, cell.zoom)
        page.bring_to_front()
        record['port_opened_at_zoom'] = 100
    page.mouse.move(0, 0)
    page.evaluate('document.fonts.ready')
    if cell.state not in ('loading',) and not cell.screen.startswith('toast'):
        page.wait_for_timeout(180)
    record['port_step'] = 'viewport'
    try:
        baseline = scan(page, motion=True)
    except Error:
        if cell.screen != 'settings':
            raise
        page.wait_for_timeout(200)
        baseline = scan(page, motion=True, root='.pv-preferences-card')
    if baseline is None:
        baseline = scan(page, motion=True)
    if baseline is None:
        raise ValueError('capture DOM root not confirmed')
    if baseline['modal'] and len(baseline['nodes']) < 3:
        page.wait_for_timeout(300)
        baseline = scan(page, motion=True)
    if opened_dialog and not baseline['modal']:
        state_proven = False
        record['issue'] = '200% に切り替えた後にダイアログの表示を確認できない。'
    record.update(observed_viewport=baseline['viewport'], device_scale=baseline['zoom'])
    if baseline['theme'] != cell.theme:
        raise ValueError('capture theme not confirmed')
    if baseline['viewport'] != [cell.width * 100 // cell.zoom, cell.height * 100 // cell.zoom]:
        raise ValueError('capture viewport not confirmed')
    if abs(baseline['zoom'] - cell.zoom / 100) > .01:
        raise ValueError('browser zoom not confirmed')
    record['port_step'] = 'screenshot'
    path = folder / 'port.png'
    visible = bool(baseline['nodes']) and page.locator('.pv-content, .q-dialog__inner').filter(visible=True).count() > 0
    busy = page.locator('[aria-busy=true]:visible, [data-busy=true]:visible, '
                        '[data-testid=ranking-notice]:visible, [data-testid=learning-candidate-status]:visible').evaluate_all(
        "els => els.some(e => e.matches('[aria-busy=true], [data-busy=true], [data-testid=learning-candidate-status]') || /取得中|読み込み中/.test(e.innerText))")
    marker = state_marker(cell.screen, cell.state)
    marker_text = page.locator(marker).filter(visible=True).first.inner_text() if marker and page.locator(marker).filter(visible=True).count() else ''
    if cell.state in {'error', 'loading'}:
        pattern = r'失敗|できません|利用できません' if cell.state == 'error' else r'取得中|スキャン中|確認中|スキャンしています|読み込んでいます|処理中'
        marker_proven = bool(re.search(pattern, marker_text))
    elif cell.state == 'empty':
        marker_proven = (cell.screen == 'queue' and marker_text.strip() == '現在のジョブはありません' and
                         not page.locator('[data-testid=queue-view] [data-testid=job-row]').filter(visible=True).count())
    else:
        marker_proven = bool(marker_text.strip())
    state_proven = capture_state(cell.state, state_proven and marker_proven, visible, busy)
    image = screenshot(page, path, cell, redact=final)
    record.update(product=output_path(out, path), state_proven=bool(state_proven),
                  image=image, state_marker=marker, marker_proven=marker_proven, busy=busy,
                  observed_viewport=baseline['viewport'], device_scale=baseline['zoom'])
    findings = inspect(baseline, zoom=cell.zoom)
    if not state_proven and cell.zoom == 200:
        content_height = page.locator('.pv-content').evaluate(
            'e => e.getBoundingClientRect().height') if page.locator('.pv-content').count() else None
        if content_height is not None and content_height <= 1:
            findings.append(finding('A11', '.pv-content', {'height_css_px': content_height},
                                    '画面内容と主操作を表示できる'))
            record['issue'] = '200% でタブ内容の高さが 0 CSS px。状態確認不可。'
    record.update(measured=True, findings=findings,
                  filled_buttons=sum(node['filled'] and node['state'] != 'disabled' for node in baseline['nodes']))
    dom_path = folder / 'dom.json'
    # Text, accessible labels and field values can carry application data.
    p5.write_json(dom_path, dict(viewport=baseline['viewport'], document_width=baseline['documentWidth'],
                                 nodes=[dict({k: v for k, v in node.items() if k not in ('text', 'name')},
                                             text_present=bool(node['text']), name_present=bool(node['name']))
                                        for node in baseline['nodes']]))
    record['dom'] = output_path(out, dom_path)
    observed = ['A1','A2','A3','A4','A5','A6','A7','A8','A9','A10','A12','A13','A15']
    record['items_observed'] = observed
    if cell.zoom == 200:
        observed.append('A11')
    if checkpoint:
        checkpoint()
    if cell.screen == 'settings':
        record['port_step'] = 'settings-groups'
        capture_settings_groups(page, out, folder, record)
        if checkpoint:
            checkpoint()
    record['port_step'] = 'motion'
    try:
        motion, runtime = motion_findings(page, baseline, toast=cell.screen.startswith('toast'))
        findings.extend(motion)
        record['motion'] = runtime
        observed.append('A14')
    except Error as error:
        record['motion_issue'] = safe_error(error)
    # Keep dynamic loading states intact during measurement. Tab traversal on a
    # busy state would cross into the completed state before reaching the end.
    if cell.state != 'loading':
        record['port_step'] = 'hover'
        try:
            hovers, sampled = hover(page, baseline)
            findings.extend(hovers)
            record['hover_groups_sampled'] = sampled
        except Error as error:
            record['hover_issue'] = safe_error(error)
        record['port_step'] = 'keyboard'
        keys, order, wrapped = keyboard(page, baseline, opener)
        findings.extend(keys)
        record.update(tab_order=order, tab_cycle_observed=wrapped)
        if cell.screen == 'settings':
            # Safely exercise Enter on a known opener after the Escape probe.
            load_page(page, url, cell)
            trigger = fixture.ready(page, 'preferences-open')
            trigger.press('Enter')
            try:
                fixture.ready(page, 'preferences-save')
                record['enter_opens_settings'] = True
            except Exception as error:
                from playwright.sync_api import TimeoutError

                if not isinstance(error, TimeoutError):
                    raise
                record['enter_opens_settings'] = False
                findings.append(finding('A12', '[data-testid="preferences-open"]',
                                        'Enter で設定が開かない', 'Enter で操作可能'))
    # Store unique measurements per selector, condition and UI state.
    record['findings'] = list({json.dumps(row, sort_keys=True, ensure_ascii=False): row
                               for row in findings}.values())
    record.pop('port_step')


def capture_original(page, url, cell, out, folder, profile, record, *, prime_zoom=False,
                     image_area_mode=False):
    from playwright.sync_api import TimeoutError

    record['original_step'] = 'load'
    page.bring_to_front()
    theme_proven = getattr(page, '_uiux_original_theme_proven', None)
    if theme_proven is not None:
        modal = page.locator('flt-semantics[role=alertdialog]').filter(visible=True).first
        if modal.count():
            page.mouse.click(5, 5)
            page.wait_for_timeout(230)
        if modal.count():
            page.goto(url, wait_until='domcontentloaded', timeout=25000)
            theme_proven = None
    if theme_proven is None:
        try:
            p5.original.enable_semantics(page)
            p5.original.set_theme(page, cell.theme)
            theme_proven = True
        except (ValueError, TimeoutError):
            if cell.zoom != 200:
                raise
            theme_proven = False
        page._uiux_original_theme_proven = theme_proven
    if not theme_proven:
        record['original_theme_unconfirmed'] = True
    record['original_step'] = 'open'
    try:
        open_view = fixture.open_area_original if image_area_mode else fixture.open_original
        state_proven = open_view(page, cell, profile)
    except (ValueError, TimeoutError):
        if image_area_mode and cell.screen == 'library-works':
            name = folder / 'original-route.png'
            screenshot(page, name, cell)
            record['original_route_image'] = output_path(out, name)
        if cell.zoom != 200:
            raise
        record['original_route_unconfirmed'] = True
        state_proven = False
    if prime_zoom:
        set_browser_zoom(page.context, cell.zoom)
        page.bring_to_front()
        record['original_opened_at_zoom'] = 100
    page.mouse.move(0, 0)
    page.evaluate('document.fonts.ready')
    page.wait_for_timeout(750 if image_area_mode else 180)
    name = folder / 'original.png'
    record['original_step'] = 'screenshot'
    screenshot(page, name, cell)
    record['original'] = output_path(out, name)
    record['original_state_proven'] = state_proven and theme_proven
    if record.get('product'):
        name_pair = folder / 'paired.png'
        p5.compose(name, out / record['product'], name_pair, (cell.width, cell.height))
        record['paired'] = output_path(out, name_pair)
    record.pop('original_step')


def new_audit_page(context, url, width, height, theme, foreign, font):
    page = context.new_page()
    try:
        page.set_viewport_size(dict(width=width, height=height))
        page.emulate_media(color_scheme=theme)
        return p5.prepare_page(page, url, foreign, font, redact_destinations=True)
    except BaseException:
        page.close()
        raise


def safe_error(error):
    if isinstance(error, ValueError) and str(error).startswith(
            ('capture ', 'browser zoom ', 'physical screenshot ', 'original author detail ')):
        return str(error)
    ids = re.findall(r'(?:data-testid[= ]|get_by_test_id\()\s*["\']?([a-z][a-z_-]+)', str(error))
    js_type = re.search(r'\b(TypeError|ReferenceError|SyntaxError|RangeError)\b', str(error))
    prop = re.search(r"Cannot read properties of (?:null|undefined) \(reading '([a-zA-Z]+)'\)", str(error))
    suffix = ', '.join(dict.fromkeys([*(m.group(1) for m in (js_type, prop) if m), *ids]))
    return f'{type(error).__name__} ({suffix})' if suffix else type(error).__name__


def capture_area_port(page, url, cell, out, folder, specs, record):
    from playwright.sync_api import TimeoutError

    record['port_step'] = 'load'
    page.bring_to_front()
    if not getattr(page, '_area_loaded', False):
        load_page(page, url, cell)
        page._area_loaded = True
    elif not smoke.set_theme(page, cell.theme):
        raise ValueError('capture theme not confirmed before opening screen')
    record['port_step'] = 'open'
    state_proven = fixture.open_area_port(page, cell, specs)
    target = image_area.target_for(cell.screen)
    page.mouse.move(0, 0)
    page.evaluate('document.fonts.ready')
    if target['item']:
        field = 'rendered_images' if target['image'] else 'items'
        try:
            page.wait_for_function(
                f'(target) => ({image_area.SCRIPT})(target).{field} > 0', arg=target, timeout=4500)
        except TimeoutError:
            pass  # Preserve the normal-state screenshot and mark the image condition unverified.
    page.wait_for_timeout(180)
    record['port_step'] = 'measure'
    if not smoke.set_theme(page, cell.theme):
        raise ValueError('capture theme not confirmed')
    metrics = measure_image_area(page, target)
    record['image_area'] = metrics
    if metrics['viewport'] != [cell.width, cell.height]:
        raise ValueError('capture viewport not confirmed')
    if page.locator('body').evaluate("e => e.classList.contains('body--dark')") != (cell.theme == 'dark'):
        raise ValueError('capture theme changed during measurement')
    record['port_step'] = 'screenshot'
    path = folder / 'port.png'
    if cell.screen in {'download', 'queue', 'failed'}:
        page.add_style_tag(content='''
            .pv-ud-row .pv-ud-c-id, .pv-job-id-seat, .pv-failure-id { color: transparent !important; }
        ''')
    screenshot(page, path, cell)
    record.update(product=output_path(out, path), state_proven=state_proven)
    record.pop('port_step')


def capture_batch(context, sources, out, cells, metadata, records, args, owned):
    from playwright.sync_api import Error

    specs = p5.helpers.load_expectations()['screens']
    foreign = set()
    old_font = p5.ORIGINAL_JAPANESE_FONT.read_bytes() if any(cell.original for cell in cells) else None
    ports = (args.port,) if args.final else (args.port, args.original_port)
    allowed = {f'127.0.0.1:{port}' for port in ports}

    def restrict_requests(route):
        address = urlsplit(route.request.url)
        if address.netloc in allowed:
            route.continue_()
        else:
            foreign.add(address.hostname or address.scheme)
            route.abort()

    def restrict_websockets(route):
        address = urlsplit(route.url)
        if address.netloc in allowed:
            route.connect_to_server()
        else:
            foreign.add(address.hostname or address.scheme)
            route.close()

    context.route('**/*', restrict_requests)
    context.route_web_socket('**/*', restrict_websockets)
    for state in dict.fromkeys(cell.state for cell in cells):
        state_cells = [cell for cell in cells if cell.state == state]
        source, product = sources / state / 'original', sources / state / 'port'
        source.mkdir(parents=True)
        profile = state if state in p5.PROFILES else 'normal'
        if any(cell.original for cell in state_cells):
            p5.copy_original(metadata['original_revision'], source)
        p5.seed_profile(product, source, profile, tone_mode=True)
        if args.image_area:
            fixture.seed_area_profile(product, source)
        if profile == 'long' and any(cell.screen == 'dedup' for cell in state_cells):
            for image_root in (source / 'Images', product / 'lib/fake-images'):
                for image in image_root.rglob('synthetic pair(*.png'):
                    image.rename(image.with_name(f'{p5.LONG[:48]} {image.name}'))
        nonce = secrets.token_urlsafe(24)
        extra_env = {'PV_UIUX_STATE': state, 'LOCALAPPDATA': str(sources / 'local-appdata'),
                     'APPDATA': str(sources / 'roaming-appdata')}
        if args.final:
            extra_env['PV_UIUX_RETRY_JUDGED'] = '1'
        if args.image_area:
            extra_env.update(PV_UI_IMAGE_AREA='1', LOCALAPPDATA=str(sources / 'local-appdata'),
                             APPDATA=str(sources / 'roaming-appdata'))
        options = dict(tone_mode=True, personal_fixture=True, server_script=Path(__file__).resolve(),
                       extra_env=extra_env)
        allow_browser()
        with ExitStack() as apps:
            old_url = apps.enter_context(p5.running_app('original', p5.ORIGINAL_PYTHON, source,
                                        args.original_port, **options)) if any(c.original for c in state_cells) else None
            port_url = apps.enter_context(p5.running_app('port', sys.executable, product,
                                                         args.port, nonce, **options))
            owned.observe()
            for width, height, zoom in image_area.SIZES if args.image_area else SIZES:
                for theme in ('light', 'dark'):
                    matching = [c for c in state_cells if (c.width, c.height, c.zoom, c.theme) ==
                                (width, height, zoom, theme)]
                    if args.image_area:
                        matching.sort(key=lambda cell: cell.screen == 'view-history')
                    if not matching:
                        continue
                    allow_browser()
                    with ExitStack() as contexts:
                        port = new_audit_page(context, port_url, width, height, theme, foreign, None)
                        contexts.callback(port.close)
                        old = None
                        if old_url and any(c.original for c in matching):
                            old = new_audit_page(context, old_url, width, height, theme, foreign, old_font)
                            contexts.callback(old.close)
                        for cell in matching:
                            allow_browser()
                            folder = out / cell.key
                            folder.mkdir(parents=True, exist_ok=True)
                            prior = records.get(cell.key, {})
                            record = records[cell.key] = {}
                            try:
                                before = json.loads(smoke.http(port_url + '/_h3/loaded-modules',
                                    headers={'X-Acceptance': nonce}, timeout=5).read())
                                if args.prime_zoom:
                                    set_browser_zoom(context, 100)
                                if args.image_area:
                                    capture_area_port(port, port_url, cell, out, folder, specs, record)
                                else:
                                    capture_port(port, port_url, cell, out, folder, specs, record,
                                                 prime_zoom=args.prime_zoom, final=args.final,
                                                 checkpoint=lambda: save_receipt(folder, metadata['fingerprint'], record))
                                after = json.loads(smoke.http(port_url + '/_h3/loaded-modules',
                                    headers={'X-Acceptance': nonce}, timeout=5).read())
                                record['loaded_modules_sha256'] = after
                                record['loaded_modules_stable'] = bool(after) and all(after.get(k) == v for k, v in before.items())
                                if not record['loaded_modules_stable']:
                                    record['state_proven'] = False
                            except (Error, ValueError, OSError) as error:
                                issue = f"移植版 {record.get('port_step')}: {safe_error(error)}"
                                record['issue'] = ' · '.join(filter(None, (record.get('issue'), issue)))
                            if args.prime_zoom and record.get('measured'):
                                record['findings'].extend(row for row in prior.get('findings', ())
                                                          if row['item'] == 'A11' and row['selector'] == '.pv-content')
                            if old and cell.original:
                                try:
                                    if args.prime_zoom:
                                        set_browser_zoom(context, 100)
                                    capture_original(old, old_url, cell, out, folder, profile, record,
                                                     prime_zoom=args.prime_zoom, image_area_mode=args.image_area)
                                except (Error, ValueError, OSError) as error:
                                    original_issue = f"元 Flet {record.get('original_step')}: {safe_error(error)}"
                                    record['issue'] = ' · '.join(filter(None, (record.get('issue'), original_issue)))
                            if prior.get('product') and not record.get('product'):
                                record = records[cell.key] = dict(prior, retry_issue=record.get('issue'))
                            save_receipt(folder, metadata['fingerprint'], record)
                            status = (image_area.result_for(cell, record.get('image_area')) if args.image_area else
                                      'captured' if proven(cell, record) else 'incomplete')
                            print(f'{cell.key} {status}', flush=True)
            net = smoke.network_state(port_url, nonce)
            metadata.setdefault('network', {})[f'{state}/{state_cells[0].zoom}'] = {
                'server_denials': net['denied'], 'fake_call_types': sorted(set(net['calls'])),
                'blocked_host_count': len(foreign),
            }
            owned.observe()


def save_receipt(folder, fingerprint, record):
    staging = folder / 'metrics.pending'
    p5.write_json(staging, dict(fingerprint=fingerprint, capture=record))
    staging.replace(folder / 'metrics.json')


def load_receipts(out, matrix, fingerprints):
    records = {}
    for cell in matrix:
        path = out / cell.key / 'metrics.json'
        if not path.is_file():
            continue
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['fingerprint'] not in fingerprints:
            raise ValueError('output includes a different product or audit revision')
        record = dict(saved['capture'], audit_fingerprint=saved['fingerprint'])
        record = {k: v for k, v in record.items() if k not in ('product', 'original', 'paired', 'dom')
                  or (out / v).is_file()}
        if 'dom' not in record:
            record['measured'] = False
        records[cell.key] = record
    return records


def browser_profile(scratch, zoom):
    folder = scratch / f'edge-{zoom}'
    default = folder / 'Default'
    default.mkdir(parents=True)
    p5.write_json(default / 'Preferences', {
        'partition': {'default_zoom_level': math.log(zoom / 100, 1.2)},
        'browser': {'check_default_browser': False},
    })
    return folder


def set_browser_zoom(context, zoom):
    """Set and read back Edge's own zoom in the owned profile."""
    page = context.pages[0]
    page.goto('edge://settings/appearance', wait_until='domcontentloaded', timeout=15000)
    verified = page.evaluate("""async target => {
        chrome.settingsPrivate.setDefaultZoom(target);
        return await new Promise(resolve => chrome.settingsPrivate.getDefaultZoom(resolve));
    }""", zoom / 100)
    if abs(verified - zoom / 100) > .01:
        raise ValueError('browser zoom preference readback failed')
    page.goto('about:blank')
    return verified


def verify_fixture(port, kind, temp, out):
    """Render the isolated app once over loopback, without a browser."""
    owned = tone.OwnedProcesses()
    scratch = temp / datetime.now().strftime('probe-%Y%m%d_%H%M%S_%f')
    seed = scratch / 'seed-output'
    seed.mkdir(parents=True)
    product = scratch / 'port'
    if kind == 'original':
        revision = p5.git(p5.ORIGINAL_REPO, 'rev-parse', 'HEAD').decode().strip()
        p5.copy_original(revision, seed)
    p5.seed_profile(product, seed, 'normal', tone_mode=True)
    nonce = secrets.token_urlsafe(24) if kind == 'port' else None
    observed = {}
    try:
        python, base = (sys.executable, product) if kind == 'port' else (p5.ORIGINAL_PYTHON, seed)
        with p5.running_app(kind, python, base, port, nonce, tone_mode=True,
                            personal_fixture=True, server_script=Path(__file__).resolve(),
                            startup_timeout=9, extra_env={'PV_UIUX_STATE': 'normal'}) as url:
            owned.observe()
            with smoke.http(url, timeout=5) as response:
                body = response.read()
            if kind == 'original':
                observed['shell_rendered'] = b'flutter_bootstrap.js' in body or b'flutter.js' in body
            else:
                observed['shell_rendered'] = b'tab-library' in body and b'preferences-open' in body
                with smoke.http(url + '/_tone/stop-recovery?_audit_theme=light', timeout=5) as response:
                    body = response.read()
                observed['recovery_rendered'] = b'native-profile-recovery' in body and b'shared-get-recovery-open' in body
                net = smoke.network_state(url, nonce)
                observed['network_denied_count'] = net['denied']
                observed['fake_call_types'] = sorted(set(net['calls']))
    finally:
        observed['lifecycle'] = owned.result(scratch, (port,) if kind == 'port' else (port, port + 1))
        name = 'FIXTURE_PROBE.json' if kind == 'port' else 'FLET_FIXTURE_PROBE.json'
        p5.write_json(out / name, observed)
    passed = (observed['shell_rendered'] and observed.get('recovery_rendered', True) and
              not observed['lifecycle']['remaining'] and all(observed['lifecycle']['owned_ports_closed'].values()))
    print(f'loopback fixture probe: {"pass" if passed else "failed"}')
    return int(not passed)


def within_pv_scratch(path):
    root = SCRATCH_ROOT.resolve()
    return path != root and path.is_relative_to(root)


def point_in_run():
    work = Path.cwd().resolve()
    override = os.environ.get('PV_UIUX_RUN_SCRATCH')
    roots = (Path(override),) if override else (TEMP, AREA_TEMP)
    return (any(within_pv_scratch(root) and work.is_relative_to(root)
                for root in (path.resolve() for path in roots))
            and (work / 'fixture.json').is_file())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--verify-fixture', nargs='?', const='port', choices=('port', 'original'))
    parser.add_argument('--image-area', action='store_true', help='measure the A17 normal image-area matrix')
    parser.add_argument('--final', action='store_true', help='measure product screens without rerunning Flet')
    parser.add_argument('--only', nargs='+', choices=tuple(dict.fromkeys((*SCREENS, *image_area.LABELS))))
    parser.add_argument('--state', action='append', choices=STATES)
    parser.add_argument('--condition', action='append',
                        choices=tuple(f'{w}x{h}-{t}-{z}' for w,h,z in dict.fromkeys((*SIZES, *image_area.SIZES))
                                      for t in ('light','dark')))
    parser.add_argument('--batch')
    parser.add_argument('--out', type=Path, help='evidence directory under this checkout’s verify')
    parser.add_argument('--scratch', type=Path, help='assigned temporary directory under S:/Work/_tmp/PV')
    parser.add_argument('--pilot', action='store_true')
    parser.add_argument('--rerun', action='store_true')
    parser.add_argument('--prime-zoom', action='store_true')
    parser.add_argument('--original-port', type=int, default=22614, choices=range(22610, 22618))
    parser.add_argument('--port', type=int, default=22628, choices=range(22620, 22630))
    parser.add_argument('--serve-original', type=int, choices=range(22610, 22618), help=argparse.SUPPRESS)
    parser.add_argument('--serve-port', type=int, choices=range(22620, 22630), help=argparse.SUPPRESS)
    parser.add_argument('--nonce', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.batch and not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', args.batch):
        parser.error('--batch requires an ASCII label with letters, digits or hyphens')
    if args.rerun and not args.only:
        parser.error('--rerun requires explicit screens')
    if args.prime_zoom and (not args.only or not args.condition or not all(c.endswith('-200') for c in args.condition)):
        parser.error('--prime-zoom requires explicit screens and 200% conditions')
    if args.serve_original or args.serve_port:
        if not point_in_run() or os.environ.get('PV_UIUX_STATE') not in STATES:
            parser.error('server requires an isolated UI audit fixture on S:')
        if args.serve_original:
            fixture.serve_original(args.serve_original)
        else:
            fixture.serve_port(args.serve_port, args.nonce)
        return 0

    if args.image_area and (args.final or args.state or args.prime_zoom or args.verify_fixture or
                            any(s not in image_area.LABELS for s in args.only or ())):
        parser.error('--image-area uses normal state, 100% zoom and the image-area screen inventory')
    if args.image_area and any(not c.endswith('-100') for c in args.condition or ()):
        parser.error('--image-area requires 100% zoom')
    if not args.image_area and 'library-works' in (args.only or ()):
        parser.error('library-works is available in --image-area mode')
    temp = (args.scratch or (AREA_TEMP if args.image_area else TEMP)).resolve()
    if not within_pv_scratch(temp):
        parser.error('--scratch must be inside the PV temporary directory')
    os.environ['PV_UIUX_RUN_SCRATCH'] = str(temp)
    matrix = plan_image_area() if args.image_area else plan()
    default_out = (args.out or (AREA_OUTPUT if args.image_area else OUTPUT)).resolve()
    if not default_out.is_relative_to((ROOT / 'verify').resolve()):
        parser.error('--out must be inside this checkout’s verify directory')
    out = temp / datetime.now().strftime('pilot-%Y%m%d_%H%M%S_%f') if args.pilot else default_out
    out.mkdir(parents=True, exist_ok=True)
    if args.verify_fixture:
        return verify_fixture(args.port if args.verify_fixture == 'port' else args.original_port,
                              args.verify_fixture, temp, out)
    metadata = dict(product_revision=p5.git(ROOT, 'rev-parse', 'HEAD').decode().strip(),
                    product_tree=p5.git(ROOT, 'rev-parse', 'HEAD:src').decode().strip(),
                    original_revision=p5.git(p5.ORIGINAL_REPO, 'rev-parse', 'HEAD').decode().strip(),
                    zoom_method='Chromium browser zoom, verified inner width and DPR',
                    dom_sha256=hashlib.sha256(DOM.encode()).hexdigest())
    if p5.git(ROOT, 'status', '--porcelain', '--', 'src'):
        raise ValueError('expected unchanged product src')
    audit_files = [*Path(__file__).parent.glob('uiux_*'), Path(p5.__file__), Path(tone.__file__),
                   ROOT / 'docs/UIUX_AUDIT_SPEC_20261122.md']
    metadata['fingerprint'] = hashlib.sha256(json.dumps(
        [metadata['product_tree'], metadata['original_revision'],
         *[hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(audit_files)]]
    ).encode()).hexdigest()
    if args.final:
        selected = plan_final(args.only, args.state)
    else:
        selected = plan_image_area(args.only) if args.image_area else plan(args.only, args.state)
    selected_matrix = [cell for cell in selected
                       if not cell.unsupported and (not args.condition or
                                                    any(condition in cell.key for condition in args.condition))]
    if args.final:
        matrix = selected_matrix
    fingerprints = {metadata['fingerprint']}
    if args.plan_only or args.batch:
        batches = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(out.glob('BATCH-*.json'))]
        for batch in batches:
            provenance = batch['metadata']
            if any(provenance[k] != metadata[k] for k in ('product_tree', 'original_revision', 'dom_sha256')):
                raise ValueError('batch belongs to a different product or DOM collector')
            fingerprints.add(provenance['fingerprint'])
        if args.plan_only:
            metadata['batches'] = batches
    records = load_receipts(out, selected_matrix if args.batch else matrix, fingerprints)
    if args.plan_only:
        if args.image_area:
            image_area.write_report(out, matrix, records, metadata)
        else:
            write_report(out, matrix, records, metadata)
        print(f'{len(matrix)} rows planned')
        return 0

    owned = tone.OwnedProcesses()
    scratch = temp / datetime.now().strftime('capture-%Y%m%d_%H%M%S_%f')
    outcome = 0
    cells = []
    try:
        allow_browser()
        scratch.mkdir(parents=True)
        os.environ.update(TEMP=str(scratch), TMP=str(scratch), PYTHONDONTWRITEBYTECODE='1',
                          PLAYWRIGHT_BROWSERS_PATH=str(scratch / 'browsers'))
        browser_env = os.environ | {
            'LOCALAPPDATA': str(scratch / 'browser-local-data'),
            'APPDATA': str(scratch / 'browser-roaming-data'),
        }
        from playwright.sync_api import sync_playwright

        cells = [cell for cell in selected_matrix if args.rerun or not (
            image_area.proven(cell, records.get(cell.key, {})) if args.image_area else
            proven(cell, records.get(cell.key, {})))]
        if cells:
            with sync_playwright() as playwright:
                for zoom in (100, 200):
                    selected = [cell for cell in cells if cell.zoom == zoom]
                    if not selected:
                        continue
                    allow_browser()
                    context = playwright.chromium.launch_persistent_context(
                        browser_profile(scratch, zoom), channel='msedge', headless=True,
                        viewport={'width': 1280, 'height': 800}, device_scale_factor=1,
                        accept_downloads=False, service_workers='block', env=browser_env, args=[
                            '--disable-gpu', '--disable-background-networking', '--no-proxy-server',
                            '--disable-crash-reporter', '--no-first-run',
                            '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                            f'--pv-uiux-audit={scratch.name}',
                        ])
                    try:
                        owned.observe()
                        if zoom != 100:
                            metadata['verified_browser_zoom'] = set_browser_zoom(context, zoom)
                        capture_batch(context, scratch / f'fixtures-{zoom}', out, selected,
                                      metadata, records, args, owned)
                    finally:
                        owned.observe()
                        context.close()
    except QuietPeriod as error:
        print(error, flush=True)
        outcome = 75
    finally:
        ports = (args.port,) if args.final else (args.original_port, args.original_port + 1, args.port)
        lifecycle = owned.result(scratch, ports)
        if args.batch:
            p5.write_json(out / f'BATCH-{args.batch}.json', dict(lifecycle=lifecycle, metadata=metadata))
        else:
            p5.write_json(out / 'LIFECYCLE.json', lifecycle)
            if args.image_area:
                image_area.write_report(out, matrix, records, metadata)
            else:
                counts, images = write_report(out, matrix, records, metadata)
                print(dict(counts=counts, images=dict(images)), flush=True)
    if args.image_area:
        return outcome or int(any(not image_area.proven(cell, records.get(cell.key, {})) for cell in cells))
    return outcome or int(any(not proven(cell, records.get(cell.key, {})) for cell in cells))


if __name__ == '__main__':
    raise SystemExit(main())
