"""Normal read-only library + owned settings path, synthetic inputs only."""
import sqlite3
import pytest
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.capacity_cache import CapacityCache


def test_normal_configuration_initializes_owned_cache_and_restores(tmp_path):
    legacy_db_root=tmp_path/'legacy-db';legacy_db_root.mkdir()
    db=legacy_db_root/'legacy.sqlite3'
    with sqlite3.connect(db) as con:
        con.executescript("CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,last_backup TEXT); CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT); INSERT INTO following_users VALUES('12','fixture'); INSERT INTO works VALUES('101','fixture',1,'[]','12',0,'2026-01-01');")
    images=tmp_path/'images';folder=images/'Author(12)';folder.mkdir(parents=True)
    (folder/'101.png').write_bytes(b'fixture-data')
    prefs=tmp_path/'preferences';before=db.read_bytes();file_before=(folder/'101.png').read_bytes()
    services=ApplicationServices(library_readonly_db=db,library_image_root=images,preferences_root=prefs)
    try:
        service=services.legacy_capacity;assert service is not None
        assert service.measure(('12','13'))=={'12':12,'13':0}
        assert service.cache.commit_success('12',0,12)
    finally:services.close()
    services=ApplicationServices(library_readonly_db=db,library_image_root=images,preferences_root=prefs)
    try:assert services.legacy_capacity.cached('12')==(1,12)
    finally:services.close()
    assert db.read_bytes()==before and (folder/'101.png').read_bytes()==file_before


def test_unmarked_existing_cache_is_not_modified(tmp_path):
    root=tmp_path/'prefs';root.mkdir();path=root/'legacy-capacity.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE foreign_data(x TEXT)')
    before=path.read_bytes()
    with pytest.raises((sqlite3.DatabaseError,ValueError)):CapacityCache.open_owned(root)
    assert path.read_bytes()==before


def test_new_cache_path_is_checked_before_open(tmp_path,monkeypatch):
    import pixivvault_nicegui.capacity_cache as module
    root=tmp_path/'prefs';root.mkdir();calls=[]
    def reject(path,*,file=False):
        calls.append((path.name,file))
        if file:raise ValueError('synthetic link refusal')
    monkeypatch.setattr(module,'_safe_path',reject)
    with pytest.raises(ValueError):CapacityCache.open_owned(root)
    assert ('legacy-capacity.sqlite3',True) in calls
    assert not (root/'legacy-capacity.sqlite3').exists()
