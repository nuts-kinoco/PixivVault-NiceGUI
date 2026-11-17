"""FL-A02/A03: --tab <key> と、起動直後のタブ＝ダウンロード（元 gui.py:98,128 / main.py:333-342）。"""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.startup_tab import (STARTUP_TAB_ORDER, TAB_INITIAL,
                                            resolve_startup_tab, shell_tab_key)
from pixivvault_nicegui.ui.shell import DemoShell


def test_original_keys_and_default():
    # 元 gui.py TAB_ORDER の並び（unified/discovery は元のキー名）
    assert STARTUP_TAB_ORDER == ('unified', 'queue', 'failed', 'history', 'discovery',
                                 'dedup', 'ranking', 'library', 'recommend')
    assert TAB_INITIAL == 'unified'


def test_resolve_matches_original_contract():
    assert resolve_startup_tab(None) == ('unified', None)
    assert resolve_startup_tab('ranking') == ('ranking', None)
    key, warning = resolve_startup_tab('nope')
    assert key == 'unified'
    assert warning == ("不明なタブ 'nope'。有効: unified・queue・failed・history・discovery・"
                       "dedup・ranking・library・recommend。既定で開きます")


def test_original_key_names_map_to_shell_tabs():
    assert shell_tab_key('unified') == 'download'
    assert shell_tab_key('discovery') == 'learning'
    assert shell_tab_key('ranking') == 'ranking'


def test_parse_args_accepts_tab_without_validating_early():
    assert parse_args([]).startup_tab is None
    assert parse_args(['--tab', 'queue']).startup_tab == 'queue'
    assert parse_args(['--tab', 'bogus']).startup_tab == 'bogus'     # 元と同じ: 不正値は警告して既定へ


@pytest.mark.asyncio
async def test_shell_opens_download_first_without_activating_ranking():
    services = ApplicationServices(120)
    client = Client(ui.page('/_h02_default'))
    try:
        with client:
            shell = DemoShell(services)
            assert shell.active_tab == 'download'
            assert shell.buttons['download'].props.get('aria-current') == 'page'
            assert 'aria-current' not in shell.buttons['ranking'].props
            assert shell.gallery.root.visible is False
            assert 'download' in shell.job_views
            shell.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_shell_honours_startup_tab_and_falls_back_when_unavailable():
    services = ApplicationServices(120)
    client = Client(ui.page('/_h02_tab'))
    try:
        services.startup_tab = 'queue'
        with client:
            shell = DemoShell(services)
            assert shell.active_tab == 'queue'
            shell.close()
        services.startup_tab = 'dedup'      # この起動では使えない → 既定へ
        with client:
            shell = DemoShell(services)
            assert shell.active_tab == 'download'
            shell.close()
        services.startup_tab = 'ranking'
        with client:
            shell = DemoShell(services)
            assert shell.active_tab == 'ranking' and shell.gallery.root.visible
            shell.close()
    finally:
        client.delete()
        services.close()


def test_uid_file_is_reflected_once_per_process_not_per_shell():
    # 元 gui.py:859: 起動時に1回。シェル（画面）を作るたびに読み直さない。
    from types import SimpleNamespace
    calls = []
    dialog = SimpleNamespace(reflect_uid_file_at_startup=lambda: calls.append(1))
    services = SimpleNamespace()
    for _ in range(3):
        DemoShell._reflect_uid_once(SimpleNamespace(services=services), dialog)
    assert calls == [1]
