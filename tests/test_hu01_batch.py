"""Synthetic differential probes; candidate modules stay read-only."""
from dataclasses import replace
import io,json,time,sqlite3
from types import SimpleNamespace,MethodType
import urllib.error
from pathlib import Path
from test_u11_zip_contract import generated_exports
from test_managed_downloads import record,Context
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
from pixivvault_nicegui import services
from pixivvault_nicegui import failed_records_repository as frozen

def test_observe_db_hashing_per_completed_receipt(tmp_path,monkeypatch):
    path=tmp_path/'synthetic-frozen.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,user_id TEXT,tags TEXT,is_deleted INTEGER)')
        db.execute('CREATE TABLE following_users(user_id TEXT,name TEXT,is_zipped INTEGER)')
        db.execute("INSERT INTO following_users VALUES ('456','Generated',0)")
        db.execute('CREATE TABLE generated_padding(data BLOB)')
        db.execute('INSERT INTO generated_padding VALUES (zeroblob(?))',(16*1024*1024,))
    repo=ReadonlyLibraryRepository(path,immutable=True)
    measurements=[]
    original=frozen.immutable_copy_signature
    total=[0]
    def counted(value):
        total[0]+=1
        return original(value)
    monkeypatch.setattr(frozen,'immutable_copy_signature',counted)
    before=original(path)
    try:
        with generated_exports(tmp_path/'saved',False,None) as f:
            s=f.exports.s
            s.library_repository=repo;s.library_readonly_db=path
            s._legacy_initial_stat=services._frozen_library_stat(path)
            s._legacy_copy_signature=before
            s._assert_legacy_library_source=MethodType(services.ApplicationServices._assert_legacy_library_source,s)
            for count in [1,17,17]:
                f.managed.resolve_record=lambda wid:replace(record(),remote_id=wid)
                while len(f.jobs)<count:
                    wid=str(123+len(f.jobs))
                    rel=f.managed.run(SimpleNamespace(kind='pixiv-illust',target_id=wid),Context())
                    f.jobs.append(SimpleNamespace(kind='pixiv-illust',target_id=wid,state='done',job_id=wid,result=rel))
                    s._managed_job_records[wid]=replace(record(),remote_id=wid)
                total[0]=0;began=time.perf_counter()
                f.exports.scan()
                elapsed=time.perf_counter()-began
                assert not f.exports.scan_errors
                assert not f.exports.jobs.snapshot()
                measurements.append({'completed_receipts':count,'whole_file_hash_calls':total[0],'db_bytes':path.stat().st_size,'bytes_hashed':total[0]*path.stat().st_size,'scan_seconds':elapsed})
    finally:repo.close()
    assert original(path)==before
    import os
    dest=Path(os.environ.get('PV_HU_MEASURE',str(tmp_path/'measure.json')))
    dest.write_text(json.dumps(measurements,indent=2),'utf-8')

    assert [v['whole_file_hash_calls'] for v in measurements] == [2,2,2]

def test_batch_query_handles_multiple_chunks_and_missing_authors(tmp_path):
    from test_u11_legacy_flag import generated_repository
    path,repo=generated_repository(tmp_path,True)
    try:
        ids=[str(i) for i in range(1,1002)]
        observed=repo.author_zip_flags(ids)
        assert len(observed)==len(ids)
        assert observed['456'] is True
        assert all(not flag for aid,flag in observed.items() if aid!='456')
    finally:repo.close()

def test_changed_source_with_same_stat_is_rejected_next_scan(tmp_path):
    import os
    from test_u11_legacy_flag import generated_repository
    path,repo=generated_repository(tmp_path,False)
    original=path.read_bytes()
    stamp=path.stat()
    try:
        with generated_exports(tmp_path/'output',False,None) as f:
            s=f.exports.s
            s.library_repository=repo;s.library_readonly_db=path
            s._legacy_initial_stat=services._frozen_library_stat(path)
            s._legacy_copy_signature=frozen.immutable_copy_signature(path)
            s._assert_legacy_library_source=MethodType(services.ApplicationServices._assert_legacy_library_source,s)
            f.exports.scan()
            assert not f.exports.scan_errors
            data=bytearray(original);data[-1]^=1
            path.write_bytes(data);os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            assert services._frozen_library_stat(path)==s._legacy_initial_stat
            f.exports.scan()
            assert f.exports.scan_errors and not f.exports.jobs.snapshot()
    finally:repo.close()

def test_mutation_during_batch_prevents_publication_and_retry_recovers(tmp_path,monkeypatch):
    from test_u11_legacy_flag import generated_repository
    from test_u11_zip_contract import settled,verify_archive
    path,repo=generated_repository(tmp_path,True)
    original=path.read_bytes()
    stamp=path.stat()
    try:
        with generated_exports(tmp_path/'output',False,None) as f:
            s=f.exports.s
            s.library_repository=repo;s.library_readonly_db=path
            s._legacy_initial_stat=services._frozen_library_stat(path)
            s._legacy_copy_signature=frozen.immutable_copy_signature(path)
            s._assert_legacy_library_source=MethodType(services.ApplicationServices._assert_legacy_library_source,s)
            read=repo.author_zip_flags
            def changed(ids):
                result=read(ids)
                data=bytearray(original);data[-1]^=1;path.write_bytes(data)
                return result
            monkeypatch.setattr(repo,'author_zip_flags',changed)
            f.exports.scan()
            assert f.exports.scan_errors and not f.exports.jobs.snapshot()
            path.write_bytes(original)
            import os
            os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            monkeypatch.setattr(repo,'author_zip_flags',read)
            f.exports.scan()
            assert not f.exports.scan_errors
            jobs=settled(f.exports);assert len(jobs)==1
            verify_archive(f.exports,f.managed,jobs[0])
    finally:repo.close()

