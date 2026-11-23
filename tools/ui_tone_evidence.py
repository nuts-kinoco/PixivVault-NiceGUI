"""UI tone evidence helpers for the isolated P5 Chromium tour."""
from __future__ import annotations

from collections import Counter
import ctypes
from ctypes import wintypes
from io import BytesIO
import json
import os
from threading import Thread
import time
from unittest.mock import patch
from urllib.parse import urljoin

from PIL import Image


EXTRA = (
    'view-history', 'metadata-ledger', 'shared-log', 'shared-log-collapsed',
    'stop-recovery', 'stop-uncertain', 'startup-reference', 'startup-cookie-rejected',
    'startup-lock-busy', 'startup-legacy-uncertain', 'startup-legacy-copy-error',
    'startup-stop-migration', 'stop-history-guide', 'stop-migration-dialog',
)
ORIGINAL = {'view-history': 'view-history', 'personal-results': 'ranking/自分'}
OPTIONAL = ('personal-results', 'request-wait', 'request-limit')
STARTUP_CASES = {
    'startup-reference': {},
    'startup-cookie-rejected': {'cookie_rejected': True},
    'startup-lock-busy': {'stop_gate_busy': True},
    'startup-legacy-uncertain': {'legacy_v5_uncertain': True},
    'startup-legacy-copy-error': {'legacy_copy_error': True},
    'startup-stop-migration': {'stop_migration_required': True},
    'stop-recovery': {'stop_get_uncertain': True},
    'stop-uncertain': {'stop_get_uncertain': True},
}
ROOTS = {
    'settings': '.pv-preferences-card',
    'view-history': '.q-dialog .q-card',
    'stop-recovery': '.q-dialog .q-card',
    'stop-uncertain': '.q-dialog .q-card',
    'stop-history-guide': '.q-dialog .q-card',
    'stop-migration-dialog': '.q-dialog .q-card',
    'shared-log': '[data-testid=shared-log-host]',
    'shared-log-collapsed': '[data-testid=shared-log-host]',
    'request-wait': '.pv-header-main',
    'request-limit': '.pv-header-main',
}


def register_recovery_pages(base):
    """Mount the product recovery page with generated state at fixture-only URLs."""
    from nicegui import app, ui
    from pixivvault_nicegui.managed_network_stop_shared_scope import _INFLIGHT_BYTES
    from pixivvault_nicegui.native_profile import NativeProfileStore
    from pixivvault_nicegui.native_startup import NativeProfileBinding, register_profile_recovery
    from stage5_generated_shared_owner import generated_shared_scope

    page_decorator, get_decorator = ui.page, app.get
    for key, kwargs in STARTUP_CASES.items():
        folder = base / 'recovery' / key
        folder.mkdir(parents=True)
        store = NativeProfileStore(folder / 'profile')
        binding = NativeProfileBinding(store)
        if kwargs.get('stop_get_uncertain'):
            scope = generated_shared_scope(folder)
            if key == 'stop-recovery':
                scope.gate.trip(429)
            else:
                scope.gate.inflight_path.write_bytes(_INFLIGHT_BYTES)
            binding.shared_network_stop_scope = scope

        def route(path, *, key=key):
            def decorate(render):
                @page_decorator(f'/_tone/{key}{path.rstrip("/")}')
                def themed():
                    render()
                    ui.dark_mode(ui.context.client.request.query_params.get('_audit_theme') != 'light')
                return themed
            return decorate

        with patch.object(ui, 'page', route), patch.object(
            app, 'get', lambda path, **kw: get_decorator(f'/_tone/{key}{path}', **kw)
        ):
            register_profile_recovery(binding, **kwargs)


def navigate_extra(page, key, kind):
    from tools.original_run import capture_original as original

    if kind == 'original' and key == 'view-history':
        rows = page.evaluate(original.OUTLINE_JS)
        label = next((e['text'] for e in rows if e['text'].startswith('閲覧履歴（学習タブ')), None)
        if label is None:
            raise ValueError('original view-history control not found')
        original.click_header(page, label)
        page.locator('flt-semantics[role=alertdialog]').first.wait_for(timeout=5000)
        return True
    if kind == 'original' and key == 'personal-results':
        original.open_screen(page, 'ranking/自分')
        page.wait_for_function(f"() => ({original.OUTLINE_JS})().some(e => e.text.startsWith('取得時刻:'))",timeout=8000)
        return True
    if kind == 'original':
        return False
    if key in {'request-wait', 'request-limit'}:
        page.goto(urljoin(page.url, f'/?_tone_retry={key}'), wait_until='domcontentloaded')
        page.get_by_test_id('request-retry-status').wait_for(timeout=5000)
        return True
    route = ('startup-stop-migration' if key in {'stop-history-guide', 'stop-migration-dialog'}
             else key if key in STARTUP_CASES else None)
    if route:
        from urllib.parse import urlsplit

        page.goto(urljoin(page.url, f'/_tone/{route}?' + urlsplit(page.url).query),
                  wait_until='domcontentloaded')
        page.get_by_test_id('native-profile-recovery').wait_for(timeout=5000)
        if key in {'stop-recovery', 'stop-uncertain'}:
            page.get_by_test_id('shared-get-recovery-open').click(timeout=5000)
            page.get_by_test_id('shared-get-recovery-notice').wait_for(timeout=5000)
        if key in {'stop-history-guide', 'stop-migration-dialog'}:
            page.get_by_test_id('stop-recovery-open').click(timeout=5000)
            page.get_by_test_id('stop-recovery-result').wait_for(timeout=5000)
        if key == 'stop-migration-dialog':
            page.get_by_test_id('stop-recovery-migration').click(timeout=5000)
            page.get_by_test_id('stop-migration-dialog').wait_for(timeout=5000)
        return True
    actions = {
        'view-history': ('view-history-open', 'view-history-dialog'),
        'shared-log': ('automation-scheduler', 'shared-log'),
        'shared-log-collapsed': ('automation-scheduler', 'shared-log-toggle'),
        'metadata-ledger': ('automation-metadata', 'metadata-view'),
        'personal-results': ('tab-personal', 'personal-ranking-view'),
    }
    if key not in actions:
        return False
    opener, expected = actions[key]
    if opener.startswith('automation-'):
        if not page.get_by_test_id('automation-dialog').is_visible():
            page.get_by_test_id('automation-open').click(timeout=5000)
    page.get_by_test_id(opener).click(timeout=5000)
    page.get_by_test_id(expected).wait_for(state='visible', timeout=5000)
    if key == 'metadata-ledger':
        page.get_by_text('台帳の詳細・保留と中断の確認', exact=True).click(timeout=5000)
        page.get_by_test_id('metadata-open').wait_for(state='visible', timeout=5000)
        page.get_by_test_id('metadata-view').evaluate('e => e.scrollTop = 0')
    if key == 'personal-results':
        page.get_by_test_id('personal-fetch').click(timeout=5000)
        page.get_by_text('件表示',exact=False).filter(visible=True).first.wait_for(timeout=8000)
    if key == 'shared-log-collapsed':
        page.get_by_test_id('shared-log-toggle').click(timeout=5000)
        page.get_by_test_id('shared-log').wait_for(state='hidden', timeout=5000)
    return True


def install_personal_fixture(ranking):
    from types import SimpleNamespace
    from pixivvault_nicegui.personal_ranking import PersonalRankingSource
    from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView

    initialize = PersonalRankingView.__init__

    def with_search(view, *args, **kwargs):
        initialize(view, *args, **kwargs)
        view.vault = SimpleNamespace(status=lambda: dict(open=True,mode='nana7mi',transport_available=True))
        view.source = PersonalRankingSource(SimpleNamespace(available=lambda:True,search=ranking.personal_search))
        view._selected_tags = ('合成タグ',)
        view._render_selected_tags()

    PersonalRankingView.__init__ = with_search


def install_retry_fixture():
    from types import SimpleNamespace
    from nicegui import ui
    from pixivvault_nicegui.ui.request_status_view import RequestStatusView

    initialize = RequestStatusView.__init__
    records = {
        'request-wait': [dict(phase='waiting',attempt=1,limit=3,status=429,
                             remaining_seconds=5,wait_seconds=5)],
        'request-limit': [dict(phase='stopped',attempt=3,limit=3,status=429,stop_cause='own_limit',notice_id=1),
                          dict(phase='stopped',attempt=2,limit=3,status=429,stop_cause='shared_limit',notice_id=2)],
    }

    def with_retry(view, policy, **kwargs):
        state = ui.context.client.request.query_params.get('_tone_retry')
        if state in records:
            policy = SimpleNamespace(progress_snapshot=lambda: records[state])
        initialize(view, policy, **kwargs)

    RequestStatusView.__init__ = with_retry


def close_extra(page, key, kind):
    if kind == 'original':
        if key == 'view-history':
            page.mouse.click(5, 5)
        return
    close_ids = {'view-history': 'view-history-close', 'shared-log-collapsed': 'automation-close',
                 'shared-log': 'automation-close', 'metadata-ledger': 'automation-close'}
    if key in close_ids:
        close = page.get_by_test_id(close_ids[key])
        if close.is_visible():
            close.click(timeout=5000)


def toolbar_rows(controls):
    """Group controls whose vertical middle falls in the same occupied row."""
    rows = []
    for item in sorted(controls, key=lambda control: (center_y(control), control['rect'][0])):
        _, y, _, height = item['rect']
        middle = y + height / 2
        if rows and all(abs(middle - center_y(i)) < min(height, i['rect'][3]) / 2 for i in rows[-1]):
            rows[-1].append(item)
        else:
            rows.append([item])
    return [sorted(row, key=lambda control: control['rect'][0]) for row in rows]


def center_y(control):
    return control['rect'][1] + control['rect'][3] / 2


def comparison(value, reference):
    return round(value - reference, 3) if value is not None and reference is not None else None


def summarize_toolbars(groups):
    return [dict(container=group['container'], rows=[
        dict(count=len(row), kinds=dict(Counter(item['kind'] for item in row)), controls=row)
        for row in toolbar_rows(group['controls'])]) for group in groups]


PORT_DOM = r"""(root) => {
  const styles = e => getComputedStyle(e);
  const round = n => Math.round(n * 100) / 100;
  const box = e => {const r = e.getBoundingClientRect(); return [r.x,r.y,r.width,r.height].map(round)};
  const visible = e => e.checkVisibility({visibilityProperty:true,opacityProperty:true}) && e.getClientRects().length;
  const name = e => e.dataset.testid || e.classList[0] || e.tagName.toLowerCase();
  const label = e => (e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || '')
    .replace(/\s+/g,' ').trim().slice(0,100);
  const content = '.pv-content';
  const item = '.pv-card,.q-card,.q-item,tr';
  const controlSelector = 'button,a,[role=button],.q-field,.q-chip,[role=switch],[role=checkbox],input,select,textarea';
  const spacing = s => ({padding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft],
    margin:[s.marginTop,s.marginRight,s.marginBottom,s.marginLeft],gap:[s.rowGap,s.columnGap]});
  const cardStyle = s => ({radius:[s.borderTopLeftRadius,s.borderTopRightRadius,s.borderBottomRightRadius,s.borderBottomLeftRadius],
    border:`${s.borderTopWidth} ${s.borderTopStyle} ${s.borderTopColor}`,shadow:s.boxShadow});
  const controls = [...root.querySelectorAll(controlSelector)].filter(e => visible(e) &&
    !e.parentElement.closest(controlSelector)).map(e => {
    const s = styles(e), r = box(e), icon = !!e.querySelector('.q-icon'),
          text = [...e.querySelectorAll('.q-icon')].reduce((t,i) => t.replace(i.textContent,''),e.innerText || '').trim();
    const kind = e.matches('[role=switch],[role=checkbox]') ? 'switch' : e.matches('.q-chip') ? 'chip'
      : e.matches('.q-field,input,select,textarea') ? 'input' : 'button';
    const background = s.backgroundColor !== 'rgba(0, 0, 0, 0)' && s.backgroundColor !== 'transparent';
    const button = kind !== 'button' ? null : icon && !text ? 'icon-only'
      : e.classList.contains('pv-pill') || parseFloat(s.borderTopLeftRadius) >= r[3]/2 - 1 ? 'pill'
      : (background || e.classList.contains('q-btn--unelevated')) ? 'filled-square'
      : e.classList.contains('q-btn--outline') || s.borderTopStyle !== 'none' ? 'outlined' : 'text-link';
    const id = name(e), title = label(e);
    const shared = /size-|カードサイズ|表示サイズ/.test(id+' '+title) ? 'size' : /refresh|更新|再読込/.test(id+' '+title) ? 'refresh'
      : /period|期間|ランキングモード/.test(id+' '+title) && (kind === 'chip' || kind === 'button') ? 'period'
      : kind === 'switch' ? 'switch' : null;
    return {e, data:{id,label:title,rect:r,kind,button,shared,enabled:!e.matches(':disabled,[aria-disabled=true],.disabled'),
      font_size:s.fontSize, font:s.fontFamily, ...cardStyle(s), background:s.backgroundColor}};
  });
  const groups = new Map();
  for (const {e,data} of controls) {
    const row = e.closest('.nicegui-row,.q-tabs,.q-expansion-item__container,fieldset,[role=toolbar],.pv-toolbar');
    const host = row && root.contains(row) ? row : e.parentElement;
    const card = e.closest(item);
    if (card && card !== root && root.contains(card) && !card.matches('.pv-preferences-card') &&
        (card.matches('tr,.pv-card') || card.closest('.pv-card,.q-table'))) continue;
    if (!groups.has(host)) groups.set(host, {container:name(host),rect:box(host),...spacing(styles(host)),controls:[]});
    groups.get(host).controls.push(data);
  }
  const fonts = {}, headings = [];
  const heading = 'h1,h2,h3,h4,h5,h6,[role=heading],.text-h4,.text-h5,.text-h6,.text-subtitle1,.pv-serif-heading,.pv-dialog-title,.pv-failure-heading,.pv-library-heading,.pv-view-title,.text-2xl.font-semibold,.text-lg.font-semibold';
  for (const e of [root,...root.querySelectorAll('*')]) {
    if (!visible(e) || e.matches('script,style,.q-icon,svg,svg *')) continue;
    const s = styles(e);
    for (const node of e.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim()) fonts[s.fontSize] = (fonts[s.fontSize] || 0) + 1;
    }
    if (e.matches(heading) && !e.querySelector(heading) && label(e)) {
      headings.push({label:label(e),font:s.fontFamily,size:s.fontSize,weight:s.fontWeight,rect:box(e)});
    }
  }
  const cards = [...root.querySelectorAll('.pv-card,.q-card,.pv-author-card,.pv-gallery-card')];
  if (root.matches('.q-card')) cards.unshift(root);
  const cardStyles = {};
  for (const e of cards.filter(visible)) {
    const style = JSON.stringify({...cardStyle(styles(e)),...spacing(styles(e))});
    cardStyles[style] = (cardStyles[style] || 0) + 1;
  }
  const scroll = [root,...root.querySelectorAll('*')].filter(e => visible(e) &&
    /auto|scroll/.test(styles(e).overflowY) && e.scrollHeight > e.clientHeight+1).map(e =>
      ({id:name(e),rect:box(e),client_height:e.clientHeight,scroll_height:e.scrollHeight}));
  const r = root.getBoundingClientRect(), bottom = Math.min(r.bottom,innerHeight), top = Math.max(0,r.top);
  const extent = Math.max(root.scrollHeight, ...scroll.map(e => Math.round(e.rect[1]-r.y)+e.scroll_height));
  return {root:name(root), rect:box(root), in_viewport_height:round(Math.max(0,bottom-top)),
    viewport_height:innerHeight, client_height:root.clientHeight, scroll_height:root.scrollHeight,
    scroll_ratio:round(root.scrollHeight/innerHeight),
    content_extent:extent, content_ratio:round(extent/innerHeight),
    document_scroll_height:document.scrollingElement.scrollHeight,
    document_scroll_ratio:round(document.scrollingElement.scrollHeight/innerHeight),
    viewport_insets:[Math.max(0,r.y),Math.max(0,innerWidth-r.right),Math.max(0,innerHeight-r.bottom),Math.max(0,r.x)].map(round),
    spacing:spacing(styles(root)), content_spacing:spacing(styles(root.querySelector(content) || root)),
    groups:[...groups.values()], controls:controls.map(c => c.data), fonts, headings,
    cards:Object.entries(cardStyles).map(([style,count]) => ({...JSON.parse(style),count})), scroll,
    states:[...root.querySelectorAll('[role=status],[role=alert],[data-testid*=empty],.pv-empty')]
      .filter(visible).map(e => ({id:name(e),label:label(e),rect:box(e)})).filter(e => e.label),
  };
}"""


FLET_DOM = r"""() => {const nodes = [...document.querySelectorAll('flt-semantics')];
const indexes = new Map(nodes.map((e,i)=>[e,i]));
return nodes.map((e,index) => {
  const r=e.getBoundingClientRect();
  const ancestor=e.parentElement.closest('flt-semantics');
  return {index, parent:ancestor ? indexes.get(ancestor) : null,
    role:e.getAttribute('role'), label:(e.getAttribute('aria-label') || e.innerText || e.textContent || '').replace(/\s+/g,' ').trim().slice(0,140),
    rect:[r.x,r.y,r.width,r.height].map(Math.round), tag:e.localName,
    client_height:e.clientHeight, scroll_height:e.scrollHeight, scroll_top:e.scrollTop};
}).filter(e=>e.rect[2]>0 && e.rect[3]>0)}"""


def measure_port(locator):
    data = locator.evaluate(PORT_DOM)
    data['toolbars'] = summarize_toolbars(data.pop('groups'))
    controls = data.pop('controls')
    data['buttons'] = dict(Counter(control['button'] for control in controls if control['button']))
    data['common_components'] = [control for control in controls if control['shared']]
    return data


def measure_original(page, key):
    nodes = page.evaluate(FLET_DOM)
    viewport = page.viewport_size
    height, width = viewport['height'], viewport['width']
    if key in {'settings', 'view-history'}:
        dialog = next((n for n in nodes if n['role'] == 'alertdialog'), None)
        parents = {dialog['index']} if dialog else set()
        content = []
        for node in nodes:
            if node['parent'] in parents:
                content.append(node)
                parents.add(node['index'])
    else:
        # Main navigation contains all eight labels in a single accessibility band.
        from tools.original_run.capture_original import TABS

        nav_bottom = max((n['rect'][1] + n['rect'][3] for n in nodes
                          if n['label'] in TABS.values() and n['role'] == 'button'), default=0)
        content = [n for n in nodes if n['rect'][1] >= nav_bottom-2]
    visible = [n for n in content if n['rect'][1] < height and n['rect'][1]+n['rect'][3] > 0
               and n['rect'][0] < width and n['rect'][0]+n['rect'][2] > 0]
    labelled = [n for n in visible if n['label']]
    controls = []
    for n in visible:
        kind = {'button':'button', 'link':'button', 'textbox':'input', 'combobox':'input',
                'slider':'input', 'spinbutton':'input', 'switch':'switch', 'checkbox':'switch',
                'radio':'switch', 'tab':'button'}.get(n['role'])
        if kind:
            controls.append({**n, 'kind':kind})
    bounds = [labelled or visible, content]
    tops = [min((n['rect'][1] for n in entries), default=None) for entries in bounds]
    bottoms = [max((n['rect'][1]+n['rect'][3] for n in entries), default=None) for entries in bounds]
    hs = [max(0, min(height,bottoms[0])-max(0,tops[0])) if tops[0] is not None else None,
          bottoms[1]-tops[1] if tops[1] is not None else None]
    return {'source':'Flutter CanvasKit accessibility bounds', 'viewport_height':height,
            'in_viewport_height':hs[0], 'semantic_extent_height':hs[1],
            'semantic_extent_ratio': round(hs[1]/height, 3) if hs[1] is not None else None,
            'scroll_height':None, 'scroll_ratio':None, 'fonts':None, 'buttons':None, 'cards':None,
            'headings':[n for n in visible if n['role'] == 'heading'], 'spacing':None,
            'semantic_scroll_nodes':[n for n in visible if n['scroll_height'] > n['client_height']+1],
            'toolbars':summarize_toolbars([{'container':'semantic-body','controls':controls}]),
            'common_components':[n for n in controls if n['kind'] == 'switch' or
                                 any(k in n['label'] for k in ('更新','再読込','期間','サイズ'))],
            'states':[n for n in labelled if any(k in n['label'] for k in ('ありません','対象なし','未実行'))]}


def original_controls(target, folder):
    """Read actual Flet controls whose styling is drawn by CanvasKit."""
    pages = []
    request = folder / 'tone-request.txt'

    def serve():
        last = None
        while True:
            time.sleep(.05)
            if not pages or not request.is_file():
                continue
            token = request.read_text(encoding='ascii')
            if not token.isascii() or not token.isdecimal() or token == last:
                continue
            page = pages[-1]
            panels = [d for d in page._dialogs.controls if d.open and type(d).__name__ == 'AlertDialog']
            data = flet_styles(panels[-1:] or page.controls[4:])
            reply = folder / f'tone-controls-{token}.json'
            reply.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
            last = token

    worker = Thread(target=serve, name='p5-original-style-reader', daemon=True)
    worker.start()

    def capture_page(page):
        target(page)
        pages[:] = [page]

    return capture_page


def flet_styles(roots):
    from flet.controls.base_control import BaseControl

    def children(control):
        for name in ('controls','content','title','actions','leading','trailing','tabs','body'):
            value = getattr(control,name,None)
            for item in value if isinstance(value,(list,tuple)) else (value,):
                if isinstance(item,BaseControl):
                    yield item

    def prop(control, name):
        value = getattr(control,name,None)
        return value if isinstance(value,(str,int,float,type(None))) else str(value)

    fonts, buttons = Counter(), Counter()
    box_styles, headings, common = [], [], []
    visited = set()

    def visit(control):
        if not getattr(control,'visible',True) or id(control) in visited:
            return
        visited.add(id(control))
        kind = type(control).__name__
        value = getattr(control,'value',None)
        if kind == 'Text' and isinstance(value,str) and value.strip():
            size, font, weight = (prop(control,k) for k in ('size','font_family','weight'))
            fonts[str(size) if size is not None else 'theme-default'] += 1
            if (isinstance(size,(int,float)) and size >= 16) or (font and 'mincho' in font.lower()):
                headings.append(dict(label=value[:100],size=size,font=font,weight=weight))
        if kind.endswith('Button'):
            style = getattr(control,'style',None)
            shape = prop(style,'shape')
            if kind == 'IconButton':
                appearance = 'icon-only'
            elif shape and any(tag in shape for tag in ('Stadium','Circle')):
                appearance = 'pill'
            elif kind == 'TextButton':
                appearance = 'text-link'
            elif kind == 'OutlinedButton':
                appearance = 'outlined'
            else:
                appearance = 'filled-default-shape'
            buttons[appearance] += 1
        label = next((getattr(control,k) for k in ('content','label','tooltip')
                      if isinstance(getattr(control,k,None),str)), '')
        if kind in {'Switch','Checkbox','Chip','SegmentedButton'} or any(s in label for s in ('更新','再読込','サイズ','期間')):
            common.append(dict(type=kind,label=label[:100]))
        if kind in {'Card','Container','AlertDialog','Row','Column','GridView','ListView'}:
            style = {k:prop(control,k) for k in ('padding','margin','spacing','run_spacing','border_radius',
                                                 'border','shadow','elevation','bgcolor','shape','scroll')}
            style = {k:v for k,v in style.items() if v is not None}
            if style:
                box_styles.append(dict(type=kind,**style))
        for item in children(control):
            visit(item)

    for root in roots:
        visit(root)
    box_counts = Counter(json.dumps(row,sort_keys=True,ensure_ascii=False) for row in box_styles)
    return {'source':'running Flet control tree, declared styles', 'fonts':dict(fonts), 'buttons':dict(buttons),
            'headings':headings,'common_components':common,
            'box_styles':[dict(json.loads(row),count=count) for row,count in box_counts.items()]}


def read_original_styles(folder):
    token = str(time.monotonic_ns())
    (folder / 'tone-request.txt').write_text(token,encoding='ascii')
    reply = folder / f'tone-controls-{token}.json'
    for _ in range(60):
        try:
            return json.loads(reply.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(.05)
    return None


def measure(page, key, kind):
    if kind == 'original':
        return measure_original(page, key)
    root = ('[data-testid=stop-migration-dialog] .q-card' if key == 'stop-migration-dialog' else
            '[data-testid=native-profile-recovery] >> xpath=..' if key.startswith('startup-') else
            ROOTS.get(key, '.pv-content'))
    return measure_port(page.locator(root).filter(visible=True).first)


def expand_settings(page):
    card = page.locator('.pv-preferences-card')
    closed = card.locator('.q-expansion-item--collapsed .q-item[aria-expanded=false]')
    for _ in range(closed.count()):
        closed.first.click(timeout=5000)
    card.evaluate('e => e.scrollTop = 0')


def capture_settings(page, folder, *, write_metrics=True):
    """Stitch actual scrolled dialog pixels, then crop each rendered group."""
    card = page.locator('.pv-preferences-card')
    card.evaluate('e => e.scrollTop = 0')
    box = card.bounding_box()
    geometry = card.evaluate('''e => ({height:e.scrollHeight, viewport:e.clientHeight,
       footer: Math.ceil(e.querySelector('.pv-preferences-footer')?.getBoundingClientRect().height || 0),
       innerTop:e.clientTop, innerLeft:e.clientLeft, width:e.clientWidth,
       groups:[...e.querySelectorAll('.pv-preferences-group')].map(group => {
         const r=group.getBoundingClientRect(), box=e.getBoundingClientRect();
         return {title:group.innerText.trim().split('\\n')[0] || '',
                 x:Math.round(r.x-box.x-e.clientLeft),y:Math.round(r.y-box.y-e.clientTop),
                 w:Math.round(r.width),h:Math.round(r.height)};
       })})''')
    left = round(box['x']) + geometry['innerLeft']
    top = round(box['y']) + geometry['innerTop']
    width, height, visible = (geometry[k] for k in ('width','height','viewport'))
    usable = max(1, visible - geometry['footer'])
    last_scroll = max(0, height - visible)
    stitch = Image.new('RGB',(width,height))
    try:
        for offset in range(0, last_scroll + usable, usable):
            actual = card.evaluate('(e,y) => {e.scrollTop=y;return e.scrollTop}',offset)
            page.wait_for_timeout(110)
            clip = dict(x=left,y=top,width=width,height=visible if actual >= last_scroll else usable)
            with Image.open(BytesIO(page.screenshot(clip=clip, timeout=10000, scale='css'))) as shot:
                stitch.paste(shot, (0,int(actual)))
    finally:
        card.evaluate('e => e.scrollTop = 0')
    stitch.save(folder / 'port-all-groups.png')
    groups = []
    for index, group in enumerate(geometry['groups']):
        stem = f'group-{index+1:02}'
        x,y,w,h = (group[k] for k in ('x','y','w','h'))
        stitch.crop((x,y,x+w,y+h)).save(folder / f'{stem}.png')
        record = dict(**group,image=f'{stem}.png')
        if write_metrics:
            measurements = measure_port(card.locator('.pv-preferences-group').nth(index))
            (folder / f'{stem}.json').write_text(json.dumps(measurements,indent=2,ensure_ascii=False)+'\n',
                                                 encoding='utf-8',newline='\n')
            record['metrics'] = f'{stem}.json'
        groups.append(record)
    return groups


class ProcessEntry(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD), ('usage',wintypes.DWORD), ('pid',wintypes.DWORD),
                ('heap',ctypes.c_size_t), ('module',wintypes.DWORD), ('threads',wintypes.DWORD),
                ('parent',wintypes.DWORD), ('priority',wintypes.LONG), ('flags',wintypes.DWORD),
                ('image',wintypes.WCHAR * 260)]


def process_snapshot():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = ProcessEntry(size=ctypes.sizeof(ProcessEntry))
    records = {}
    try:
        found = kernel.Process32FirstW(wintypes.HANDLE(handle), ctypes.byref(entry))
        while found:
            records[entry.pid] = (entry.parent, entry.image)
            found = kernel.Process32NextW(wintypes.HANDLE(handle), ctypes.byref(entry))
    finally:
        kernel.CloseHandle(wintypes.HANDLE(handle))
    return records


def creation_time(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    times = [wintypes.FILETIME() for _ in range(4)]
    try:
        if kernel.GetProcessTimes(wintypes.HANDLE(handle), *(ctypes.byref(t) for t in times)):
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(wintypes.HANDLE(handle))
    return None


class OwnedProcesses:
    def __init__(self):
        self.root = os.getpid()
        self.created = creation_time(self.root)
        self.observed = {}

    def observe(self):
        snapshot = process_snapshot()
        parents = {self.root:self.created}
        for pid, (_,_,created) in self.observed.items():
            if pid in snapshot and creation_time(pid) == created:
                parents[pid] = created
        while True:
            descendants = {}
            for pid,(parent,image) in snapshot.items():
                if parent in parents and pid not in parents:
                    created = creation_time(pid)
                    if created is not None and created >= parents[parent]:
                        descendants[pid] = (parent,image,created)
            if not descendants:
                return
            parents.update({pid:created for pid,(_,_,created) in descendants.items()})
            self.observed.update(descendants)

    def result(self, temp, ports):
        from tools.ui_smoke_browser import port_free

        self.observe()
        snapshot = process_snapshot()
        return {'run_temp':str(temp), 'observed_by_image':dict(Counter(image for _,image,_ in self.observed.values())),
                'remaining':sorted(pid for pid,(parent,image,created) in self.observed.items()
                                   if snapshot.get(pid) == (parent,image) and creation_time(pid) == created),
                'owned_ports_closed':{port:port_free(port) for port in ports}}
