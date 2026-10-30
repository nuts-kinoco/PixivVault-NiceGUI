"""Synthetic roots and injected cache only; no original database or media."""
import asyncio
import sqlite3
from threading import Event
import pytest
from nicegui import Client,ui
from pixivvault_nicegui.capacity_cache import CapacityCache
from pixivvault_nicegui.legacy_capacity import LegacyCapacityReader,LegacyCapacityService,MeasurementLease
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


def test_original_folder_zip_priority_zero_and_failure(tmp_path,monkeypatch):
    root=tmp_path/'legacy';root.mkdir();folder=root/'Author(12)';folder.mkdir()
    (folder/'one.bin').write_bytes(b'a'*30);(folder/'nested').mkdir();(folder/'nested'/'two.bin').write_bytes(b'b'*7)
    reader=LegacyCapacityReader(root);assert reader.measure('12')==37 and reader.measure('13')==0
    (root/'Author(12).zip').write_bytes(b'z'*11)
    assert reader.measure('12')==11
    db=sqlite3.connect(tmp_path/'bind.sqlite3');cache=CapacityCache(db);cache.initialize()
    service=LegacyCapacityService(reader,cache)
    def fail(author):
        if author=='12':raise OSError('fixture')
        return 0
    monkeypatch.setattr(reader,'measure',fail)
    assert service.measure(('12','13'))=={'12':None,'13':0}
    db.close()


def test_cancelled_lease_preserves_persisted_value_and_stale_revision(tmp_path):
    db=sqlite3.connect(tmp_path/'cache.sqlite3',check_same_thread=False);cache=CapacityCache(db);cache.initialize()
    try:
        assert cache.commit_success('12',0,500)
        lease=MeasurementLease();lease.cancel()
        assert lease.publish(cache,{'12':0},{'12':1})=={}
        assert cache.snapshot('12')==(1,500)
        assert MeasurementLease().publish(cache,{'12':2},{'12':0})=={}
        assert cache.snapshot('12')==(1,500)
    finally:db.close()
    db=sqlite3.connect(tmp_path/'cache.sqlite3',check_same_thread=False);cache=CapacityCache(db)
    try:assert cache.snapshot('12')==(1,500)
    finally:db.close()

@pytest.mark.asyncio
async def test_ui_measure_restart_hydration_and_cancelled_worker(tmp_path,monkeypatch):
    db=sqlite3.connect(tmp_path/'cache.sqlite3',check_same_thread=False);cache=CapacityCache(db);cache.initialize()
    services=ApplicationServices(120);client=Client(ui.page('/_legacy_capacity_fixture'))
    entered,release=Event(),Event()
    class Reader:
        root=tmp_path/'synthetic-root'
        unavailable=False
        def measure(self,author):
            if self.unavailable:raise OSError('synthetic unmeasured author unavailable')
            entered.set();release.wait(5);return 456
    reader=Reader()
    services.configure_legacy_capacity(reader,cache)
    hydration_tasks=[]
    def schedule(coro,**kwargs):
        task=asyncio.create_task(coro);hydration_tasks.append(task);return task
    monkeypatch.setattr("nicegui.background_tasks.create",schedule)
    try:
        with client:view=LibraryView(services)
        assert len(hydration_tasks)==1
        await hydration_tasks[0]
        first=view.state.visible_authors[0].author_id
        task=view._legacy_auto_task
        assert task is not None
        assert await asyncio.to_thread(entered.wait,5)
        view.set_active(False);release.set();await task
        assert cache.snapshot(first)==(0,None)
        reader.unavailable=True
        view.set_active(True)
        # Re-entry restores cache then retries unmeasured authors once. A failed
        # automatic attempt must not prevent the existing explicit measurement.
        index=0
        while index<len(hydration_tasks):
            batch=hydration_tasks[index:];index=len(hydration_tasks)
            await asyncio.gather(*batch)
        assert cache.snapshot(first)==(0,None)
        reader.unavailable=False
        await view.measure_legacy_capacity()
        assert cache.snapshot(first)==(1,456)
        assert view.state.snapshot.author_capacities[first]==456
        assert not view._legacy_capacity_busy
        view.close()
        count=len(hydration_tasks)
        with client:second=LibraryView(services)
        # All displayed authors were explicitly measured, so restart only reads.
        assert len(hydration_tasks)==count+1
        await hydration_tasks[-1]
        assert second.state.snapshot.author_capacities[first]==456
        second.close()
    finally:
        release.set()
        if hydration_tasks:await asyncio.gather(*hydration_tasks,return_exceptions=True)
        client.delete();services.close();db.close()


def test_cache_refuses_different_root_and_preserves_values(tmp_path):
    db=sqlite3.connect(tmp_path/'cache.sqlite3');cache=CapacityCache(db);cache.initialize()
    try:
        cache.bind_root(tmp_path/'first');assert cache.commit_success('12',0,17)
        with pytest.raises(ValueError):cache.bind_root(tmp_path/'second')
        assert cache.snapshot('12')==(1,17)
    finally:db.close()


def test_original_folder_unknowns_and_reparse_refusal_are_not_zero(tmp_path,monkeypatch):
    import pixivvault_nicegui.legacy_capacity as module
    root=tmp_path/'legacy';root.mkdir();folder=root/'Author(12)';folder.mkdir()
    (folder/'page.bin').write_bytes(b'data')
    reader=LegacyCapacityReader(root)
    original=module._safe_path
    def reject(path,**kwargs):
        if str(path).endswith('page.bin'):raise ValueError('simulated reparse rejection')
        return original(path,**kwargs)
    monkeypatch.setattr(module,'_safe_path',reject)
    db=sqlite3.connect(tmp_path/'cache.sqlite3');cache=CapacityCache(db);cache.initialize()
    try:
        service=LegacyCapacityService(reader,cache)
        assert service.measure(('12','13'))=={'12':None,'13':0}
        assert cache.commit_success('12',0,88)
        assert MeasurementLease().publish(cache,{'12':None},{'12':1})=={}
        assert cache.snapshot('12')==(1,88)
    finally:db.close()
