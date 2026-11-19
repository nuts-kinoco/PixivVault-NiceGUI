from types import SimpleNamespace
import json
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
        def state():
            db = service._database()
            rows = db.conn.execute('SELECT work_id,source,model_fingerprint,tags FROM managed_local_seed').fetchall()
            return [(r['work_id'],r['source'],r['model_fingerprint'],json.loads(r['tags'])) for r in rows], service._effective_preferences(db)
        rows, prefs = service._submit(state).result(10)
        assert len(rows) == 3
        assert all(row[1] == 'local' and row[2] and row[3] == ['cat'] for row in rows)
        assert prefs['cat']['seed_saved'] == 3
        assert 'cat' not in service._submit(lambda: service._database().get_discovery_preferences()).result()
        assert 'cat' in service.preferred_tags()
        trend = service._submit(service._trend_snapshot).result()
        assert ('cat', 3) in [(row['tag'], row['count']) for row in trend['local_seed_rows']]
        service.close()
        if invalidate == 'disabled': store.set_setting('local_tagger_enabled', '0')
        elif invalidate == 'path': store.set_setting('local_tagger_path', str(tmp_path / 'other'))
        else: (folder / 'model.onnx').write_bytes(b'changed')
        service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=manager,
            local_image=lambda _: png(), saved_ids=lambda: frozenset(w.work_id for w in works), saved_works=lambda: works)
        assert 'cat' not in service._submit(lambda: service._effective_preferences(service._database())).result()
        assert 'cat' not in service.preferred_tags()
        assert service._submit(service._trend_snapshot).result()['local_seed_rows'] == ()
    finally:
        service.close()
        manager.close()
        store.close()


def test_refresh_requested_during_scan_is_not_lost(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=object(),
        local_image=lambda _: b'', saved_works=lambda: ())
    calls = []
    def scan():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
    monkeypatch.setattr(service, '_scan_saved_seeds', scan)
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
