"""Legacy library may contain NULL title under the original nullable schema."""
import hashlib,sqlite3
import pytest
from pixivvault_nicegui.legacy_author_sync import LegacyAuthorBridge, LegacyAuthorSourceError

def make_bridge(tmp_path,title):
    source=tmp_path/'source';source.mkdir()
    media=tmp_path/'media';media.mkdir()
    managed=tmp_path/'managed';managed.mkdir()
    db=source/'generated.sqlite3'
    connection=sqlite3.connect(db)
    try:
        connection.execute('CREATE TABLE works (work_id TEXT PRIMARY KEY, user_id TEXT, title TEXT, update_date TEXT, is_deleted INTEGER, missing_streak INTEGER, page_count INTEGER, content_type TEXT)')
        connection.execute('CREATE TABLE failed_queue (work_id TEXT,user_id TEXT,title TEXT,work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        connection.execute('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)',('101','11',title,'2020',0,0,1,'illust'))
        connection.commit()
    finally:connection.close()
    return LegacyAuthorBridge(db,media,managed),db

def test_nullable_legacy_title_does_not_fail_author_check(tmp_path):
    bridge,db=make_bridge(tmp_path,None)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    try:
        result=bridge.observe_author('11',())
        assert '101' in result.saved
        assert result.saved['101'].author_id=='11'
    finally:bridge.close()
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before

def test_binary_title_is_still_rejected(tmp_path):
    bridge,db=make_bridge(tmp_path,b'invalid')
    try:
        with pytest.raises(LegacyAuthorSourceError):
            bridge.observe_author('11',())
    finally:bridge.close()
