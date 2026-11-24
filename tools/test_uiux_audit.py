"""Small HTML fixtures for the audit rules and the Chromium DOM collector."""
from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess

import pytest

from tools.uiux_rules import inspect, contrast


class FakeComputedDOM(HTMLParser):
    """Translate the fixture's explicit computed values without starting Chromium."""

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        style = dict(item.split(':', 1) for item in attrs.get('style', '').split(';') if item)
        self.nodes.append(dict(
            selector='#' + attrs['id'], tag=tag, role=attrs.get('role', tag),
            text=attrs.get('aria-label', ''), name=attrs.get('aria-label', ''),
            control=tag == 'button', button=tag == 'button', state='normal',
            rect=[float(attrs.get('data-x', 0)), 0, float(style.get('width', 32)), 32],
            foreground=[int(v) for v in style.get('color', '0,0,0').split(',')],
            background=[int(v) for v in style.get('background', '255,255,255').split(',')],
            fontSize=float(style.get('font-size', 13)), weight=400, font='sans-serif',
            bodyText=tag == 'p', scrollWidth=int(attrs.get('data-scroll', 32)),
            clientWidth=32, clip=attrs.get('data-clip', ''), fullText=attrs.get('title', ''),
            filled='data-filled' in attrs, cluster='example',
            tabIndex=0 if tag == 'button' else -1,
        ))


def test_known_html_violations_and_clean_controls():
    html = '''
      <button id="bad" aria-label="OK" style="width:19;color:155,155,155"></button>
      <button id="unnamed" data-x="150"></button>
      <button id="good" data-x="300" aria-label="保存"></button>
      <p id="clipped" aria-label="本文" data-scroll="100" data-clip="ellipsis"></p>
      <p id="full" aria-label="本文" data-scroll="100" data-clip="ellipsis" title="本文"></p>
    '''
    findings = inspect(dict(nodes=FakeComputedDOM(html).nodes, viewport=[1280, 800]), zoom=100)
    failed = {(f['item'], f['selector']) for f in findings if f['status'] == 'violation'}
    assert {('A1', '#bad'), ('A3', '#bad'), ('A7', '#bad'), ('A13', '#unnamed'),
            ('A15', '#clipped')} <= failed
    assert not any(selector in ('#good', '#full') for _, selector in failed)


@pytest.mark.parametrize('fg,bg,expected', [
    ([0, 0, 0], [255, 255, 255], 21),
    ([255, 255, 255], [255, 255, 255], 1),
    ([119, 119, 119], [255, 255, 255], 4.478),
])
def test_wcag_contrast(fg, bg, expected):
    assert contrast(fg, bg) == pytest.approx(expected, abs=.001)


@pytest.mark.parametrize(('size', 'weight', 'needs_45'), (
    (13, 700, True), (18.6, 700, True), (18.7, 700, False),
    (23.9, 400, True), (24, 400, False),
))
def test_small_bold_text_requires_normal_text_contrast(size, weight, needs_45):
    nodes = FakeComputedDOM('<p id="sample" aria-label="本文" style="color:130,130,130"></p>').nodes
    nodes[0].update(fontSize=size, weight=weight)
    findings = inspect(dict(nodes=nodes, viewport=[1280, 800]), zoom=100)
    assert any(row['item'] == 'A1' and row['threshold'] == 4.5 for row in findings) == needs_45


def test_inventory_keeps_missing_and_inapplicable_cells_distinct():
    from tools.uiux_audit import plan
    from tools.uiux_report import coverage

    matrix = plan(['ranking', 'settings'], ['normal', 'loading'])
    assert len(matrix) == 32
    counts = coverage(matrix, {})
    assert counts == {'planned': 24, 'missing': 24, 'captured': 0, 'inapplicable': 8}


def test_a16_requires_the_state_to_be_proven():
    from tools.uiux_audit import plan
    from tools.uiux_report import coverage

    cell = plan(['ranking'], ['loading'])[0]
    row = dict(product='port.png', measured=True, state_proven=False)
    assert coverage([cell], {cell.key: row})['missing'] == 1


def test_numeric_and_semantic_rules_keep_manual_candidates_separate():
    html = '''
      <button id="one" data-filled aria-label="保存"></button>
      <button id="two" data-filled data-x="31" aria-label="削除"></button>
      <button id="three" data-filled data-x="64" aria-label="続ける"></button>
      <p id="warning" aria-label="状態" style="font-size:10"></p>
      <h2 id="section" aria-label="見出し"></h2>
      <h4 id="subsection" aria-label="小見出し"></h4>
    '''
    nodes = FakeComputedDOM(html).nodes
    nodes[1].update(danger=True, defaultAction=True, inDialog=True, offHorizontal=True, tabIndex=-1)
    nodes[2].update(actionLabel=True)
    nodes[3].update(indicator='warning', lines=3, lineHeight=12, fontIssue='font')
    nodes[4]['heading'], nodes[5]['heading'] = 2, 4
    findings = inspect(dict(nodes=nodes, viewport=[640,400], documentWidth=720), zoom=200)
    observed = {(f['item'], f['status']) for f in findings}
    assert {('A2', 'review'), ('A4', 'violation'), ('A5', 'violation'), ('A6', 'violation'),
            ('A8', 'violation'), ('A9', 'review'), ('A10', 'violation'), ('A11', 'violation'),
            ('A12', 'violation'), ('A13', 'violation'), ('A15', 'violation')} <= observed


def test_quiet_period_blocks_the_browser_gate(monkeypatch):
    from tools import uiux_audit

    class Quiet:
        def exists(self):
            return True

    monkeypatch.setattr(uiux_audit, 'QUIET', Quiet())
    with pytest.raises(uiux_audit.QuietPeriod):
        uiux_audit.allow_browser()


def test_serve_rejects_scratch_override_outside_pv_temp(monkeypatch):
    from tools import uiux_audit

    outside = uiux_audit.ROOT / 'verify' / 'isolated-fixture-test'
    monkeypatch.setenv('PV_UIUX_RUN_SCRATCH', str(outside))
    monkeypatch.setattr(Path, 'cwd', lambda: outside)
    monkeypatch.setattr(Path, 'is_file', lambda path: path == outside / 'fixture.json')
    assert not uiux_audit.point_in_run()


def test_script_parses_without_starting_chromium():
    js = Path(__file__).with_name('uiux_dom.js')
    result = subprocess.run(['node', '--check', str(js)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_a17_inventory_and_thresholds():
    from tools.uiux_audit import plan_image_area
    from tools.uiux_image_area import result_for

    matrix = plan_image_area()
    assert len(matrix) == len({cell.key for cell in matrix}) == 112
    assert {cell.state for cell in matrix} == {'normal'}
    assert {cell.theme for cell in matrix} == {'light', 'dark'}
    cells = {(cell.screen, cell.width): cell for cell in matrix}
    small = cells['personal', 1280]
    large = cells['personal', 1920]
    metrics = dict(area_ratio=.55, items=30, visible_images=6, rendered_images=30)
    assert result_for(small, metrics) == '達成'
    assert result_for(large, metrics) == '未達'
    assert result_for(large, dict(metrics, area_ratio=.60)) == '達成'
    assert result_for(small, dict(metrics, rendered_images=0)) == '未確認'
    assert result_for(cells['download', 1280], dict(metrics, rendered_images=0)) == '達成'
    assert result_for(cells['learning', 1280], metrics) == '学習基準'
    assert result_for(cells['metadata', 1280], None) == '対象外'


@pytest.mark.skipif(os.environ.get('PV_UIUX_BROWSER_UNIT') != '1', reason='Chromium suite requires explicit invocation')
def test_a17_clips_to_scrollports_and_distinguishes_missing_images(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.uiux_audit import allow_browser, measure_image_area

    allow_browser()
    env = os.environ | {'APPDATA': str(tmp_path / 'roaming'), 'LOCALAPPDATA': str(tmp_path / 'local')}
    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel='msedge', headless=True, env=env, args=[
            '--disable-gpu', '--disable-background-networking', '--no-proxy-server',
            '--host-resolver-rules=MAP * ~NOTFOUND',
        ])
        try:
            page = browser.new_page(viewport={'width': 400, 'height': 300}, service_workers='block')
            page.set_content("""
                <style>body{margin:0} img{display:block} .area{border:2px solid}</style>
                <div class="banner" style="height:20px">Warning</div>
                <div class="headline" style="height:20px">Title</div>
                <div style="margin:10px; height:210px; width:160px; overflow:hidden">
                  <div class="area" style="width:200px;height:280px;overflow:auto;padding-top:10px">
                    <img class="thumb" style="width:80px;height:40px;object-fit:contain">
                    <div style="height:300px"></div>
                  </div>
                </div>
                <script>
                  const image = new Image();
                  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>';
                  document.querySelector('.thumb').src = 'data:image/svg+xml,' + encodeURIComponent(svg);
                </script>
            """)
            page.wait_for_function("document.querySelector('.thumb').naturalWidth === 20")
            target = {'region': '.area', 'item': '.thumb', 'image': '.thumb',
                      'chrome': {'banner': ['.banner'], 'heading': ['.headline', '.headline']}}
            grid = measure_image_area(page, target)
            assert grid['visible_rect'] == [12, 52, 158, 208]
            assert grid['area_ratio'] == pytest.approx(158 * 208 / (400 * 300))
            assert grid['first_image_y'] == 62
            assert grid['chrome']['heading']['height'] == 20
            assert grid['chrome_unassigned_height'] == 12
            single = measure_image_area(page, dict(target, single=True))
            assert single['image_rect'] == [32, 62, 40, 40]
            page.locator('.thumb').evaluate("e => {e.removeAttribute('src');}")
            missing = measure_image_area(page, target)
            assert missing['first_image_y'] is None
            assert missing['rendered_images'] == 0
            assert missing['area_ratio'] == grid['area_ratio']
        finally:
            browser.close()


@pytest.mark.skipif(os.environ.get('PV_UIUX_BROWSER_UNIT') != '1', reason='Chromium suite requires explicit invocation')
def test_chromium_detects_real_dom_and_composites_alpha():
    from playwright.sync_api import sync_playwright
    from tools.uiux_audit import allow_browser, scan

    allow_browser()
    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel='msedge', headless=True, args=[
            '--disable-gpu', '--disable-background-networking', '--no-proxy-server',
            '--host-resolver-rules=MAP * ~NOTFOUND',
        ])
        try:
            page = browser.new_page(viewport={'width':1280, 'height':800}, service_workers='block')
            page.context.route('**/*', lambda route: route.abort())
            page.set_content("""
                <body style="background:white">
                  <button data-testid="bad" style="width:16px;height:16px;padding:0;
                      background:white;border:0;color:rgba(0,0,0,.35);font-size:11px">OK</button>
                  <button data-testid="clean" style="width:60px;height:32px;margin:50px;
                      background:white;color:black">保存</button>
                  <button data-testid="no-name" style="width:50px;height:32px;margin:50px"></button>
                  <p data-testid="clip" style="width:36px;overflow:hidden;white-space:nowrap;
                      text-overflow:ellipsis">A long description</p>
                </body>
            """)
            data = scan(page)
            assert data['viewport'] == [1280, 800]
            measured = {row['selector']: row for row in data['nodes']}
            assert measured['[data-testid="bad"]']['background'] == [255, 255, 255]
            assert measured['[data-testid="bad"]']['foreground'] == pytest.approx([166,166,166], abs=1)
            results = inspect(data, zoom=100)
            violating = {(r['item'],r['selector']) for r in results if r['status'] == 'violation'}
            assert ('A1', '[data-testid="bad"]') in violating
            assert ('A3', '[data-testid="bad"]') in violating
            assert ('A7', '[data-testid="bad"]') in violating
            assert ('A13', '[data-testid="no-name"]') in violating
            assert ('A15', '[data-testid="clip"]') in violating
            assert not any(selector == '[data-testid="clean"]' for _, selector in violating)
        finally:
            browser.close()


@pytest.mark.skipif(os.environ.get('PV_UIUX_BROWSER_UNIT') != '1', reason='Chromium suite requires explicit invocation')
def test_edge_zoom_uses_browser_setting_and_reflows_css_viewport(tmp_path):
    from playwright.sync_api import sync_playwright
    from tools.uiux_audit import allow_browser, browser_profile, set_browser_zoom

    allow_browser()
    env = os.environ | {'APPDATA': str(tmp_path / 'roaming'), 'LOCALAPPDATA': str(tmp_path / 'local')}
    with sync_playwright() as runner:
        context = runner.chromium.launch_persistent_context(
            browser_profile(tmp_path, 200), channel='msedge', headless=True, env=env,
            viewport={'width': 1280, 'height': 800}, device_scale_factor=1,
            args=['--disable-gpu', '--disable-background-networking', '--host-resolver-rules=MAP * ~NOTFOUND'])
        try:
            assert set_browser_zoom(context, 200) == 2
            page = context.new_page()
            page.set_content('<p>synthetic zoom check</p>')
            assert page.evaluate('() => [innerWidth, innerHeight, devicePixelRatio]') == [640, 400, 2]
        finally:
            context.close()
