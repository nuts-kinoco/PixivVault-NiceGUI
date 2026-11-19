from pixivvault_nicegui.services import ApplicationServices


def test_app_owns_tagger_until_discovery_shutdown(tmp_path):
    services = ApplicationServices(preferences_root=tmp_path / 'preferences')
    manager = services.local_tagger
    assert manager is not None
    services.close()
    assert manager._closed
