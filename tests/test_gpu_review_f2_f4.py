"""Focused CPU-only regressions for similarity receipt and legacy generation reuse."""
import sqlite3
from types import SimpleNamespace

from pixivvault_nicegui.egara_inference import MODEL, STYLE, COMPOSITION
from pixivvault_nicegui.managed_similarity import CombinedSimilaritySource, ManagedSimilarityIndex
from pixivvault_nicegui.legacy_similarity_inputs import LegacySimilarityInputs
from pixivvault_nicegui.similar_saved import SavedSimilaritySource
from test_managed_similarity import digest, embeddings, frozen_db, vector, Writer, Inference


def test_f2_search_uses_header_proof_and_preserves_generation_gate(tmp_path):
    root=tmp_path/'owned';root.mkdir()
    index=ManagedSimilarityIndex(root/'index')
    class WriterSnapshot:
        def __init__(self):self.verify=[];self.revision='same'
        def catalog_snapshot(self, *, verify_pages=True):
            self.verify.append(verify_pages)
            return SimpleNamespace(receipts=(),source_pages={},revision=self.revision)
    writer=WriterSnapshot()
    combined=CombinedSimilaritySource(None,index,writer=writer)
    try:
        assert combined.search('1')['status']=='empty_pool'
        assert writer.verify==[False,False]
        writer.verify.clear()
        writer.revision='new'
        assert combined.search('1')['status']=='empty_pool'
        assert writer.verify==[False,False]
    finally:combined.close()


def test_f3_completed_page_does_not_read_original_again(tmp_path):
    root=tmp_path/'owned';root.mkdir()
    index=ManagedSimilarityIndex(root/'index')
    writer=Writer();engine=Inference()
    try:
        assert index.analyze_saved_page(writer,engine,'11',0)
        reads=writer.calls.count('read')
        assert index.analyze_saved_page(writer,engine,'11',0) is False
        assert writer.calls.count('read')==reads
        assert engine.calls==1
    finally:index.close()


def test_f4_old_cpu_vectors_survive_stale_gpu_ledger(tmp_path):
    old=frozen_db(tmp_path)
    root=tmp_path/'owned';root.mkdir()
    index=ManagedSimilarityIndex(root/'index')
    index.commit('1',1,digest(b'legacy A page'),digest(b'legacy A token'),embeddings())
    combined=CombinedSimilaritySource(old,index)
    try:
        assert combined.search('1',1)['status']=='ok'
        assert combined.search('1',1)['pool_size']>=2
    finally:combined.close()


def test_f4_same_frozen_db_changed_image_does_not_revive_old_partial_cpu(tmp_path):
    from test_legacy_gpu_inputs import generated_library, FakeEngine
    from pixivvault_nicegui.services import ApplicationServices
    from test_local_media_adapter import png
    db,images,model,paths=generated_library(tmp_path,preexisting=True)
    with sqlite3.connect(db) as connection:
        connection.execute('DELETE FROM work_embeddings WHERE work_id=? AND kind=?',('101',COMPOSITION))
    service=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        service.configure_similarity_index(tmp_path/'index',model_file=model)
        # Avoid model/device startup: only a synthetic vector producer is used.
        class Synthetic:
            model_sha256=service.similarity_index.source_status.__globals__['EGARA_BB_SHA256']
            def infer(self,data):return embeddings()
        service._similarity_legacy_inputs.analyze(service.similarity_index,Synthetic(),'101',1)
        paths[0].write_bytes(png('blue'))
        assert service.similarity_source.search('101',1)['status']=='empty_pool'
    finally:service.close()


def test_f4_replaced_frozen_db_preserves_new_complete_cpu_pair(tmp_path):
    from test_legacy_gpu_inputs import generated_library
    from pixivvault_nicegui.services import ApplicationServices
    db,images,model,_=generated_library(tmp_path)
    root=tmp_path/'index'
    first=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        first.configure_similarity_index(root,model_file=model)
        class Synthetic:
            from pixivvault_nicegui.egara_inference import EGARA_BB_SHA256 as model_sha256
            def infer(self,data):return embeddings()
        assert first._similarity_legacy_inputs.analyze(first.similarity_index,Synthetic(),'101',1)
    finally:first.close()
    # A new, standalone frozen generation B has a validated original CPU pair.
    with sqlite3.connect(db) as conn:
        for kind in (STYLE,COMPOSITION):
            conn.execute('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',
                         ('101',kind,MODEL,1,1024,vector()))
    second=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        second.configure_similarity_index(root,model_file=model)
        assert second.analyze_saved_for_similarity('101',1) is False
        assert second.similarity_source.search('101',1)['status']=='ok'
    finally:second.close()


def test_f4_cpu_adoption_search_only_then_media_drift_then_new_db(tmp_path):
    from test_legacy_gpu_inputs import generated_library
    from test_local_media_adapter import png
    from pixivvault_nicegui.services import ApplicationServices
    db,images,model,paths=generated_library(tmp_path)
    root=tmp_path/'index'
    class Synthetic:
        from pixivvault_nicegui.egara_inference import EGARA_BB_SHA256 as model_sha256
        def infer(self,data):return embeddings()
    first=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        first.configure_similarity_index(root,model_file=model)
        assert first._similarity_legacy_inputs.analyze(first.similarity_index,Synthetic(),'101',1)
    finally:first.close()
    with sqlite3.connect(db) as conn:
        for kind in (STYLE,COMPOSITION):
            conn.execute('INSERT INTO work_embeddings VALUES(?,?,?,?,?,?)',
                         ('101',kind,MODEL,1,1024,vector()))
    second=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        second.configure_similarity_index(root,model_file=model)
        assert second.similarity_source.search('101',1)['status']=='ok'
        paths[0].write_bytes(png('blue'))
        assert second.similarity_source.search('101',1)['status']=='empty_pool'
    finally:second.close()
    # A third complete frozen DB generation can establish a new CPU adoption.
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE work_embeddings SET vec=? WHERE work_id=? AND kind=?',
                     (vector(2),'101',STYLE))
    third=ApplicationServices(library_readonly_db=db,library_image_root=images)
    try:
        third.configure_similarity_index(root,model_file=model)
        assert third.similarity_source.search('101',1)['status']=='ok'
    finally:third.close()
