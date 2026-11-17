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
