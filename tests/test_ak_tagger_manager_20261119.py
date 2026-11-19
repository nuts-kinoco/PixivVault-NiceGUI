import json
from pixivvault_nicegui.local_tagger import TaggerManager
from pixivvault_nicegui.preferences import PreferencesStore
from test_ak_tagger_20261119 import make_model


def test_manager_persists_verification_and_respects_setting(tmp_path):
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    prefs = PreferencesStore(tmp_path / 'preferences.sqlite')
    prefs.set_setting('local_tagger_path', str(model))
    manager = TaggerManager(prefs)
    assert manager.verify() in ('利用可能', 'onnxruntime なし')
    assert json.loads(prefs.get_setting('local_tagger_receipt'))['verified'] is True
    prefs.set_setting('local_tagger_enabled', '0')
    assert manager.predict(b'not-an-image') == []
    manager.close()
    prefs.close()
