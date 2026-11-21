from array import array
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event
from types import SimpleNamespace
import sys

import pytest

from pixivvault_nicegui import egara_inference, local_tagger


def make_egara(tmp_path, **options):
    path = tmp_path / 'egara.onnx'
    path.write_bytes(b'synthetic egara')
    return egara_inference.EgaraInference(
        path, expected_sha256=sha256(path.read_bytes()).hexdigest(), **options)


def fake_ort(monkeypatch, *, run=None, available=None, preload=None):
    calls = []
    cuda, cpu = 'CUDAExecutionProvider', 'CPUExecutionProvider'

    class Session:
        def __init__(self, path, options=None, *, providers):
            self.provider = providers[0]
            name = self.provider if isinstance(self.provider, str) else self.provider[0]
            self.name = name
            self.tagging = path.endswith('model.onnx')
            calls.append(('load', self.provider))

        def get_providers(self):
            return [self.name]

        def disable_fallback(self):
            calls.append(('disable', self.name))

        def get_inputs(self):
            return [SimpleNamespace(name='image')]

        def get_outputs(self):
            return [SimpleNamespace(name='style_embedding'),
                    SimpleNamespace(name=egara_inference.BACKBONE)]

        def run(self, *_):
            if run is not None:
                run(self.name, self.tagging)
            return [[(0.97,)]] if self.tagging else object()

    def preload_dlls(*, directory):
        calls.append(('preload', directory))
        if preload is not None:
            preload()

    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(
        get_available_providers=lambda: [cuda, cpu] if available is None else available,
        preload_dlls=preload_dlls,
        SessionOptions=SimpleNamespace, InferenceSession=Session))
    monkeypatch.setattr(egara_inference, 'preprocess', lambda _: object())
    vector = SimpleNamespace(astype=lambda *_args, **_kwargs: array('f', [1.0] * 1024))
    monkeypatch.setattr(egara_inference, 'unpack_outputs',
                        lambda *_: {'style': vector})
    return calls


def make_tagger(monkeypatch, tmp_path, **options):
    (tmp_path / 'selected_tags.csv').write_text('name,category\nsynthetic,0\n', encoding='ascii')
    monkeypatch.setattr(local_tagger, 'verify_model', lambda *args, **kwargs: ('利用可能', {'verified': True}))
    monkeypatch.setattr(local_tagger.LocalTagger, '_runtime_available', lambda _: True)
    monkeypatch.setattr(local_tagger, 'prepare_image', lambda _: object())
    return local_tagger.LocalTagger(tmp_path, tensor_factory=lambda x: x, **options)


@pytest.mark.parametrize(('provider', 'fallback', 'succeeds'), [
    ('cuda', True, False), ('auto', False, False), ('auto', True, True),
])
def test_cuda_runtime_failure_releases_without_implicit_cpu(
        monkeypatch, tmp_path, provider, fallback, succeeds):
    def run(current, _):
        if current == 'CUDAExecutionProvider':
            raise RuntimeError('synthetic CUDA fault')

    calls = fake_ort(monkeypatch, run=run)
    egara = make_egara(tmp_path, provider=provider,
                       gpu_mem_limit_bytes=123456, allow_runtime_fallback=fallback)
    if succeeds:
        vectors, actual = egara.infer_with_meta(b'image')
        assert len(vectors['style']) == 4096
        assert actual == 'cpu'
        assert egara.fallback_reason == 'RuntimeError'
    else:
        with pytest.raises(egara_inference.EgaraInferenceError):
            egara.infer(b'image')
        assert egara.provider_active is None
    assert ('preload', '') in calls
    cuda_load = next(arg for op, arg in calls if op == 'load')
    assert cuda_load[1]['gpu_mem_limit'] == '123456'
    assert cuda_load[1]['use_tf32'] == '0'
    assert (('load', 'CPUExecutionProvider') in calls) == succeeds


def test_auto_still_falls_back_at_load_and_cpu_release_is_reloadable(monkeypatch, tmp_path):
    calls = fake_ort(monkeypatch, available=['CPUExecutionProvider'])
    egara = make_egara(tmp_path)
    assert len(egara.infer(b'image')['style']) == 4096
    assert egara.provider_active == 'cpu'
    egara.release_session()
    assert egara.provider_active is None
    assert egara.ensure() is egara
    assert calls.count(('load', 'CPUExecutionProvider')) == 2
    egara.close()
    egara.release_session()
    with pytest.raises(egara_inference.EgaraBackendUnavailable):
        egara.ensure()


def test_tagger_preloads_bounds_cuda_and_reloads(monkeypatch, tmp_path):
    calls = fake_ort(monkeypatch)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_mem_limit_bytes=567890)
    assert tagger.predict(b'image') == [('synthetic', 0.97)]
    assert calls[:2] == [('preload', ''), ('load', ('CUDAExecutionProvider', {'gpu_mem_limit': '567890'}))]
    first = tagger.session
    tagger.release_session()
    assert tagger.session is None
    assert tagger.predict(b'image') == [('synthetic', 0.97)]
    assert tagger.session is not first
    tagger.close()
    tagger.release_session()
    assert tagger.predict(b'image') == []


def test_explicit_cpu_does_not_preload_or_require_cuda(monkeypatch, tmp_path):
    calls = fake_ort(monkeypatch, available=['CPUExecutionProvider'])
    egara = make_egara(tmp_path, provider='cpu')
    tagger = make_tagger(monkeypatch, tmp_path, providers=['CPUExecutionProvider'])
    assert egara.infer_with_meta(b'image')[1] == 'cpu'
    assert tagger.predict(b'image') == [('synthetic', 0.97)]
    assert ('preload', '') not in calls


def test_shared_default_serializes_ort_run_and_tag_load(monkeypatch, tmp_path):
    from pixivvault_nicegui.gpu_arbiter import DEFAULT_GPU_ARBITER

    running, finish, tag_attempted = Event(), Event(), Event()

    def run(_, tagging):
        if not tagging:
            running.set()
            assert finish.wait(5)

    calls = fake_ort(monkeypatch, run=run)
    egara = make_egara(tmp_path, provider='cuda')
    tagger = make_tagger(monkeypatch, tmp_path)
    original_acquire = DEFAULT_GPU_ARBITER.acquire

    def observed_acquire(owner, **kwargs):
        if owner is tagger:
            tag_attempted.set()
        return original_acquire(owner, **kwargs)

    monkeypatch.setattr(DEFAULT_GPU_ARBITER, 'acquire', observed_acquire)
    try:
        with ThreadPoolExecutor(2) as executor:
            inference = executor.submit(egara.infer, b'image')
            try:
                assert running.wait(5)
                tags = executor.submit(tagger.predict, b'image')
                assert tag_attempted.wait(5)
                assert [op for op, _ in calls].count('load') == 1
            finally:
                finish.set()
            assert len(inference.result(5)['style']) == 4096
            assert tags.result(5) == [('synthetic', 0.97)]
        assert DEFAULT_GPU_ARBITER.release_idle()
        assert egara.provider_active is None and tagger.session is None
    finally:
        egara.close()
        tagger.close()


def test_priority_and_fifo_order_of_waiters():
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter

    arbiter = GpuInferenceArbiter()
    order = []
    owner = SimpleNamespace(release_session=lambda: None)

    def work(label, priority):
        with arbiter.acquire(owner, priority=priority):
            order.append(label)

    with ThreadPoolExecutor(3) as pool:
        with arbiter.acquire(owner):
            futures = []
            for count, args in enumerate([('low', 0), ('high-first', 10), ('high-second', 10)], 1):
                futures.append(pool.submit(work, *args))
                with arbiter._condition:
                    assert arbiter._condition.wait_for(lambda: len(arbiter._waiting) == count, 5)
            assert order == []
            assert not arbiter.release_idle()
        for future in futures:
            future.result(5)
    assert order == ['high-first', 'high-second', 'low']


def test_release_all_cancels_queued_and_drains_active_before_releasing(monkeypatch, tmp_path):
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, GpuInferenceStopped

    arbiter = GpuInferenceArbiter()
    entered, finish = Event(), Event()

    def block(_, tagging):
        if not tagging:
            entered.set()
            assert finish.wait(5)

    calls = fake_ort(monkeypatch, run=block)
    egara = make_egara(tmp_path, provider='cuda', gpu_arbiter=arbiter)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    with ThreadPoolExecutor(3) as pool:
        running = pool.submit(egara.infer, b'image')
        try:
            assert entered.wait(5)
            queued = pool.submit(tagger.predict, b'image')
            with arbiter._condition:
                assert arbiter._condition.wait_for(lambda: len(arbiter._waiting) == 1, 5)
            stopping = pool.submit(arbiter.release_all)
            with pytest.raises(GpuInferenceStopped):
                queued.result(5)
            assert egara.provider_active == 'cuda'
            assert not stopping.done()
        finally:
            finish.set()
        with pytest.raises(GpuInferenceStopped):
            running.result(5)
        stopping.result(5)
    assert egara.provider_active is None and tagger.session is None
    assert [op for op, _ in calls].count('load') == 1
    assert egara.infer_with_meta(b'image')[1] == 'cuda'
    arbiter.release_all()


def test_stop_between_tagger_manager_steps_prevents_reloading(monkeypatch, tmp_path):
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, GpuInferenceStopped

    arbiter = GpuInferenceArbiter()
    calls = fake_ort(monkeypatch)
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)
    preferences = SimpleNamespace(subscribe_local_tagger_settings=lambda _: lambda: None)
    manager = local_tagger.TaggerManager(preferences)
    manager.tagger = tagger
    verified, resume = Event(), Event()

    def fingerprint():
        assert tagger.verify() == '利用可能'
        verified.set()
        assert resume.wait(5)
        return 'synthetic'

    monkeypatch.setattr(manager, 'current_fingerprint', fingerprint)
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(manager.predict_with_fingerprint, b'image')
        try:
            assert verified.wait(5)
            arbiter.release_all()
            assert tagger.session is None
        finally:
            resume.set()
        with pytest.raises(GpuInferenceStopped):
            running.result(5)
    assert tagger.session is None
    assert [op for op, _ in calls].count('load') == 1


def test_stop_during_tagger_tensor_preparation_skips_native_run(monkeypatch, tmp_path):
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, GpuInferenceStopped

    arbiter = GpuInferenceArbiter()
    preparing, resume, native_run = Event(), Event(), Event()
    fake_ort(monkeypatch, run=lambda *_: native_run.set())
    tagger = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter)

    def prepare_tensor(image):
        preparing.set()
        assert resume.wait(5)
        return image

    tagger.tensor_factory = prepare_tensor
    with ThreadPoolExecutor(2) as pool:
        prediction = pool.submit(tagger.predict, b'image')
        try:
            assert preparing.wait(5)
            with arbiter._condition:
                stop = arbiter._active[1]
            stopping = pool.submit(arbiter.release_all)
            assert stop.wait(5)
        finally:
            resume.set()
        with pytest.raises(GpuInferenceStopped):
            prediction.result(5)
        stopping.result(5)
    assert not native_run.is_set()
    assert tagger.session is None


@pytest.mark.parametrize('model', ['egara', 'tagger'])
def test_stop_during_dll_preload_skips_session_creation(monkeypatch, tmp_path, model):
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, GpuInferenceStopped

    arbiter = GpuInferenceArbiter()
    preloading, resume = Event(), Event()

    def preload():
        preloading.set()
        assert resume.wait(5)

    calls = fake_ort(monkeypatch, preload=preload)
    if model == 'egara':
        inference = make_egara(tmp_path, provider='cuda', gpu_arbiter=arbiter).ensure
    else:
        inference = make_tagger(monkeypatch, tmp_path, gpu_arbiter=arbiter).verify
    with ThreadPoolExecutor(2) as pool:
        creating = pool.submit(inference)
        try:
            assert preloading.wait(5)
            with arbiter._condition:
                stop = arbiter._active[1]
            stopping = pool.submit(arbiter.release_all)
            assert stop.wait(5)
        finally:
            resume.set()
        with pytest.raises(GpuInferenceStopped):
            creating.result(5)
        stopping.result(5)
    assert calls == [('preload', '')]
