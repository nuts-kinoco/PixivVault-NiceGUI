from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.legacy_backup_history import author_storage_target
from pixivvault_nicegui.ui.theme import CSS
from pixivvault_nicegui.ui.viewer_backup_view import ViewerBackupView


def walk(node):
    yield node
    for child in node.default_slot.children:
        yield from walk(child)


@pytest.mark.asyncio
async def test_legacy_tile_is_square_hover_and_keyboard_accessible():
    row = SimpleNamespace(author_id='42', author_name='作者', thumbnail='/image', title='題', last_backup='2026')
    pin = SimpleNamespace(close=lambda: None)
    async def rows():
        return (row,), 0, pin
    services = SimpleNamespace(legacy_backup_history_available=True, desktop=object(), recent_legacy_backup_authors=rows)
    client = Client(ui.page('/_ac_backup'))
    try:
        with client:
            view = ViewerBackupView(services)
            view.active = True
            await view.refresh()
        opener = view.openers['42']
        assert opener.props['title'] == '作者 (42)\nクリックして開く'
        assert opener.props['tabindex'] == '0'
        assert {'click', 'keydown.enter', 'keydown.space'} <= {e.type for e in opener._event_listeners.values()}
        assert any('pv-backup-name' in e.classes and e.text == '作者' for e in walk(opener))
        assert not any(getattr(e, 'text', '') in ('題', '2026') for e in walk(opener))
        assert '.pv-backup-tile:hover .pv-backup-name' in CSS
        assert '.pv-backup-tile:focus-visible .pv-backup-name' in CSS
        assert 'aspect-ratio:1 / 1' in CSS
        view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_legacy_history_failure_reports_original_message():
    async def fail():
        raise ValueError('synthetic-private-detail')
    services = SimpleNamespace(legacy_backup_history_available=True, recent_legacy_backup_authors=fail)
    client = Client(ui.page('/_ac_backup_error'))
    try:
        with client:
            view = ViewerBackupView(services)
            view.active = True
            await view.refresh()
        assert view.status.text == '直近バックアップ履歴を確認できません。'
        assert 'synthetic-private-detail' not in view.status.text
        view.close()
    finally:
        client.delete()


def test_zip_opens_author_folder_if_present_otherwise_zip(tmp_path):
    archive = tmp_path / '作者(42).zip'
    archive.write_bytes(b'zip')
    assert author_storage_target(tmp_path, '42', archive) == archive
    folder = tmp_path / '作者(42)'
    folder.mkdir()
    assert author_storage_target(tmp_path, '42', archive) == folder

@pytest.mark.asyncio
async def test_managed_backup_uses_author_tile_and_opens_manifest():
    row = {'author_id': '42', 'author_name': 'Author', 'thumbnail': '/image',
           'title': 'Title', 'saved_at': '2026', 'manifest': 'manifest'}
    opened = []
    async def rows():
        return (row,), ()
    async def open_saved(manifest):
        opened.append(manifest)
    services = SimpleNamespace(desktop=object(), recent_viewer_backups=rows, open_viewer_backup=open_saved)
    client = Client(ui.page('/_ac_managed_backup'))
    try:
        with client:
            view = ViewerBackupView(services)
            view.active = True
            await view.refresh()
            opener = view.openers['42']
            assert 'pv-backup-tile' in opener.classes
            assert opener.props['title'] == 'Author (42)\n\u30af\u30ea\u30c3\u30af\u3057\u3066\u958b\u304f'
            assert any('pv-backup-name' in e.classes and e.text == 'Author' for e in walk(opener))
            handler = next(e.handler for e in opener._event_listeners.values() if e.type == 'click')
            await handler(None)
            assert opened == ['manifest']
            view.close()
    finally:
        client.delete()


def test_backup_name_bar_matches_original_transparency_and_line_limit():
    assert 'background:rgba(0,0,0,.6)' in CSS
    assert '-webkit-line-clamp:2' in CSS[CSS.index('.pv-backup-name'):CSS.index('.pv-backup-tile:hover')]


def test_reparse_adjacent_folder_does_not_hide_zip(tmp_path, monkeypatch):
    archive = tmp_path / 'author(42).zip'
    archive.write_bytes(b'zip')
    folder = tmp_path / 'author(42)'
    folder.mkdir()
    original_lstat = Path.lstat
    def lstat(path):
        stat = original_lstat(path)
        if path == folder:
            return SimpleNamespace(st_mode=stat.st_mode, st_file_attributes=0x400)
        return stat
    monkeypatch.setattr(Path, 'lstat', lstat)
    assert author_storage_target(tmp_path, '42', archive) == archive
