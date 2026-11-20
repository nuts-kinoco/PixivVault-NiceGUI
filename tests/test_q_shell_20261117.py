"""P2: 共通シェル（ヘッダー・タブバー・配色）を元の Flet 版の見た目へ戻す。

仕様: docs/ORIGINAL_SHELL_SPEC.md。構造は DemoShell の要素木で、配色は theme.CSS の数値で、
実寸（全タブ同幅）は実ブラウザの計算値で判定する。
"""
import re
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui import shell as shell_module
from pixivvault_nicegui.ui.shell import DemoShell
from pixivvault_nicegui.ui.theme import CSS

TAB_ORDER = ('download', 'queue', 'failed', 'dedup', 'learning', 'ranking', 'personal', 'library',
             'recommend', 'scheduler', 'metadata')
TAB_ICONS = {'download': 'download', 'queue': 'queue', 'failed': 'report_problem', 'dedup': 'content_copy',
             'learning': 'explore', 'ranking': 'leaderboard', 'personal': 'military_tech',
             'library': 'photo_library', 'recommend': 'recommend', 'scheduler': 'schedule',
             'metadata': 'edit_note'}
HEADER_BUTTONS = (('tab-history', 'history'), ('view-history-open', 'visibility'),
                  ('saved-folder-open', 'folder_open'), ('theme-toggle', 'light_mode'),
                  ('pixiv-home-link', 'public'), ('preferences-open', 'settings'))


def walk(element):
    yield element
    for child in element.default_slot.children:
        yield from walk(child)


def tid(element):
    return element.props.get('data-testid')


def build(services):
    services.live_client = SimpleNamespace(close=lambda: None)  # 「自分のランキング」を並べる条件
    client = Client(ui.page('/_q_shell'))
    return client


@pytest.fixture
def shell_and_client():
    services = ApplicationServices(120)
    client = build(services)
    with client:
        shell = DemoShell(services)
    try:
        yield shell
    finally:
        with client:
            shell.close()
        client.delete()
        services.close()


# ---------------------------------------------------------------- 構造
def test_navigation_constant_matches_the_decided_order():
    assert tuple(key for key, _ in shell_module.NAVIGATION) == TAB_ORDER


def test_tab_bar_order_icons_and_no_backup_tab(shell_and_client):
    shell = shell_and_client
    nav = next(e for e in walk(shell.root) if 'pv-nav' in e.classes)
    ids = [tid(e) for e in walk(nav) if (tid(e) or '').startswith('tab-')]
    assert ids == [f'tab-{k}' for k in TAB_ORDER]
    assert 'tab-history' not in ids
    for key in TAB_ORDER:
        assert shell.buttons[key].props.get('icon') == TAB_ICONS[key], key
        assert 'pv-tab' in shell.buttons[key].classes


def test_backup_is_a_header_icon_button_and_still_opens_the_backup_screen(shell_and_client):
    shell = shell_and_client
    header = next(e for e in walk(shell.root) if 'pv-header' in e.classes)
    button = next(e for e in walk(header) if tid(e) == 'tab-history')
    assert button.props.get('icon') == 'history'
    assert button is shell.buttons['history']
    assert 'history' in shell_module.AVAILABLE_TABS


def test_header_right_column_has_six_icon_buttons_in_order(shell_and_client):
    shell = shell_and_client
    right = next(e for e in walk(shell.root) if 'pv-header-actions' in e.classes)
    found = []
    for e in walk(right):
        if tid(e) in {t for t, _ in HEADER_BUTTONS}:
            icon = e.props.get('icon')
            if icon is None:  # Pixiv を開くはリンク内のアイコン
                icon = next(c for c in walk(e) if c is not e and c.tag == 'q-icon').props['name']
            found.append((tid(e), icon))
    assert found == list(HEADER_BUTTONS)


def test_header_left_column_title_then_auth_then_connection_row(shell_and_client):
    shell = shell_and_client
    left = next(e for e in walk(shell.root) if 'pv-header-main' in e.classes)
    kids = list(left.default_slot.children)
    assert 'pv-title' in kids[0].classes and kids[0].text == 'PixivVault'
    row = next(e for e in walk(left) if 'pv-conn-row' in e.classes)
    ids = [tid(e) for e in walk(row)]
    # 元の「アプリ: 接続中…」は iOS 連携の接続状態。除外したので表示しない（API切り替えは残る）。
    assert 'premium-borrow-toggle' in ids and 'app-connection-status' not in ids
    labels = [e.text for e in walk(row) if getattr(e, 'text', None)]
    assert 'API切り替え' in labels
    assert not any('iOS' in t or 'Shaft' in t for t in labels)


def test_header_does_not_carry_the_old_subtitle_or_text_buttons(shell_and_client):
    shell = shell_and_client
    texts = [e.text for e in walk(shell.root) if getattr(e, 'text', None)]
    assert not any('専用管理先へ保存' in t or 'ランキング・学習は合成データ' in t for t in texts)
    settings = next(e for e in walk(shell.root) if tid(e) == 'preferences-open')
    assert not settings.props.get('label') and not getattr(settings, 'text', '')


def test_db_banner_stays_on_top(shell_and_client):
    shell = shell_and_client
    assert shell.root.default_slot.children[0] is shell.db_banner


def test_settings_dialog_title_uses_the_serif_class():
    from pathlib import Path
    src = Path(shell_module.__file__).with_name('preferences_view.py').read_text(encoding='utf-8')
    assert re.search(r"ui\.label\('設定'\)\.classes\('[^']*pv-dialog-title", src)


# ---------------------------------------------------------------- 配色
def css_block(selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', CSS)
    assert match, selector
    return dict(re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', match.group(1)))


DARK, LIGHT = css_block(':root'), css_block('body.body--light')


def hex_to_rgb(value):
    value = value.strip().lstrip('#')
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = sorted((luminance(hex_to_rgb(fg)), luminance(hex_to_rgb(bg))), reverse=True)
    return (a + 0.05) / (b + 0.05)


def blend(fg, bg, alpha):
    f, b = hex_to_rgb(fg), hex_to_rgb(bg)
    return '#%02x%02x%02x' % tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


def test_design_tokens_are_css_variables_with_original_dark_values():
    expected = {'--pv-bg-vault': '#0b0b10', '--pv-bg-surface': '#15161c', '--pv-bg-card': '#1e202d',
                '--pv-border-vault': '#2a2b38', '--pv-pixiv-blue': '#0096FA', '--pv-pixiv-blue-hover': '#0080d6',
                '--pv-text-main': '#e8e8ef', '--pv-text-muted': '#8a8a99', '--pv-text-elsewhere': '#b9b9c4'}
    for name, value in expected.items():
        assert DARK[name].strip().lower() == value.lower(), name
    assert DARK['--pv-pixiv-blue-glow'].replace(' ', '') == 'rgba(0,150,250,.25)'
    assert DARK['--pv-glass-bg'].replace(' ', '') == 'rgba(21,22,28,.94)'
    assert DARK['--pv-text-elsewhere'] != DARK['--pv-text-muted']
    # 元のダークの地色（Material 3 の自動配色の実測）
    assert DARK['--pv-page'].lower() == '#111418'
    assert LIGHT['--pv-page'].lower() == '#f8f9ff'


def test_title_and_tabs_use_sans_and_the_original_title_size():
    title = re.search(r'\.pv-title\s*\{([^}]*)\}', CSS).group(1)
    assert 'font-size:32px' in title.replace(' ', '') and 'font-weight:700' in title.replace(' ', '')
    assert 'Mincho' not in title
    tab = re.search(r'\.pv-tab\s*\{([^}]*)\}', CSS).group(1)
    assert 'Mincho' not in tab
    assert DARK['--pv-ui-font'].startswith('"Noto Sans JP"')
    assert re.search(r'body\s*\{[^}]*font-family:var\(--pv-ui-font\)', CSS)
    assert re.search(r'\.pv-dialog-title\s*\{[^}]*Yu Mincho', CSS)


def test_selected_tab_ground_is_the_measured_original_color_over_transparent_unselected():
    # 2026-11-18: 「PRIMARY の 12%」の推定をやめ、元の実測色（ダーク #2d3844 / ライト #d3dde9）に合わせた。
    assert re.search(r'\.pv-tab\.pv-tab-active\s*\{[^}]*background:\s*var\(--pv-tab-selected\)', CSS)
    plain = re.search(r'\.pv-nav \.q-btn\.pv-tab\s*\{([^}]*)\}', CSS).group(1)
    assert 'transparent' in plain and 'var(--pv-on-surface)' in plain


@pytest.mark.parametrize('theme,tokens', [('dark', DARK), ('light', {**DARK, **LIGHT})])
def test_shell_text_contrast_is_at_least_4_5(theme, tokens):
    page = tokens['--pv-page']
    pairs = {
        'on-surface/page': (tokens['--pv-on-surface'], page),
        'on-surface-variant/page (header icons, connection label)': (tokens['--pv-on-surface-variant'], page),
        'tab selected text/selected ground': (tokens['--pv-primary'], tokens['--pv-tab-selected']),
        'cookie banner title/container': (tokens['--pv-on-surface'], tokens['--pv-container']),
        'cookie banner body/container': (tokens['--pv-on-surface-variant'], tokens['--pv-container']),
        'banner open button text/pill': (tokens['--pv-primary'], tokens['--pv-container-pill']),
        'muted text/page': (tokens['--pv-text-muted'], page),
        'muted text/surface': (tokens['--pv-text-muted'], tokens['--pv-bg-surface']),
        'main text/surface': (tokens['--pv-text-main'], tokens['--pv-bg-surface']),
    }
    for tone in ('red', 'green', 'yellow', 'blue', 'primary'):
        pairs[f'auth {tone}/page'] = (tokens[f'--pv-auth-{tone}'], page)
    low = {name: round(contrast(*pair), 2) for name, pair in pairs.items() if contrast(*pair) < 4.5}
    assert not low, low


def test_light_block_redeclares_the_aliases_so_views_do_not_keep_dark_cards():
    # :root の `--pv-surface: var(--pv-bg-surface)` は :root で解決される。body 側で別名を宣言し直さないと、
    # ライトでも暗色カードのままになる（実画面で確認した不具合の再発防止）。
    for alias, token in (('--pv-bg', '--pv-bg-vault'), ('--pv-surface', '--pv-bg-surface'),
                         ('--pv-raised', '--pv-bg-card'), ('--pv-border', '--pv-border-vault'),
                         ('--pv-text', '--pv-text-main'), ('--pv-muted', '--pv-text-muted'),
                         ('--pv-overlay', '--pv-glass-bg')):
        assert LIGHT.get(alias, '').replace(' ', '') == f'var({token})', alias


def test_light_theme_cards_are_bright():
    surface, card = LIGHT['--pv-bg-surface'], LIGHT['--pv-bg-card']
    assert luminance(hex_to_rgb(surface)) > 0.8 and luminance(hex_to_rgb(card)) > 0.75
    assert luminance(hex_to_rgb(LIGHT['--pv-text-main'])) < 0.1


# ---------------------------------------------------------------- 実ブラウザの計算値
def _browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        return pw, pw.chromium.launch(channel='msedge', headless=True)
    except Exception:
        pw.stop()
        pytest.skip('Edge が起動できない')


def test_all_tabs_have_the_same_width_and_keep_icon_and_label_visible():
    pw, browser = _browser()
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        buttons = ''.join(
            f'<button class="q-btn q-btn--flat pv-tab{" pv-tab-active" if i == 0 else ""}">'
            f'<span class="q-btn__content"><i class="q-icon material-icons">{TAB_ICONS[key]}</i>'
            f'<span>{label}</span></span></button>'
            for i, (key, label) in enumerate(shell_module.NAVIGATION))
        page.set_content('<style>.q-btn{display:inline-flex;align-items:center;justify-content:center;border:0}'
                         '.q-btn__content{display:flex;align-items:center;gap:8px;white-space:nowrap}'
                         '.q-icon{width:18px;height:18px;overflow:hidden;font-size:0}</style>'
                         '<style>' + CSS + f'</style><nav class="pv-nav">{buttons}</nav>')
        boxes = page.evaluate('''() => [...document.querySelectorAll('.pv-tab')].map(e => {
            const r = e.getBoundingClientRect(), c = e.querySelector('.q-btn__content').getBoundingClientRect();
            return {w: Math.round(r.width * 10) / 10, h: Math.round(r.height), fits: c.width <= r.width};})''')
        assert len(boxes) == len(TAB_ORDER)
        assert len({b['w'] for b in boxes}) == 1, boxes
        assert all(b['fits'] for b in boxes)
        assert len({b['h'] for b in boxes}) == 1
        # 1280px では 2 段に折り返し（本人の裁定 2026-11-18）、全タブが画面内に見える。バー内スクロールはしない。
        rects = page.evaluate("[...document.querySelectorAll('.pv-tab')].map(e => { const r = e.getBoundingClientRect(); return {top: Math.round(r.top), right: r.right}; })")
        assert len({r['top'] for r in rects}) == 2, rects
        assert all(r['right'] <= 1280 for r in rects), rects
        assert page.evaluate("getComputedStyle(document.querySelector('.pv-nav')).flexWrap") == 'wrap'
        assert page.evaluate("getComputedStyle(document.querySelector('.pv-nav')).overflowX") != 'auto'
        # 1920px では 1 行に収まる
        page.set_viewport_size({'width': 1920, 'height': 1080})
        tops = page.evaluate("[...document.querySelectorAll('.pv-tab')].map(e => Math.round(e.getBoundingClientRect().top))")
        assert len(set(tops)) == 1, tops
    finally:
        browser.close()
        pw.stop()
