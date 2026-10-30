"""Regression for repeated scans, upgrade intents and independent bad items."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from fastapi import FastAPI
from test_save_exports import fixture,settled
from test_bookmark_media_projection import save,finish
from tools.p3_residuals_fixtures import create_services
from pixivvault_nicegui.save_exports import SaveExports,packet,digest
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.bookmark_projection import _largest_storage,author_folder


def identities(ex):
    return [(j.job_id,j.target_id,j.attempt,ex.jobs.operation_ids(j.job_id)) for j in ex.jobs.snapshot()]


def test_repeated_scan_after_master_appears_and_restart_preserves_first_pin(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        ex.scan();assert all(j.state=='done' for j in settled(ex))
        original=identities(ex)
        master=native_io_path(ex.projection.root)/'Later(23)';master.mkdir()
        for _ in range(4):ex.scan()
        assert identities(ex)==original
        service=ex.s;root=ex.root;ex.close();ex=SaveExports(service,root,'12')
        ex.scan();assert identities(ex)==original
        assert list(master.iterdir())==[]
    finally:ex.close();managed.close()


def test_old_unpinned_intent_is_reused_but_real_layout_setting_change_is_new(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        rel,row,sha=next(r for r in managed.saved_receipts() if r[1]['work_id']=='301')
        old=dict(relative=rel,sha256=sha,work_id='301',account='12',mode='link',use_work_folder=False)
        key=digest(old);native_io_path(ex.root/(key+'.json')).write_bytes(packet(old))
        job=ex.jobs.enqueue('bookmark-projection',key,operation_id='bookmark-projection:'+key)
        assert all(j.state=='done' for j in settled(ex))
        manifest=native_io_path(ex.root/(key+'.export.json'));before=manifest.read_bytes()
        ex.scan();settled(ex);original=identities(ex)
        for _ in range(3):ex.scan()
        assert identities(ex)==original
        assert ex.enqueue('bookmark-projection',old).job_id==job.job_id
        assert manifest.read_bytes()==before
        assert 'author_folder' not in json.loads(native_io_path(ex.root/(key+'.json')).read_bytes())
        service=ex.s;root=ex.root;ex.close();ex=SaveExports(service,root,'12')
        ex.scan();assert identities(ex)==original
        changed=ex.enqueue('bookmark-projection',dict(old,mode='direct'))
        assert changed.job_id!=job.job_id
        settled(ex);assert manifest.read_bytes()==before
    finally:ex.close();managed.close()


def test_media_enqueue_scan_and_restart_cannot_repin_after_master_appears(tmp_path):
    services,reader=create_services(FastAPI(),tmp_path/'data')
    try:
        save(services,'novel','TXT');services.configure_save_exports(tmp_path/'exports','91');ex=services.save_exports
        services.preferences.update({'novel_save_format':'txt'},expected_revision=services.preferences.snapshot().revision)
        ex.remember_bookmark('novel','123','91');ex.scan_media();settled(ex)
        original=identities(ex);calls=list(reader.calls)
        (native_io_path(ex.projection.root)/'Later(91)').mkdir()
        ex.scan_media();assert identities(ex)==original
        assert ex.enqueue_media('novel','123','TXT').job_id==original[0][0]
        root=ex.root;ex.close();services.save_exports=SaveExports(services,root,'91');ex=services.save_exports
        ex.scan_media();assert identities(ex)==original and reader.calls==calls
    finally:services.close()


def test_extensionless_regular_file_is_not_a_master_folder_and_media_continues(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    visited=[]
    bad=native_io_path(ex.projection.root)/'Unsafe(23)';bad.write_bytes(b'preserve unknown')
    ex.scan_media=lambda **_:visited.append('media')
    try:
        ex.scan();jobs=settled(ex)
        assert any(j.state=='done' for j in jobs)
        sources=[json.loads(native_io_path(ex.root/(j.target_id+'.json')).read_bytes()) for j in jobs]
        assert {v['work_id'] for v in sources}=={'301','302','303'}
        assert visited==['media'] and 'static:301' not in ex.scan_errors
        assert bad.read_bytes()==b'preserve unknown'
    finally:ex.close();managed.close()


def test_bad_marker_does_not_starve_valid_novel_and_ugoira_markers(tmp_path):
    services,_=create_services(FastAPI(),tmp_path/'data')
    try:
        save(services,'novel','TXT');save(services,'ugoira','frames')
        services.configure_save_exports(tmp_path/'exports','91');ex=services.save_exports
        services.preferences.update({'novel_save_format':'txt','ugoira_save_format':'folder'},expected_revision=services.preferences.snapshot().revision)
        bad=native_io_path(ex.root/'000.bookmark.json');bad.write_bytes(b'{broken')
        ex.remember_bookmark('novel','123','91');ex.remember_bookmark('ugoira','123','91')
        ex.scan_media();jobs=settled(ex)
        assert len(jobs)==2 and all(j.state=='done' for j in jobs)
        assert ex.scan_errors and bad.read_bytes()==b'{broken'
    finally:services.close()


def test_largest_uses_original_stat_size_warns_and_ties_are_deterministic(caplog):
    class Node:
        def __init__(self,name,size):self.name,self.size=name,size
        def stat(self):return SimpleNamespace(st_size=self.size)
    small,big=Node('Old(91)',2),Node('New(91)',20)
    assert _largest_storage([big,small],'フォルダ') is big
    assert _largest_storage([small,big],'フォルダ') is big
    tie=Node('Z(91)',20)
    assert _largest_storage([tie,big],'フォルダ') is tie
    assert _largest_storage([big,tie],'フォルダ') is big
    assert any('一番大きい' in r.message for r in caplog.records)


def test_historical_duplicate_pins_reuse_earliest_accepted_job_without_aliases(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    try:
        rel,row,sha=next(r for r in managed.saved_receipts() if r[1]['work_id']=='301')
        logical=dict(relative=rel,sha256=sha,work_id='301',account='12',mode='link',use_work_folder=False)
        first=ex.enqueue('bookmark-projection',dict(logical,author_folder=None));settled(ex)
        # Simulate a duplicate already accepted by a previous application build.
        duplicate=dict(logical,author_folder='Later(23)');key=digest(duplicate)
        native_io_path(ex.root/(key+'.json')).write_bytes(packet(duplicate))
        ex.jobs.enqueue('bookmark-projection',key,operation_id='bookmark-projection:'+key);settled(ex)
        before=identities(ex)
        assert ex.enqueue('bookmark-projection',logical).job_id==first.job_id
        assert identities(ex)==before
    finally:ex.close();managed.close()


def test_zip_author_in_scan_authors_is_skipped_and_next_author_completes(tmp_path):
    _,managed,ex=fixture(tmp_path,auto='0',mode='link')
    legacy=native_io_path(tmp_path/'legacy');legacy.mkdir()
    archive=legacy/'Zipped(23).zip';archive.write_bytes(b'original ZIP never modified')
    ex.s.library_image_root=legacy
    receipts={row['work_id']:(relative,row,sha) for relative,row,sha in managed.saved_receipts()}
    def proof(kind,wid,fmt):
        relative,_,sha=receipts[wid]
        return dict(receipt=relative,sha256=sha)
    # Feed the already-verified admission boundary: its real all-media proof
    # validation is covered by test_author_output_connection.py. This case
    # targets the scan_authors -> enqueue -> legacy ZIP exception boundary.
    ex.s.author_media.completed_projection_sources=lambda:tuple(
        dict(kind='illustration',work_id=wid,author_id=aid,format='original',proof=proof('illustration',wid,'original'))
        for wid,aid in [('301','23'),('303','24')])
    ex.s.author_media._proof=proof
    try:
        ex.scan()
        jobs=settled(ex)
        assert jobs and all(j.state=='done' for j in jobs)
        author_jobs=[j for j in jobs if j.kind=='author-projection']
        assert len(author_jobs)==1
        identity=json.loads(native_io_path(ex.root/(author_jobs[0].target_id+'.json')).read_bytes())
        assert identity['author_id']=='24' and identity['work_id']=='303'
        assert 'author:301' in ex.scan_errors and ex.last_error=='post_save_item_failed'
        ex.scan();settled(ex)
        assert len([j for j in ex.jobs.snapshot() if j.kind=='author-projection'])==1
        assert 'author:301' in ex.scan_errors
        assert archive.read_bytes()==b'original ZIP never modified'
        assert list(legacy.iterdir())==[archive]
        assert not any(p.name.endswith('(23)') for p in native_io_path(ex.projection.root).iterdir())
    finally:ex.close();managed.close()
