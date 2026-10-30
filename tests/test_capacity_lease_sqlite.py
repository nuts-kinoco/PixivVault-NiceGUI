"""Real SQLite transaction boundaries for cancel versus commit decision."""
import asyncio
import sqlite3
from threading import Event
import pytest
from pixivvault_nicegui.capacity_cache import CapacityCache,CommitOutcomeUnknown
from pixivvault_nicegui.legacy_capacity import MeasurementLease

class WrappedConnection:
    def __init__(self,db,*,before_begin=None,before_commit=None,fail_commit=None,fail_rollback=False):
        self.db=db;self.before_begin=before_begin;self.before_commit=before_commit;self.fail_commit=fail_commit;self.fail_rollback=fail_rollback
    def close(self):self.db.close()
    @property
    def in_transaction(self):return self.db.in_transaction
    def execute(self,sql,*args):
        if sql=='ROLLBACK' and self.fail_rollback:raise sqlite3.OperationalError('synthetic rollback failure')
        if sql=='BEGIN IMMEDIATE' and self.before_begin:self.before_begin()
        if sql=='COMMIT':
            if self.before_commit:self.before_commit()
            if self.fail_commit=='before':raise sqlite3.OperationalError('synthetic commit failure')
            if self.fail_commit=='after':
                self.db.execute(sql,*args);raise sqlite3.OperationalError('synthetic lost result')
        return self.db.execute(sql,*args)

@pytest.mark.asyncio
async def test_real_sqlite_lock_cancel_before_decision_preserves_restart(tmp_path):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False,timeout=5)
    cache=CapacityCache(db);cache.initialize();assert cache.commit_success('12',0,9)
    blocker=sqlite3.connect(path,check_same_thread=False);blocker.execute('BEGIN IMMEDIATE')
    entered=Event();cache._db=WrappedConnection(db,before_begin=entered.set)
    lease=MeasurementLease();task=asyncio.create_task(asyncio.to_thread(lease.publish,cache,{'12':42},{'12':1}))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        assert lease.cancel()==MeasurementLease.CANCELLED
    finally:blocker.rollback();blocker.close()
    assert await task=={} and lease.state==MeasurementLease.CANCELLED
    cache.close();db=sqlite3.connect(path)
    try:assert CapacityCache(db).snapshot('12')==(1,9)
    finally:db.close()

@pytest.mark.asyncio
async def test_cancel_after_decision_does_not_claim_commit_before_success(tmp_path):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize();assert cache.commit_success('12',0,9)
    entered,release=Event(),Event()
    def pause():entered.set();release.wait(5)
    cache._db=WrappedConnection(db,before_commit=pause);lease=MeasurementLease()
    task=asyncio.create_task(asyncio.to_thread(lease.publish,cache,{'12':42},{'12':1}))
    try:
        assert await asyncio.to_thread(entered.wait,5)
        assert lease.state==MeasurementLease.SEALED
        assert lease.cancel()==MeasurementLease.SEALED
        assert lease.state==MeasurementLease.SEALED
    finally:release.set()
    assert await task=={'12':42} and lease.state==MeasurementLease.COMMITTED
    cache.close();db=sqlite3.connect(path)
    try:assert CapacityCache(db).snapshot('12')==(2,42)
    finally:db.close()

@pytest.mark.parametrize('failure,expected_state,expected_snapshot',[
    ('before',MeasurementLease.FAILED,(1,9)),('after',MeasurementLease.UNKNOWN,(2,42))])
def test_commit_failure_and_unknown_result_are_distinct_after_restart(tmp_path,failure,expected_state,expected_snapshot):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize();assert cache.commit_success('12',0,9)
    cache._db=WrappedConnection(db,fail_commit=failure);lease=MeasurementLease()
    expected_exception=CommitOutcomeUnknown if failure=='after' else sqlite3.OperationalError
    with pytest.raises(expected_exception):lease.publish(cache,{'12':42},{'12':1})
    assert lease.state==expected_state
    cache.close();db=sqlite3.connect(path)
    try:assert CapacityCache(db).snapshot('12')==expected_snapshot
    finally:db.close()

def test_batch_failure_none_and_stale_revision_do_not_overwrite(tmp_path):
    db=sqlite3.connect(tmp_path/'cache.sqlite3');cache=CapacityCache(db);cache.initialize()
    try:
        assert cache.commit_success('12',0,9)
        lease=MeasurementLease()
        assert lease.publish(cache,{'12':50,'13':None,'14':0},{'12':0,'13':0,'14':0})=={'14':0}
        assert cache.snapshot('12')==(1,9) and cache.snapshot('13')==(0,None)
        assert cache.snapshot('14')==(1,0)
    finally:cache.close()

@pytest.mark.asyncio
async def test_view_invalidation_after_decision_rejects_late_screen_update(tmp_path,monkeypatch):
    from nicegui import Client,ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize()
    class Reader:
        root=tmp_path/'legacy'
        def measure(self,author_id):return 456
    services=ApplicationServices(120);services.configure_legacy_capacity(Reader(),cache)
    entered,release=Event(),Event()
    def pause():entered.set();release.wait(5)
    cache._db=WrappedConnection(db,before_commit=pause)
    client=Client(ui.page('/_capacity_sealed_view'))
    hydration_tasks=[]
    def schedule(coro,**kwargs):
        task=asyncio.create_task(coro);hydration_tasks.append(task);return task
    monkeypatch.setattr('nicegui.background_tasks.create',schedule)
    task=None
    try:
        with client:view=LibraryView(services)
        assert len(hydration_tasks)==1
        await hydration_tasks[0]
        original=view.state.snapshot
        first=view.state.visible_authors[0].author_id
        # The first uncached author now starts through the actual activation path.
        task=view._legacy_auto_task
        assert task is not None
        assert await asyncio.to_thread(entered.wait,5)
        assert view._legacy_lease.state==MeasurementLease.SEALED
        view.set_active(False)
        assert view._legacy_capacity_busy and view.legacy_capacity_status.text==''
        release.set();await task
        assert not view._legacy_capacity_busy
        assert view.state.snapshot is original
        assert cache.snapshot(first)==(1,456)
    finally:
        release.set()
        if task is not None:await task
        if hydration_tasks:await asyncio.gather(*hydration_tasks,return_exceptions=True)
        client.delete();services.close();cache.close()


def test_rollback_failure_is_unknown_until_reopen(tmp_path):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize();assert cache.commit_success('12',0,9)
    cache._db=WrappedConnection(db,fail_commit='before',fail_rollback=True);lease=MeasurementLease()
    with pytest.raises(CommitOutcomeUnknown):lease.publish(cache,{'12':42},{'12':1})
    assert lease.state==MeasurementLease.UNKNOWN
    cache.close();db=sqlite3.connect(path)
    try:assert CapacityCache(db).snapshot('12')==(1,9)
    finally:db.close()


def test_cancel_after_decision_then_commit_failure_retains_old_value(tmp_path):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize();assert cache.commit_success('12',0,9)
    lease=MeasurementLease();seen=[]
    cache._db=WrappedConnection(db,before_commit=lambda:seen.append(lease.cancel()),fail_commit='before')
    with pytest.raises(sqlite3.OperationalError):lease.publish(cache,{'12':42},{'12':1})
    assert seen==[MeasurementLease.SEALED] and lease.state==MeasurementLease.FAILED
    assert cache.snapshot('12')==(1,9)
    cache.close()


@pytest.mark.asyncio
async def test_owned_cache_close_waits_for_inflight_worker_before_reopen(tmp_path):
    path=tmp_path/'cache.sqlite3';db=sqlite3.connect(path,check_same_thread=False)
    cache=CapacityCache(db);cache.initialize();entered,release,closing=Event(),Event(),Event()
    def pause():entered.set();release.wait(5)
    cache._db=WrappedConnection(db,before_commit=pause);lease=MeasurementLease()
    writing=asyncio.create_task(asyncio.to_thread(lease.publish,cache,{'12':42},{'12':0}))
    def close():closing.set();cache.close()
    closing_task=None
    try:
        assert await asyncio.to_thread(entered.wait,5)
        closing_task=asyncio.create_task(asyncio.to_thread(close))
        assert await asyncio.to_thread(closing.wait,5)
        assert not closing_task.done()
    finally:release.set()
    assert await writing=={'12':42}
    await closing_task
    db=sqlite3.connect(path)
    try:assert CapacityCache(db).snapshot('12')==(1,42)
    finally:db.close()
