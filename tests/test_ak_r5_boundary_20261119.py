from pathlib import Path
from types import SimpleNamespace
import asyncio
import pytest
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


def test_missing_model_updates_public_status_on_nonblocking_check(tmp_path):
    from pixivvault_nicegui.preferences import PreferencesStore
    make_model(tmp_path)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(tmp_path))
    manager = local_tagger.TaggerManager(store)
    try:
        manager.verify()
        (tmp_path / 'model.onnx').unlink()
        assert manager.try_current_fingerprint() is None
        assert manager.status == '未検出'
    finally:
        manager.close()
        store.close()


@pytest.mark.asyncio
async def test_open_settings_follows_sha_failure_from_view_scan(tmp_path, monkeypatch):
    from nicegui import Client, ui
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    from test_ak_tagger_inference_20261119 import png
    from z_learning_support_20261118 import FakeReader
    folder = tmp_path / 'model'
    folder.mkdir()
    make_model(folder)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(folder))
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]
    original = local_tagger.LocalTagger
    class Injected(original):
        def __init__(self, path, **kwargs):
            super().__init__(path, session_factory=lambda _: Session(), tensor_factory=lambda x: x, **kwargs)
    monkeypatch.setattr(local_tagger, 'LocalTagger', Injected)
    manager = local_tagger.TaggerManager(store)
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=manager,
        local_image=lambda _: png(), saved_works=lambda: (SimpleNamespace(work_id='1', tags=()),), saved_ids=lambda: ('1',))
    client = Client(ui.page('/ak-r5-sha-status'))
    try:
        with client:
            view = PreferencesView(store, tagger=manager)
            view.open()
            await view._verify_tagger(view._epoch)
            assert view.tagger_status.text == '利用可能'
            await asyncio.wrap_future(service.refresh_saved_seeds())
            assert service.local_tag_view.current_tags('1') == ('cat',)
            (folder / 'model.onnx').write_bytes(b'changed')
            await asyncio.wrap_future(service.refresh_saved_seeds())
            await asyncio.sleep(.25)
            assert manager.status == 'SHA 不一致'
            assert view.tagger_status.text == 'SHA 不一致'
            assert not service.local_tag_view.current_tags('1')
            view.close()
    finally:
        client.delete()
        service.close()
        manager.close()
        store.close()
