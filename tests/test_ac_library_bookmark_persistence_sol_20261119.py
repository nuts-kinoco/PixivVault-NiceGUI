import asyncio
import sqlite3
import json
from datetime import datetime

import pytest

from pixivvault_nicegui.bookmark_cache_store import BookmarkCacheStore
from pixivvault_nicegui.services import ApplicationServices
from test_managed_download_services import FakeSource


class AccountSource(FakeSource):
    def __init__(self,user_id='12'):
        super().__init__()
        self.user_id=user_id
        self._verified_identity=None
    def verify_identity(self):
        self.calls.append('identity')
        self._verified_identity=self.user_id
        self._verified_identity = self.user_id
        return self._verified_identity
    def get_bookmarked_works(self,identity,rest_type):
        assert identity==self.user_id
        self.calls.append(('bookmarks',rest_type))
        if rest_type=='hide':
            yield {'id':'204','title':'synthetic private','user_id':'23','user_name':'fixture',
                   'tags':[],'type':0,'thumb_url':'https://i.pximg.net/synthetic/204.png','page_count':1}


async def seed(root):
    first=ApplicationServices(live_client=AccountSource(),preferences_root=root)
    try:
        await first.refresh_bookmark_cache()
        assert first._remote_records[0].visibility=='hide'
        assert first.bookmark_remote_snapshot()[0].work_id=='204'
        return first.bookmark_cache_fetched_at
    finally:first.close()


@pytest.mark.asyncio
async def test_cache_account_binding_cannot_be_relabelled(tmp_path):
    root=tmp_path/'prefs'
    await seed(root)
    source=AccountSource('13')
    second=ApplicationServices(live_client=source,preferences_root=root)
    try:
        assert not await second.restore_bookmark_cache()
        assert second.bookmark_remote_snapshot() is None
    finally:second.close()
    # Inject one synthetic on-disk integrity fault before opening new owner.
    with sqlite3.connect(root/'bookmark-cache.sqlite3') as db:
        db.execute("UPDATE snapshot SET account=? WHERE id=1",('13',))
    second=ApplicationServices(live_client=AccountSource('13'),preferences_root=root)
    try:
        adopted=await second.restore_bookmark_cache()
        print('relabelled snapshot adopted',adopted)
        assert not adopted
        assert second.bookmark_remote_snapshot() is None
    finally:second.close()


@pytest.mark.asyncio
async def test_revision_corruption_does_not_block_live_refresh(tmp_path):
    root=tmp_path/'prefs'
    await seed(root)
    with sqlite3.connect(root/'bookmark-cache.sqlite3') as db:
        db.execute("UPDATE cache_revision SET value='invalid'")
    source=AccountSource()
    services=ApplicationServices(live_client=source,preferences_root=root)
    try:
        remote,_=await services.refresh_bookmark_cache()
        assert [w.work_id for w in remote]==['204']
        assert 'identity' in source.calls
        assert services.bookmark_cache_persist_failed
    finally:services.close()


@pytest.mark.asyncio
async def test_unbound_older_snapshot_is_only_replaced_after_complete_refresh(tmp_path):
    root=tmp_path/'prefs'
    await seed(root)
    with sqlite3.connect(root/'bookmark-cache.sqlite3') as db:
        payload=db.execute('SELECT payload FROM snapshot').fetchone()[0]
        db.execute('UPDATE snapshot SET payload=?', (json.dumps(json.loads(payload)['records']),))
    svc=ApplicationServices(live_client=AccountSource(),preferences_root=root)
    try:
        assert not await svc.restore_bookmark_cache()
        assert svc.bookmark_remote_snapshot() is None
        await svc.refresh_bookmark_cache()
        assert svc._bookmark_cache_store.read('12')[0][0].work_id=='204'
    finally:svc.close()


@pytest.mark.asyncio
async def test_turning_off_restore_while_waiting_does_not_verify_account(tmp_path):
    root=tmp_path/'prefs'
    await seed(root)
    source=AccountSource()
    services=ApplicationServices(live_client=source,preferences_root=root)
    try:
        async with services._library_refresh_lock:
            task=asyncio.create_task(services.restore_bookmark_cache())
            await asyncio.sleep(0)
            services.preferences.set_setting('bookmark_auto_refresh','0')
        try:
            result=await task
        except RuntimeError:
            result='stale'
        print('queued restore',result,'identity calls',source.calls.count('identity'))
        assert source.calls==[]
        assert services.bookmark_remote_snapshot() is None
    finally:services.close()


@pytest.mark.asyncio
async def test_old_private_cache_is_not_a_verified_live_account_cache(tmp_path):
    images=tmp_path/'images'
    images.mkdir()
    legacy=tmp_path/'readonly'
    legacy.mkdir()
    db=legacy/'old.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,user_id TEXT,tags TEXT,is_deleted INTEGER,content_type TEXT);'
            'CREATE TABLE following_users(user_id TEXT,name TEXT);'
            'CREATE TABLE settings(key TEXT,value TEXT);'
            'CREATE TABLE bookmark_remote_cache(work_id TEXT,is_novel INTEGER,title TEXT,user_id TEXT,'
            'user_name TEXT,tags TEXT,create_date TEXT,thumb_url TEXT,rest_type TEXT,x_restrict INTEGER);')
        conn.execute('INSERT INTO settings VALUES (?,?)',('bookmark_cache_fetched_at',datetime.now().isoformat()))
        conn.execute('INSERT INTO bookmark_remote_cache VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('205',0,'different old private','23','fixture','[]','',
             'https://i.pximg.net/synthetic/205.png','hide',0))
    before=db.read_bytes()
    source=AccountSource('13')
    svc=ApplicationServices(live_client=source,preferences_root=tmp_path/'prefs',
        library_readonly_db=db,library_image_root=images,legacy_copy_root=_legacy_copy_root(tmp_path))
    try:
        immediate=svc.bookmark_remote_snapshot()
        remote,_=await svc.refresh_bookmark_cache(automatic=True)
        print('unbound old private visible before verify and after auto',bool(immediate),
              [w.work_id for w in remote if remote],source.calls.count('identity'))
        assert immediate is None or all(w.work_id!='205' for w in immediate)
        assert all(w.work_id!='205' for w in remote)
    finally:
        svc.close()
        assert db.read_bytes()==before


def test_durable_cas_across_connections_and_ambiguous_commit_recovery(tmp_path):
    first=BookmarkCacheStore(tmp_path)
    second=BookmarkCacheStore(tmp_path)
    now='2026-11-19T08:00:00'
    try:
        old_rev=first.revision()
        assert old_rev==second.revision()==0
        assert first.write('12',now,(),expected_revision=old_rev)
        assert not second.write('13',now,(),expected_revision=old_rev)
        assert second.read('13') is None and second.read('12')==((),now)
        class Fault:
            def __init__(self,inner,post):self.inner=inner;self.post=post;self.commits=0
            def __getattr__(self,key):return getattr(self.inner,key)
            def execute(self,command,*args):
                if command=='COMMIT':
                    self.commits+=1
                    if self.post:self.inner.execute(command,*args)
                    raise sqlite3.OperationalError('generated uncertain commit')
                return self.inner.execute(command,*args)
        original=first._db
        prev=Fault(original,post=False)
        first._db=prev
        with pytest.raises(sqlite3.OperationalError):
            first.write('12','2026-11-19T08:01:00',(),expected_revision=1)
        assert prev.commits==1 and second.revision()==1 and second.read('12')==((),now)
        after=Fault(original,post=True)
        first._db=after
        assert first.write('12','2026-11-19T08:02:00',(),expected_revision=1)
        assert after.commits==1 and second.revision()==2
        assert second.read('12')==((),'2026-11-19T08:02:00')
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_actual_stop_integration_blocks_unverified_restore_and_auto_refresh(tmp_path):
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped
    root=tmp_path/'prefs'
    await seed(root)
    read=AccountSource()
    svc=ApplicationServices(live_client=read,preferences_root=root)
    original=tmp_path/'managed'
    writer=ManagedPixivDownloads(original,object(),resolve_record=lambda _:None)
    writer.close()
    svc.managed_network_stop=ManagedNetworkStopIntegration(original)
    try:
        svc.managed_network_stop.gate.trip(403)
        with pytest.raises(ManagedNetworkStopped):
            await svc.restore_bookmark_cache()
        with pytest.raises(ManagedNetworkStopped):
            await svc.refresh_bookmark_cache(automatic=True)
        assert read.calls==[] and svc.bookmark_remote_snapshot() is None
    finally:svc.close()


@pytest.mark.asyncio
async def test_verified_identity_can_restore_without_network_during_stop(tmp_path):
    from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
    from pixivvault_nicegui.managed_network_stop_integration import ManagedNetworkStopIntegration
    root=tmp_path/'prefs'
    await seed(root)
    reader=AccountSource()
    reader._verified_identity='12'
    svc=ApplicationServices(live_client=reader,preferences_root=root)
    writer=ManagedPixivDownloads(tmp_path/'managed',object(),resolve_record=lambda _:None)
    writer.close()
    svc.managed_network_stop=ManagedNetworkStopIntegration(tmp_path/'managed')
    try:
        svc.managed_network_stop.gate.trip(403)
        assert await svc.restore_bookmark_cache()
        assert [w.work_id for w in svc.bookmark_remote_snapshot()]==['204']
        assert reader.calls==[]
    finally:svc.close()


@pytest.mark.asyncio
async def test_each_view_adopts_verified_stale_cache_while_one_listing_is_pending(tmp_path,monkeypatch):
    from nicegui import Client,core,ui
    from threading import Event
    from pixivvault_nicegui.ui.library_view import LibraryView
    monkeypatch.setattr(core,'loop',asyncio.get_running_loop())
    root=tmp_path/'prefs'
    await seed(root)
    with sqlite3.connect(root/'bookmark-cache.sqlite3') as db:
        db.execute("UPDATE snapshot SET fetched_at='2020-01-01T00:00:00'")
    class Delayed(AccountSource):
        def __init__(self):
            super().__init__()
            self.entered,self.release=Event(),Event()
        def get_bookmarked_works(self,identity,rest_type):
            self.entered.set()
            assert self.release.wait(5)
            yield from super().get_bookmarked_works(identity,rest_type)
    reader=Delayed()
    svc=ApplicationServices(live_client=reader,preferences_root=root)
    clients=[Client(ui.page(f'/_sol_k06_durable_{i}')) for i in range(2)]
    views=[]
    async def until(pred):
        async def wait():
            while not pred():await asyncio.sleep(.01)
        await asyncio.wait_for(wait(),5)
    try:
        for client in clients:
            with client:
                view=LibraryView(svc)
                views.append(view)
                view.show_subview('bookmarks')
        await until(reader.entered.is_set)
        statuses=[v.bookmark_state.status for v in views]
        titles=[v.bookmarks.freshness.text for v in views]
        reader.release.set()
        await until(lambda:all(not v._bookmark_refreshing for v in views))
        print('two views while listing held',statuses,titles,'identity',reader.calls.count('identity'))
        assert reader.calls.count(('bookmarks','show'))==1
        assert reader.calls.count('identity')==1
        assert statuses==['ready','ready'] and all('2020-01-01' in t for t in titles)
    finally:
        reader.release.set()
        for view,client in zip(views,clients):
            view.close()
            client.delete()
        svc.close()


def _legacy_copy_root(tmp_path):
    root = tmp_path.parent / (tmp_path.name + '-legacy-copies')
    root.mkdir(exist_ok=True)
    return root
