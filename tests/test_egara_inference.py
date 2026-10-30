"""GPU/CPU Egara backend: synthetic ONNX sessions, no Pixiv or legacy writes."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import types

import numpy as np
from PIL import Image
import pytest

from pixivvault_nicegui.egara_inference import (
    EgaraBackendUnavailable, EgaraInference, EgaraInferenceError, EgaraInvalidImage,
    preprocess, normalized_float32, unpack_outputs, STYLE, COMPOSITION,
)


def picture(width=165, height=37):
    image = Image.new('RGB', (width, height))
    image.putpixel((0, 0), (100, 50, 30))
    data = BytesIO()
    image.save(data, 'PNG')
    return data.getvalue()


def model(tmp_path):
    path = tmp_path / 'generated-model.onnx'
    path.write_bytes(b'one uniquely identified synthetic model')
    return path, sha256(path.read_bytes()).hexdigest()


class Session:
    def __init__(self, provider, *, fail_run=False):
        self.provider = provider
        self.fail_run = fail_run
        self.disabled_fallback = False
        self.calls = 0

    def get_providers(self):
        return ['CUDAExecutionProvider', 'CPUExecutionProvider'] if self.provider == 'cuda' else ['CPUExecutionProvider']

    def disable_fallback(self):
        self.disabled_fallback = True

    def get_inputs(self):
        return [types.SimpleNamespace(name='images')]

    def get_outputs(self):
        return [types.SimpleNamespace(name='style_embedding'),
                types.SimpleNamespace(name='layer_norm_48')]

    def run(self, outputs, feed):
        self.calls += 1
        if self.fail_run:
            raise RuntimeError('synthetic accelerator error')
        assert set(feed) == {'images'} and feed['images'].shape[1] == 3
        style = np.arange(1, 1025, dtype=np.float32)[None, :]
        tokens = np.zeros((1, 7, 1024), dtype=np.float32)
        tokens[0, 5] = style[0]
        tokens[0, 6] = style[0] * 2
        return [style, tokens]


@pytest.fixture
def fake_ort(monkeypatch):
    available = ['CPUExecutionProvider', 'CUDAExecutionProvider']
    preload = []
    fake = types.SimpleNamespace(
        SessionOptions=lambda: types.SimpleNamespace(),
        get_available_providers=lambda: available[:],
        preload_dlls=lambda **kwargs: preload.append(kwargs),
    )
    monkeypatch.setitem(sys.modules, 'onnxruntime', fake)
    return available, preload


def test_preprocess_matches_original_bicubic_geometry_and_float32_formula():
    encoded = picture()
    actual = preprocess(encoded)
    with Image.open(BytesIO(encoded)) as src:
        img = src.convert('RGB')
        width, height = img.size
        scale = 512 / max(width, height)
        new_width = max(16, round(int(width * scale) / 16) * 16)
        new_height = max(16, round(int(height * scale) / 16) * 16)
        img = img.resize((new_width, new_height), Image.BICUBIC)
        mean = np.array([.485, .456, .406], dtype=np.float32)
        std = np.array([.229, .224, .225], dtype=np.float32)
        old = ((np.asarray(img, np.float32) / 255.0 - mean) / std).transpose(2, 0, 1)[None]
    assert actual.dtype == np.float32
    assert np.array_equal(actual, old)
    assert actual.shape == (1, 3, 112, 512)


@pytest.mark.parametrize('case', ['empty', 'corrupt', 'oversized'])
def test_bad_input_fails_without_loading_model(case):
    data = {'empty': b'', 'corrupt': b'bad'}.get(case)
    if case == 'oversized':
        data = b'0' * (20 * 1024 * 1024 + 1)
    with pytest.raises(EgaraInvalidImage):
        preprocess(data)


def test_normalization_and_patch_tokens_match_old_contract():
    style = np.ones((1, 1024), np.float32)
    backbone = np.zeros((1, 6, 1024), np.float32)
    backbone[0, 0:5] = 1000000
    backbone[0, 5] = 1
    value = unpack_outputs([style, backbone], 1)
    assert set(value) == {STYLE, COMPOSITION}
    assert np.array_equal(value[STYLE], value[COMPOSITION])
    assert np.max(np.abs(value[STYLE] - np.float32(1 / np.sqrt(1024)))) < 1e-5
    with pytest.raises(EgaraInferenceError):
        normalized_float32(np.array([float('nan')], np.float32))
    with pytest.raises(EgaraInferenceError):
        unpack_outputs([style, backbone[:, :5, :]], 1)
    with pytest.raises(EgaraInferenceError):
        unpack_outputs([style[:, :512], backbone], 1)


def test_exact_model_hash_is_required_even_with_synthetic_session(tmp_path):
    path, digest = model(tmp_path)
    with pytest.raises(EgaraBackendUnavailable):
        EgaraInference(path)
    assert EgaraInference(path, expected_sha256=digest).model_sha256 == digest
    alias = tmp_path / 'alias.onnx'
    try:
        alias.symlink_to(path)
    except OSError:
        pytest.skip('symlink not allowed on this OS')
    with pytest.raises(ValueError):
        EgaraInference(alias, expected_sha256=digest)


def test_explicit_gpu_and_lazy_model_no_cpu_silent_downgrade(tmp_path, fake_ort):
    path, digest = model(tmp_path)
    available, preloaded = fake_ort
    sessions = []

    def factory(model_path, options, provider):
        created = Session(provider)
        sessions.append(created)
        return created

    gpu = EgaraInference(path, provider='cuda', session_factory=factory,
                         expected_sha256=digest)
    assert not sessions
    result = gpu.infer(picture())
    assert gpu.provider_active == 'cuda' and gpu.fallback_reason is None
    assert sessions[0].disabled_fallback and preloaded == [{'directory': ''}]
    assert len(result[STYLE]) == len(result[COMPOSITION]) == 4096
    gpu.close()
    available[:] = ['CPUExecutionProvider']
    refused = EgaraInference(path, provider='cuda', session_factory=factory, expected_sha256=digest)
    with pytest.raises(EgaraBackendUnavailable):
        refused.infer(picture())
    assert len(sessions) == 1, 'explicit CUDA cannot invoke the CPU factory'


def test_auto_cpu_fallback_is_reported_and_model_change_refused(tmp_path, fake_ort):
    path, digest = model(tmp_path)
    available, preloaded = fake_ort
    available[:] = ['CPUExecutionProvider']
    sessions = []

    def factory(model_path, options, provider):
        sessions.append(Session(provider))
        return sessions[-1]

    predictor = EgaraInference(path, provider='auto', session_factory=factory, expected_sha256=digest)
    result = predictor.infer(picture())
    assert predictor.provider_active == 'cpu'
    assert predictor.fallback_reason == 'EgaraBackendUnavailable'
    assert len(sessions) == 1 and len(result[STYLE]) == 4096
    path.write_bytes(b'model was deliberately changed')
    with pytest.raises(EgaraBackendUnavailable):
        predictor.infer(picture())


def test_auto_retries_only_failed_gpu_run_and_never_commits_partial_vectors(tmp_path, fake_ort):
    path, digest = model(tmp_path)
    sessions = []

    def factory(model_path, options, provider):
        instance = Session(provider, fail_run=(provider == 'cuda'))
        sessions.append(instance)
        return instance

    predictor = EgaraInference(path, provider='auto', session_factory=factory, expected_sha256=digest)
    value = predictor.infer(picture())
    assert predictor.provider_active == 'cpu'
    assert predictor.fallback_reason == 'RuntimeError'
    assert [session.provider for session in sessions] == ['cuda', 'cpu']
    assert sessions[0].calls == sessions[1].calls == 1
    assert value[STYLE] and value[COMPOSITION]
