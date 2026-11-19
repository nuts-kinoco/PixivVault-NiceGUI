"""Control vs treatment contract. Add every new local-tag consumer to SURFACES."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import shutil
from threading import Event
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui import learning_repository, local_tagger
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.legacy import database
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.learning_view import LearningView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ak_tagger_20261119 import make_model
from test_ak_tagger_inference_20261119 import png
from z_learning_support_20261118 import FakeReader, FakeServices, candidate, seed_candidates, settled


# These rows compare the live consumer outputs after the required invalidation.
SURFACES = (
    'fetch_return',       # Acquisition already in flight at invalidation.
    'first_scan',        # First open before inference and a reopen after invalidation.
    'open_learning',     # Order, trend and rendered text of the same open screen.
    'after_judgment',    # Committed work and next visible card.
    'ranking_card',      # A cached preferred-tag set from before invalidation.
    'refill',
    'rank_pending',
    'trend',
    'preferred_tags',
)
IMMEDIATE = ('disabled', 'path', 'digest', 'nonlocal', 'inference')
DELAYED = ('unsaved', 'unreadable', 'api')
CANDIDATES = ('1000', '1001', '1002')


def work_ids(artworks):
    return tuple(a.work_id for a in artworks)


async def until(condition):
    async with asyncio.timeout(4):
        while not condition():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('transition', IMMEDIATE + DELAYED)
async def test_control_and_invalidated_treatment_are_identical(tmp_path, monkeypatch, transition):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    # Preserve exact persisted-value comparisons even across successive judgments.
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 19, 12, tzinfo=tz)
    monkeypatch.setattr(learning_repository, 'datetime', FixedDate)
    monkeypatch.setattr(database, 'datetime', FixedDate)

    class Reader(FakeReader):
        def __init__(self):
            super().__init__()
            self.words = []
            self.started, self.release = Event(), Event()

        def search_recommendation_illusts(self, word, **kwargs):
            self.words.append(word)
            self.started.set()
            assert self.release.wait(8)
            return {'works': [], 'total': 0}

    template = tmp_path / 'identical-initial-db'
    initial = DiscoveryLearningService(template, FakeReader(), following=())
    try:
        seed_candidates(initial, [candidate(0, tags=('different',)), candidate(1, tags=('cat',)),
                                  candidate(2, tags=('other',))])

        def seed_api():
            db = initial._database()
            with db._transaction():
                db._bump_tag('api_alpha', 'seed_saved', 5, now='2026-01-01')
        initial._submit(seed_api).result(10)
    finally:
        initial.close()

    async def execute(label, enabled):
        folder = tmp_path / (label + '-model')
        folder.mkdir()
        make_model(folder)
        store = PreferencesStore(tmp_path / (label + '-preferences.sqlite'))
        store.set_setting('local_tagger_path', str(folder))
        store.set_setting('local_tagger_enabled', '1' if enabled else '0')
        manager, reader = local_tagger.TaggerManager(store), Reader()
        fail_inference = False

        class Session:
            def get_inputs(self): return [SimpleNamespace(name='input')]
            def run(self, *_):
                if fail_inference:
                    raise RuntimeError('synthetic inference failure')
                return [[(.9,)]]

        manager.tagger = local_tagger.LocalTagger(folder, session_factory=lambda _: Session(),
                                                 tensor_factory=lambda value: value)
        manager._provider = 'auto'
        works = [SimpleNamespace(work_id=str(i), tags=()) for i in range(1, 9)]
        saved = {w.work_id for w in works}
        png_data = png()
        image_available = True
        reading, release_image = Event(), Event()

        def image_token(_):
            return (1, len(png_data)) if image_available else None

        def read_image(_):
            reading.set()
            assert release_image.wait(8)
            return png_data if image_available else None

        root = tmp_path / (label + '-learning')
        root.mkdir()
        for source in template.iterdir():
            shutil.copy2(source, root / source.name)

        def create():
            return DiscoveryLearningService(root, reader, following=(), tagger=manager,
                local_image=read_image, image_token=image_token, saved_works=lambda: works, saved_ids=lambda: saved)

        service = create()

        def dump():
            db = service._database()
            tables = [row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {name: sorted((tuple(row) for row in db.conn.execute('SELECT * FROM "' + name + '"')), key=repr)
                    for name in tables}

        def display():
            return (work_ids(view.state.artworks), view._trend_snapshot,
                    tuple(getattr(item, 'text', None) for item in view.trend_content.descendants()))

        client = Client(ui.page('/ak-r5-contract-' + label + '-' + transition))
        owned_before = set(background_tasks.running_tasks)
        view, fetching = None, None
        observations = {}
        completed = False
        try:
            with client:
                view = LearningView(FakeServices(service))
            view._initial_done = True
            if enabled:
                assert await asyncio.to_thread(reading.wait, 3)
            await settled(view)
            first_order = work_ids(view.state.artworks)
            assert first_order == CANDIDATES
            assert not view._trend_snapshot['local_seed_rows']

            release_image.set()
            await asyncio.wrap_future(service._seed_future)
            view.set_active(True)
            await settled(view)
            local = service.local_tag_view
            if enabled:
                await until(lambda: view._trend_snapshot['local_seed_rows'] and
                            work_ids(view.state.artworks)[0] == '1001')
                assert local.current_tags('1') == ('cat',)

            ranking = SimpleNamespace(services=SimpleNamespace(
                local_tag_view=local, preferred_ranking_tags=service.preferred_tags,
                preferred_ranking_tags_base=service.base_preferred_tags))
            await asyncio.to_thread(RankingView._rebuild_preferred, ranking)
            assert RankingView.preferred_tags_in(ranking, ('cat',)) is enabled

            before = local.snapshot()
            fetching = asyncio.create_task(service.fetch_candidates())
            assert await asyncio.to_thread(reader.started.wait, 3)
            if transition == 'disabled':
                store.set_setting('local_tagger_enabled', '0')
            elif transition == 'path':
                store.set_setting('local_tagger_path', str(folder / 'missing'))
            elif transition == 'digest' and enabled:
                (folder / 'model.onnx').write_bytes(b'changed')
                assert await asyncio.to_thread(manager.current_fingerprint) is None
            elif transition == 'nonlocal' and enabled:
                target, resolve = folder / 'model.onnx', Path.resolve
                monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw:
                    Path(r'\\server\share\model') if p == target else resolve(p, *a, **kw))
                assert manager.try_current_fingerprint() is None
            elif transition == 'inference' and enabled:
                fail_inference = True
                assert await asyncio.to_thread(manager.predict_with_fingerprint, png_data + b'uncached') is None
            elif transition == 'unsaved':
                saved.clear()
            elif transition == 'unreadable':
                image_available = False
            elif transition == 'api':
                works[:] = [SimpleNamespace(work_id=w.work_id, tags=('api_arrived',)) for w in works]

            if transition in IMMEDIATE:
                if enabled:
                    assert local.epoch() > before.epoch
                    assert local.merge_preferences({}, before) == {}
                    assert not local.current_tags('1')
            else:
                if enabled:
                    assert local.snapshot() is before
                await asyncio.to_thread(local.refresh_now)

            # Observe the already cached ranking card without rebuilding its preferences.
            observations['ranking_card'] = tuple(RankingView.preferred_tags_in(ranking, (tag,))
                                                 for tag in ('cat', 'api_alpha', 'unrelated'))
            reader.release.set()
            batch = await fetching
            observations['fetch_return'] = work_ids(batch.artworks), batch.frames, batch.records
            refill, _ = await service.refill_snapshot()
            observations['refill'] = work_ids(refill.artworks), refill.frames
            observations['rank_pending'] = await service.rank_pending(CANDIDATES)
            observations['trend'] = await service.trend_snapshot()
            observations['preferred_tags'] = await asyncio.to_thread(service.preferred_tags)

            await until(lambda: view._trend_snapshot == observations['trend'] and
                        work_ids(view.state.artworks) == observations['rank_pending'][0])
            observations['open_learning'] = display()
            active = view.state.current_artwork
            assert active.work_id == '1000'
            view._on_loaded(SimpleNamespace(args=view._image_url))
            assert view.judge('like', {'work_id': active.work_id, 'generation': view.state.generation,
                                      'operation_id': 'contract-judgment'})
            await asyncio.wait_for(asyncio.shield(view._save_task), 4)
            assert view.state.current_artwork.work_id != active.work_id
            observations['after_judgment'] = active.work_id, view.state.current_artwork.work_id, view.title.text
            data = service._submit(dump).result(10)
            assert 'different' in json.dumps(data['discovery_swipe_history'], ensure_ascii=False)
            assert 'cat' not in json.dumps(data['discovery_swipe_history'], ensure_ascii=False)
            completed = True
        finally:
            release_image.set()
            reader.release.set()
            if fetching is not None:
                await asyncio.gather(fetching, return_exceptions=True)
            if view is not None:
                view.close()
            client.delete()
            owned = set(background_tasks.running_tasks) - owned_before
            for task in owned:
                task.cancel()
            await asyncio.gather(*owned, return_exceptions=True)
            await asyncio.to_thread(service.close)
            if not completed:
                manager.close()
                store.close()

        service = create()
        try:
            reopened = await service.open_session()
            await asyncio.wrap_future(service._seed_future)
            observations['first_scan'] = (first_order, work_ids(service.artworks),
                                          reopened.judged_ids, await service.trend_snapshot())
            reopened_data = service._submit(dump).result(10)
            words = tuple(reader.words)
            assert words and set(words) == {'api_alpha'}
            assert 'managed_local_seed' not in data and 'managed_local_seed' not in reopened_data
            assert observations.keys() == set(SURFACES)
            return observations, data, reopened_data, words
        finally:
            await asyncio.to_thread(service.close)
            manager.close()
            store.close()

    treatment, treatment_data, treatment_reopened, treatment_words = await execute('treatment', True)
    control, control_data, control_reopened, control_words = await execute('control', False)
    for surface in SURFACES:
        assert treatment[surface] == control[surface], surface
    assert (treatment_data, treatment_reopened, treatment_words) == (control_data, control_reopened, control_words)
