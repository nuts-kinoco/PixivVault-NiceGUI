from concurrent.futures import ThreadPoolExecutor
from threading import Event, RLock
from types import SimpleNamespace
from weakref import ref

import pytest

from pixivvault_nicegui import egara_inference, local_tagger
from pixivvault_nicegui.gpu_arbiter import DEFAULT_GPU_ARBITER, GpuInferenceArbiter, GpuInferenceStopped
from pixivvault_nicegui.local_tag_view import LocalTagView
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from test_gpuarb_inference import fake_ort, make_egara, make_tagger


def test_priority_has_a_bounded_overtake_budget():
    arbiter = GpuInferenceArbiter()
    grants = []
    owner = object()

    def request(name, priority):
        with arbiter.acquire(owner, priority=priority):
            grants.append(name)

    with ThreadPoolExecutor(13) as pool:
        with arbiter.acquire(owner):
            low = pool.submit(request, 'low', 0)
            with arbiter._condition:
                assert arbiter._condition.wait_for(lambda: len(arbiter._waiting) == 1, 5)
            high = [pool.submit(request, f'high-{i}', 10) for i in range(12)]
            with arbiter._condition:
                assert arbiter._condition.wait_for(lambda: len(arbiter._waiting) == 13, 5)
        for future in [low, *high]:
            future.result(5)
    assert grants[0].startswith('high')
    assert 0 < grants.index('low') <= 8


def test_arbiter_reports_all_failed_releases_and_attempts_all_owners():
    arbiter = GpuInferenceArbiter()
    attempted = []

    class Owner:
        def __init__(self, fails):
            self.fails = fails

        def release_session(self):
            attempted.append(self)
            if self.fails:
                raise ValueError('synthetic release failure')

    owners = [Owner(True), Owner(True), Owner(False)]
    for owner in owners:
        with arbiter.acquire(owner):
            arbiter.retain_session(owner)
    with pytest.raises(ExceptionGroup) as error:
        arbiter.release_all()
    assert len(error.value.exceptions) == 2
    assert set(attempted) == set(owners)
    attempted.clear()
    for owner in owners:
        owner.fails = False
    assert arbiter.release_idle()
    assert set(attempted) == set(owners[:2])


def test_tagger_recovers_after_transient_cuda_run_error(monkeypatch, tmp_path):
    attempts = []

    def run(*_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('synthetic CUDA allocation failure')

    fake_ort(monkeypatch, run=run)
    arbiter = GpuInferenceArbiter()
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    assert tagger.predict(b'image') == []
    assert tagger.session is None
    arbiter.release_all()
    assert tagger.predict(b'image') == [('synthetic', 0.97)]
    assert len(attempts) == 2


@pytest.mark.parametrize('model', ['egara', 'tagger'])
def test_auto_cpu_only_never_takes_gpu_lease(monkeypatch, tmp_path, model):
    arbiter = GpuInferenceArbiter()
    fake_ort(monkeypatch, available=['CPUExecutionProvider'])
    if model == 'egara':
        infer = make_egara(tmp_path, gpu_arbiter=arbiter).infer
    else:
        infer = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter).predict

    def unexpected(*_args, **_kwargs):
        pytest.fail('CPU inference requested a GPU lease')

    monkeypatch.setattr(arbiter, 'acquire', unexpected)
    assert infer(b'image')
    assert infer(b'next image')


def test_egara_cpu_fallback_runs_outside_cuda_lease(monkeypatch, tmp_path):
    arbiter = GpuInferenceArbiter()
    cpu_leases = []

    def run(provider, _):
        if provider == 'CUDAExecutionProvider':
            raise RuntimeError('synthetic CUDA fault')
        cpu_leases.append(arbiter._active)

    fake_ort(monkeypatch, run=run)
    egara = make_egara(tmp_path, gpu_arbiter=arbiter)
    assert egara.infer_with_meta(b'image')[1] == 'cpu'
    assert egara.infer_with_meta(b'next image')[1] == 'cpu'
    assert cpu_leases == [None, None]


def test_preprocess_and_tagger_verification_and_cache_run_without_lease(monkeypatch, tmp_path):
    arbiter = GpuInferenceArbiter()
    fake_ort(monkeypatch)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    egara = make_egara(tmp_path, provider='cuda', gpu_arbiter=arbiter)
    observed = []

    for module, name in [(egara_inference, 'preprocess'), (local_tagger, 'verify_model'),
                         (local_tagger, 'prepare_image')]:
        original = getattr(module, name)

        def observed_call(*args, _original=original, **kwargs):
            observed.append(arbiter._active)
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, observed_call)
    egara.infer(b'image')
    assert tagger.predict(b'image')
    arbiter.release_all()
    assert tagger.predict(b'image')
    assert tagger.session is None
    assert observed and all(lease is None for lease in observed)


@pytest.mark.parametrize('explicit', [False, True])
def test_tagger_preloads_before_provider_check_and_fallback_is_auto_only(monkeypatch, tmp_path, explicit):
    import sys

    def preload():
        order.append('preload')
        raise RuntimeError('synthetic missing CUDA DLL')

    order = []
    calls = fake_ort(monkeypatch, preload=preload)
    ort = sys.modules['onnxruntime']
    original = ort.get_available_providers

    def available():
        order.append('providers')
        return original()

    monkeypatch.setattr(ort, 'get_available_providers', available)
    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if explicit else None
    tagger = make_tagger(monkeypatch, tmp_path, providers=providers)
    assert bool(tagger.predict(b'image')) is not explicit
    assert order[0] == 'preload'
    assert (('load', 'CPUExecutionProvider') in calls) is not explicit


def test_failed_egara_validation_does_not_retain_session_in_error(monkeypatch, tmp_path):
    import sys

    fake_ort(monkeypatch)
    ort = sys.modules['onnxruntime']
    original = ort.InferenceSession
    sessions = []

    class Session(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(ref(self))

        def get_inputs(self):
            raise RuntimeError('synthetic invalid session metadata')

    monkeypatch.setattr(ort, 'InferenceSession', Session)
    egara = make_egara(tmp_path, provider='cuda')
    with pytest.raises(egara_inference.EgaraBackendUnavailable) as error:
        egara.ensure()
    assert error.value is not None
    assert len(sessions) == 1 and sessions[0]() is None


def test_similarity_worker_parks_without_counting_cooperative_stop():
    called = []

    def analyze(*args, **kwargs):
        called.append(1)
        raise GpuInferenceStopped()

    worker = SimilarityWorker(lambda: [('synthetic', 1), ('synthetic', 2)], analyze)
    worker._phase = 'running'
    worker._scan_once(0)
    assert not issubclass(GpuInferenceStopped, RuntimeError)
    assert called == [1]
    assert worker.status()['phase'] == 'paused'
    assert worker.status()['errors'] == 0


def test_local_tag_view_aborts_scan_without_publishing_partial_result():
    tagger = SimpleNamespace(current_fingerprint=lambda: 'synthetic')
    view = LocalTagView(tagger, lambda _: b'image', lambda: (), lambda: ())
    calls = []
    try:
        view._eligible = lambda: ['a', 'b']

        def stop(*args):
            calls.append(args[0])
            raise GpuInferenceStopped()

        view._refresh_entry = stop
        view.refresh_now()
        assert len(calls) == 1
        assert view.snapshot().fingerprint is None
    finally:
        view.close()


def test_tag_scan_with_no_missing_images_keeps_gpu_session_released(monkeypatch, tmp_path):
    calls = fake_ort(monkeypatch)
    tagger = make_tagger(monkeypatch, tmp_path)
    preferences = SimpleNamespace(
        subscribe_local_tagger_settings=lambda _: lambda: None,
        snapshot=lambda: SimpleNamespace(values={
            'local_tagger_path': str(tmp_path), 'local_tagger_enabled': '1',
        }),
    )
    manager = local_tagger.TaggerManager(preferences)
    manager.tagger, manager._provider = tagger, 'auto'
    view = LocalTagView(manager, lambda _: None, lambda: (), lambda: ())
    try:
        assert manager.current_fingerprint() is not None
        DEFAULT_GPU_ARBITER.release_all()
        assert tagger.session is None
        view.refresh_now()
        assert [op for op, _ in calls].count('load') == 1
        assert tagger.session is None
    finally:
        view.close()
        manager.close()


def test_idle_release_cannot_interrupt_initial_tagger_verification(monkeypatch, tmp_path):
    arbiter = GpuInferenceArbiter()
    fake_ort(monkeypatch)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    acquire = arbiter.acquire
    trials = []

    def interrupt_before_prediction(owner, **kwargs):
        trials.append(None)
        if len(trials) == 2:
            trials[-1] = arbiter.release_idle()
        return acquire(owner, **kwargs)

    monkeypatch.setattr(arbiter, 'acquire', interrupt_before_prediction)
    assert tagger.predict(b'image') == [('synthetic', 0.97)]
    assert trials == [None, False]
    assert arbiter.release_idle()


@pytest.mark.parametrize('worker_kind', ['tagger', 'similarity'])
def test_cooperative_stop_between_pages_prevents_new_gpu_work(worker_kind):
    entered, resume = Event(), Event()
    processed = []
    owner = object()

    def analyze(work, *_args, **_kwargs):
        if work == 'b':
            entered.set()
            assert resume.wait(5)
        with DEFAULT_GPU_ARBITER.acquire(owner):
            processed.append(work)
        return True

    if worker_kind == 'similarity':
        worker = SimilarityWorker(lambda: [('a', 1), ('b', 1)], analyze)
        worker._phase = 'running'
        scan = lambda: worker._scan_once(0)
    else:
        tagger = SimpleNamespace(current_fingerprint=lambda: 'synthetic')
        worker = LocalTagView(tagger, lambda _: None, lambda: (), lambda: ())
        worker._eligible = lambda: ['a', 'b']

        def refresh_entry(wid, _fingerprint, _old):
            analyze(wid)
            return None

        worker._refresh_entry = refresh_entry
        scan = worker.refresh_now

    try:
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(scan)
            try:
                assert entered.wait(5)
                DEFAULT_GPU_ARBITER.release_all()
            finally:
                resume.set()
            running.result(5)
        assert processed == ['a']
        if worker_kind == 'similarity':
            assert worker.status()['phase'] == 'paused'
            assert worker.status()['errors'] == 0
        else:
            completed = worker._future
            assert worker.refresh_async() is completed
    finally:
        if worker_kind == 'tagger':
            worker.close()


@pytest.mark.parametrize('warm', [False, True])
def test_tagger_rejects_model_replaced_while_queued(monkeypatch, tmp_path, warm):
    from test_ak_tagger_20261119 import make_model

    make_model(tmp_path)
    arbiter = GpuInferenceArbiter()
    native_runs = []
    calls = fake_ort(monkeypatch, run=lambda *_: native_runs.append(1))
    monkeypatch.setattr(local_tagger.LocalTagger, '_runtime_available', lambda _: True)
    monkeypatch.setattr(local_tagger, 'prepare_image', lambda _: object())
    tagger = local_tagger.LocalTagger(tmp_path, gpu_arbiter=arbiter, tensor_factory=lambda image: image)
    if warm:
        assert tagger.verify() == '利用可能'
    with ThreadPoolExecutor(1) as pool:
        with arbiter.acquire(object()):
            prediction = pool.submit(tagger.predict, b'image')
            with arbiter._condition:
                assert arbiter._condition.wait_for(lambda: len(arbiter._waiting) == 1, 5)
            (tmp_path / 'model.onnx').write_bytes(b'synthetic replacement of different size')
        assert prediction.result(5) == []
    assert native_runs == []
    assert [op for op, _ in calls].count('load') == int(warm)


def test_tagger_manager_cpu_retry_survives_gpu_stop(monkeypatch, tmp_path):
    import sys

    arbiter = GpuInferenceArbiter()

    def run(provider, _):
        assert provider == 'CPUExecutionProvider'
        assert arbiter._active is None
        arbiter.release_all()

    fake_ort(monkeypatch, run=run)
    ort = sys.modules['onnxruntime']
    original = ort.InferenceSession

    def unavailable_cuda(*args, **kwargs):
        if not isinstance(kwargs['providers'][0], str):
            raise RuntimeError('synthetic CUDA load failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(ort, 'InferenceSession', unavailable_cuda)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    preferences = SimpleNamespace(
        subscribe_local_tagger_settings=lambda _: lambda: None,
        snapshot=lambda: SimpleNamespace(values={
            'local_tagger_path': str(tmp_path), 'local_tagger_enabled': '1',
        }),
    )
    manager = local_tagger.TaggerManager(preferences)
    manager.tagger, manager._provider = tagger, 'auto'
    try:
        assert manager.predict(b'image') == [('synthetic', 0.97)]
        assert manager.predict(b'next image') == [('synthetic', 0.97)]
    finally:
        manager.close()


def test_metadata_only_scan_does_not_publish_unverified_backend(monkeypatch, tmp_path):
    fake_ort(monkeypatch, available=['CPUExecutionProvider'])
    tagger = make_tagger(monkeypatch, tmp_path, providers=['CUDAExecutionProvider'])
    preferences = SimpleNamespace(
        subscribe_local_tagger_settings=lambda _: lambda: None,
        snapshot=lambda: SimpleNamespace(values={
            'local_tagger_path': str(tmp_path), 'local_tagger_enabled': '1',
        }),
    )
    manager = local_tagger.TaggerManager(preferences, provider=lambda: 'cuda')
    manager.tagger, manager._provider = tagger, 'cuda'
    view = LocalTagView(manager, lambda _: None, lambda: (), lambda: ())
    try:
        view.refresh_now()
        assert not manager.usable
        assert manager.status != '利用可能'
        assert manager.try_current_fingerprint() is None
    finally:
        view.close()
        manager.close()


def test_cooperatively_paused_view_still_revalidates_published_metadata():
    waits = iter([False, True])
    calls = []
    worker = SimpleNamespace(
        _stop=SimpleNamespace(wait=lambda _: next(waits)),
        _lock=RLock(), _running=False, _gpu_paused=True,
        _revalidate_published=lambda: calls.append('metadata'),
        refresh_async=lambda: calls.append('refresh'),
    )
    LocalTagView._periodic(worker)
    assert calls == ['metadata']


def test_stop_before_projection_publication_discards_pending_rescan():
    entered, resume = Event(), Event()
    owner = object()
    tagger = SimpleNamespace(current_fingerprint=lambda: 'synthetic')
    view = LocalTagView(tagger, lambda _: None, lambda: (), lambda: ())
    view._eligible = lambda: ['a']
    project = view._project
    works = []

    def refresh_entry(wid, fingerprint, _old):
        with DEFAULT_GPU_ARBITER.acquire(owner):
            works.append(wid)
        return fingerprint, b'synthetic', ('tag',)

    def blocked_projection(entries):
        entered.set()
        assert resume.wait(5)
        return project(entries)

    view._refresh_entry = refresh_entry
    view._project = blocked_projection
    try:
        first = view.refresh_async()
        try:
            assert entered.wait(5)
            assert view.refresh_async() is first
            DEFAULT_GPU_ARBITER.release_all()
        finally:
            resume.set()
        first.result(5)
        assert works == ['a']
        assert not view.snapshot().by_work
    finally:
        view.close()
