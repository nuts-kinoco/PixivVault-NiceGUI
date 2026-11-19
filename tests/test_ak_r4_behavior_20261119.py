import asyncio
from copy import deepcopy
from types import SimpleNamespace
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from z_learning_support_20261118 import FakeReader, candidate, seed_candidates


def test_saved_local_seed_affects_ranking_only_while_enabled(tmp_path):
    class Tagger:
        enabled = False
        def current_fingerprint(self): return 'model' if self.enabled else None
        def try_current_fingerprint(self): return self.current_fingerprint()
        def predict_with_fingerprint(self, data): return ('model', [('local_special', .9)])
    tagger = Tagger()
    works = tuple(SimpleNamespace(work_id=str(i), tags=()) for i in range(1, 9))
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=tagger,
        local_image=lambda _: b'image', saved_works=lambda: works, saved_ids=lambda: {w.work_id for w in works})
    try:
        candidates = [candidate(0, tags=('different',)), candidate(1, tags=('local_special',))]
        seed_candidates(service, candidates)
        before = deepcopy(service._submit(lambda: service._candidates).result())
        off = asyncio.run(service.rank_pending(('1000', '1001')))
        tagger.enabled = True
        service.refresh_saved_seeds().result(10)
        on = asyncio.run(service.rank_pending(('1000', '1001')))
        assert on[0] != off[0]
        tagger.enabled = False
        assert asyncio.run(service.rank_pending(('1000', '1001'))) == off
        assert service._submit(lambda: service._candidates).result() == before
        assert service._submit(lambda: service._database().get_judged_work_ids()).result() == set()
        session = service._submit(service._open_session).result()
        assert session.judged_ids == frozenset()
        assert all(art.work_id not in {w.work_id for w in works} for art in service.artworks)
    finally:
        service.close()


def test_local_seed_never_enters_search_even_while_enabled(tmp_path):
    class Tagger:
        enabled = False
        def current_fingerprint(self): return 'model' if self.enabled else None
        def try_current_fingerprint(self): return self.current_fingerprint()
        def predict_with_fingerprint(self, data): return ('model', [('local_special', .9)])
    class Reader(FakeReader):
        def __init__(self): super().__init__(); self.words = []
        def search_recommendation_illusts(self, word, **kwargs):
            self.words.append(word)
            return {'works': [], 'total': 0}
    tagger, reader = Tagger(), Reader()
    works = (SimpleNamespace(work_id='1', tags=()),)
    service = DiscoveryLearningService(tmp_path / 'discovery', reader, following=(), tagger=tagger,
        local_image=lambda _: b'image', saved_works=lambda: works, saved_ids=lambda: {'1'})
    try:
        asyncio.run(service.fetch_candidates())
        off = tuple(reader.words)
        tagger.enabled = True
        service.refresh_saved_seeds().result(10)
        reader.words.clear()
        asyncio.run(service.fetch_candidates())
        assert tuple(reader.words) == off
        assert 'local_special' not in reader.words
        tagger.enabled = False
        reader.words.clear()
        asyncio.run(service.fetch_candidates())
        assert tuple(reader.words) == off
    finally:
        service.close()
