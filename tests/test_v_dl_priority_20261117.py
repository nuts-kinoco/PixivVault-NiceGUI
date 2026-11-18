"""WU-6: one home for the stars, the periodic check on the engine, downloads win over background work."""
import sqlite3
import threading
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui.batch_engine import BatchRunEngine
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, run_author_check
from pixivvault_nicegui.services import ApplicationServices
from test_t_dl_engine_20261117 import FakePort, cand, wait_for

ACCOUNT = '55'


class Reader:
    def __init__(self, count):
        self.count = count

    def require_identity(self):
        return ACCOUNT

    def get_following_users_detailed(self, uid, rest):
        if rest == 'hide':
            return ()
        return tuple(dict(user_id=str(1000 + i), name='n%d' % i, account='', profile_img='')
                     for i in range(self.count))


@pytest.fixture
def world(tmp_path):
    (tmp_path / 'managed').mkdir()
    following = FollowingSyncStore(tmp_path / 'follow')
    scheduler = ManagedScheduler(tmp_path / 'managed')
    yield tmp_path, following, scheduler
    scheduler.close()
    following.close()


def sync(following, count):
    following.sync(Reader(count))


def now_state(scheduler):
    return scheduler.snapshot()


# ---- one home for the stars --------------------------------------------------------------

def test_stars_are_empty_until_a_follow_sync_and_flip_in_the_ledger(world):
    _, following, scheduler = world
    book = FavouriteBook(following, scheduler=scheduler)
    assert book.ids() == () and book.set('1000', True) is False   # nothing synced yet
    sync(following, 5)
    assert book.set('1002', True) and book.set('1000', True)
    assert book.ids() == ('1000', '1002')
    assert book.set('9999', True) is False                        # a star lives on a follow row only
    assert book.set('1002', False) and book.ids() == ('1000',)


def test_the_old_scheduler_list_keeps_working_until_the_one_time_import_has_run(world):
    _, following, scheduler = world
    scheduler.set_favourites(('1001', '1003'), expected_revision=now_state(scheduler).revision)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))   # no sync yet
    assert scheduler.favourites_seeded() is False
    assert scheduler.favourites() == ('1001', '1003')            # the schedule does not go quiet


def test_import_is_the_union_of_the_legacy_db_and_the_scheduler_list_and_runs_once(world):
    tmp_path, following, scheduler = world
    scheduler.set_favourites(('1001', '1003', '7777'), expected_revision=now_state(scheduler).revision)
    legacy = tmp_path / 'legacy.db'
    database = sqlite3.connect(str(legacy))
    database.execute('CREATE TABLE following_users (user_id TEXT, is_favorite INTEGER)')
    database.executemany('INSERT INTO following_users VALUES (?,?)',
                         [('1003', 1), ('1004', 1), ('1000', 0), ('8888', 1)])
    database.commit()
    database.close()
    before = legacy.read_bytes()
    sync(following, 6)
    book = FavouriteBook(following, scheduler=scheduler, legacy_db=legacy)
    scheduler.attach_favourites(book)
    assert scheduler.favourites_seeded()
    assert scheduler.favourites() == ('1001', '1003', '1004')
    assert tuple(scheduler.favourite_seed_result()) == (3, 2)    # 7777 and 8888 are not followed: dropped
    assert book.seed() is None                                    # never twice
    assert legacy.read_bytes() == before                          # the legacy copy is only read


def test_a_star_change_reaches_the_periodic_check_at_once(world):
    _, following, scheduler = world
    sync(following, 4)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    assert scheduler.favourites_seeded() and scheduler.favourites() == ()
    book.set('1001', True)
    assert scheduler.favourites() == ('1001',)
    book.set('1001', False)
    assert scheduler.favourites() == ()


def test_more_than_128_stars_are_kept_and_all_returned(world):
    _, following, scheduler = world
    sync(following, 260)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    ids = tuple(str(1000 + i) for i in range(200))
    saved = scheduler.set_favourites(ids, expected_revision=now_state(scheduler).revision)
    assert len(saved) == 200 and len(scheduler.favourites()) == 200


def test_the_old_128_limit_is_gone_even_before_the_import(world):
    _, _, scheduler = world
    ids = tuple(str(100 + i) for i in range(300))
    assert len(scheduler.set_favourites(ids, expected_revision=now_state(scheduler).revision)) == 300


def test_replacing_the_stars_through_the_scheduler_writes_the_ledger_and_prunes_deferred_work(world):
    _, following, scheduler = world
    sync(following, 5)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    scheduler.set_favourites(('1001', '1002'), expected_revision=now_state(scheduler).revision)
    scheduler.defer_work(author_id='1001', work=dict(id='5', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc))
    assert scheduler.has_deferred()
    scheduler.set_favourites(('1002',), expected_revision=now_state(scheduler).revision)
    assert scheduler.favourites() == ('1002',) and not scheduler.has_deferred()


# ---- the periodic check on the engine ------------------------------------------------------

class Services:
    closing = False

    def __init__(self, engine, following):
        self.batch_runs = engine
        self.following = following

    def start_batch_run(self, ids, origin='manual', target_type=None):
        self.engine_account = ACCOUNT
        self.batch_runs.account_id = ACCOUNT
        return self.batch_runs.start(ids, origin=origin, target_type=target_type)


@pytest.fixture
def auto(world):
    tmp_path, following, scheduler = world
    sync(following, 210)
    ledger = BatchRunLedger(tmp_path / 'runs', synchronous='NORMAL')
    port = FakePort({str(1000 + i): () for i in range(210)})
    port.ledger = ledger
    engine = BatchRunEngine(port, ledger, following=following, account_id=ACCOUNT,
                            poll_seconds=0.001, wait=lambda s: None)
    yield NS(tmp_path=tmp_path, following=following, scheduler=scheduler, ledger=ledger, port=port,
             engine=engine, services=Services(engine, following))
    ledger.close()


def test_periodic_check_runs_every_star_author_in_turn_and_stamps_them(auto):
    ids = tuple(str(1000 + i) for i in range(200))
    for uid in ids:
        auto.following.set_favorite(ACCOUNT, uid, True)
    result = run_author_check(auto.services, auto.scheduler.favourites() or ids,
                              cancellation=threading.Event(), ledger=auto.scheduler, slot='s1', poll=0.01)
    assert result['authors'] == 200
    run = auto.ledger.runs(1)[0]
    assert run.origin == 'auto' and run.state == 'done' and run.target_type == 'both'
    assert [a.state for a in auto.ledger.authors(run.run_id)] == ['done'] * 200
    stamped = {a.user_id for a in auto.following.snapshot(ACCOUNT).authors if a.last_downloaded}
    assert stamped == set(ids)                                    # last_downloaded is updated too


def test_periodic_check_never_joins_a_run_the_user_started(auto):
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)
    auto.engine.start(('1000', '1001'))
    assert wait_for(lambda: auto.engine.active)
    result = run_author_check(auto.services, ('1002',), cancellation=threading.Event(),
                              ledger=auto.scheduler, slot='s2', poll=0.01)
    assert result['accepted'] == 0 and len(auto.ledger.runs(5)) == 1
    gate.set()
    assert auto.engine.join(5)


def test_a_gate_paused_automatic_run_is_ended_so_it_cannot_block_every_later_slot(auto):
    auto.port.gate_closed = True
    auto.port.inventories['1000'] = (cand('1000', 1),)
    result = run_author_check(auto.services, ('1000',), cancellation=threading.Event(),
                              ledger=auto.scheduler, slot='s3', poll=0.01)
    assert auto.port.enqueued == []                               # no traffic while the gate is closed
    assert auto.engine.active is False and auto.ledger.runs(1)[0].state == 'stopped'
    assert result['accepted'] == 0
    auto.port.gate_closed = False                                 # the next slot is differential and runs
    run_author_check(auto.services, ('1000',), cancellation=threading.Event(),
                     ledger=auto.scheduler, slot='s3b', poll=0.01)
    assert auto.ledger.runs(1)[0].state == 'done' and len(auto.port.enqueued) == 1


def test_the_manual_check_returns_at_once_and_the_run_goes_on(auto):
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)
    result = run_author_check(auto.services, ('1000', '1001'), cancellation=threading.Event(),
                              ledger=auto.scheduler, slot='s5', poll=0.01, wait=False)
    assert result['started'] is True and auto.engine.active
    gate.set()
    assert auto.engine.join(5) and auto.ledger.runs(1)[0].state == 'done'


def test_a_late_first_sync_imports_the_stars_on_the_next_read_and_stales_an_open_editor(world):
    _, following, scheduler = world
    scheduler.set_favourites(('1001',), expected_revision=now_state(scheduler).revision)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)                             # nothing synced yet
    assert scheduler.favourites() == ('1001',)
    editor_revision = now_state(scheduler).revision               # a settings form is opened here
    sync(following, 4)
    assert scheduler.favourites() == ('1001',) and scheduler.favourites_seeded()   # seeded lazily
    with pytest.raises(Exception):                                # the stale form cannot wipe the stars
        scheduler.set_favourites((), expected_revision=editor_revision)
    assert scheduler.favourites() == ('1001',)


def test_dispatch_author_check_uses_the_engine_when_provisioned(auto):
    ledger = auto.scheduler
    auto.following.set_favorite(ACCOUNT, '1001', True)
    ledger.attach_favourites(FavouriteBook(auto.following, scheduler=ledger))
    services = NS(batch_runs=auto.engine, closing=False, start_batch_run=auto.services.start_batch_run)
    result = ApplicationServices.dispatch_author_check(services, ledger, 'd1', threading.Event())
    assert result['authors'] == 1 and auto.ledger.runs(1)[0].origin == 'auto'
    assert any(a.last_downloaded for a in auto.following.snapshot(ACCOUNT).authors if a.user_id == '1001')


def test_a_cancelled_slot_stops_the_run(auto):
    cancel = threading.Event()
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)
    threading.Timer(0.2, lambda: (cancel.set(), gate.set())).start()
    run_author_check(auto.services, ('1000', '1001', '1002'), cancellation=cancel,
                     ledger=auto.scheduler, slot='s4', poll=0.01)
    assert auto.ledger.runs(1)[0].state == 'stopped'


# ---- downloads win over background work ---------------------------------------------------

def test_a_batch_run_counts_as_a_download_even_when_the_job_queue_is_empty():
    idle = NS(batch_runs=NS(active=False), jobs=NS(snapshot=lambda: []))
    idle.batch_run_active = lambda: ApplicationServices.batch_run_active(idle)
    assert ApplicationServices._download_busy(idle) is False
    busy = NS(batch_runs=NS(active=True), jobs=NS(snapshot=lambda: []))
    busy.batch_run_active = lambda: ApplicationServices.batch_run_active(busy)
    assert ApplicationServices._download_busy(busy) is True
    assert ApplicationServices.batch_run_active(NS(batch_runs=None)) is False


def test_background_work_waits_while_the_engine_lists_or_waits_between_authors(auto):
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)           # the 4-6 s wait between authors
    services = NS(batch_runs=auto.engine, jobs=NS(snapshot=lambda: []))
    services.batch_run_active = lambda: ApplicationServices.batch_run_active(services)
    auto.engine.start(('1000', '1001'))
    assert wait_for(lambda: auto.ledger.authors(auto.ledger.active_run().run_id)[0].state == 'done')
    assert ApplicationServices._download_busy(services) is True   # zero jobs, still a download
    gate.set()
    assert auto.engine.join(5)
    assert ApplicationServices._download_busy(services) is False


@pytest.mark.asyncio
async def test_metadata_backfill_waits_while_an_author_run_is_active_even_with_an_empty_queue(tmp_path):
    import asyncio
    from test_i_metadata_auto_20261117 import Transport, make_services
    services = await make_services(tmp_path)
    try:
        services.batch_runs = NS(active=True)                  # no job at all, but a run is listing
        transport = Transport()
        summary = await asyncio.to_thread(services.run_managed_metadata_all, transport=transport)
        assert summary.stopped == 'download_busy' and transport.calls == []
        one = await asyncio.to_thread(services.run_managed_metadata_one, '202', transport=Transport())
        assert one.stopped == 'download_busy'
        services.batch_runs = NS(active=False)
        again = Transport()
        assert (await asyncio.to_thread(services.run_managed_metadata_all, transport=again)).stopped == ''
        assert again.calls                                      # it runs once the run is over
    finally:
        services.batch_runs = None
        await asyncio.to_thread(services.close)


# ---- the real services: provisioning, restart, close ------------------------------------------

@pytest.mark.asyncio
async def test_real_services_provision_the_engine_recover_after_a_restart_and_close_cleanly(tmp_path):
    import asyncio
    from test_managed_download_services import FakeSource
    root, exports, prefs = tmp_path / 'managed', tmp_path / 'exports', tmp_path / 'prefs'
    runs = tmp_path / 'batch-runs'

    def build():
        services = ApplicationServices(live_client=FakeSource(), pixiv_download_root=root,
                                       preferences_root=prefs)
        services.configure_save_exports(exports, '12')       # brings the all-media author path with it
        return services
    services = build()
    try:
        engine = services.enable_batch_runs(runs)
        assert services.enable_batch_runs(runs) is engine and services.batch_run_active() is False
        assert services._download_busy() is False
        # the periodic check is provisioned together with the schedule and reaches the same engine
        assert services.enable_managed_scheduler() is services.managed_scheduler
        assert services.batch_runs is engine and services.managed_scheduler.favourites_seeded() is False
        run_id = services.batch_run_ledger.begin_run('authors', 'manual', 'both', ('41', '42'))
    finally:
        await asyncio.to_thread(services.close)
    reopened = build()
    try:
        engine = reopened.enable_batch_runs(runs)
        assert reopened.batch_run_ledger.run(run_id).state == 'interrupted'   # never restarted by itself
        assert engine.resumable_run().run_id == run_id and engine.active is False
    finally:
        await asyncio.to_thread(reopened.close)
