"""FL-B11: DB状態バナー（元 gui.py:470-483 / main.py:357-364）。"""
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
import os

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.db_status import db_status
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell


def fake(**kw):
    base = dict(pixiv_download_root=None, library_readonly_db=None, download_sandbox=None, learning_sandbox=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_live_managed_storage_is_the_production_equivalent():
    root = Path('S:/data/downloads')
    prod, text = db_status(fake(pixiv_download_root=root))
    assert prod is True
    assert text == f"⚠️⚠️ 本番DBを使用中: {os.path.abspath(root)}"      # 元の文言


def test_other_modes_show_plain_path():
    db = Path('S:/copy/pixiv_vault.db')
    prod, text = db_status(fake(library_readonly_db=db))
    assert prod is False and text == f"DB: {os.path.abspath(db)}"
    prod, text = db_status(fake(download_sandbox=Path('S:/sb')))
    assert prod is False and text == f"DB: {os.path.abspath(Path('S:/sb'))}"
    prod, text = db_status(fake())
    assert prod is False and text.startswith('DB: ')


@pytest.mark.asyncio
async def test_shell_shows_banner_above_header_grey_for_demo_red_for_live(tmp_path):
    services = ApplicationServices(120)
    client = Client(ui.page('/_h03'))
    try:
        with client:
            shell = DemoShell(services)
            banner = shell.db_banner
            assert banner.text.startswith('DB: ')
            assert 'data-testid' in banner.props and banner.props['data-testid'] == 'db-status-banner'
            assert 'background-color' not in banner._style
            # 位置: 最上段（ヘッダーより前）
            assert shell.root.default_slot.children[0] is banner
            shell.close()
        services.pixiv_download_root = tmp_path / ('long-folder-' * 5) / 'downloads'
        with client:
            shell = DemoShell(services)
            banner = shell.db_banner
            path = os.path.abspath(services.pixiv_download_root)
            compact = banner.default_slot.children[0]
            summary = compact.default_slot.children[0]
            assert summary.text == f"本番DBを使用中：{PureWindowsPath(path).anchor}…\\downloads"
            assert summary.props['role'] == 'status'
            entry = next(n for n in banner.descendants() if n.props.get('data-testid') == 'db-warning-details')
            dialog = next(n for n in client.layout.descendants()
                          if n.props.get('data-testid') == 'db-warning-dialog'
                          and n.props.get('data-pv-focus-origin') == entry.html_id)
            assert any(getattr(n, 'text', None) == f"⚠️⚠️ 本番DBを使用中: {path}" for n in dialog.descendants())
            assert 'pv-db-production' in banner.classes
            assert shell.root.default_slot.children[0] is banner
            shell.close()
    finally:
        client.delete()
        services.close()
