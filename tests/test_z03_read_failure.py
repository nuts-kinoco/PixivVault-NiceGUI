"""Z03: real NiceGUI components, synthetic local files, no network."""
import asyncio
from types import SimpleNamespace as NS

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from test_external_vault_ui import setup, upload_event


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,expected', [
    ('missing', '見つかりません'),
    ('permission', '読み取る権限がありません'),
    ('io', '読み取りに失敗しました'),
    ('unexpected', '読み取りに失敗しました'),
    ('invalid-result', '読取結果を確認できません'),
])
async def test_read_failure_is_not_reported_as_oversize(tmp_path, kind, expected):
    services, vault, transport, _ = setup(tmp_path)
    client = Client(ui.page('/_z03_read_' + kind))
    try:
        with client:
            view = ExternalVaultView(services)
            await view.pick_uid_file(upload_event('first.txt', b'7')[0])
            current = (vault.root / 'CURRENT').read_bytes()
            async def read():
                if kind == 'missing':
                    return (tmp_path / 'does-not-exist.txt').read_bytes()
                if kind == 'invalid-result':
                    return None
                raise {'permission': PermissionError, 'io': OSError,
                       'unexpected': RuntimeError}[kind]('private-path-or-content')
            await view.pick_uid_file(NS(file=NS(name='selected.txt', read=read)))
            assert expected in view.notice.text
            assert '65536' not in view.notice.text
            assert 'private-path-or-content' not in view.notice.text
            assert '再' in view.notice.text or '選び直' in view.notice.text
            assert view._picked_uids == []
            assert view.picked_count.text == '選択済み: 0 件'
            assert (vault.root / 'CURRENT').read_bytes() == current
            assert transport.calls == []
            await view.action('update')
            assert 'no-changes' in view.notice.text
            assert (vault.root / 'CURRENT').read_bytes() == current
            view.close()
    finally:
        client.delete(); vault.close(); services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('size,accepted', [(65536, True), (65537, False)])
async def test_size_boundary_stays_distinct_from_read_failure(tmp_path, size, accepted):
    services, vault, transport, _ = setup(tmp_path)
    client = Client(ui.page('/_z03_size_' + str(size)))
    try:
        with client:
            view = ExternalVaultView(services)
            current = (vault.root / 'CURRENT').read_bytes()
            await view.pick_uid_file(upload_event('selected.txt', b'7\n#' + b'x' * (size - 3))[0])
            assert bool(view._picked_uids) is accepted
            assert ('65536' in view.notice.text) is not accepted
            assert (vault.root / 'CURRENT').read_bytes() == current
            assert transport.calls == []
            view.close()
    finally:
        client.delete(); vault.close(); services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['replace', 'lock', 'close'])
async def test_late_read_failure_cannot_overwrite_newer_state(tmp_path, change):
    services, vault, transport, _ = setup(tmp_path)
    client = Client(ui.page('/_z03_late_' + change))
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        with client:
            view = ExternalVaultView(services)
            async def read():
                entered.set()
                await release.wait()
                raise PermissionError('private-path-or-content')
            task = asyncio.create_task(view.pick_uid_file(NS(file=NS(name='selected.txt', read=read))))
            await entered.wait()
            if change == 'replace':
                await view.pick_uid_file(upload_event('new.txt', b'8')[0])
            elif change == 'lock':
                view.lock()
            else:
                view.close()
            expected = view.notice.text
            release.set(); await task
            assert view.notice.text == expected
            assert view._picked_uids == ([8] if change == 'replace' else [])
            assert transport.calls == []
            view.close()
    finally:
        release.set()
        if task is not None:
            await task
        client.delete(); vault.close(); services.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reasons,expected', [
    (['max-file-size'], '65536'),
    (['accept'], 'txt ファイルを選択'),
    ([], '選択できませんでした'),
])
async def test_upload_rejection_shows_only_confirmed_reason(tmp_path, reasons, expected):
    services, vault, transport, _ = setup(tmp_path)
    client = Client(ui.page('/_z03_reject_' + str(len(reasons))))
    try:
        with client:
            view = ExternalVaultView(services)
            await view.pick_uid_file(upload_event('first.txt', b'7')[0])
            current = (vault.root / 'CURRENT').read_bytes()
            view.pick_uid_rejected(NS(args=reasons))
            assert expected in view.notice.text
            assert ('65536' in view.notice.text) == ('max-file-size' in reasons)
            assert view._picked_uids == []
            assert (vault.root / 'CURRENT').read_bytes() == current
            assert transport.calls == []
            # QUploader sends only failure categories, never file values.
            listener = [e for e in view.uid_upload._event_listeners.values() if e.type == 'rejected']
            assert len(listener) == 1 and 'failedProp' in listener[0].js_handler
            view.close()
    finally:
        client.delete(); vault.close(); services.close()
