from types import SimpleNamespace

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.services import ApplicationServices


def test_missing_saved_mode_uses_existing_direct_layout_and_explicit_link_survives(tmp_path):
    path = tmp_path / 'prefs.sqlite'
    prefs = PreferencesStore(path)
    prefs.set_setting('use_work_folder', '1')
    prefs.close()
    prefs = PreferencesStore(path)  # existing user with no stored bookmark mode
    try:
        service = SimpleNamespace(preferences=prefs)
        assert prefs.get_setting('bookmark_download_mode') == 'direct'
        assert ApplicationServices.bookmark_layout(service) == dict(hierarchical=False, work_folder=True, editable=True)
        prefs.set_setting('bookmark_download_mode', 'link')
        assert prefs.get_setting('bookmark_download_mode') == 'link'
        assert ApplicationServices.bookmark_layout(service)['hierarchical'] is True
    finally:
        prefs.close()
    prefs = PreferencesStore(path)
    try:
        assert prefs.get_setting('bookmark_download_mode') == 'link'
    finally:
        prefs.close()


def test_layout_fallback_when_preference_provider_has_no_mode():
    prefs = SimpleNamespace(get_setting=lambda key, default=None: default)
    assert ApplicationServices.bookmark_layout(SimpleNamespace(preferences=prefs))['hierarchical'] is False


def test_layout_buttons_reflect_and_change_the_actual_projection_mode(tmp_path):
    prefs = PreferencesStore(tmp_path / 'prefs.sqlite')
    try:
        service = SimpleNamespace(preferences=prefs)
        prefs.set_setting('use_work_folder', '1')
        layout = ApplicationServices.bookmark_layout(service)
        assert layout['hierarchical'] is False and layout['work_folder'] is True
        ApplicationServices.set_bookmark_layout(service, hierarchical=True)
        assert prefs.get_setting('bookmark_download_mode') == 'link'
        assert prefs.get_setting('use_work_folder') == '1'
        ApplicationServices.set_bookmark_layout(service, work_folder=False)
        assert prefs.get_setting('bookmark_download_mode') == 'link'
        assert prefs.get_setting('use_work_folder') == '0'
    finally:
        prefs.close()
