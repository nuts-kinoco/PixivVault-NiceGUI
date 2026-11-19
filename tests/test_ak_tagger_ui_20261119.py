from nicegui import Client, ui
import asyncio
import pytest
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView


@pytest.mark.asyncio
async def test_settings_tagger_path_enable_and_status(tmp_path):
    class Manager:
        status = '未検出'
        def verify(self):
            self.status = 'onnxruntime なし'
            return self.status
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    client = Client(ui.page('/ak-tagger-settings'))
    try:
        with client:
            view = PreferencesView(store, tagger=Manager())
            view.open()
            assert view.tagger_path.value
            view.tagger_path.set_value(str(tmp_path))
            view.tagger_enabled.set_value(False)
            assert view.save()
            assert store.get_setting('local_tagger_path') == str(tmp_path)
            assert store.get_setting('local_tagger_enabled') == '0'
            await view._verify_tagger(view._epoch)
            assert view.tagger_status.text == 'onnxruntime なし'
            view.close()
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
async def test_open_dialog_tracks_background_status_change(tmp_path):
    class Manager:
        status = '利用可能'
        def verify(self): return self.status
    manager = Manager()
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    client = Client(ui.page('/ak-tagger-live-status'))
    try:
        with client:
            view = PreferencesView(store, tagger=manager)
            view.open()
            await view._verify_tagger(view._epoch)
            assert view.tagger_status.text == '利用可能'
            manager.status = '読込失敗'
            await asyncio.sleep(.25)
            assert view.tagger_status.text == '読込失敗'
            view.close()
    finally:
        client.delete()
        store.close()
