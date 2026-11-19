from pixivvault_nicegui.local_tagger import TaggerManager
from pixivvault_nicegui.preferences import PreferencesStore
from test_ak_tagger_20261119 import make_model


def test_verification_does_not_conflict_with_open_settings(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(model))
    before = store.snapshot()
    manager = TaggerManager(store)
    manager.verify()
    assert store.snapshot().revision == before.revision
    store.update({'bookmark_auto_refresh': '0'}, expected_revision=before.revision)
    state = store.read_tagger_verification()
    assert state['receipt']['verified'] is True
    assert state['verified_at']
    manager.close()
    store.close()
