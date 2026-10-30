"""P1 audit: application read-only startup must never create source WAL/SHM.

Unlike the direct live-WAL repository API, application-level original data
configuration is a frozen, standalone copy. This protects the user's old DB.
"""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pixivvault_nicegui.services import ApplicationServices


def frozen_wal_header_db(tmp_path):
    folder=tmp_path/'frozen-source'
    folder.mkdir()
    source=folder/'old.sqlite3'
    writer=sqlite3.connect(source)
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()=='wal'
        writer.executescript('''
            CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,
                tags TEXT,user_id TEXT,is_deleted INTEGER);
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
            CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,
                work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER);
            INSERT INTO following_users VALUES('91','Synthetic');
            INSERT INTO works VALUES('501','Generated',1,'[]','91',0);
            INSERT INTO failed_queue VALUES('501','91','Synthetic failed','illust',
                'Synthetic timeout','2026-10-28T12:00:00',1);
        ''')
        writer.commit()
    finally:
        writer.close()
    assert source.is_file() and not any(Path(str(source)+suffix).exists()
       for suffix in ('-wal','-shm','-journal'))
    images=tmp_path/'frozen-images'
    images.mkdir()
    return source, images


@pytest.mark.parametrize('use_preferences',[False,True])
def test_application_uses_immutable_source_without_changing_wal_header_db(tmp_path,monkeypatch,use_preferences):
    source,images=frozen_wal_header_db(tmp_path)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    stat=source.stat()
    original_connect=sqlite3.connect
    source_uris=[]

    def intercept(path,*args,**kwargs):
        if isinstance(path,str) and path.startswith('file:') and 'old.sqlite3' in path:
            source_uris.append(path)
        return original_connect(path,*args,**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',intercept)
        service=ApplicationServices(library_readonly_db=source,library_image_root=images,
                                   preferences_root=tmp_path/'owned-settings' if use_preferences else None)
        try:
            assert len(service.library_snapshot().works)==1
            assert source_uris and all('immutable=1' in uri for uri in source_uris)
            if use_preferences:
                result=service.export_legacy_metadata()
                assert result.path.is_file()
        finally:
            service.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert source.stat().st_mtime_ns==stat.st_mtime_ns
    assert list(source.parent.iterdir())==[source]


@pytest.mark.asyncio
async def test_application_failed_records_reopens_frozen_wal_header_immutably(tmp_path,monkeypatch):
    source,images=frozen_wal_header_db(tmp_path)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    original_connect=sqlite3.connect
    uris=[]

    def intercept(path,*args,**kwargs):
        if isinstance(path,str) and path.startswith('file:') and 'old.sqlite3' in path:
            uris.append(path)
        return original_connect(path,*args,**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3,'connect',intercept)
        service=ApplicationServices(library_readonly_db=source,library_image_root=images)
        try:
            records=await service.read_failed_records()
            assert len(records)==1 and records[0].work_id=='501'
            assert uris and all('immutable=1' in uri for uri in uris)
        finally:
            service.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert list(source.parent.iterdir())==[source]


@pytest.mark.parametrize('use_preferences',[False,True])
def test_application_rejects_active_wal_before_any_created_preferences(tmp_path,use_preferences):
    source,images=frozen_wal_header_db(tmp_path)
    owner=sqlite3.connect(source)
    owned=tmp_path/'owned-settings'
    try:
        owner.execute('PRAGMA journal_mode=WAL')
        owner.execute("INSERT INTO works VALUES('502','In-flight',1,'[]','91',0)")
        owner.commit()
        assert Path(str(source)+'-wal').exists()
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.parent.iterdir() if p.is_file()}
        with pytest.raises(ValueError,match='standalone|frozen|WAL'):
            ApplicationServices(library_readonly_db=source,library_image_root=images,
                                preferences_root=owned if use_preferences else None)
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
               for p in source.parent.iterdir() if p.is_file()}
        assert after==before and not owned.exists()
    finally:
        owner.close()
