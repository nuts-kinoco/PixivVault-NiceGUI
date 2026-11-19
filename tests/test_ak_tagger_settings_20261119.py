from pixivvault_nicegui.local_tagger import DEFAULT_MODEL
from pixivvault_nicegui.preferences import PreferencesStore


def test_tagger_settings_persist_and_defaults(tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    initial = store.snapshot()
    assert initial.values['local_tagger_path'] == DEFAULT_MODEL
    assert initial.values['local_tagger_enabled'] == '1'
    assert initial.values['local_tagger_receipt'] == ''
    store.update({'local_tagger_path': str(tmp_path), 'local_tagger_enabled': '0',
                  'local_tagger_receipt': '{"identities":[]}'}, expected_revision=initial.revision)
    store.close()
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    assert store.get_setting('local_tagger_path') == str(tmp_path)
    assert store.get_setting('local_tagger_enabled') == '0'
    assert store.get_setting('local_tagger_receipt') == '{"identities":[]}'
    store.close()
