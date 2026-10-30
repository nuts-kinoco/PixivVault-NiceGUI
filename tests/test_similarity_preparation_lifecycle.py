"""CPU adoption recovery and cancellable search lifetime, synthetic only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from threading import Event
from zipfile import BadZipFile

import pytest

from pixivvault_nicegui.egara_inference import MODEL, STYLE, COMPOSITION
from pixivvault_nicegui.local_media_adapter import MediaUnavailable
from pixivvault_nicegui.managed_similarity import (
    CombinedSimilaritySource, ManagedSimilarityIndex, SimilarityPreparationBusy,
)
from pixivvault_nicegui.similar_saved import SimilaritySourceError
from pixivvault_nicegui.services import ApplicationServices
from test_gpu_opus_review_guards import _gpu_original_then_frozen_b
from test_legacy_gpu_inputs import generated_library, FakeEngine
from test_managed_similarity import vector


def replaced_cpu(tmp_path):
    db, images, model, files, root = _gpu_original_then_frozen_b(tmp_path)
    with closing(sqlite3.connect(db)) as connection:
        for kind in (STYLE, COMPOSITION):
            connection.execute('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',
                               ('101', kind, MODEL, 1, 1024, vector()))
        connection.commit()
    service = ApplicationServices(library_readonly_db=db, library_image_root=images)
    service.configure_similarity_index(root, model_file=model)
    return service


@pytest.mark.parametrize('error', [MediaUnavailable('temporary generated media'),
                                 BadZipFile('generated corrupt ZIP'), KeyError('generated member')])
def test_failed_media_preparation_is_retried_on_next_explicit_search(tmp_path, monkeypatch, error):
    service = replaced_cpu(tmp_path)
    legacy = service._similarity_legacy_inputs
    real = legacy._token
    unavailable = True
    def transient(wid, page):
        if unavailable and str(wid) == '101':
            raise error
        return real(wid, page)
    monkeypatch.setattr(legacy, '_token', transient)
    try:
        assert service.similarity_source.search('102', 1)['status'] == 'ok'
        assert service.similarity_source._legacy_cpu_retry == frozenset({('101', 1)})
        unavailable = False
        assert service.similarity_source.search('101', 1)['status'] == 'ok'
        assert service.similarity_source._legacy_cpu_retry == frozenset()
        assert service._egara_inference is None
    finally:
        service.close()


def test_media_identity_once_per_key_per_search_and_without_index_lock(tmp_path, monkeypatch):
    db, images, model, _ = generated_library(tmp_path)
    service = ApplicationServices(library_readonly_db=db, library_image_root=images)
    monkeypatch.setattr('pixivvault_nicegui.egara_inference.EgaraInference', FakeEngine)
    try:
        service.configure_similarity_index(tmp_path/'index', model_file=model)
        for wid, page in service._similarity_pages():
            service.analyze_saved_for_similarity(wid, page)
        legacy, index = service._similarity_legacy_inputs, service.similarity_index
        calls = []
        real = legacy._token
        def lock_free():
            obtained = index._lock.acquire(timeout=.2)
            if obtained:
                index._lock.release()
            return obtained
        with ThreadPoolExecutor(max_workers=1) as executor:
            def counted(wid, page):
                assert executor.submit(lock_free).result(timeout=2)
                calls.append((str(wid), page))
                return real(wid, page)
            monkeypatch.setattr(legacy, '_token', counted)
            assert service.similarity_source.search('101', 1)['status'] == 'ok'
            assert len(calls) == len(set(calls)) == 3
            calls.clear()
            assert service.similarity_source.search('102', 1)['status'] == 'ok'
            assert len(calls) == len(set(calls)) == 3
    finally:
        service.close()


def test_close_publishes_cancel_before_draining_preparation(tmp_path, monkeypatch):
    index = ManagedSimilarityIndex(tmp_path/'index')
    source = CombinedSimilaritySource(None, index)
    entered = Event()
    def prepare(tokens):
        entered.set()
        assert source._closing.wait(3), 'close must not block behind the preparation lock'
        raise SimilaritySourceError()
    monkeypatch.setattr(source, '_prepare_legacy_cpu', prepare)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            task = executor.submit(source.search, '1')
            assert entered.wait(3)
            source.close()
            with pytest.raises(SimilaritySourceError):
                task.result(timeout=3)
        assert source._closed and source._active == 0
    finally:
        source.close()
        index.close()


def test_parallel_preparation_is_retryable_not_blocking(tmp_path):
    service = replaced_cpu(tmp_path)
    source = service.similarity_source
    try:
        assert source._prepare_lock.acquire(blocking=False)
        try:
            with pytest.raises(SimilarityPreparationBusy):
                source.search('101', 1)
            assert source._active == 0
        finally:
            source._prepare_lock.release()
        assert source.search('101', 1)['status'] == 'ok'
    finally:
        service.close()
