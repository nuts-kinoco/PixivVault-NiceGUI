from types import SimpleNamespace
import json
import asyncio
from threading import Event
import pytest
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.local_tagger import LocalTagger, TaggerManager
from pixivvault_nicegui.preferences import PreferencesStore
from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png
from z_learning_support_20261118 import FakeReader, candidate, seed_candidates


@pytest.mark.parametrize('invalidate', ['disabled', 'path', 'digest'])
def test_saved_seed_is_separate_and_revalidated_on_query(tmp_path, monkeypatch, invalidate):
    folder = tmp_path / 'model'
    folder.mkdir()
    make_model(folder)
    store = PreferencesStore(tmp_path / 'prefs.sqlite')
    store.set_setting('local_tagger_path', str(folder))
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]
    manager = TaggerManager(store)
    manager.tagger = LocalTagger(folder, session_factory=lambda _: Session(), tensor_factory=lambda x: x)
    manager._provider = 'auto'
    works = tuple(SimpleNamespace(work_id=str(1000+i), tags=()) for i in range(3)) + (SimpleNamespace(work_id='1003', tags=('api',)),)
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=manager,
        local_image=lambda _: png(), saved_ids=lambda: frozenset(w.work_id for w in works), saved_works=lambda: works)
    try:
        seed_candidates(service, [candidate(0, tags=())])
        service.refresh_saved_seeds().result(15)
        snapshot = service.local_tag_view.snapshot()
        prefs = service._submit(lambda: service._effective_preferences(service._database(), snapshot)).result(10)
        assert len(snapshot.by_work) == 3
        assert snapshot.fingerprint and all(tags == ('cat',) for tags in snapshot.by_work.values())
        assert service._submit(lambda: service._database().conn.execute("SELECT 1 FROM sqlite_master WHERE name='managed_local_seed'").fetchone()).result(10) is None
        assert prefs['cat']['seed_saved'] == 3
        assert 'cat' not in service._submit(lambda: service._database().get_discovery_preferences()).result()
        assert 'cat' in service.preferred_tags()
        trend = asyncio.run(service.trend_snapshot())
        assert ('cat', 3) in [(row['tag'], row['count']) for row in trend['local_seed_rows']]
        service.close()
        if invalidate == 'disabled': store.set_setting('local_tagger_enabled', '0')
        elif invalidate == 'path': store.set_setting('local_tagger_path', str(tmp_path / 'other'))
        else: (folder / 'model.onnx').write_bytes(b'changed')
        service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=manager,
            local_image=lambda _: png(), saved_ids=lambda: frozenset(w.work_id for w in works), saved_works=lambda: works)
        snapshot = service.local_tag_view.snapshot()
        assert 'cat' not in service._submit(lambda: service._effective_preferences(service._database(), snapshot)).result()
        assert 'cat' not in service.preferred_tags()
        assert asyncio.run(service.trend_snapshot())['local_seed_rows'] == ()
    finally:
        service.close()
        manager.close()
        store.close()


def test_refresh_requested_during_scan_is_not_lost(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    class Tagger:
        def current_fingerprint(self):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                assert release.wait(10)
            return None
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=lambda _: b'', saved_works=lambda: ())
    try:
        future = service.refresh_saved_seeds()
        assert entered.wait(10)
        assert service.refresh_saved_seeds() is future
        release.set()
        future.result(10)
        assert len(calls) == 2
    finally:
        release.set()
        service.close()
