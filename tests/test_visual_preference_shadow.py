import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from hashlib import sha256
from http.cookiejar import CookieJar
import json
import os
import sqlite3
import sys
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import discovery_ranking, local_tagger
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceArbiter, GpuInferenceStopped
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_prefetch import LearningImagePrefetch
from pixivvault_nicegui.learning_service import LearningService
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.local_tag_view import LocalTagView
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.shared_image_cache import SharedImageCache
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from pixivvault_nicegui.visual_preference_store import VisualPreferenceStore, VisualPreferenceStoreError
from test_gpuarb_inference import fake_ort, make_tagger
from test_gpuarb_remote_media import Client, png, record
from test_managed_network_stop_transport import _client
from test_visual_preference_store import generation, model
from z_learning_support_20261118 import open_discovery_view


def store_at(root):
    return VisualPreferenceStore(root, profile='test-profile', account='test-account', protected_paths=())


def wait_for(predicate):
    deadline = monotonic() + 10
    while not predicate():
        assert monotonic() < deadline
        sleep(.01)


def test_committed_notifications_and_generation_invalidation(tmp_path):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    store = store_at(tmp_path / 'shadow')
    observer = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    service = LearningService(tmp_path / 'learning', generate_demo_artworks(2), shadow_observer=observer)
    work = service.artworks[0].work_id

    async def exercise():
        session = (await service.open_session()).session_id
        first = Judgment(work, 'hate', 'first')
        second = Judgment(work, 'like', 'second')

        async def invalidates(a, b=None):
            old = generation(store)
            fitted = model(old)
            assert store.publish(old, fitted.to_payload())
            await a()
            assert not store.is_current(old)
            assert not store.publish(old, fitted.to_payload())
            if b is not None:
                await b()

        await invalidates(lambda: service.record(session, first), lambda: service.record(session, first))
        await invalidates(lambda: service._wait(service.submit_hate_reason(session, first, 'style')))
        await invalidates(lambda: service.revert(session, first, 'revert'))
        await invalidates(lambda: service.record(session, second))
        row = (await service.review_snapshot(session))['rows'][0]
        await invalidates(lambda: service._wait(service.submit_review_undo(session, work, row['revision'], 'undo')))
        await invalidates(lambda: service._wait(service.submit_review_restore(session, 'undo', 'restore')))
        await invalidates(service.reset_learning)
        assert await service.judged_count() == 0

    try:
        asyncio.run(exercise())
    finally:
        service.close()
    events = store.events()
    assert [e.kind for e in events] == ['judgment', 'reason_change', 'undo', 'judgment', 'undo', 'restore', 'reset']
    assert [e.payload.get('revision') for e in events[:-1]] == [1, 2, 3, 4, 5, 6]
    assert [e.payload.get('judgment') for e in events[:-1]] == ['hate', 'hate', None, 'like', None, 'like']
    assert events[1].payload['reason'] == 'style'
    assert observer.status()['phase'] == 'disabled'


def test_observer_failure_isolated_and_receipt_is_replay_safe(tmp_path, caplog):
    calls = []

    def fail(*args):
        calls.append(args)
        raise RuntimeError('sensitive exception details')

    service = LearningService(tmp_path / 'learning', generate_demo_artworks(1),
                              shadow_observer=SimpleNamespace(judgment_changed=fail))

    async def exercise():
        session = (await service.open_session()).session_id
        judgment = Judgment(service.artworks[0].work_id, 'like', 'first')
        await service.record(session, judgment)
        await service.record(session, judgment)
        assert await service.judged_count() == 1

    try:
        asyncio.run(exercise())
    finally:
        service.close()
    assert len(calls) == 1
    assert 'visual preference' in caplog.text.lower()
    assert 'sensitive exception details' not in caplog.text


def test_revocation_replace_failure_cannot_leave_old_model_visible(tmp_path, monkeypatch):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    root = tmp_path / 'shadow'
    store = store_at(root)
    reader = store_at(root)
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    service = LearningService(tmp_path / 'learning', generate_demo_artworks(1), shadow_observer=worker)
    original = store._write_record

    def fail_replacement(path, value):
        if path == store._revocation:
            raise OSError('synthetic control replace failure')
        return original(path, value)

    async def exercise():
        session = (await service.open_session()).session_id
        work = service.artworks[0].work_id
        judgment = Judgment(work, 'like', 'first')
        await service.record(session, judgment)
        active = generation(store)
        assert store.publish(active, model(active).to_payload())
        monkeypatch.setattr(store, '_write_record', fail_replacement)
        await service.revert(session, judgment, 'undo')
        assert await service.judged_count() == 0
        assert not reader.is_current(active)
        assert reader.load_model(active) is None
        assert not store_at(root).is_current(active)

    try:
        asyncio.run(exercise())
    finally:
        service.close()


def test_ranking_diagnostics_and_observer_preserve_frames_and_ties(tmp_path):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    candidates = [{'work_id': str(i), 'author_id': str(i), 'tags': ['known' if i % 2 else 'unknown']}
                  for i in (39, 15, 29, 18, 19)]
    preferences = {'known': {'like': 20}}
    ordered, frames = discovery_ranking.rank_candidates(candidates, preferences, {})
    with_diagnostics = discovery_ranking.rank_candidates_with_diagnostics(candidates, preferences, {})
    assert with_diagnostics[:2] == (ordered, frames)
    assert set(with_diagnostics[2]) == set(frames)
    assert all('score' in row and 'upside' in row for row in with_diagnostics[2].values())
    diagnostic_settings = {'limit': 3, 'recent_author_ids': ('15', '29')}
    assert discovery_ranking.rank_candidates_with_diagnostics(candidates, preferences, {}, **diagnostic_settings)[:2] == (
        discovery_ranking.rank_candidates(candidates, preferences, {}, **diagnostic_settings))

    store = store_at(tmp_path / 'shadow')
    observer = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    service = DiscoveryLearningService(tmp_path / 'learning', object(), shadow_observer=observer)
    service._candidates = {row['work_id']: row for row in candidates}

    async def ranks():
        ids = tuple(service._candidates)
        enabled = await service.rank_pending(ids, ('15',))
        service.shadow_observer = None
        assert await service.rank_pending(ids, ('15',)) == enabled
        return enabled

    try:
        order, slots = asyncio.run(ranks())
    finally:
        service.close()
    event, = store.events()
    assert event.kind == 'ranking'
    rows = event.payload['candidates']
    assert [row['work_id'] for row in rows] == list(order)
    assert [row['frame'] for row in rows] == [slots[work] for work in order]
    assert [row['base_rank'] for row in rows] == list(range(1, len(rows) + 1))
    assert [row['revision'] for row in rows] == [0] * len(rows)


def test_prefetch_passes_existing_result_without_extra_dispatch_or_lru_touches():
    results = []
    preferences = SimpleNamespace(snapshot=lambda: SimpleNamespace(values={
        'discovery_cache_limit_mode': 'count', 'discovery_cache_size_count': '5'}))

    async def run(missing, on_image=None):
        client = Client(missing=missing)
        pin = RemoteBookmarkImages(client, (record('first', known=not missing), record('second', known=not missing)))
        other = RemoteBookmarkImages(client, (record('other'),))
        cache = SharedImageCache(max_bytes=2 * len(png()))
        try:
            pin.configure_cache(cache)
            other.configure_cache(cache)
            ready = []
            kwargs = {'on_image': on_image} if on_image else {}
            prefetch = LearningImagePrefetch(preferences, pin, on_ready=ready.append, **kwargs)
            prefetch.set_queue(('first',))
            assert (await prefetch.run()).fetched == 1
            if not missing:
                other.get('other')
                pin.get('second')
                assert pin.peek_with_variant('first', 0, 'view') is None
            return client.calls, ready
        finally:
            pin.close()
            other.close()
            cache.close()

    for missing in (False, True):
        baseline = asyncio.run(run(missing))
        assert asyncio.run(run(missing, lambda *args: results.append(args))) == baseline
    assert [(work, page, image.variant) for work, page, image in results] == [
        ('first', 0, 'regular_view'), ('first', 0, 'listing_thumb_fallback')]


@pytest.mark.parametrize('missing', [False, True])
async def test_real_learning_load_and_prefetch_share_transport_with_observer_off(tmp_path, monkeypatch, missing):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    calls = []
    regular_url = 'https://i.pximg.net/synthetic-regular.png'

    def transport(request, *_):
        calls.append((request.get_method(), request.full_url))
        if '/pages' in request.full_url:
            if missing:
                raise HTTPStatusRejected(404)
            return json.dumps({'error': False, 'body': [{'urls': {'regular': regular_url}}]}).encode()
        return png()

    client = _client(ManagedNetworkStopGate(tmp_path / 'gate.sqlite3'), transport)
    client._cookies = CookieJar()
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch)
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    preferences = SimpleNamespace(snapshot=lambda: SimpleNamespace(values={
        'discovery_cache_limit_mode': 'count', 'discovery_cache_size_count': '5'}))
    before = tuple(a.work_id for a in view.state.artworks)
    key = before[0]
    try:
        for observing in (False, True):
            calls.clear()
            cache = SharedImageCache(max_bytes=4096)
            registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
            pin = registry.acquire_catalog(RemoteBookmarkImages(client, (record(key, known=not missing),)))
            try:
                service.shadow_observer = worker if observing else None
                prefetch = LearningImagePrefetch(preferences, pin, on_image=service.shadow_image if observing else None)
                prefetch.set_queue((key,))
                assert (await prefetch.run()).fetched == 1
                baseline = list(calls) if not observing else baseline
                assert len(baseline) == 2
                # Same registry entry point as the versioned HTTP media route.
                assert registry.get_versioned(pin.token, key, 0, 'view').data
                with view.client:
                    view._discovery_pin = view._images = pin
                    view.request_image()
                    view._on_loaded(SimpleNamespace(args='stale-load'))
                    view._on_loaded(SimpleNamespace(args=view._image_url))
                    view._on_loaded(SimpleNamespace(args=view._image_url))
                assert tuple(a.work_id for a in view.state.artworks) == before
                assert calls == baseline
                await service._wait(service._submit(lambda: None))
            finally:
                pin.close()
                registry.close()
                cache.close()
        exposure, = store.events()
        assert exposure.kind == 'exposure'
        assert exposure.payload['image_available']
        assert exposure.payload['image_variant'] == ('thumb_fallback' if missing else 'view')
        assert [pair[0] for pair in exposure.payload['pending_candidates']] == list(before)
        assert exposure.payload['base_rank'] == 1
        assert exposure.payload['revision'] == 0
    finally:
        await cleanup()
        client.close()


def test_worker_is_opt_in_cuda_only_records_all_image_variants_and_stops(tmp_path, monkeypatch):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    arbiter = GpuInferenceArbiter()
    calls = fake_ort(monkeypatch)
    store = store_at(tmp_path / 'shadow')
    path = tmp_path / 'egara.onnx'
    path.write_bytes(b'synthetic egara')
    digest = sha256(path.read_bytes()).hexdigest()
    worker = VisualPreferenceWorker(store, path, model_sha=digest, gpu_arbiter=arbiter)
    regular = RemoteBookmarkImages(Client(), (record('a'),))
    fallback = RemoteBookmarkImages(Client(missing=True), (record('a', known=False),))
    assert not worker.submit_image('a', 0, regular.get_with_variant('a'))
    assert calls == []
    try:
        worker.start()
        assert worker.submit_image('a', 0, regular.get_with_variant('a'))
        assert worker.submit_image('a', 0, fallback.get_with_variant('a'))
        assert worker.submit_image('a', 0, regular.get_with_variant('a', variant='thumb'))
        wait_for(lambda: worker.status()['indexed'] == 3)
        assert worker.status()['error'] is None
    finally:
        worker.stop()
        regular.close()
        fallback.close()
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute('SELECT variant, model_sha, preprocessing_version, length(vector) '
                          'FROM candidate_embeddings ORDER BY variant').fetchall()
    assert {row[0] for row in rows} == {'view', 'thumb', 'thumb_fallback'}
    assert all(row[1] == digest and row[2] and row[3] == 4096 for row in rows)
    assert len([call for call in calls if call[0] == 'load']) == 1
    assert not arbiter._sessions
    assert store.current_generation() is None


@pytest.mark.parametrize('cuda_error', [False, True])
def test_worker_parks_on_shared_stop_or_cuda_failure_and_can_restart(tmp_path, monkeypatch, cuda_error):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    arbiter = GpuInferenceArbiter()
    entered, unblock = Event(), Event()

    def run(_provider, _tagging):
        if not entered.is_set():
            entered.set()
            assert unblock.wait(10)
            if cuda_error:
                raise RuntimeError('synthetic CUDA OOM')

    calls = fake_ort(monkeypatch, run=run)
    store = store_at(tmp_path / 'shadow')
    path = tmp_path / 'egara.onnx'
    path.write_bytes(b'synthetic egara')
    worker = VisualPreferenceWorker(store, path, model_sha=sha256(path.read_bytes()).hexdigest(), gpu_arbiter=arbiter)
    catalog = RemoteBookmarkImages(Client(), (record('a'),))
    image = catalog.get_with_variant('a')
    try:
        active = generation(store)
        worker.start()
        assert worker.submit_image('a', 0, image)
        assert entered.wait(10)
        if cuda_error:
            unblock.set()
        else:
            with ThreadPoolExecutor(1) as pool:
                releasing = pool.submit(arbiter.release_all)
                try:
                    with arbiter._condition:
                        assert arbiter._condition.wait_for(lambda: arbiter._stopping, 5)
                finally:
                    unblock.set()
                releasing.result(10)
        wait_for(lambda: worker.status()['phase'] == ('failed' if cuda_error else 'paused'))
        assert worker.status()['indexed'] == 0
        assert worker.status()['error'] == ('cuda_failure' if cuda_error else None)
        assert not store.is_current(active)
        assert not arbiter._sessions
        worker.start()
        assert worker.submit_image('a', 0, image)
        wait_for(lambda: worker.status()['indexed'] == 1)
        assert all(call[1][0] == 'CUDAExecutionProvider' for call in calls if call[0] == 'load')
    finally:
        unblock.set()
        worker.stop()
        catalog.close()


def test_idle_shared_release_requires_a_fresh_start(tmp_path, monkeypatch):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    calls = fake_ort(monkeypatch)
    arbiter = GpuInferenceArbiter()
    path = tmp_path / 'egara.onnx'
    path.write_bytes(b'synthetic egara')
    worker = VisualPreferenceWorker(store_at(tmp_path / 'shadow'), path,
                                    model_sha=sha256(path.read_bytes()).hexdigest(), gpu_arbiter=arbiter)
    catalog = RemoteBookmarkImages(Client(), (record('a'), record('b')))
    try:
        worker.start()
        assert worker.submit_image('a', 0, catalog.get_with_variant('a'))
        wait_for(lambda: worker.status()['indexed'] == 1)
        active = generation(worker.store)
        assert worker.store.publish(active, model(active).to_payload())
        arbiter.release_all()
        wait_for(lambda: not worker.store.is_current(active))
        assert not worker.submit_image('b', 0, catalog.get_with_variant('b'))
        wait_for(lambda: worker.status()['phase'] == 'paused')
        assert not arbiter._sessions
        assert len([item for item in calls if item[0] == 'load']) == 1
        worker.start()
        assert worker.submit_image('b', 0, catalog.get_with_variant('b'))
        wait_for(lambda: worker.status()['indexed'] == 2)
    finally:
        worker.stop()
        catalog.close()


def test_shared_release_crossing_shadow_commit_retires_the_pending_row(tmp_path, monkeypatch):
    from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker

    arbiter = GpuInferenceArbiter()
    fake_ort(monkeypatch)
    store = store_at(tmp_path / 'shadow')
    path = tmp_path / 'egara.onnx'
    path.write_bytes(b'synthetic egara')
    worker = VisualPreferenceWorker(store, path, model_sha=sha256(path.read_bytes()).hexdigest(), gpu_arbiter=arbiter)
    catalog = RemoteBookmarkImages(Client(), (record('a'),))
    entered, unblock = Event(), Event()
    connect = store._connect

    class Connection:
        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        def commit(self):
            if not entered.is_set():
                entered.set()
                assert unblock.wait(10)
            return self.db.commit()

    def connect_with_barrier(*, write, **kwargs):
        db = connect(write=write, **kwargs)
        return Connection(db) if write else db

    monkeypatch.setattr(store, '_connect', connect_with_barrier)
    try:
        worker.start()
        assert worker.submit_image('a', 0, catalog.get_with_variant('a'))
        assert entered.wait(10)
        arbiter.release_all()
        unblock.set()
        wait_for(lambda: worker.status()['phase'] == 'paused')
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute('SELECT count(*) FROM candidate_embeddings').fetchone() == (0,)
    finally:
        unblock.set()
        worker.stop()
        catalog.close()


def test_evaluation_aggregates_latest_rows_without_vectors(monkeypatch):
    from tools import evaluate_visual_preference as evaluator

    data = evaluator.synthetic_input()
    sample = data['training'][0]
    data['training'] = [dict(sample, judgment='', revision=2),
        dict(sample, vector=None, judgment='like', saved=True, revision=2),
        dict(sample, work_id='conflict', judgment='like', revision=2),
        dict(sample, work_id='conflict', vector=None, judgment='hate', reason='style', revision=2),
        dict(sample, work_id='superseded', judgment='like', revision=1),
        dict(sample, work_id='superseded', vector=None, judgment='skip', revision=2)]
    captured = []
    actual = evaluator.fit

    def fit(active, samples, **kwargs):
        captured.extend(samples)
        return actual(active, samples, **kwargs)

    monkeypatch.setattr(evaluator, 'fit', fit)
    evaluator.evaluate(data, bootstrap_samples=2)
    assert len(captured) == 2
    assert all(row.embedding.key.work_id == sample['work_id'] and row.judgment == 'like' and row.saved
               for row in captured)


def test_purge_does_not_revoke_before_ownership_is_proven(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    active = generation(store)
    original = VisualPreferenceStore._read_database_owner

    def unexpected_owner(instance):
        assert store.is_current(active)
        original(instance)
        return (-1, -1)

    monkeypatch.setattr(VisualPreferenceStore, '_read_database_owner', unexpected_owner)
    with pytest.raises(VisualPreferenceStoreError):
        VisualPreferenceStore.purge_owned(store.root, profile='test-profile', account='test-account', protected_paths=())
    assert store.is_current(active)


@pytest.mark.skipif(os.name != 'nt', reason='Windows generation readers use a separate lock range')
def test_recovery_purge_drains_current_reader_before_unlinking(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    active = generation(store)
    holding_reader, unblock = Event(), Event()
    original = store._validated_read

    @contextmanager
    def hold(*args, **kwargs):
        with original(*args, **kwargs) as db:
            holding_reader.set()
            assert unblock.wait(10)
            yield db

    monkeypatch.setattr(store, '_validated_read', hold)
    with ThreadPoolExecutor(2) as pool:
        reader = pool.submit(store.is_current, active)
        assert holding_reader.wait(5)
        sidecar = store.path.with_suffix('.sqlite3-journal')
        sidecar.write_bytes(b'synthetic recovery sidecar')
        purge = pool.submit(VisualPreferenceStore.purge_owned, store.root, profile='test-profile',
                            account='test-account', protected_paths=())
        try:
            wait_for(lambda: not store._fence_allows(active))
            assert sidecar.exists()
            assert not purge.done()
        finally:
            unblock.set()
        assert not reader.result(10)
        purge.result(10)
    assert not sidecar.exists()
    assert not store.path.exists()


@pytest.mark.parametrize('kind', ['tags', 'similarity'])
@pytest.mark.parametrize('resume_before_release', [False, True])
def test_resume_after_release_before_stopped_handler(kind, resume_before_release, monkeypatch):
    before_release, release, caught, unblock, second = Event(), Event(), Event(), Event(), Event()
    calls = []
    previous = DEFAULT_GPU_ARBITER.stop_scope

    @contextmanager
    def stop_scope():
        try:
            with previous():
                yield
        except GpuInferenceStopped:
            caught.set()
            assert unblock.wait(10)
            raise

    def operation():
        calls.append(1)
        if len(calls) == 1:
            with DEFAULT_GPU_ARBITER.acquire(operation):
                pass
            before_release.set()
            assert release.wait(10)
            DEFAULT_GPU_ARBITER.release_all()
            DEFAULT_GPU_ARBITER.check_scope()
        second.set()

    monkeypatch.setattr(DEFAULT_GPU_ARBITER, 'stop_scope', stop_scope)
    if kind == 'tags':
        tagger = SimpleNamespace(current_fingerprint=lambda: 'fingerprint')
        worker = LocalTagView(tagger, None, tuple, tuple)
        monkeypatch.setattr(worker, '_collect_entries', lambda *_: (operation(), {}))
        future = worker.refresh_async()
        resume, close = lambda: worker.refresh_async(resume=True), worker.close
    else:
        worker = SimilarityWorker(lambda: (operation(), ())[1], lambda *_: True, auto=lambda: False)
        worker.start()
        resume, close = worker.resume, worker.stop
    try:
        assert before_release.wait(5)
        if resume_before_release:
            resume()
        release.set()
        assert caught.wait(5)
        resumed = None if resume_before_release else resume()
        unblock.set()
        if resume_before_release:
            if kind == 'tags':
                future.result(3)
                assert worker._gpu_paused
            else:
                wait_for(lambda: worker.status()['phase'] != 'running')
                assert worker.status()['phase'] == 'paused'
            assert not second.is_set()
            return
        assert second.wait(3)
        if kind == 'tags':
            assert resumed is future
            resumed.result(3)
    finally:
        release.set()
        unblock.set()
        close()


def test_explicit_tagger_cuda_load_failure_requires_new_tagger(tmp_path, monkeypatch):
    calls = fake_ort(monkeypatch)
    runtime = sys.modules['onnxruntime']
    session = runtime.InferenceSession
    attempts = []

    def load(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('synthetic CUDA load OOM')
        return session(*args, **kwargs)

    monkeypatch.setattr(runtime, 'InferenceSession', load)
    arbiter = GpuInferenceArbiter()
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter, providers=providers)
    assert tagger.predict(b'image') == []
    arbiter.release_all()
    assert tagger.predict(b'image') == []
    assert len(attempts) == 1
    tagger.close()
    recreated = local_tagger.LocalTagger(tmp_path, gpu_arbiter=arbiter, providers=providers, tensor_factory=lambda x: x)
    try:
        assert recreated.predict(b'image') == [('synthetic', .97)]
        assert len(attempts) == 2
        assert all(call[1][0] == 'CUDAExecutionProvider' for call in calls if call[0] == 'load')
    finally:
        recreated.close()


def test_similarity_paused_resume_is_invalidated_by_a_later_shared_release():
    scanned = Event()

    def pages():
        scanned.set()
        return ()

    worker = SimilarityWorker(pages, lambda *_: True, auto=lambda: False)
    worker.pause()
    worker.start()
    try:
        with worker._lock:
            worker.resume()
            DEFAULT_GPU_ARBITER.release_all()
        wait_for(lambda: worker.status()['phase'] == 'paused')
        assert not scanned.is_set()
        worker.resume()
        assert scanned.wait(5)
    finally:
        worker.stop()
