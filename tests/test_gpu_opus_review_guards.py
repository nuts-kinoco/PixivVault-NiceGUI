"""Focused reproductions from the independent Claude Opus High GPU review."""
from hashlib import sha256
import sqlite3

import numpy as np
import pytest

from pixivvault_nicegui.egara_inference import EgaraInvalidImage, preprocess
from pixivvault_nicegui.legacy_similarity_inputs import LegacySimilarityInputs
from pixivvault_nicegui.managed_similarity import ManagedSimilarityError, ManagedSimilarityIndex
from test_egara_inference import picture
from test_managed_similarity import Writer, Inference, digest, embeddings, vector
from test_legacy_gpu_inputs import generated_library
from pixivvault_nicegui.egara_inference import MODEL, STYLE, COMPOSITION
from pixivvault_nicegui.services import ApplicationServices


def test_current_managed_receipt_disappearing_on_resume_is_guarded(tmp_path, monkeypatch):
    index = ManagedSimilarityIndex(tmp_path / 'owned-index')
    writer = Writer()
    index.commit('11', 1, digest(writer.content), writer.revision, embeddings())
    original = writer.verified_saved_receipt
    calls = []

    def disappears(work_id, *, verify_pages=True):
        calls.append(True)
        return original(work_id, verify_pages=verify_pages) if len(calls) == 1 else None

    monkeypatch.setattr(writer, 'verified_saved_receipt', disappears)
    try:
        with pytest.raises(ManagedSimilarityError, match='保存世代'):
            index.analyze_saved_page(writer, Inference(), '11', 0)
        assert calls == [True, True]
        assert writer.calls.count('read') == 0
    finally:
        index.close()


def test_missing_owned_sqlite_path_uses_fixed_fail_closed_error(tmp_path):
    index = ManagedSimilarityIndex(tmp_path / 'owned-index')
    original = index.path
    index.path = index.root / 'missing-synthetic-ledger.sqlite3'
    try:
        with pytest.raises(ManagedSimilarityError, match='解析台帳'):
            index.source_status('11', 1, digest(b'image'), digest(b'receipt'))
    finally:
        index.path = original
        index.close()


def test_legacy_20_to_24_mib_png_is_analyzable_without_lifting_pixel_limit():
    source = picture()
    # Only synthetic trailing bytes; Pillow ignores PNG trailing data.
    padded = source + b'\0' * (21 * 1024 * 1024 - len(source))
    assert 20 * 1024 * 1024 < len(padded) <= 24 * 1024 * 1024
    assert np.array_equal(preprocess(padded), preprocess(source))
    too_large = source + b'\0' * (24 * 1024 * 1024 + 1 - len(source))
    with pytest.raises(EgaraInvalidImage):
        preprocess(too_large)


def _gpu_original_then_frozen_b(tmp_path):
    db, images, model, files = generated_library(tmp_path)
    owner = tmp_path / 'index'
    class SyntheticInference:
        from pixivvault_nicegui.egara_inference import EGARA_BB_SHA256 as model_sha256
        def infer(self, data):
            return embeddings()
    first = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        first.configure_similarity_index(owner, model_file=model)
        assert first._similarity_legacy_inputs.analyze(
            first.similarity_index, SyntheticInference(), '101', 1)
    finally:
        first.close()
    with sqlite3.connect(db) as conn:
        for kind in (STYLE, COMPOSITION):
            conn.execute('INSERT INTO work_embeddings VALUES (?,?,?,?,?,?)',
                         ('102', kind, MODEL, 1, 1024, vector()))
    return db, images, model, files, owner


def test_replaced_db_missing_image_does_not_abort_other_work_search_or_adopt(tmp_path):
    db, images, model, files, owner = _gpu_original_then_frozen_b(tmp_path)
    files[0].unlink()  # Generated original only. No real media is involved.
    second = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        second.configure_similarity_index(owner, model_file=model)
        result = second.similarity_source.search('102', 1)
        assert result['status'] == 'ok'
        with sqlite3.connect(second.similarity_index.path) as db_owner:
            assert db_owner.execute('SELECT COUNT(*) FROM legacy_cpu_adoption').fetchone()[0] == 0
    finally:
        second.close()


def test_read_only_similarity_search_does_not_write_adoption_for_incomplete_cpu_pair(tmp_path):
    db, images, model, files, owner = _gpu_original_then_frozen_b(tmp_path)
    second = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        second.configure_similarity_index(owner, model_file=model)
        ledger = second.similarity_index.path
        before = sha256(ledger.read_bytes()).hexdigest()
        assert second.similarity_source.search('102', 1)['status'] == 'ok'
        assert sha256(ledger.read_bytes()).hexdigest() == before
        with sqlite3.connect(ledger) as db_owner:
            assert db_owner.execute('SELECT COUNT(*) FROM legacy_cpu_adoption').fetchone()[0] == 0
    finally:
        second.close()


def test_partial_replacement_cpu_vector_does_not_resurrect_stale_gpu_page(tmp_path):
    db, images, model, _files, owner = _gpu_original_then_frozen_b(tmp_path)
    with sqlite3.connect(db) as frozen:
        frozen.execute('INSERT INTO work_embeddings VALUES (?,?,?,?,?,?)',
                       ('101', STYLE, MODEL, 1, 1024, vector()))
    second = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        second.configure_similarity_index(owner, model_file=model)
        assert second.similarity_source.search('101', 1)['status'] == 'no_query_embedding'
        with sqlite3.connect(second.similarity_index.path) as ledger:
            assert ledger.execute('SELECT COUNT(*) FROM legacy_cpu_adoption').fetchone()[0] == 0
    finally:
        second.close()


def test_bulk_legacy_scan_checks_sha_at_boundaries_not_for_each_page(tmp_path, monkeypatch):
    from test_legacy_gpu_inputs import FakeEngine
    from pixivvault_nicegui import frozen_source_pin
    db, images, model, files = generated_library(tmp_path)
    owner = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        owner.configure_similarity_index(tmp_path/'index', model_file=model, provider='cpu')
        legacy = owner._similarity_legacy_inputs
        calls = []
        real = frozen_source_pin._digest
        def fingerprint(*args):
            calls.append(True)
            return real(*args)
        monkeypatch.setattr(frozen_source_pin, '_digest', fingerprint)
        def reject_whole_table():
            raise AssertionError('owned_keys full-table scan is forbidden on a per-page path')
        monkeypatch.setattr(owner.similarity_index, 'owned_keys', reject_whole_table)
        monkeypatch.setattr('pixivvault_nicegui.egara_inference.EgaraInference', FakeEngine)
        for work, page in owner._similarity_pages():
            assert owner.analyze_saved_for_similarity(work, page)
        first_count = len(calls)
        if frozen_source_pin._WINDOWS:
            assert first_count == 1, 'one SHA with mandatory deny-write ownership'
        for work, page in owner._similarity_pages():
            assert not owner.analyze_saved_for_similarity(work, page)
        if frozen_source_pin._WINDOWS:
            assert len(calls) == 2
        else:
            assert len(calls) > first_count  # Full-SHA portable fallback.
    finally:
        owner.close()


def test_aborted_legacy_scan_still_checks_frozen_database_sha(tmp_path):
    from pixivvault_nicegui import frozen_source_pin
    db, images, model, files = generated_library(tmp_path)
    owner = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        owner.configure_similarity_index(tmp_path/'index', model_file=model, provider='cpu')
        scan = owner._similarity_pages()
        assert next(scan) == ('101', 1)
        if frozen_source_pin._WINDOWS:
            # Stronger than detecting changes only AFTER vector publication:
            # Windows rejects the writer while the source pin is alive.
            with pytest.raises((PermissionError, sqlite3.OperationalError)):
                with sqlite3.connect(db) as connection:
                    connection.execute("UPDATE works SET title='changed' WHERE work_id='101'")
            scan.close()
        else:
            scan.close()
        # The released fixture is writable. Explicit subsequent analysis
        # still detects the changed full DB before reading an image.
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE works SET title='changed' WHERE work_id='101'")
        with pytest.raises(ManagedSimilarityError, match='旧解析元'):
            owner._similarity_legacy_inputs.check()
    finally:
        owner.close()


def test_search_with_shared_legacy_guard_hashes_once_per_boundary(tmp_path, monkeypatch):
    db, images, model, files = generated_library(tmp_path, preexisting=True)
    owner = ApplicationServices(library_readonly_db=db, library_image_root=images)
    try:
        owner.configure_similarity_index(tmp_path/'index', model_file=model)
        assert owner.similarity_source.old is owner._similarity_legacy_inputs.source
        calls = []
        real = owner.similarity_source.old._fingerprint
        def observed(**kwargs):
            calls.append(True)
            return real(**kwargs)
        monkeypatch.setattr(owner.similarity_source.old, '_fingerprint', observed)
        assert owner.similarity_source.search('101', 1)['status'] == 'ok'
        assert len(calls) == 2
    finally:
        owner.close()


def test_close_cancels_other_workers_even_if_similarity_join_times_out():
    app = ApplicationServices()
    class StuckWorker:
        def stop(self, timeout):
            assert timeout == 150.0
            raise TimeoutError('synthetic worker thread did not join')
    app._similarity_worker = StuckWorker()
    try:
        with pytest.raises(TimeoutError, match='synthetic worker'):
            app.close()
        assert app.closing and not app.closed
        assert app._similarity_stop.is_set()
        assert app._startup_discovery_stop.is_set()
        assert app._legacy_export_stop.is_set()
        assert app._scheduler_cancel_event.is_set()
        assert app._metadata_cancel_event.is_set()
    finally:
        # Synthetic only: restore normal cleanup after the simulated timeout.
        app._similarity_worker = None
        app.close()
