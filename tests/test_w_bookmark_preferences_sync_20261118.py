from types import MethodType, SimpleNamespace

import pytest

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.ui.shell import DemoShell
from test_w_dl_screen_20261118 import finish, make


@pytest.mark.asyncio
async def test_settings_save_refreshes_existing_download_without_extra_writes(tmp_path):
    services, client, view = make(tmp_path)
    prefs = PreferencesStore(tmp_path / 'settings.sqlite')
    services.preferences = prefs
    services.bookmark_layout = MethodType(ApplicationServices.bookmark_layout, services)
    services.set_bookmark_layout = MethodType(ApplicationServices.set_bookmark_layout, services)
    services.apply_preferences = lambda snapshot: None
    shell = SimpleNamespace(services=services, learning=None, job_views={'download': view})
    try:
        view.set_mode('bookmark')
        with client:
            dialog = PreferencesView(prefs, enabled_keys=('bookmark_download_mode', 'use_work_folder'),
                                     on_change=lambda snap: DemoShell.apply_preferences(shell, snap))
            dialog.open()
        dialog.controls['bookmark_download_mode'].set_value('link')
        dialog.controls['use_work_folder'].set_value('1')
        assert dialog.save()
        assert view.bm_buttons['hier'].props['data-selected'] == 'true'
        assert view.bm_work_folder_box.value is True
        assert '階層化' in view.band_summary.text
        assert dialog.snapshot.revision == prefs.snapshot().revision == 1
        dialog.controls['use_work_folder'].set_value('0')
        assert dialog.save()
        assert prefs.snapshot().revision == dialog.snapshot.revision == 2
        assert view.bm_work_folder_box.value is False
        view.bm_work_folder_box.set_value(True)
        assert prefs.snapshot().revision == 3
        assert prefs.get_setting('bookmark_download_mode') == 'link'
        prefs.set_setting('bookmark_download_mode', 'direct')
        rev = prefs.snapshot().revision
        view.set_mode('followed')
        view.set_mode('bookmark')
        assert view.bm_buttons['flat'].props['data-selected'] == 'true'
        assert prefs.snapshot().revision == rev
    finally:
        prefs.close()
        finish(services, client)
