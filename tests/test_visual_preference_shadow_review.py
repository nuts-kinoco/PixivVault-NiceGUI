import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from http.cookiejar import CookieJar
import json
from random import Random
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import discovery_ranking
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.fixtures import generate_demo_artworks
from pixivvault_nicegui.gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceArbiter
from pixivvault_nicegui.learning import Judgment
from pixivvault_nicegui.learning_service import LearningService
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.shared_image_cache import SharedImageCache
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from pixivvault_nicegui.visual_preference import EmbeddingKey
from pixivvault_nicegui.visual_preference_store import VisualPreferenceStoreError
from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker
from test_gpuarb_inference import fake_ort
from test_gpuarb_remote_media import Client, png, record
from test_managed_network_stop_transport import _client
from test_visual_preference_shadow import store_at, wait_for
from test_visual_preference_store import generation, model
from z_learning_support_20261118 import candidate, open_discovery_view


def block_revocation_replacement(store, monkeypatch):
    write = store._write_record

    def replace(path, data):
        if path in (store._revocation, store._revocation_recovery):
            raise PermissionError('synthetic replacement failure')
        return write(path, data)

    monkeypatch.setattr(store, '_write_record', replace)


def test_both_control_replacements_fail_while_fit_and_embedding_are_pending(tmp_path, monkeypatch):
    invalidator = store_at(tmp_path / 'shadow')
    fitter = store_at(invalidator.root)
    embeddings = store_at(invalidator.root)
    active = generation(invalidator)
    assert invalidator.publish(active, model(active).to_payload())
    epoch = embeddings.capture_embedding_epoch()
    key = EmbeddingKey('synthetic', 0, 'view', 'd' * 64, active.model_sha, active.preprocessing_version)
    block_revocation_replacement(invalidator, monkeypatch)
    captured, resume = Event(), Event()
    transaction = fitter._transaction

    def pause_before_write(**kwargs):
        if kwargs.get('write'):
            captured.set()
            assert resume.wait(10)
        return transaction(**kwargs)

    with monkeypatch.context() as patch, ThreadPoolExecutor(1) as pool:
        patch.setattr(fitter, '_transaction', pause_before_write)
        pending = pool.submit(generation, fitter)
        try:
            assert captured.wait(5)
            invalidator.invalidate('undo')
        finally:
            resume.set()
        with pytest.raises(VisualPreferenceStoreError):
            pending.result(5)
    reopened = store_at(invalidator.root)
    for handle in (embeddings, reopened):
        assert not handle.is_current(active)
        assert not handle.put_candidate_embedding(epoch, key, (1, 0))
    assert reopened.put_candidate_embedding(reopened.capture_embedding_epoch(), key, (1, 0))


@pytest.mark.parametrize('operation', ['publish', 'load_model', 'advance'])
def test_database_retirement_also_fences_returned_generation_results(tmp_path, monkeypatch, operation):
    revoker = store_at(tmp_path / 'shadow')
    caller = store_at(revoker.root)
    active = generation(caller)
    payload = model(active).to_payload()
    if operation == 'load_model':
        assert caller.publish(active, payload)
    block_revocation_replacement(revoker, monkeypatch)
    transaction = caller._transaction
    committed, resume = Event(), Event()

    @contextmanager
    def pause_after_transaction(**kwargs):
        with transaction(**kwargs) as db:
            yield db
        committed.set()
        assert resume.wait(5)

    monkeypatch.setattr(caller, '_transaction', pause_after_transaction)
    actions = dict(publish=lambda: caller.publish(active, payload), load_model=lambda: caller.load_model(active),
                   advance=lambda: generation(caller))
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(actions[operation])
        try:
            assert committed.wait(5)
            revoker.invalidate('undo')
        finally:
            resume.set()
        if operation == 'advance':
            with pytest.raises(VisualPreferenceStoreError):
                pending.result(5)
        else:
            assert not pending.result(5)


def test_rejected_embedding_seal_survives_a_failed_cleanup(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    active = generation(store)
    epoch = store.capture_embedding_epoch()
    key = EmbeddingKey('synthetic', 0, 'view', 'd' * 64, active.model_sha, active.preprocessing_version)
    block_revocation_replacement(store, monkeypatch)

    def interrupted_cleanup(*_):
        raise OSError('synthetic cleanup failure')

    monkeypatch.setattr(store, '_remove_unaccepted_embedding', interrupted_cleanup)
    running = iter((True, False))
    with pytest.raises(VisualPreferenceStoreError):
        store.put_candidate_embedding(epoch, key, (1, 0), is_current=lambda: next(running))
    assert store._read_intent()['pending_embedding']['rejected']
    reopened = store_at(store.root)
    assert not reopened.has_candidate_embedding(key)


def test_normal_revocation_cannot_split_job_fence_capture(tmp_path, monkeypatch):
    revoker = store_at(tmp_path / 'shadow')
    fitter = store_at(revoker.root)
    captured, resume = Event(), Event()
    read_number = fitter._generation_number

    def pause(db):
        number = read_number(db)
        captured.set()
        assert resume.wait(5)
        return number

    monkeypatch.setattr(fitter, '_generation_number', pause)
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(generation, fitter)
        try:
            assert captured.wait(5)
            revoker.invalidate('undo')
        finally:
            resume.set()
        with pytest.raises(VisualPreferenceStoreError):
            job.result(5)


def test_queued_image_cannot_repopulate_visual_data_after_purge(tmp_path, monkeypatch):
    entered, resume = Event(), Event()

    def infer(*_):
        if not entered.is_set():
            entered.set()
            assert resume.wait(10)

    fake_ort(monkeypatch, run=infer)
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'synthetic model')
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, path, model_sha=sha256(path.read_bytes()).hexdigest(),
                                    gpu_arbiter=GpuInferenceArbiter())
    works = ('running', 'queued', 'new')
    catalog = RemoteBookmarkImages(Client(), tuple(record(work) for work in works))
    try:
        worker.start()
        assert worker.submit_image('running', 0, catalog.get_with_variant('running'))
        assert entered.wait(5)
        assert worker.submit_image('queued', 0, catalog.get_with_variant('queued'))
        store_at(store.root).purge()
        assert worker.submit_image('new', 0, catalog.get_with_variant('new'))
        resume.set()
        wait_for(lambda: worker.status()['indexed'] >= 1)
        worker.stop()
        with store._transaction() as db:
            assert db.execute('SELECT work_id FROM candidate_embeddings').fetchall() == [('new',)]
    finally:
        resume.set()
        worker.stop()
        catalog.close()


def test_undo_during_worker_fence_capture_only_skips_that_image(tmp_path, monkeypatch):
    fake_ort(monkeypatch)
    store = store_at(tmp_path / 'shadow')
    revoker = store_at(store.root)
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'synthetic model')
    worker = VisualPreferenceWorker(store, path, model_sha=sha256(path.read_bytes()).hexdigest(),
                                    gpu_arbiter=GpuInferenceArbiter())
    catalog = RemoteBookmarkImages(Client(), (record('first'), record('next')))
    captured, resume = Event(), Event()
    read_number = store._generation_number

    def pause_one_capture(db):
        number = read_number(db)
        if not captured.is_set():
            captured.set()
            assert resume.wait(10)
        return number

    monkeypatch.setattr(store, '_generation_number', pause_one_capture)
    try:
        worker.start()
        assert worker.submit_image('first', 0, catalog.get_with_variant('first'))
        assert captured.wait(5)
        assert worker.submit_image('next', 0, catalog.get_with_variant('next'))
        revoker.invalidate('undo')
        resume.set()
        wait_for(lambda: worker.status()['indexed'] == 1 or worker.status()['phase'] == 'failed')
        assert worker.status()['phase'] == 'running'
        with store._transaction() as db:
            assert db.execute('SELECT work_id FROM candidate_embeddings').fetchall() == [('next',)]
    finally:
        resume.set()
        worker.stop()
        catalog.close()


def test_shadow_stop_cancels_waiter_without_releasing_another_owner(tmp_path, monkeypatch):
    fake_ort(monkeypatch)
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'synthetic model')
    arbiter = GpuInferenceArbiter()
    worker = VisualPreferenceWorker(store_at(tmp_path / 'shadow'), path,
        model_sha=sha256(path.read_bytes()).hexdigest(), gpu_arbiter=arbiter)
    catalog = RemoteBookmarkImages(Client(), (record('synthetic'),))
    try:
        worker.start()
        with arbiter.acquire(object(), priority=10) as lease:
            assert worker.submit_image('synthetic', 0, catalog.get_with_variant('synthetic'))
            with arbiter._condition:
                assert arbiter._condition.wait_for(lambda: bool(arbiter._waiting), 5)
            worker.stop(timeout=.5)
            assert not lease.is_set()
        assert worker.status()['phase'] == 'stopped'
        worker.start()
    finally:
        worker.stop()
        catalog.close()


def test_idle_release_publishes_fence_before_return_without_worker_wakeup(tmp_path):
    arbiter = GpuInferenceArbiter()
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx', gpu_arbiter=arbiter)
    worker.start()
    try:
        active = generation(store)
        assert store.publish(active, model(active).to_payload())
        other = store_at(store.root)
        # Hold the state lock so the idle thread can never supply the fence.
        with worker._condition:
            arbiter.release_all()
            assert not other.is_current(active)
    finally:
        worker.stop()


def test_idle_similarity_resume_replaces_pending_older_resume():
    scanned = Event()
    worker = SimilarityWorker(lambda: (scanned.set(), ())[1], lambda *_: True, auto=lambda: False)
    worker.pause()
    worker.start()
    try:
        with worker._lock:
            worker.resume()
            DEFAULT_GPU_ARBITER.release_all()
            worker.resume()
        assert scanned.wait(1)
    finally:
        worker.stop()


def test_slow_event_append_does_not_block_learning_fifo(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    service = LearningService(tmp_path / 'learning', generate_demo_artworks(2), shadow_observer=worker)
    entered, resume = Event(), Event()
    append = store.append_event

    def slow_append(*args):
        entered.set()
        assert resume.wait(10)
        return append(*args)

    monkeypatch.setattr(store, 'append_event', slow_append)

    async def exercise():
        session = (await service.open_session()).session_id
        receipt = service.submit_record(session, Judgment(service.artworks[0].work_id, 'like', 'one'))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert await asyncio.wait_for(service.judged_count(), 1) == 1
        finally:
            resume.set()
            await service._wait(receipt)

    try:
        asyncio.run(exercise())
    finally:
        service.close()
    worker.flush_events()
    assert [event.kind for event in store.events()] == ['judgment']


def test_in_place_purge_discards_events_waiting_to_append(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    entered, resume = Event(), Event()
    append = store.append_event

    def pause(*args, **kwargs):
        entered.set()
        assert resume.wait(10)
        return append(*args, **kwargs)

    monkeypatch.setattr(store, 'append_event', pause)
    worker.ranking_finalized(lambda: [{'work_id': 'before'}])
    try:
        assert entered.wait(5)
        store_at(store.root).purge()
    finally:
        resume.set()
        worker.flush_events()
    assert store.events() == ()
    worker.ranking_finalized(lambda: [{'work_id': 'after'}])
    worker.flush_events()
    assert len(store.events()) == 1


def test_event_drain_reports_a_final_unwritten_gap(tmp_path, monkeypatch):
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')

    def fail(*args, **kwargs):
        raise OSError('synthetic write failure')

    with monkeypatch.context() as patch:
        patch.setattr(store, 'append_event', fail)
        worker.ranking_finalized(lambda: [{'work_id': 'lost'}])
        with pytest.raises(RuntimeError, match='gap'):
            worker.flush_events()
    worker.ranking_finalized(lambda: [{'work_id': 'kept'}])
    worker.flush_events()
    assert [event.kind for event in store.events()] == ['capture_gap', 'ranking']


async def test_fetch_records_only_its_final_ranking(tmp_path, monkeypatch):
    from pixivvault_nicegui import discovery_engine

    observed = []
    observer = SimpleNamespace(ranking_finalized=lambda snapshot: observed.append(snapshot()))
    service = DiscoveryLearningService(tmp_path / 'learning', SimpleNamespace(require_identity=lambda: None),
                                       following=(), shadow_observer=observer)
    monkeypatch.setattr(discovery_engine, 'get_next_batch', lambda **_: SimpleNamespace(
        items=(candidate(1), candidate(2)), ok=('following',), failed=()))
    try:
        batch = await service.fetch_candidates()
        assert len(observed) == 1
        assert [row['work_id'] for row in observed[0]] == [a.work_id for a in batch.artworks]
    finally:
        service.close()


def test_nullable_labels_are_not_conflicts_and_exclusions_are_counted():
    from tools import evaluate_visual_preference as evaluator

    data = evaluator.synthetic_input()
    row = data['training'][0]
    data['training'] = [dict(row, work_id='null', judgment=None, reason=None, saved=False),
        dict(row, work_id='saved', judgment=None, reason=None, saved=True),
        dict(row, work_id='no-reason', judgment='hate', reason=None),
        dict(row, work_id='conflict', judgment='like'),
        dict(row, work_id='conflict', vector=None, judgment='hate', reason='style'),
        dict(row, work_id='resolved', judgment='hate', reason=None),
        dict(row, work_id='resolved', vector=None, judgment='hate', reason='style')]
    counts = evaluator.evaluate(data, bootstrap_samples=2)['training_exclusions']
    assert counts == {'unjudged': 2, 'missing_hate_reason': 1, 'conflict': 1,
                      'invalid': 0, 'missing_vector': 0}


async def test_learning_view_prefetch_follows_live_observer_on_same_pin(tmp_path, monkeypatch):
    fake_ort(monkeypatch)
    path = tmp_path / 'model.onnx'
    path.write_bytes(b'synthetic model')
    worker = VisualPreferenceWorker(store_at(tmp_path / 'shadow'), path,
        model_sha=sha256(path.read_bytes()).hexdigest(), gpu_arbiter=GpuInferenceArbiter())
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=3)
    works = tuple(a.work_id for a in view.state.artworks)
    produced = png()
    calls = []

    def transport(request, *_):
        calls.append((request.get_method(), request.full_url))
        if '/pages' in request.full_url:
            return json.dumps({'error': False, 'body': [{'urls': {
                'regular': 'https://i.pximg.net/synthetic-regular.png'}}]}).encode()
        return produced

    client = _client(ManagedNetworkStopGate(tmp_path / 'gate.sqlite3'), transport)
    client._cookies = CookieJar()
    settings = {'discovery_cache_limit_mode': 'count', 'discovery_cache_size_count': '1'}
    services.preferences = SimpleNamespace(snapshot=lambda: SimpleNamespace(values=settings))
    old_pin = view._discovery_pin
    worker.start()
    try:
        for mixed in (False, True):
            calls.clear()
            cache = SharedImageCache(max_bytes=4 * len(produced))
            registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
            pin = registry.acquire_catalog(RemoteBookmarkImages(client, tuple(record(w) for w in works)))
            view._images = view._discovery_pin = pin
            services.images = registry
            try:
                for count in (1, 2, 3):
                    service.shadow_observer = worker if mixed and count == 2 else None
                    settings['discovery_cache_size_count'] = str(count)
                    with view.client:
                        view._start_prefetch()
                    await view._prefetch_task
                    if count == 1:
                        same_prefetch = view._prefetch
                    assert view._prefetch is same_prefetch
                assert len(calls) == 6
                if mixed:
                    assert calls == baseline
                    deadline = asyncio.get_running_loop().time() + 5
                    while worker.status()['indexed'] != 1:
                        assert asyncio.get_running_loop().time() < deadline
                        await asyncio.sleep(.01)
                else:
                    baseline = list(calls)
            finally:
                view._stop_prefetch(close=True)
                pin.close()
                registry.close()
                cache.close()
        with worker.store._transaction() as db:
            assert db.execute('SELECT work_id FROM candidate_embeddings').fetchall() == [(works[1],)]
    finally:
        view._images = view._discovery_pin = old_pin
        worker.stop()
        await cleanup()
        client.close()


async def test_shadow_exposure_does_not_touch_shared_lru(tmp_path, monkeypatch):
    view, _, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=3)
    store = store_at(tmp_path / 'shadow')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    client = Client()
    works = tuple(a.work_id for a in view.state.artworks)
    cache = SharedImageCache(max_bytes=2 * len(png()))
    registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
    remote = RemoteBookmarkImages(client, tuple(record(w) for w in works))
    pin = registry.acquire_catalog(remote)
    original = view._discovery_pin
    try:
        view._images = view._discovery_pin = pin
        service.shadow_observer = worker
        registry.get_versioned(pin.token, works[0], 0, 'view')
        registry.get_versioned(pin.token, works[1], 0, 'view')
        with view.client:
            view.request_image()
            view._on_loaded(SimpleNamespace(args=view._image_url))
        assert client.calls == ['pages', 'regular'] * 2
        registry.get_versioned(pin.token, works[2], 0, 'view')
        assert remote.peek_with_variant(works[0], 0, 'view') is None
        assert remote.peek_with_variant(works[1], 0, 'view') is not None
        await service._wait(service._submit(lambda: None))
        worker.flush_events()
        assert [event.kind for event in store.events()] == ['exposure']
    finally:
        view._images = view._discovery_pin = original
        await cleanup()
        pin.close()
        registry.close()
        cache.close()


def rank_parity_digest(rank):
    rand = Random(20261122)
    tags = ['shape', 'terrain', 'warm', 'blue', 'unknown']
    authors = [str(i) for i in range(1, 9)]
    outputs = []
    for case in range(192):
        works = [{'work_id': str(100 + i), 'author_id': rand.choice(authors),
                  'tags': rand.sample(tags, rand.randrange(4))} for i in range(case % 36)]
        rand.shuffle(works)
        prefs = {tag: {'like': rand.randrange(18), 'skip': rand.randrange(12)}
                 for tag in tags[:-1]} if case % 2 else {}
        auth = {a: {'like': rand.randrange(10), 'hate_style': rand.randrange(6)}
                for a in authors} if case % 3 else {}
        options = dict(total_works=case * 3 or None, limit=(None, 0, 7, 18)[case % 4],
                       recent_author_ids=tuple(rand.choices(authors, k=case % 12)))
        ordered, frames = rank(works, prefs, auth, **options)[:2]
        outputs.append(([work['work_id'] for work in ordered], frames))
    return sha256(json.dumps(outputs, sort_keys=True).encode('ascii')).hexdigest()


def test_ranker_matches_a466487_on_192_reproducible_inputs():
    # Fingerprint derived from git show a466487:src/pixivvault_nicegui/discovery_ranking.py.
    expected = '2a262d5b30ff47e52d2312bf777413bf8e30972b992942809d63dae746c05fd0'
    assert rank_parity_digest(discovery_ranking.rank_candidates) == expected
    assert rank_parity_digest(discovery_ranking.rank_candidates_with_diagnostics) == expected
