"""Regressions found while wiring GPU inference into the actual application."""
from hashlib import sha256
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import sqlite3
import pytest

from test_egara_inference import model, picture, Session, fake_ort
from test_managed_similarity import embeddings, digest, frozen_db
from pixivvault_nicegui.egara_inference import EgaraInference, EgaraInferenceError
from pixivvault_nicegui.managed_similarity import (
    ManagedSimilarityIndex, ManagedSimilarityError, CombinedSimilaritySource, MARKER,
)


def test_closed_inference_cannot_reload_even_if_model_still_exists(tmp_path, fake_ort):
    path, checksum = model(tmp_path)
    calls = []
    def factory(path, options, provider):
        calls.append(provider)
        return Session(provider)
    engine = EgaraInference(path, session_factory=factory, expected_sha256=checksum)
    engine.close()
    with pytest.raises(EgaraInferenceError):
        engine.infer(picture())
    with pytest.raises(EgaraInferenceError):
        engine.ensure()
    assert not calls


def test_cuda_options_preserve_fp32_and_bound_arena(tmp_path, fake_ort, monkeypatch):
    import sys
    calls = []
    def factory(path, options, *, providers):
        calls.append(providers)
        return Session('cuda')
    monkeypatch.setattr(sys.modules['onnxruntime'], 'InferenceSession', factory, raising=False)
    path, checksum = model(tmp_path)
    engine = EgaraInference(path, provider='cuda', expected_sha256=checksum)
    try:
        engine.infer(picture())
        name, options = calls[0][0]
        assert name == 'CUDAExecutionProvider'
        assert str(options['use_tf32']) == '0'
        assert 0 < int(options['gpu_mem_limit']) <= 8 * 1024**3
        assert str(options['do_copy_in_default_stream']) == '1'
    finally:
        engine.close()


@pytest.mark.parametrize('damage', ['missing-kind', 'corrupt-vector'])
def test_resume_does_not_skip_incomplete_or_corrupt_vectors(tmp_path, damage):
    index = ManagedSimilarityIndex(tmp_path / 'index')
    image, receipt = digest(b'image'), digest(b'receipt')
    try:
        index.commit('11', 1, image, receipt, embeddings())
        if damage == 'missing-kind':
            index._conn.execute("DELETE FROM work_embeddings WHERE kind='composition'")
        else:
            index._conn.execute("UPDATE work_embeddings SET vec=x'00' WHERE kind='composition'")
        assert index.source_status('11', 1, image, receipt) is False
        assert index.commit('11', 1, image, receipt, embeddings()) is True
        assert index.source_status('11', 1, image, receipt) is True
    finally:
        index.close()


def test_open_index_rechecks_ownership_marker(tmp_path):
    index = ManagedSimilarityIndex(tmp_path / 'index')
    try:
        (index.root / MARKER).write_bytes(b'changed marker')
        with pytest.raises((ManagedSimilarityError, ValueError)):
            index.commit('11', 1, digest(b'i'), digest(b'r'), embeddings())
    finally:
        index.close()


def test_index_checks_every_sqlite_sidecar_before_connect(tmp_path, monkeypatch):
    index = ManagedSimilarityIndex(tmp_path / 'index')
    index.close()
    Path(str(index.path) + '-journal').mkdir()
    calls = []
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: calls.append((a,k)))
    with pytest.raises((ManagedSimilarityError, ValueError)):
        ManagedSimilarityIndex(index.root)
    assert calls == []


class CurrentWriter:
    def __init__(self):
        self.closed = False
        self.rev = 1
        self.receipt = digest(b'receipt')
        self.visible = True
        self.present = True
    def catalog_snapshot(self, *, verify_pages=True):
        receipt = {'work_id': '3', 'page_count': 1,
                   'files': [{'sha256': digest(b'image')}]}
        return SimpleNamespace(
            revision=str(self.rev),
            receipts=(('r.json', receipt, self.receipt),) if self.present else (),
            source_pages={'3': (0,) if self.visible else ()} if self.present else {},
        )


@pytest.mark.parametrize('change', ['receipt', 'hidden', 'removed'])
def test_search_excludes_new_vectors_no_longer_current(tmp_path, change):
    index = ManagedSimilarityIndex(tmp_path / 'index')
    old = frozen_db(tmp_path)
    writer = CurrentWriter()
    index.commit('3', 1, digest(b'image'), writer.receipt, embeddings())
    source = CombinedSimilaritySource(old, index, writer=writer)
    try:
        assert source.search('1')['results'][0][0] == ('3', 1)
        writer.rev += 1
        if change == 'receipt': writer.receipt = digest(b'replaced')
        elif change == 'hidden': writer.visible = False
        else: writer.present = False
        assert source.search('1')['results'] == []
    finally:
        source.close()
        index.close()


def test_managed_generation_masks_older_legacy_vectors(tmp_path):
    index = ManagedSimilarityIndex(tmp_path / 'index')
    old = frozen_db(tmp_path)
    writer = CurrentWriter()
    original = writer.catalog_snapshot
    def managed_old_id(*, verify_pages=True):
        result = original(verify_pages=verify_pages)
        result.receipts[0][1]['work_id'] = '1'
        result.source_pages = {'1': (0,)}
        return result
    writer.catalog_snapshot = managed_old_id
    source = CombinedSimilaritySource(old, index, writer=writer)
    try:
        result = source.search('1')
        assert result['status'] == 'no_query_embedding'
    finally:
        source.close()
        index.close()
