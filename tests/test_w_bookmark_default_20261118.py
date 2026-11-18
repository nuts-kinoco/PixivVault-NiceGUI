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
        assert ApplicationServices.bookmark_layout(service) == dict(hierarchical=True, hardlink=False, editable=True)
        prefs.set_setting('bookmark_download_mode', 'link')
        assert prefs.get_setting('bookmark_download_mode') == 'link'
        assert ApplicationServices.bookmark_layout(service)['hardlink'] is True
    finally:
        prefs.close()
    prefs = PreferencesStore(path)
    try:
        assert prefs.get_setting('bookmark_download_mode') == 'link'
    finally:
        prefs.close()


def test_layout_fallback_when_preference_provider_has_no_mode():
    prefs = SimpleNamespace(get_setting=lambda key, default=None: default)
    assert ApplicationServices.bookmark_layout(SimpleNamespace(preferences=prefs))['hardlink'] is False
