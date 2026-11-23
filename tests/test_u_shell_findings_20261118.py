"""checker 指摘（コミット 4e4ec38, 4bc14bb の共通シェル）の修正: R1〜R4, A1, P。"""
import asyncio

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell


def walk(element):
    yield element
    for child in element.default_slot.children:
        yield from walk(child)


@pytest.fixture
def shell_env(tmp_path, monkeypatch):
    services = ApplicationServices(2, learning_sandbox=tmp_path / 'learning')
    client = Client(ui.page('/_u_shell'))
    with client:
        shell = DemoShell(services)
    try:
        yield services, client, shell
    finally:
        with client:
            shell.close()
        client.delete()
        services.close()


# ---------------------------------------------------------------- R1
async def test_view_history_button_opens_the_dialog_without_opening_the_learning_tab(shell_env, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, client, shell = shell_env
    before = shell.active_tab
    with client:
        await shell.open_view_history()
    assert shell.active_tab == before
    assert shell.learning is None
    assert shell.view_history is not None and shell.view_history.dialog.value is True


async def test_view_history_dialog_in_the_header_lists_rows_and_clears_without_learning(shell_env, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, client, shell = shell_env
    await services.learning.open_session()
    await services.learning.record_view('demo-0001')
    with client:
        await shell.open_view_history()
        assert len(shell.view_history.rows) == 1
    assert shell.learning is None


# ---------------------------------------------------------------- R2
async def test_view_history_count_is_reflected_at_startup(shell_env, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, client, shell = shell_env
    await services.learning.open_session()
    await services.learning.record_view('demo-0001')
    await services.learning.record_view('demo-0002')
    # 起動時に一度だけ件数を読む timer がシェル側に張られている（学習タブは開かない）。
    assert shell.view_history.initial.callback.__func__ is type(shell.view_history).refresh
    with client:
        await shell.view_history.refresh()
    assert shell.view_history_badge.text == '2'
    assert shell.view_history_badge.visible is True
    assert shell.view_history_tooltip.text == '閲覧履歴（学習タブ・2件）'
    assert shell.learning is None


def test_view_history_tooltip_default_matches_the_original(shell_env):
    _, _, shell = shell_env
    assert shell.view_history_tooltip.text == '閲覧履歴（学習タブ）'


# ---------------------------------------------------------------- R3
def test_borrow_toggle_keeps_the_fullwidth_paren_aria_label(shell_env):
    _, _, shell = shell_env
    assert shell.borrow_switch.props.get('aria-label') == '借用プレミアム（API切り替え）'
    assert shell.borrow_switch.props.get('data-testid') == 'premium-borrow-toggle'


# ---------------------------------------------------------------- R4
def test_disabled_history_button_explains_why(shell_env):
    _, _, shell = shell_env
    button = shell.buttons['history']
    assert not button.enabled
    texts = [getattr(e, 'text', '') for e in walk(button)]
    assert 'この起動設定では利用できません' in texts


# ---------------------------------------------------------------- CSS（R4 / A1 / P）
import re

from pixivvault_nicegui.ui.theme import CSS
from test_q_shell_20261117 import DARK, LIGHT, contrast


def rule(selector):
    match = re.search(r'(?:^|\n)' + re.escape(selector) + r'\s*\{([^}]*)\}', CSS)
    assert match, selector
    return match.group(1).replace(' ', '')


def test_selected_tab_ground_is_the_measured_original_color():
    assert DARK['--pv-tab-selected'].lower() == '#2d3844'
    assert LIGHT['--pv-tab-selected'].lower() == '#d3dde9'
    assert 'background:var(--pv-tab-selected)' in rule('.pv-nav .q-btn.pv-tab.pv-tab-active')
    for tokens in (DARK, {**DARK, **LIGHT}):
        assert contrast(tokens['--pv-primary'], tokens['--pv-tab-selected']) >= 4.5


def test_tab_icon_is_drawn_small_like_the_original():
    # 元の実測 12x13（18px 相当の描画）。製品は 15x17 だった。
    assert 'font-size:18px' in rule('.pv-tab .q-icon')


def test_cookie_banner_icon_is_the_original_size():
    assert 'font-size:24px' in rule('.pv-banner-icon')


def test_api_switch_track_is_the_original_43x26():
    inner = rule('.pv-conn-row .q-toggle__inner')
    assert 'width:43px' in inner and 'height:26px' in inner


def test_banner_and_tabs_sit_where_the_original_does():
    # 元: Cookie 帯の枠 y=174、タブ y=269（製品は 170 / 265.1）。差 3.9px を接続行と帯の上余白で埋める。
    assert 'margin-top:9.4px' in rule('.pv-conn-row')
    assert 'margin-top:2.5px' in rule('.pv-cookie-banner')


def test_keyboard_focus_is_clearly_visible_on_tabs_and_header_buttons():
    match = re.search(r'\.pv-tab:focus-visible,\s*\.pv-hbtn:focus-visible\s*\{([^}]*)\}', CSS)
    assert match
    body = match.group(1).replace(' ', '')
    assert 'outline:2pxsolidvar(--pv-primary)' in body and 'outline-offset:2px' in body
    # .no-outline（outline:0 !important）に消されるボタンのため、box-shadow の輪も要る。
    assert 'box-shadow:0002pxvar(--pv-page),0004pxvar(--pv-primary)' in body
    for tokens in (DARK, {**DARK, **LIGHT}):
        assert contrast(tokens['--pv-primary'], tokens['--pv-page']) >= 3


def test_light_fills_keep_text_at_4_5_and_primary_is_darkened_in_light_only():
    assert DARK['--pv-fill'].lower() == '#0096fa'
    light = {**DARK, **LIGHT}
    assert contrast(light['--pv-on-fill'], light['--pv-fill']) >= 4.5
    assert contrast(light['--pv-fill'], light['--pv-page']) >= 4.5   # 主色の文字ボタン（flat）
    assert contrast(light['--pv-fill'], light['--pv-bg-surface']) >= 4.5
    block = re.search(r'body\.body--light\s*\{[^}]*\}', CSS).group(0)
    assert re.search(r'--q-primary:\s*var\(--pv-fill\)\s*!important', block)
    assert '--q-primary' not in re.search(r':root\s*\{[^}]*\}', CSS).group(0)


def test_selected_size_and_mode_buttons_have_visible_text_everywhere():
    for selector in ('.pv-toolbar .q-btn.pv-size-selected',):
        body = rule(selector)
        assert 'background:var(--pv-fill)' in body and 'color:var(--pv-on-fill)' in body
    # ライブラリは元のダークの文字色（#e8e8ef）を保ち、ライトだけ白にする。
    lib = re.search(r'\.pv-library \.q-btn\.pv-size-selected[^{]*\{([^}]*)\}', CSS).group(1).replace(' ', '')
    assert 'color:var(--pv-on-fill-lib)!important' in lib and 'background:var(--pv-fill)' in lib
    assert DARK['--pv-on-fill-lib'].lower() == '#e8e8ef'
    assert contrast({**DARK, **LIGHT}['--pv-on-fill-lib'], {**DARK, **LIGHT}['--pv-fill']) >= 4.5
    assert 'background:var(--pv-fill)' in rule('.pv-learning .q-btn.pv-judgment-like')


def test_failed_list_colors_come_from_theme_tokens():
    from pixivvault_nicegui.ui import failed_records_view as view
    source = open(view.__file__, encoding='utf-8').read()
    css = re.search(r'\.pv-failure-chips.*?"""', source, re.S).group(0)
    assert re.search(r'\.pv-failure-reason\s*\{[^}]*color:var\(--pv-muted\)', css)
    assert re.search(r'\.pv-failure-meta\s*\{[^}]*color:var\(--pv-text\)', css)
    assert re.search(r'aria-pressed=true\]\s*\{[^}]*background:var\(--pv-fill\)[^}]*color:var\(--pv-action-text\)', css)


def test_duplicate_selected_buttons_do_not_carry_the_primary_text_class():
    # Quasar の `.text-primary`（レイヤ内の !important）は、こちらの !important の文字色に勝つ。
    # 選択中の地は主色の塗りなので、主色の文字クラスを持つと文字と地が同色になる（実画面で 1:1 を確認した不具合）。
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    client = Client(ui.page('/_u_dup'))
    try:
        with client:
            view = DuplicateView(type('S', (), {'undecidable_available': False})())
        buttons = [*view.mode_buttons.values(), *view.size_buttons.values()]
        assert buttons
        for button in buttons:
            assert 'text-primary' not in button.classes and button.props.get('color') is None, button.props
            assert ('pv-size-selected' in button.classes) != ('pv-size-button' in button.classes)
        with client:
            view.close()
    finally:
        client.delete()


def test_focus_ring_survives_quasar_no_outline_in_a_real_browser():
    from test_q_shell_20261117 import _browser
    pw, browser = _browser()
    try:
        page = browser.new_page(viewport={'width': 800, 'height': 600})
        # Quasar のレイヤ内 `.no-outline { outline:0 !important }` は、こちらの outline に勝つ。
        page.set_content('<style>@layer quasar{.no-outline{outline:0 !important}}</style><style>' + CSS +
                         '</style><body class="body--light"><button class="q-btn no-outline pv-tab" id="a">x</button></body>')
        page.keyboard.press('Tab')
        shadow = page.evaluate("getComputedStyle(document.getElementById('a')).boxShadow")
        assert page.evaluate("document.activeElement.id") == 'a'
        assert '42, 88, 134' in shadow and '4px' in shadow, shadow
    finally:
        browser.close()
        pw.stop()


async def test_opening_a_work_from_the_header_history_closes_the_dialog_so_the_viewer_is_visible(shell_env, monkeypatch):
    # ダイアログ（Quasar のオーバーレイ）はビューアーより前面に出るので、作品を開くときは閉じる。
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, client, shell = shell_env
    await services.learning.open_session()
    await services.learning.record_view('demo-0001')
    with client:
        await shell.open_view_history()
        assert shell.view_history.dialog.value is True
        await shell.view_history.open_work('demo-0001')
        assert shell.view_history.viewer.selection is not None
        assert shell.view_history.dialog.value is False
    assert shell.active_tab == 'download' and shell.learning is None
    with client:
        shell.show('ranking')
        assert shell.view_history.viewer.selection is None  # 別タブへ持ち越さない


def test_no_hardcoded_muted_text_colors_in_views():
    import pathlib
    ui_dir = pathlib.Path(__import__('pixivvault_nicegui.ui.shell', fromlist=['x']).__file__).parent
    bad = [p.name for p in ui_dir.glob('*.py') if p.name != 'theme.py'
           and re.search(r'color:\s*#8a8a99|color:\{TOKENS\["muted"\]\}', p.read_text(encoding='utf-8'))]
    assert not bad, bad
