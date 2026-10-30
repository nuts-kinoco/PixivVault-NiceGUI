"""Reported native-path DB restart and all-author cache sorting regressions."""
import sqlite3
from contextlib import closing
import os
from urllib.parse import urlsplit,unquote,parse_qs
from pathlib import Path
import pytest
from pixivvault_nicegui.fs_paths import native_io_path,logical_path,sqlite_readonly_uri
from pixivvault_nicegui.job_service import JobService,JobStorageError
from pixivvault_nicegui.library_models import LibrarySnapshot,LibraryWork
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.ui.library_view import LibraryView
from test_capacity_reactivation import env


def done(service,jid):
    with service._condition:
        assert service._condition.wait_for(lambda:any(j.job_id==jid and j.state=='done' for j in service.snapshot()),5)
    return next(j for j in service.snapshot() if j.job_id==jid)


@pytest.mark.parametrize('spelling',['native','resolved'])
def test_native_job_database_restart_retains_readonly_verification_and_operation(tmp_path,monkeypatch,spelling,request):
    folder=tmp_path
    while len(str(folder))<285:folder/='synthetic-long-database-path'
    path=native_io_path(folder/'jobs.sqlite3')
    service=JobService(lambda *_:'receipt',path)
    first=service.enqueue('demo','1',operation_id='single-accepted-operation');first=done(service,first.job_id)
    service.close()
    real=sqlite3.connect;uris=[]
    def capture(database,*args,**kwargs):
        if kwargs.get('uri'):uris.append(database)
        try:return real(database,*args,**kwargs)
        except sqlite3.Error as exc:
            request.node.user_properties.append(('sqlite_connect_error',
                type(exc).__name__+':'+str(getattr(exc,'sqlite_errorname',None))+':'+str(getattr(exc,'sqlite_errorcode',None))))
            raise
    monkeypatch.setattr(sqlite3,'connect',capture)
    reopen=path if spelling=='native' else path.resolve()
    service=JobService(lambda *_:pytest.fail('must replay the accepted completion'),reopen)
    try:
        assert service.enqueue('demo','1',operation_id='single-accepted-operation')==first
        assert len(service.accepted_operations())==1
        assert uris
        for uri in uris:
            parsed=urlsplit(uri)
            assert parsed.scheme=='file' and parsed.netloc=='' and parsed.fragment==''
            assert parse_qs(parsed.query)=={'mode':['ro'],'immutable':['1']}
            # Verify lossless native filename, not the implementation's formula.
            expected=str(path) if os.name=='nt' else str(logical_path(path))
            assert unquote(parsed.path)==expected
            assert 'file://%3F/' not in uri
        with closing(real(uris[0],uri=True)) as verified:
            assert verified.execute('PRAGMA application_id').fetchone()==(0x50564A31,)
            with pytest.raises(sqlite3.OperationalError):verified.execute('CREATE TABLE forbidden_write(x)')
    finally:service.close()


@pytest.mark.parametrize('corruption',["PRAGMA application_id=0", "CREATE TABLE foreign_data(x)"])
def test_native_uri_fix_does_not_weaken_database_identity_or_schema(tmp_path,corruption):
    path=native_io_path(tmp_path/'jobs.sqlite3');service=JobService(lambda *_:'receipt',path);service.close()
    with sqlite3.connect(path) as connection:connection.execute(corruption)
    with pytest.raises(JobStorageError,match='unmanaged job database rejected'):
        JobService(lambda *_:'receipt',path)


@pytest.mark.asyncio
async def test_cached_author_beyond_fifty_participates_in_capacity_sort_before_measurement(env):
    works=tuple(LibraryWork(Artwork(str(9000+i),f'work {i}',f'author {i}',author_id=str(3000+i)),
        str(3000+i),view_count=100-i,capacity_bytes=i) for i in range(1,63))
    env.services.library=LibrarySnapshot(works)
    env.cache.commit_success('3061',0,999999)
    env.cache.commit_success('3062',0,0)
    calls=[];original=env.services.legacy_capacity.cached
    env.services.legacy_capacity.cached=lambda aid:(calls.append(aid) or original(aid))
    with env.client:
        view=LibraryView(env.services)
        assert '3061' not in {a.author_id for a in view.state.visible_authors[:50]}
        await env.schedule.drain()
        assert set(calls)=={str(3000+i) for i in range(1,63)}
        assert view.state.snapshot.author_capacities['3061']==999999
        assert view.state.snapshot.author_capacities['3062']==0
        view.more_authors();await env.schedule.drain()
        assert len(view.author_cards)==62 and '3062' in view.author_cards
        assert next(a for a in view.state.visible_authors if a.author_id=='3062').capacity_bytes==0
        view.sort('capacity');await env.schedule.drain()
        assert view.state.visible_authors[0].author_id=='3061'
        view.search('work 62');await env.schedule.drain()
        assert view.state.visible_authors[0].capacity_bytes==0
        view.search('');await env.schedule.drain()
        measured=[];env.reader.measure=lambda aid:(measured.append(aid) or 123)
        expected=tuple(a.author_id for a in view.state.visible_authors[:50])
        await view.measure_legacy_capacity();await env.schedule.drain()
        assert tuple(measured)==expected and len(measured)==50
        assert env.cache.snapshot('3062')==(1,0)  # unmeasured zero retained
        view.close()


@pytest.mark.parametrize('basename',['jobs #1%.sqlite3','日本語 jobs.sqlite3'])
def test_sqlite_readonly_uri_escapes_filename_without_changing_database(tmp_path,basename):
    path=native_io_path(tmp_path/basename)
    owner=JobService(lambda *_:'receipt',path);owner.close()
    import hashlib
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    uri=sqlite_readonly_uri(path)
    parsed=urlsplit(uri)
    assert parsed.netloc=='' and parsed.fragment==''
    assert parse_qs(parsed.query)=={'mode':['ro'],'immutable':['1']}
    assert unquote(parsed.path)==str(path)
    with closing(sqlite3.connect(uri,uri=True)) as connection:
        assert connection.execute('PRAGMA application_id').fetchone()==(0x50564A31,)
        assert connection.execute('PRAGMA user_version').fetchone()==(2,)
        with pytest.raises(sqlite3.OperationalError):connection.execute('CREATE TABLE forbidden_write(x)')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_native_uri_rejects_missing_database_without_creating_file(tmp_path):
    missing=native_io_path(tmp_path/'missing.sqlite3')
    with pytest.raises(sqlite3.OperationalError):sqlite3.connect(sqlite_readonly_uri(missing),uri=True)
    assert not missing.exists()
