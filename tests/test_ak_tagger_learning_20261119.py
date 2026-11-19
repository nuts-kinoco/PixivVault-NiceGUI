import json
from types import SimpleNamespace
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media import ImageBlob
from z_learning_support_20261118 import FakeReader, candidate, seed_candidates


def test_local_image_reader_never_calls_remote():
    class Catalog:
        works = {'1000': SimpleNamespace(work_type='illustration')}
        def get(self, *args): return ImageBlob(b'local', 'image/png', 'x')
        def close(self): pass
    class Remote(Catalog):
        def get(self, *args): raise AssertionError('remote must not be read')
    registry = LibraryImageRegistry(Catalog())
    registry.publish(Catalog(), Remote())
    assert registry.local_bytes('1000') == b'local'
    assert registry.local_bytes('9999') is None
    registry.close()


def test_only_missing_api_tags_are_used_in_real_judgment(tmp_path):
    calls = []
    class Tagger:
        enabled = True
        def predict(self, data):
            calls.append(data)
            return [('cat', .9)] if self.enabled else []
    tagger = Tagger()
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), following=(), saved_ids=(),
                                       tagger=tagger, local_image=lambda wid: b'image' if wid != '1002' else None)
    try:
        seed_candidates(service, [candidate(0, tags=()), candidate(1, tags=('api_tag',)), candidate(2, tags=())])
        session = service._submit(service._open_session).result()
        for i in range(3):
            service.submit_record(session.session_id, Judgment(str(1000+i), 'like', f'op{i}')).result()
        def read():
            return {r['work_id']: json.loads(r['tags']) for r in service._database().conn.execute('SELECT work_id,tags FROM discovery_swipe_history')}
        rows = service._submit(read).result()
        assert rows['1000'] == ['cat']
        assert rows['1001'] == ['api_tag']
        assert rows['1002'] == []
        assert calls == [b'image']
        tagger.enabled = False
        service.submit_record(session.session_id, Judgment('1000', 'hate', 'op-new')).result()
        assert service._submit(read).result()['1000'] == []
    finally:
        service.close()
