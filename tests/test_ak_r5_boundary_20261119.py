from pathlib import Path
from pixivvault_nicegui import local_tagger
from test_ak_tagger_20261119 import make_model


def test_resolved_remote_model_is_rejected_before_hash(tmp_path, monkeypatch):
    make_model(tmp_path)
    original = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw: Path(r'\\server\share\model') if p == tmp_path / 'model.onnx' else original(p, *a, **kw))
    monkeypatch.setattr(local_tagger, '_sha256', lambda _: (_ for _ in ()).throw(AssertionError('hash')))
    status, receipt = local_tagger.verify_model(tmp_path)
    assert status == '利用不可（ローカルのフォルダを指定）'
    assert receipt is None


def test_manager_publishes_sha_failure(tmp_path):
    from pixivvault_nicegui.preferences import PreferencesStore
    make_model(tmp_path)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(tmp_path))
    manager = local_tagger.TaggerManager(store)
    try:
        manager.verify()
        (tmp_path / 'model.onnx').write_bytes(b'changed')
        assert manager.current_fingerprint() is None
        assert manager.status == 'SHA 不一致'
    finally:
        manager.close()
        store.close()
