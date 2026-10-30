"""S03 settings wiring uses the running service, without automatic Pixiv GET."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.shell import DemoShell
from test_following_sync_ui import Reader, author, runtime


class OwnedReader(Reader):
    def close(self):
        self._closed = True


@pytest.mark.asyncio
async def test_settings_reopen_observes_same_local_sync_without_background_get(tmp_path, monkeypatch):
    runtime(monkeypatch)
    reader = OwnedReader(public=(author(),))
    services = ApplicationServices(live_client=reader,
        preferences_root=tmp_path / 'preferences', following_sync_root=tmp_path / 'following')
    client = Client(ui.page('/_following_settings'))
    try:
        with client:
            shell = DemoShell(services)
            shell.open_preferences()
            view = shell.following_view
            await view._initial_task
            assert view._active and view.snapshot.last_synced_at is None
            assert reader.calls == [] and reader.auth_calls == 0
            assert await view.sync_now()
            saved = view.snapshot
            calls = (tuple(reader.calls), reader.auth_calls)
            shell.preferences_view.close_dialog()
            assert not view._active and not view._timer.active
            assert not await view.sync_now()
            shell.open_preferences()
            assert shell.following_view is view
            await view._initial_task
            assert view.snapshot == saved
            assert (tuple(reader.calls), reader.auth_calls) == calls
            roots = [node for node in client.elements.values()
                     if node.props.get('data-testid') == 'following-sync-view']
            assert roots == [view.root]
        client.delete()
        assert view._closed and not view._timer.active
        assert not services.following_sync._closed and not reader._closed
        assert services.following_sync.snapshot('9001') == saved
    finally:
        if client.id in Client.instances:
            client.delete()
        services.close()


@pytest.mark.asyncio
async def test_settings_explains_missing_sync_root_without_authenticating(tmp_path, monkeypatch):
    runtime(monkeypatch)
    reader = OwnedReader()
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences')
    client = Client(ui.page('/_following_unconfigured'))
    try:
        with client:
            shell = DemoShell(services)
            shell.open_preferences()
            view = shell.following_view
            await view._initial_task
            assert '保存先が未設定' in view.summary.text
            assert not view.sync_button.enabled and not await view.sync_now()
            assert reader.calls == [] and reader.auth_calls == 0
    finally:
        client.delete()
        services.close()
