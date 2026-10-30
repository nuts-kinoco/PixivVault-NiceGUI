"""Actual service ownership with a bounded synthetic model, no network/GPU."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
import pytest

from test_legacy_gpu_inputs import generated_library, FakeEngine
from test_managed_similarity import embeddings
from pixivvault_nicegui.services import ApplicationServices


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('expected lifecycle state not observed')


def setup(tmp_path, monkeypatch):
    db, images, model, _ = generated_library(tmp_path)
    entered, release = Event(), Event()
    class Controlled(FakeEngine):
        instances = []
        def infer(self, data):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                assert release.wait(5), 'test did not release its own synthetic model'
            return embeddings()
    monkeypatch.setattr('pixivvault_nicegui.egara_inference.EgaraInference', Controlled)
    services = ApplicationServices(library_readonly_db=db, library_image_root=images)
    services.configure_similarity_index(tmp_path/'index',model_file=model,provider='cpu')
    return services, entered, release, Controlled


def test_bulk_pause_revokes_inflight_commit_resume_completes_all_and_close_joins(tmp_path,monkeypatch):
    services, entered, release, engine = setup(tmp_path,monkeypatch)
    worker = None
    try:
        assert services.similarity_analysis_status()['started'] is False
        assert engine.instances == []
        services.start_similarity_analysis()
        assert entered.wait(5)
        worker = services._similarity_worker
        services.start_similarity_analysis()
        assert services._similarity_worker is worker and len(engine.instances) == 1
        services.pause_similarity_analysis()
        release.set()
        with services._similarity_run_lock:
            assert services.similarity_index.owned_keys() == frozenset()
        state = services.similarity_analysis_status()
        assert state['phase'] == 'paused' and state['errors'] == 0
        services.resume_similarity_analysis()
        until(lambda: services.similarity_analysis_status()['indexed'] == 3)
        until(lambda: services.similarity_analysis_status()['phase'] == 'idle')
        assert services.similarity_analysis_status()['provider'] == 'cpu'
        assert services.similarity_source.search('102')['results'] == [(('101',1),1.),(('101',2),1.)]
    finally:
        release.set()
        services.close()
    assert services.closed and worker.status()['phase'] == 'stopped'
    assert engine.instances[0].closed


def test_manual_inference_and_shutdown_do_not_deadlock_or_publish_after_close(tmp_path,monkeypatch):
    services, entered, release, engine = setup(tmp_path,monkeypatch)
    index = services.similarity_index
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(services.analyze_saved_for_similarity,'101',1)
            assert entered.wait(5)
            closing = pool.submit(services.close)
            until(lambda: services.closing)
            release.set()
            with pytest.raises(ValueError):
                pending.result(timeout=5)
            closing.result(timeout=5)
        assert services.closed and engine.instances[0].closed
        import sqlite3
        from contextlib import closing as close_connection
        from pixivvault_nicegui.fs_paths import sqlite_readonly_uri
        with close_connection(sqlite3.connect(sqlite_readonly_uri(index.path),uri=True)) as db:
            assert db.execute('SELECT COUNT(*) FROM pages').fetchone()[0] == 0
    finally:
        release.set()
        services.close()


def test_analysis_failure_does_not_close_library_and_requires_explicit_resume(tmp_path,monkeypatch):
    from pixivvault_nicegui.egara_inference import EgaraBackendUnavailable
    services, entered, release, engine = setup(tmp_path,monkeypatch)
    def failed(self, data):
        raise EgaraBackendUnavailable('synthetic model unavailable')
    monkeypatch.setattr(engine,'infer',failed)
    try:
        services.start_similarity_analysis()
        until(lambda: services.similarity_analysis_status()['phase'] == 'failed')
        assert services.similarity_analysis_status()['errors'] == 1
        assert not services.closed and len(services.library_snapshot().works) == 2
        assert services.similarity_index.owned_keys() == frozenset()
        services.start_similarity_analysis()  # wake must not silently retry failure
        assert services.similarity_analysis_status()['phase'] == 'failed'
    finally:
        services.close()
