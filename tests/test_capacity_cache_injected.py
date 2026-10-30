"""Synthetic-only capacity cache contracts; connection is explicitly injected."""
import sqlite3
import pytest
from pixivvault_nicegui.capacity_cache import CapacityCache

def test_success_zero_restart_and_stale_writer(tmp_path):
    path=tmp_path/'isolated.sqlite3';db=sqlite3.connect(path);cache=CapacityCache(db);cache.initialize()
    assert cache.snapshot('1')==(0,None)
    assert cache.commit_success('1',0,128)
    assert not cache.commit_success('1',0,999)
    assert cache.snapshot('1')==(1,128)
    assert cache.commit_success('2',0,0)
    db.close();db=sqlite3.connect(path);cache=CapacityCache(db);cache.initialize()
    try:assert cache.snapshot('1')==(1,128) and cache.snapshot('2')==(1,0)
    finally:db.close()

def test_failure_is_not_a_zero_value(tmp_path):
    db=sqlite3.connect(tmp_path/'isolated.sqlite3');cache=CapacityCache(db);cache.initialize()
    try:
        assert cache.commit_success('1',0,128)
        with pytest.raises(ValueError):cache.commit_success('1',1,None)
        assert cache.snapshot('1')==(1,128)
    finally:db.close()
