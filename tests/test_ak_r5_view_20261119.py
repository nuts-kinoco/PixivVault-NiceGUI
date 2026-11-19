from types import SimpleNamespace
from threading import Event
import asyncio
import pytest
from pixivvault_nicegui.local_tag_view import LocalTagView


@pytest.mark.parametrize('transition', ['off', 'fingerprint', 'nonlocal', 'unsaved', 'unreadable', 'api'])
def test_view_fails_closed_after_current_conditions_change(transition):
    class Tagger:
        fp = 'model'
        def current_fingerprint(self): return self.fp
        def try_current_fingerprint(self): return self.fp
        def predict_with_fingerprint(self, data): return self.fp, [('local_tag', .9)]
    tagger = Tagger()
    saved, image = {'1'}, [b'image']
    works = [SimpleNamespace(work_id='1', tags=())]
    view = LocalTagView(tagger, lambda _: image[0], lambda: works, lambda: saved)
    try:
        view.refresh_async().result(10)
        assert view.current_tags('1') == ('local_tag',)
        if transition in ('off', 'fingerprint', 'nonlocal'): tagger.fp = None
        elif transition == 'unsaved': saved.clear()
        elif transition == 'unreadable': image[0] = None
        else: works[0] = SimpleNamespace(work_id='1', tags=('api',))
        assert view.current_tags('1') == ()
        assert not view.snapshot().seed_counts
        assert view.merge_preferences({'api': {'seed_saved': 1}}, view.snapshot()) == {'api': {'seed_saved': 1}}
    finally:
        view.close()


@pytest.mark.asyncio
async def test_image_validation_does_not_block_judgment_fifo(tmp_path):
    from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
    from pixivvault_nicegui.learning import Judgment
    from z_learning_support_20261118 import FakeReader, candidate, seed_candidates
    entered, release = Event(), Event()
    block = [False]
    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data): return 'model', [('local_tag', .9)]
    def image(_):
        if block[0]:
            entered.set()
            assert release.wait(10)
        return b'image'
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=image, saved_works=lambda: (SimpleNamespace(work_id='1', tags=()),), saved_ids=lambda: ('1',))
    try:
        seed_candidates(service, [candidate(0, tags=('api_tag',))])
        service.refresh_saved_seeds().result(10)
        session = service._submit(service._open_session).result(10)
        block[0] = True
        task = asyncio.create_task(service.trend_snapshot())
        assert await asyncio.to_thread(entered.wait, 5)
        pending = service.submit_record(session.session_id, Judgment('1000', 'like', 'test'))
        await asyncio.wait_for(asyncio.wrap_future(pending), 3)
        release.set()
        await task
    finally:
        release.set()
        service.close()
