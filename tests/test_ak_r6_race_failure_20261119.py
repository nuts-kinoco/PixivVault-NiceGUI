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
