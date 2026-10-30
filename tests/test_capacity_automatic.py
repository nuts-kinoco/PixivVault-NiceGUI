"""Original unmeasured-author flow: real UI, synthetic legacy files and SQLite."""
import asyncio
from collections import Counter
import hashlib
import sqlite3
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest
import pytest_asyncio
from nicegui import Client, ui
from pixivvault_nicegui.capacity_cache import CapacityCache
from pixivvault_nicegui.legacy_capacity import LegacyCapacityReader, MeasurementLease
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView, format_capacity
from test_capacity_reactivation import ScheduledReads
from test_capacity_lease_sqlite import WrappedConnection


class CountingReader:
    def __init__(self, root):
        self.root=root;self.delegate=LegacyCapacityReader(root)
        self.calls=[];self.fail=set();self.gate=None
        self.lock=Lock();self.active=0;self.max_active=0

    def measure(self, aid):
        with self.lock:
            self.calls.append(aid);ordinal=len(self.calls)
            self.active+=1;self.max_active=max(self.max_active,self.active)
        try:
            if self.gate:
                value=self.gate(aid,ordinal)
                if value is not None:return value
            if aid in self.fail:raise OSError('synthetic author unavailable')
            return self.delegate.measure(aid)
        finally:
            with self.lock:self.active-=1


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    schedule=ScheduledReads()
    monkeypatch.setattr('nicegui.background_tasks.create',schedule.create)
    environments=[]
    def create(count=4, cached=None, missing=()):
        base=tmp_path/str(len(environments));root=base/'legacy';root.mkdir(parents=True)
        works=[];sizes={}
        for index in range(1,count+1):
            aid=str(1000+index);wid=str(9000+index)
            works.append(LibraryWork(Artwork(wid,f'work {index}',f'author {index}',author_id=aid),
                aid,view_count=count-index,capacity_bytes=None))
            if aid in missing:sizes[aid]=0;continue
            folder=root/f'Author({aid})';folder.mkdir()
            body=b'x'*(index+10);(folder/'payload.bin').write_bytes(body);sizes[aid]=len(body)
        path=base/'cache.sqlite3';cache=CapacityCache(sqlite3.connect(path,check_same_thread=False));cache.initialize()
        reader=CountingReader(root);services=ApplicationServices(120)
        services.library=LibrarySnapshot(tuple(works));services.configure_legacy_capacity(reader,cache)
        for aid,value in (cached or {}).items():assert cache.commit_success(aid,0,value)
        client=Client(ui.page('/_capacity_auto_'+str(len(environments))))
        e=SimpleNamespace(root=root,path=path,cache=cache,reader=reader,services=services,
            client=client,schedule=schedule,sizes=sizes,views=[],releases=[])
        def view():
            with client:result=LibraryView(e.services)
            e.views.append(result);return result
        e.view=view;environments.append(e);return e
    try:yield create
    finally:
        for e in environments:
            for view in e.views:view.close()
            for release in e.releases:release.set()
        await schedule.drain()
        for e in environments:e.client.delete();e.services.close();e.cache.close()


@pytest.mark.asyncio
async def test_visible_uncached_authors_progress_sequentially_then_more_and_restart(factory):
    e=factory(62,cached={'1001':777,'1002':0})
    entered,release=Event(),Event();e.releases.append(release)
    def gate(aid,ordinal):
        if ordinal==1:entered.set();assert release.wait(5)
    e.reader.gate=gate
    before={str(p.relative_to(e.root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in e.root.rglob('*') if p.is_file()}
    with e.client:
        view=e.view()
        assert await asyncio.to_thread(entered.wait,5)
        assert e.reader.calls==['1003'] and len(view.author_cards)==50
        assert view._legacy_capacity_busy and view.legacy_auto_capacity_status.text
        # Ordinary UI remains usable while the worker is paused.
        view.change_size('small');assert view.card_size=='small'
        assert e.cache.snapshot('1003')==(0,None)
        release.set();await e.schedule.drain()
        assert e.reader.calls==[str(1000+i) for i in range(3,51)]
        assert e.cache.snapshot('1003')==(1,13)
        assert e.cache.snapshot('1051')==(0,None)
        assert view.state.snapshot.author_capacities['1001']==777
        assert view.state.snapshot.author_capacities['1002']==0
        view.more_authors();await e.schedule.drain()
        assert e.reader.calls==[str(1000+i) for i in range(3,63)]
        assert len(view.author_cards)==62 and e.cache.snapshot('1062')==(1,72)
        assert e.reader.max_active==1 and not view._legacy_capacity_busy
        calls=list(e.reader.calls)
        view.sort('capacity');await e.schedule.drain()
        assert view.state.visible_authors[0].author_id=='1001'
        view.search('work 62');await e.schedule.drain()
        assert view.state.visible_authors[0].capacity_bytes==72
        view.search('');view.set_active(False);view.set_active(True);await e.schedule.drain()
        assert e.reader.calls==calls
        view.close();e.services.close();e.cache.close()
        e.cache=CapacityCache(sqlite3.connect(e.path,check_same_thread=False))
        e.services=ApplicationServices(120)
        e.services.library=LibrarySnapshot(tuple(view.state.snapshot.works))
        e.services.configure_legacy_capacity(e.reader,e.cache)
        restarted=e.view();await e.schedule.drain()
        assert e.reader.calls==calls
        assert restarted.state.snapshot.author_capacities['1062']==72
        assert e.cache.snapshot('1002')==(1,0)
    assert before=={str(p.relative_to(e.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in e.root.rglob('*') if p.is_file()}


@pytest.mark.asyncio
async def test_failure_stays_unknown_once_per_visit_zero_is_success_and_other_authors_continue(factory):
    e=factory(cached={'1001':777},missing=('1003',));e.reader.fail.add('1002')
    with e.client:
        view=e.view();await e.schedule.drain()
        assert e.reader.calls==['1002','1003','1004']
        assert e.cache.snapshot('1002')==(0,None)
        assert e.cache.snapshot('1003')==(1,0) and e.cache.snapshot('1004')==(1,14)
        assert next(a.capacity_bytes for a in view.state.visible_authors if a.author_id=='1002') is None
        assert any(format_capacity(None) in getattr(node,'text','') for node in view.author_cards['1002'].descendants())
        assert '未計測 1作者' in view.legacy_auto_capacity_status.text
        view.sort('capacity');view.search('work 2');await e.schedule.drain()
        view.search('');view.reload();await e.schedule.drain()
        assert Counter(e.reader.calls)['1002']==1
        e.reader.fail.clear();view.set_active(False);view.set_active(True);await e.schedule.drain()
        assert e.reader.calls==['1002','1003','1004','1002']
        assert e.cache.snapshot('1002')==(1,12) and e.cache.snapshot('1001')==(1,777)
        assert view.state.snapshot.author_capacities['1002']==12


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['inactive','subview','author','search','sort','reload','close'])
async def test_late_measurement_after_lifetime_change_never_commits_or_renders_old_value(factory,action):
    e=factory(3);entered,release=Event(),Event();e.releases.append(release)
    def gate(aid,ordinal):
        if ordinal==1:entered.set();assert release.wait(5);return 999999
    e.reader.gate=gate
    with e.client:
        view=e.view();assert await asyncio.to_thread(entered.wait,5)
        lease=view._legacy_lease;initial=view.state.snapshot
        if action=='inactive':view.set_active(False)
        elif action=='subview':view.show_subview('bookmarks')
        elif action=='author':view.open_author('1001')
        elif action=='search':view.search('work 3')
        elif action=='sort':view.sort('capacity')
        elif action=='reload':view.reload()
        else:view.close()
        assert lease.state==MeasurementLease.CANCELLED
        assert view._legacy_capacity_busy  # not falsely finished while I/O drains
        release.set();await e.schedule.drain()
        assert e.reader.max_active==1 and not view._legacy_capacity_busy
        assert all(e.cache.snapshot(aid)[1]!=999999 for aid in e.sizes)
        assert 999999 not in view.state.snapshot.author_capacities.values()
        if action in ('inactive','subview','author','close'):
            assert e.cache.snapshot('1001')==(0,None)
            assert view.state.snapshot is initial
        if action=='search':assert e.cache.snapshot('1001')==(0,None) and e.cache.snapshot('1003')==(1,13)
        if action in ('sort','reload'):assert e.cache.snapshot('1001')==(1,11)


@pytest.mark.asyncio
async def test_cache_publication_failure_is_local_and_retry_requires_next_visit(factory,monkeypatch):
    e=factory(3);original=e.cache.commit_batch;fired=[]
    def fail(values,revisions,*,decide):
        if '1001' in values:fired.append('1001');raise sqlite3.OperationalError('synthetic commit unavailable')
        return original(values,revisions,decide=decide)
    monkeypatch.setattr(e.cache,'commit_batch',fail)
    with e.client:
        view=e.view();await e.schedule.drain()
        assert fired==['1001'] and e.cache.snapshot('1001')==(0,None)
        assert e.cache.snapshot('1002')==(1,12) and e.cache.snapshot('1003')==(1,13)
        view.sort('capacity');await e.schedule.drain();assert fired==['1001']
        monkeypatch.setattr(e.cache,'commit_batch',original)
        view.set_active(False);view.set_active(True);await e.schedule.drain()
        assert e.cache.snapshot('1001')==(1,11)


@pytest.mark.asyncio
async def test_auto_sqlite_lock_cancel_before_decision_keeps_cache_absent(factory):
    e=factory(1);entered=Event()
    blocker=sqlite3.connect(e.path,check_same_thread=False);blocker.execute('BEGIN IMMEDIATE')
    e.cache._db=WrappedConnection(e.cache._db,before_begin=entered.set)
    try:
        with e.client:
            view=e.view();assert await asyncio.to_thread(entered.wait,5)
            view.set_active(False)
            assert view._legacy_lease.state==MeasurementLease.CANCELLED
            assert view._legacy_capacity_busy
            blocker.rollback();await e.schedule.drain()
            assert not view._legacy_capacity_busy and e.cache.snapshot('1001')==(0,None)
            assert view.state.snapshot.author_capacities.get('1001') is None
    finally:blocker.rollback();blocker.close()


@pytest.mark.asyncio
async def test_auto_cancel_after_decision_does_not_claim_completion_or_update_inactive_view(factory):
    e=factory(1);entered,release=Event(),Event();e.releases.append(release)
    def pause():entered.set();assert release.wait(5)
    e.cache._db=WrappedConnection(e.cache._db,before_commit=pause)
    with e.client:
        view=e.view();assert await asyncio.to_thread(entered.wait,5)
        lease=view._legacy_lease;initial=view.state.snapshot
        assert lease.state==MeasurementLease.SEALED
        view.set_active(False)
        assert lease.state==MeasurementLease.SEALED and view._legacy_capacity_busy
        release.set();await e.schedule.drain()
        assert lease.state==MeasurementLease.COMMITTED
        assert e.cache.snapshot('1001')==(1,11) and view.state.snapshot is initial
        calls=list(e.reader.calls)
        view.set_active(True);await e.schedule.drain()
        assert view.state.snapshot.author_capacities['1001']==11 and e.reader.calls==calls


@pytest.mark.asyncio
async def test_normal_readonly_configuration_auto_measures_zip_folder_zero_and_reopens(tmp_path,monkeypatch):
    schedule=ScheduledReads();monkeypatch.setattr('nicegui.background_tasks.create',schedule.create)
    dbroot=tmp_path/'original-copy';dbroot.mkdir();dbpath=dbroot/'library.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.executescript("CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,page_count INTEGER,tags TEXT,user_id TEXT,is_deleted INTEGER,last_backup TEXT); CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);")
        db.execute('CREATE TABLE library_capacity_cache(user_id TEXT PRIMARY KEY,total_bytes INTEGER)')
        db.execute("INSERT INTO library_capacity_cache VALUES('15',0)")
        for aid in (12,13,14,15):
            db.execute('INSERT INTO following_users VALUES(?,?)',(str(aid),'synthetic '+str(aid)))
            db.execute('INSERT INTO works VALUES(?,?,?,?,?,?,?)',(str(900+aid),'fixture',1,'[]',str(aid),0,'2026-01-01'))
    db.close()
    root=tmp_path/'media';root.mkdir();folder=root/'Author(12)';folder.mkdir()
    (folder/'payload.bin').write_bytes(b'x'*99);(root/'Author(12).zip').write_bytes(b'z'*17)
    other=root/'Author(13)';other.mkdir();(other/'data.bin').write_bytes(b'x'*23)
    cached=root/'Author(15)';cached.mkdir();(cached/'keep.bin').write_bytes(b'changed-after-original-cache')
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [dbpath,*root.rglob('*.bin'),*root.glob('*.zip')]}
    prefs=tmp_path/'preferences';client=Client(ui.page('/_capacity_auto_normal'))
    service=None;view=None
    try:
        for restart in (False,True):
            service=ApplicationServices(library_readonly_db=dbpath,library_image_root=root,preferences_root=prefs)
            if restart:
                def forbidden(aid):pytest.fail('cached author must not rescan on restart')
                monkeypatch.setattr(service.legacy_capacity.reader,'measure',forbidden)
            with client:
                view=LibraryView(service);await schedule.drain()
                assert {aid:service.legacy_capacity.cached(aid) for aid in ('12','13','14')}=={'12':(1,17),'13':(1,23),'14':(1,0)}
                assert {aid:view.state.snapshot.author_capacities[aid] for aid in ('12','13','14')}=={'12':17,'13':23,'14':0}
                view.sort('capacity');await schedule.drain()
                assert [a.author_id for a in view.state.visible_authors]==['13','12','14','15']
                assert view.state.snapshot.author_capacities['15']==0
                assert service.legacy_capacity.cached('15')==(0,None)  # old readonly row is sufficient
                view.close();view=None
            service.close();service=None
        assert before=={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before}
    finally:
        if view:view.close()
        await schedule.drain();client.delete()
        if service:service.close()


@pytest.mark.asyncio
async def test_other_writer_success_wins_cas_and_same_view_cannot_duplicate_worker(factory):
    e=factory(1);entered,release=Event(),Event();e.releases.append(release)
    def pause(aid,ordinal):entered.set();assert release.wait(5)
    e.reader.gate=pause
    with e.client:
        view=e.view();assert await asyncio.to_thread(entered.wait,5)
        view.set_active(True);view.set_active(True)
        await view.measure_legacy_capacity()
        assert e.reader.calls==['1001'] and e.reader.max_active==1
        assert e.cache.commit_success('1001',0,777)
        release.set();await e.schedule.drain()
        assert e.cache.snapshot('1001')==(1,777)
        assert view.state.snapshot.author_capacities['1001']==777


@pytest.mark.asyncio
async def test_service_shutdown_during_measurement_does_not_write_or_reschedule(factory):
    e=factory(1);entered,release=Event(),Event();e.releases.append(release)
    def pause(aid,ordinal):entered.set();assert release.wait(5)
    e.reader.gate=pause
    with e.client:
        view=e.view();assert await asyncio.to_thread(entered.wait,5)
        e.services.close();release.set();await e.schedule.drain()
        assert e.cache.snapshot('1001')==(0,None)
        assert e.reader.calls==['1001'] and not view._legacy_capacity_busy
        count=len(e.schedule.tasks);view.set_active(True)
        assert len(e.schedule.tasks)==count
