import asyncio
import hashlib
import statistics
import time
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui import local_tagger
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.learning_view import LearningView
from pixivvault_nicegui.ui.ranking_view import RankingView

from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png
from z_learning_support_20261118 import FakeReader, FakeServices, candidate, seed_candidates, settled


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    folder = tmp_path / 'fake-model'
    folder.mkdir()
    make_model(folder)
    behavior = SimpleNamespace(fail=False, calls=0)

    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name='input')]

        def run(self, *_):
            behavior.calls += 1
            if behavior.fail:
                raise RuntimeError('synthetic inference failure')
            return [[(.9,)]]

    original = local_tagger.LocalTagger

    class Injected(original):
        def __init__(self, path, **kwargs):
            super().__init__(path, session_factory=lambda _: Session(),
                             tensor_factory=lambda im: im, **kwargs)

    monkeypatch.setattr(local_tagger, 'LocalTagger', Injected)
    store = PreferencesStore(tmp_path / 'fake-settings.sqlite3')
    store.set_setting('local_tagger_path', str(folder))
    tagger = local_tagger.TaggerManager(store)
    try:
        yield folder, store, tagger, behavior
    finally:
        tagger.close()
        store.close()


def create(root, tagger, works, local_image, saved=None, reader=None):
    return DiscoveryLearningService(root, reader or FakeReader(), following=(), tagger=tagger,
        local_image=local_image, saved_works=lambda: works,
        saved_ids=lambda: ({w.work_id for w in works} if saved is None else saved))


def get_state(folder):
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns,
                     hashlib.sha256(p.read_bytes()).hexdigest()) for p in folder.iterdir()}


@pytest.mark.asyncio
async def test_after_inference_failure_cached_tag_loses_eligibility(tmp_path, fixture):
    _, _, tagger, behavior = fixture
    # Valid PNG with different bytes for the second image so cache lookup misses.
    image = [png()]
    works = [SimpleNamespace(work_id='1', tags=())]
    service = create(tmp_path / 'learning', tagger, works,
        lambda wid: image[0] if wid == '1' else image[0] + b'distinct')
    try:
        await asyncio.wrap_future(service.refresh_saved_seeds())
        assert service.local_tag_view.current_tags('1') == ('cat',)
        behavior.fail = True
        works.append(SimpleNamespace(work_id='2', tags=()))
        await asyncio.wrap_future(service.refresh_saved_seeds())
        assert tagger.status != '利用可能' and tagger.current_fingerprint() is None
        projection = service.local_tag_view.snapshot()
        print('failed-model truthy receipt fingerprint', projection.fingerprint is not None,
              'projection after failed inference', dict(projection.by_work))
        assert not projection.by_work
    finally:
        service.close()


@pytest.mark.asyncio
async def test_first_session_snapshot_does_not_abort_seed_scan(tmp_path, fixture, monkeypatch):
    _, _, tagger, behavior = fixture
    tagger.verify()  # A valid receipt exists, as on an ordinary restart.
    image = png()
    entered, release = Event(), Event()
    def slow_image(_):
        entered.set()
        assert release.wait(6)
        return image
    works = [SimpleNamespace(work_id='1', tags=())]
    service = create(tmp_path / 'learning', tagger, works, slow_image)
    try:
        snapshot = service.local_tag_view.snapshot
        def order_workers():
            assert entered.wait(3)
            return snapshot()
        monkeypatch.setattr(service.local_tag_view, 'snapshot', order_workers)
        # Pause the worker at its first image read before the normal session snapshot.
        await service.open_session()
        assert await asyncio.to_thread(entered.wait, 3)
        pending = service._seed_future
        release.set()
        await asyncio.wrap_future(pending)
        view = service.local_tag_view.snapshot()
        print('first session post-scan tags', dict(view.by_work),
              'inferences', behavior.calls, 'epoch', view.epoch)
        assert view.by_work.get('1') == ('cat',)
    finally:
        release.set()
        service.close()


@pytest.mark.asyncio
async def test_readability_change_during_fetch_result_uses_fresh_order(tmp_path, fixture):
    _, store, tagger, _ = fixture
    started, release = Event(), Event()

    class Reader(FakeReader):
        def __init__(self):
            super().__init__()
            self.words = []

        def search_recommendation_illusts(self, word, **kwargs):
            self.words.append(word)
            started.set()
            assert release.wait(6)
            return {'works': [], 'total': 0}

    reader = Reader()
    works = [SimpleNamespace(work_id=str(i), tags=()) for i in range(1, 9)]
    image = [png()]
    service = create(tmp_path / 'learning', tagger, works, lambda _: image[0], reader=reader)
    try:
        seed_candidates(service, [candidate(0, tags=('different',)), candidate(1, tags=('cat',))])
        def seed_api():
            db = service._database()
            with db._transaction():
                db._bump_tag('api_only', 'seed_saved', 5)
        await asyncio.wrap_future(service._submit(seed_api))
        await asyncio.wrap_future(service.refresh_saved_seeds())
        ids = ('1000', '1001')
        baseline = (await service.rank_pending(ids))[0]
        assert baseline == ('1001', '1000')
        fetching = asyncio.create_task(service.fetch_candidates())
        assert await asyncio.to_thread(started.wait, 3)
        # Ordinary I/O failure can happen without a library publish notification.
        image[0] = None
        await asyncio.to_thread(service.local_tag_view.refresh_now)
        release.set()
        batch = await fetching
        fresh = (await service.rank_pending(ids))[0]
        store.set_setting('local_tagger_enabled', '0')
        disabled = (await service.rank_pending(ids))[0]
        actual = tuple(a.work_id for a in batch.artworks)
        print('search words', reader.words, 'fetch result', actual,
              'fresh rank', fresh, 'disabled rank', disabled)
        assert fresh == disabled and actual == disabled
    finally:
        release.set()
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', ['disabled', 'digest', 'nonlocal', 'unsaved', 'unreadable', 'api'])
async def test_open_learning_trend_removes_stale_projection(tmp_path, fixture, monkeypatch, transition):
    folder, store, tagger, behavior = fixture
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    works = [SimpleNamespace(work_id=str(i), tags=()) for i in range(1, 9)]
    saved = {w.work_id for w in works}
    image = [png()]
    service = create(tmp_path / 'learning', tagger, works, lambda _: image[0], saved)
    seed_candidates(service, [candidate(0, tags=('different',)), candidate(1, tags=('cat',))])
    await asyncio.wrap_future(service.refresh_saved_seeds())
    client = Client(ui.page('/r4-learning-' + transition))
    owned_before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(FakeServices(service))
    try:
        view._initial_done = True
        await settled(view)
        view.set_active(True)
        await settled(view)
        async with asyncio.timeout(5):
            while not view._trend_snapshot or not view._trend_snapshot['local_seed_rows']:
                await asyncio.sleep(.01)
            while view._seed_watch_task is None or not view._seed_watch_task.done():
                await asyncio.sleep(.01)
        assert view._trend_snapshot['local_seed_rows'][0]['tag'] == 'cat'

        if transition == 'disabled':
            # Settings click notification follows the actual shell entry point.
            store.set_setting('local_tagger_enabled', '0')
            from pixivvault_nicegui.services import ApplicationServices
            from pixivvault_nicegui.ui.shell import DemoShell
            app = FakeServices(service)
            app.local_tag_view = service.local_tag_view
            app.preferences = store
            app._sync_metadata_auto = lambda: None
            app._sync_similarity_auto = lambda: None
            app.shared_image_cache = None
            app.apply_preferences = lambda snapshot: ApplicationServices.apply_preferences(app, snapshot)
            shell = SimpleNamespace(services=app, learning=view, gallery=None,
                                    personal_view=None, job_views={})
            DemoShell.apply_preferences(shell)
            DemoShell._tagger_settings_changed(shell)
        elif transition == 'digest':
            (folder / 'model.onnx').write_bytes(b'changed-contents')
        elif transition == 'nonlocal':
            target = folder / 'model.onnx'
            original = Path.resolve
            monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw:
                Path(r'\\synthetic\share\model') if p == target else original(p, *a, **kw))
        elif transition == 'unsaved':
            saved.clear()
            service.local_tag_view.invalidate()
            service.refresh_saved_seeds()
        elif transition == 'unreadable':
            image[0] = None
        else:
            works[:] = [SimpleNamespace(work_id=w.work_id, tags=('api_arrived',)) for w in works]
            service.local_tag_view.invalidate()
            service.refresh_saved_seeds()

        if transition in ('digest', 'nonlocal'):
            assert await asyncio.to_thread(tagger.current_fingerprint) is None
        if transition in ('unsaved', 'unreadable', 'api'):
            await asyncio.to_thread(service.local_tag_view.refresh_now)
        current = await service.trend_snapshot()
        assert not current['local_seed_rows']
        expected, _ = await service.rank_pending(('1000', '1001'))
        await asyncio.sleep(1.15)
        displayed = (tuple(row['tag'] for row in view._trend_snapshot['local_seed_rows']),
                     tuple(a.work_id for a in view.state.artworks))
        print('transition', transition, 'current-service-order', expected, 'displayed', displayed)
        assert displayed == ((), expected)
    finally:
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - owned_before
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(service.close)


@pytest.mark.asyncio
async def test_visible_judgment_waits_for_unrelated_image_after_db_commit(tmp_path, fixture, monkeypatch):
    _, store, tagger, _ = fixture
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    image = png()
    blocked, release = Event(), Event()
    hold = False
    def local_image(_):
        if hold:
            blocked.set()
            assert release.wait(6)
        return image
    service = create(tmp_path / 'learning', tagger, [SimpleNamespace(work_id='1', tags=())], local_image)
    seed_candidates(service, [candidate(i, tags=('api_untouched',)) for i in range(4)])
    await asyncio.wrap_future(service.refresh_saved_seeds())
    client = Client(ui.page('/r4-judgment'))
    owned_before = set(background_tasks.running_tasks)
    with client:
        view = LearningView(FakeServices(service))
    try:
        view._initial_done = True
        await settled(view)
        view.set_active(True)
        await settled(view)
        async with asyncio.timeout(5):
            while view._seed_watch_task is None or not view._seed_watch_task.done():
                await asyncio.sleep(.01)

        def judged():
            return service._database().get_judged_work_ids()

        async def judge_and_observe(enabled, operation):
            nonlocal hold
            store.set_setting('local_tagger_enabled', enabled)
            work = view.state.current_artwork
            assert work is not None
            view._on_loaded(SimpleNamespace(args=view._image_url))
            title_before = view.title.text
            hold = True
            start = time.perf_counter()
            assert view.judge('like', {'work_id': work.work_id, 'generation': view.state.generation,
                                      'operation_id': operation})
            assert work.work_id in await asyncio.wrap_future(service._submit(judged))
            elapsed_commit = time.perf_counter()-start
            if enabled == '1':
                service.refresh_saved_seeds()
                assert await asyncio.to_thread(blocked.wait, 2)
            else:
                await asyncio.wait_for(asyncio.shield(view._save_task), 2)
            print('enabled', enabled, 'database committed before release', True,
                  'commit ms', round(elapsed_commit*1000, 3),
                  'next title observation ms', round((time.perf_counter()-start)*1000, 3),
                  'title updated', title_before != view.title.text,
                  'save task done', view._save_task.done())
            return title_before != view.title.text

        assert await judge_and_observe('0', 'off-like')
        updated = await judge_and_observe('1', 'on-like')
        release.set()
        await asyncio.wait_for(view._save_task, 4)
        assert updated
    finally:
        release.set()
        view.close()
        client.delete()
        owned = set(background_tasks.running_tasks) - owned_before
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await asyncio.to_thread(service.close)
