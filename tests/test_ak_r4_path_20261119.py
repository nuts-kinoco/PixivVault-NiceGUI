from types import SimpleNamespace
import pytest
from pixivvault_nicegui import local_tagger
from pixivvault_nicegui.preferences import PreferencesStore
from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png


@pytest.mark.parametrize('style', ['forward', 'case', 'extended'])
def test_equivalent_path_uses_same_session_and_fingerprint(tmp_path, monkeypatch, style):
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(model))
    created, calls = [], []
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): calls.append(1); return [[(.9,)]]
    original = local_tagger.LocalTagger
    class Injected(original):
        def __init__(self, path, **kwargs):
            created.append(path)
            super().__init__(path, session_factory=lambda _: Session(), tensor_factory=lambda x: x, **kwargs)
    monkeypatch.setattr(local_tagger, 'LocalTagger', Injected)
    manager = local_tagger.TaggerManager(store)
    try:
        initial = manager.predict_with_fingerprint(png())
        path = {'forward': model.as_posix(), 'case': str(model).swapcase(),
                'extended': '\\\\?\\' + str(model)}[style]
        store.set_setting('local_tagger_path', path)
        assert manager.predict_with_fingerprint(png()) == initial
        assert manager.try_current_fingerprint() == initial[0]
        assert len(created) == len(calls) == 1
    finally:
        manager.close()
        store.close()


def test_extended_unc_is_rejected_before_access(monkeypatch):
    monkeypatch.setattr(local_tagger, '_identity', lambda *_: (_ for _ in ()).throw(AssertionError('access')))
    status, receipt = local_tagger.verify_model(r'\\?\UNC\server\share\model')
    assert status == '利用不可（ローカルのフォルダを指定）'
    assert receipt is None
