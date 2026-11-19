from threading import Event, Thread
from types import SimpleNamespace
import pytest
from pixivvault_nicegui.local_tagger import LocalTagger, TaggerManager
from pixivvault_nicegui.preferences import PreferencesStore
from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png


@pytest.mark.parametrize('change', ['disabled', 'path', 'digest'])
def test_inflight_result_discarded_if_configuration_changes(tmp_path, change):
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(model))
    started, release = Event(), Event()
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_):
            started.set()
            assert release.wait(10)
            return [[(.9,)]]
    manager = TaggerManager(store)
    manager.tagger = LocalTagger(model, session_factory=lambda _: Session(), tensor_factory=lambda x: x)
    manager._provider = 'auto'
    result = []
    thread = Thread(target=lambda: result.extend(manager.predict(png())))
    try:
        thread.start()
        assert started.wait(10)
        assert manager.try_current_fingerprint() is not None
        if change == 'disabled': store.set_setting('local_tagger_enabled', '0')
        elif change == 'path': store.set_setting('local_tagger_path', str(tmp_path / 'other'))
        else: (model / 'model.onnx').write_bytes(b'changed')
        assert manager.try_current_fingerprint() is None
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive()
    assert result == []
    manager.close()
    store.close()
