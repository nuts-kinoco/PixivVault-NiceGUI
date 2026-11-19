"""WU-6: one home for the stars, the periodic check on the engine, downloads win over background work."""
import threading
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from account_test_support import observed_scope
from pixivvault_nicegui.batch_engine import BatchRunEngine
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.favourites import FavouriteBook
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_scheduler import ManagedScheduler, ScheduleAccountError, ScheduleError, run_author_check
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

def test_stars_are_unavailable_until_a_follow_sync_and_flip_in_the_ledger(world):
    _, following, scheduler = world
    book = FavouriteBook(following, scheduler=scheduler)
    with pytest.raises(ScheduleError, match='unavailable'):
        book.ids(ACCOUNT)
    with pytest.raises(ScheduleAccountError):
        book.set('1000', True, account_id=None)
    sync(following, 5)
    assert book.set('1002', True, account_id=ACCOUNT) and book.set('1000', True, account_id=ACCOUNT)
    assert book.ids(ACCOUNT) == ('1000', '1002')
    assert book.set('9999', True, account_id=ACCOUNT) is False                        # a star lives on a follow row only
    assert book.set('1002', False, account_id=ACCOUNT) and book.ids(ACCOUNT) == ('1000',)


def test_the_unowned_scheduler_list_is_preserved_and_never_used_as_follow_stars(world):
    _, following, scheduler = world
    ids = ('1001', '1003')
    scheduler.set_favourites(ids, expected_revision=now_state(scheduler).revision, account_id=None)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    with pytest.raises(ScheduleError, match='unavailable'):
        scheduler.favourites(ACCOUNT)
    sync(following, 5)
    assert scheduler.favourites(ACCOUNT) == ()
    with pytest.raises(ScheduleAccountError):
        scheduler.bind_account(ACCOUNT, scope=observed_scope(ACCOUNT))
    assert scheduler.legacy_favourites() == ids


def test_a_star_change_reaches_the_periodic_check_at_once(world):
    _, following, scheduler = world
    sync(following, 4)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    assert scheduler.favourites(ACCOUNT) == ()
    book.set('1001', True, account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1001',)
    book.set('1001', False, account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ()


def test_more_than_128_stars_are_kept_and_all_returned(world):
    _, following, scheduler = world
    sync(following, 260)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    ids = tuple(str(1000 + i) for i in range(200))
    saved = scheduler.set_favourites(ids, expected_revision=now_state(scheduler).revision, account_id=ACCOUNT)
    assert len(saved) == 200 and len(scheduler.favourites(ACCOUNT)) == 200


def test_the_old_128_limit_is_gone_in_the_standalone_scheduler(world):
    _, _, scheduler = world
    ids = tuple(str(100 + i) for i in range(300))
    assert len(scheduler.set_favourites(ids, expected_revision=now_state(scheduler).revision, account_id=None)) == 300


def test_replacing_the_stars_through_the_scheduler_writes_the_ledger_and_prunes_deferred_work(world):
    _, following, scheduler = world
    sync(following, 5)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    scheduler.bind_account(ACCOUNT, scope=observed_scope(ACCOUNT))
    scheduler.set_favourites(('1001', '1002'), expected_revision=now_state(scheduler).revision, account_id=ACCOUNT)
    scheduler.defer_work(author_id='1001', work=dict(id='5', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert scheduler.has_deferred()
    scheduler.set_favourites(('1002',), expected_revision=now_state(scheduler).revision, account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1002',) and not scheduler.has_deferred()


# ---- the periodic check on the engine ------------------------------------------------------

class Services:
    closing = False

    def __init__(self, engine, following):
        self.batch_runs = engine
        self.following = following

    def start_batch_run(self, ids, *, account_id, origin='manual', target_type=None, on_finish=None):
        self.engine_account = account_id
        return self.batch_runs.start(ids, origin=origin, target_type=target_type, account_id=account_id,
                                     on_finish=on_finish)


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
    result = run_author_check(auto.services, auto.scheduler.favourites(ACCOUNT) or ids,
                              cancellation=threading.Event(), ledger=auto.scheduler, slot='s1', poll=0.01, account_id=ACCOUNT)
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
                              ledger=auto.scheduler, slot='s2', poll=0.01, account_id=ACCOUNT)
    assert result['accepted'] == 0 and len(auto.ledger.runs(5)) == 1
    gate.set()
    assert auto.engine.join(5)


def test_a_gate_paused_automatic_run_is_ended_so_it_cannot_block_every_later_slot(auto):
    auto.port.gate_closed = True
    auto.port.inventories['1000'] = (cand('1000', 1),)
    result = run_author_check(auto.services, ('1000',), cancellation=threading.Event(),
                              ledger=auto.scheduler, slot='s3', poll=0.01, account_id=ACCOUNT)
    assert auto.port.enqueued == []                               # no traffic while the gate is closed
    assert auto.engine.active is False and auto.ledger.runs(1)[0].state == 'stopped'
    assert result['accepted'] == 0
    auto.port.gate_closed = False                                 # the next slot is differential and runs
    run_author_check(auto.services, ('1000',), cancellation=threading.Event(),
                     ledger=auto.scheduler, slot='s3b', poll=0.01, account_id=ACCOUNT)
    assert auto.ledger.runs(1)[0].state == 'done' and len(auto.port.enqueued) == 1


def test_the_manual_check_returns_at_once_and_the_run_goes_on(auto):
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)
    result = run_author_check(auto.services, ('1000', '1001'), cancellation=threading.Event(),
                              ledger=auto.scheduler, slot='s5', poll=0.01, wait=False, account_id=ACCOUNT)
    assert result['started'] is True and auto.engine.active
    gate.set()
    assert auto.engine.join(5) and auto.ledger.runs(1)[0].state == 'done'


def test_a_late_first_sync_stales_an_editor_opened_before_the_sync(world):
    _, following, scheduler = world
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    assert scheduler.favourite_account() is None
    editor_revision = now_state(scheduler).revision
    sync(following, 4)
    following.set_favorite(ACCOUNT, '1001', True)
    with pytest.raises(ScheduleAccountError):
        scheduler.set_favourites((), expected_revision=editor_revision, account_id=None)
    assert scheduler.favourites(ACCOUNT) == ('1001',)


def test_dispatch_author_check_uses_the_engine_when_provisioned(auto):
    ledger = auto.scheduler
    auto.following.set_favorite(ACCOUNT, '1001', True)
    ledger.attach_favourites(FavouriteBook(auto.following, scheduler=ledger))
    services = NS(batch_runs=auto.engine, closing=False, following_sync=auto.following, managed_scheduler=ledger,
                  live_client=NS(_verified_identity=ACCOUNT, verify_identity=lambda: ACCOUNT))
    services.start_batch_run = lambda *args, **kwargs: ApplicationServices.start_batch_run(services, *args, **kwargs)
    services.scheduler_favourite_authors = lambda **kwargs: ApplicationServices.scheduler_favourite_authors(services, **kwargs)
    result = ApplicationServices.dispatch_author_check(services, ledger, 'd1', threading.Event())
    assert result['authors'] == 1 and auto.ledger.runs(1)[0].origin == 'auto'
    assert any(a.last_downloaded for a in auto.following.snapshot(ACCOUNT).authors if a.user_id == '1001')


def test_a_cancelled_slot_stops_the_run(auto):
    cancel = threading.Event()
    gate = threading.Event()
    auto.engine._external_wait = lambda s: gate.wait(5)
    threading.Timer(0.2, lambda: (cancel.set(), gate.set())).start()
    run_author_check(auto.services, ('1000', '1001', '1002'), cancellation=cancel,
                     ledger=auto.scheduler, slot='s4', poll=0.01, account_id=ACCOUNT)
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
        assert services.batch_runs is engine and services.managed_scheduler._bound_account() is None
        run_id = services.batch_run_ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=None).run_id
    finally:
        await asyncio.to_thread(services.close)
    reopened = build()
    try:
        engine = reopened.enable_batch_runs(runs)
        assert reopened.batch_run_ledger.run(run_id).state == 'interrupted'   # never restarted by itself
        assert engine.resumable_run(None).run_id == run_id and engine.active is False
    finally:
        await asyncio.to_thread(reopened.close)


# ---- checker findings D4-D6 ------------------------------------------------------------------

def test_d4_a_star_change_bumps_the_schedule_revision_so_a_stale_form_cannot_erase_it(world):
    _, following, scheduler = world
    sync(following, 4)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    form_revision = now_state(scheduler).revision                # a settings form is opened here
    assert book.set('1001', True, account_id=ACCOUNT)
    assert now_state(scheduler).revision == form_revision + 1
    with pytest.raises(Exception):
        scheduler.set_favourites((), expected_revision=form_revision, account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1001',)
    assert book.set('1001', True, account_id=ACCOUNT) is False                       # no change, no bump
    assert now_state(scheduler).revision == form_revision + 1


def test_d4_the_services_entry_point_bumps_it_too(world):
    _, following, scheduler = world
    sync(following, 3)
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))
    before = now_state(scheduler).revision
    services = NS(favourite_book=lambda: FavouriteBook(following, scheduler=scheduler), managed_scheduler=scheduler)
    assert ApplicationServices.set_favourite(services, '1002', True, account_id=ACCOUNT)
    assert now_state(scheduler).revision == before + 1


def test_d6_an_unattached_or_unsynced_store_never_prunes_the_pending_work(world):
    _, following, scheduler = world
    scheduler.bind_account(ACCOUNT, scope=observed_scope(ACCOUNT))
    scheduler._sync_favourites(('1001',))
    scheduler.defer_work(author_id='1001', work=dict(id='5', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    scheduler.attach_favourites(FavouriteBook(None, scheduler=scheduler))   # no follow store at all
    assert scheduler.has_deferred()                               # nothing was deleted
    scheduler.attach_favourites(FavouriteBook(following, scheduler=scheduler))   # a store, never synced
    assert scheduler.has_deferred()


def test_d6_reopening_the_scheduler_keeps_the_pending_work_of_ledger_stars(world):
    tmp_path, following, scheduler = world
    sync(following, 5)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    scheduler.bind_account(ACCOUNT, scope=observed_scope(ACCOUNT))
    book.set('1001', True, account_id=ACCOUNT)
    scheduler.defer_work(author_id='1001', work=dict(id='5', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    scheduler.close()
    reopened = ManagedScheduler(tmp_path / 'managed')
    try:
        assert reopened.deferred_count() == 1
        reopened.attach_favourites(FavouriteBook(None, scheduler=reopened))
        assert reopened.has_deferred()
        reopened.attach_favourites(FavouriteBook(following, scheduler=reopened))
        assert reopened.favourites(ACCOUNT) == ('1001',) and reopened.has_deferred()
    finally:
        reopened.close()
        scheduler._closed = True


# ---- checker round 2, item 2: imported stars whose ledger cannot be read are "unknown" ----------------

def bound_world(world):
    _, following, scheduler = world
    sync(following, 5)
    book = FavouriteBook(following, scheduler=scheduler)
    scheduler.attach_favourites(book)
    scheduler.bind_account(ACCOUNT, scope=observed_scope(ACCOUNT))
    book.set('1001', True, account_id=ACCOUNT)
    scheduler.defer_work(author_id='1001', work=dict(id='5', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    return following, scheduler, book


def test_r2_2_an_unreadable_ledger_is_unknown_not_none_and_not_the_old_list(world):
    following, scheduler, book = bound_world(world)
    scheduler.attach_favourites(FavouriteBook(None, scheduler=scheduler))
    with pytest.raises(ScheduleError, match='unavailable'):
        scheduler.favourites(ACCOUNT)
    with pytest.raises(ScheduleAccountError):
        scheduler.set_favourites((), expected_revision=now_state(scheduler).revision, account_id=ACCOUNT)
    assert scheduler.deferred_count() == 1 and book.ids(ACCOUNT) == ('1001',)


def test_r2_2_a_transient_error_reading_the_ledger_is_unknown_too(world, monkeypatch):
    following, scheduler, book = bound_world(world)

    def boom(*args):
        raise RuntimeError('database is locked')

    with monkeypatch.context() as m:
        m.setattr(following, 'snapshot', boom)
        m.setattr(following, 'last_synced_account', boom)
        with pytest.raises(RuntimeError, match='database is locked'):
            scheduler.favourites(ACCOUNT)
        with pytest.raises(RuntimeError, match='database is locked'):
            scheduler.set_favourites((), expected_revision=now_state(scheduler).revision, account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1001',) and scheduler.deferred_count() == 1


def test_r2_2_a_deferral_for_a_star_is_kept_while_the_ledger_cannot_be_read(world):
    following, scheduler, book = bound_world(world)
    scheduler.attach_favourites(FavouriteBook(None, scheduler=scheduler))
    scheduler.defer_work(author_id='1001', work=dict(id='6', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert scheduler.deferred_count() == 2


# ---- checker round 3 -------------------------------------------------------------------------------

def make_view(ledger, name):
    from nicegui import Client, ui
    from pixivvault_nicegui.ui.scheduler_view import SchedulerView
    services = NS(managed_scheduler=ledger, managed_schedule_runner=NS(settings_changed=lambda: None))
    client = Client(ui.page('/' + name))
    with client:
        view = SchedulerView(services)
    return view


def starred_world(world, stars=('1001', '1002')):
    _, following, ledger = world
    sync(following, 5)
    book = FavouriteBook(following, scheduler=ledger)
    ledger.attach_favourites(book)
    for star in stars:
        book.set(star, True, account_id=ACCOUNT)
    return following, ledger, book


@pytest.mark.asyncio
async def test_r3_d1_a_form_filled_while_the_stars_were_unknown_never_saves_them_as_none(world):
    following, ledger, book = starred_world(world)
    ledger.attach_favourites(FavouriteBook(None, scheduler=ledger))          # unreadable
    view = make_view(ledger, 'r3-d1-a')
    view._load(ledger)
    assert view._stars_unknown_in_form is True and view.authors.value == ''
    assert '読み込めません' in view.star_notice.text
    view.interval.set_value('12')
    view._mark_settings_dirty()
    before = ledger.snapshot()
    await view.save()
    assert ledger.snapshot() == before
    assert '再読込' in view.action_notice.text
    ledger.attach_favourites(FavouriteBook(following, scheduler=ledger))
    assert ledger.favourites(ACCOUNT) == ('1001', '1002')                            # nothing was touched


@pytest.mark.asyncio
async def test_r3_d1_b_after_it_becomes_readable_a_save_of_that_form_is_refused_with_a_reload_prompt(world):
    following, ledger, book = starred_world(world)
    ledger.attach_favourites(FavouriteBook(None, scheduler=ledger))
    view = make_view(ledger, 'r3-d1-b')
    view._load(ledger)
    view.interval.set_value('48')
    view._mark_settings_dirty()
    ledger.attach_favourites(FavouriteBook(following, scheduler=ledger))      # readable again
    view._load(ledger)                                                        # dirty: the form is kept
    await view.save()
    assert ledger.favourites(ACCOUNT) == ('1001', '1002') and ledger.snapshot().interval_hours != 48
    assert '再読込' in view.action_notice.text
    view.discard_changes()                                                    # the user reloads ...
    assert view.authors.value == '1001,1002' and view._stars_unknown_in_form is False
    assert view.star_notice.text == ''
    view.interval.set_value('48')
    view._mark_settings_dirty()
    await view.save()                                                         # ... and now it saves
    assert ledger.snapshot().interval_hours == 48 and ledger.favourites(ACCOUNT) == ('1001', '1002')


def test_r3_d1_configure_all_without_stars_leaves_them_alone(world):
    following, ledger, book = starred_world(world)
    ledger.configure_all(author_ids=None, interval_hours=6, startup_check=True,
                         expected_revision=now_state(ledger).revision, now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert ledger.snapshot().interval_hours == 6 and ledger.favourites(ACCOUNT) == ('1001', '1002')


def test_r3_d2_only_star_authors_are_added_to_a_readable_backlog(world):
    _, scheduler, _ = bound_world(world)
    scheduler.defer_work(author_id='1001', work=dict(id='7', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    scheduler.defer_work(author_id='1003', work=dict(id='8', update_date='d', page_count=1, type=0),
                         now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert scheduler.deferred_count() == 2 and scheduler.deferred_ids('1001') == {'5', '7'}


def test_r3_d3_a_screen_star_removed_in_the_form_does_not_come_back(world):
    following, scheduler, book = starred_world(world)
    assert scheduler.favourites(ACCOUNT) == ('1001', '1002')
    scheduler.configure_all(author_ids=('1001',), interval_hours=24, startup_check=True,
                            expected_revision=now_state(scheduler).revision, now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1001',) and book.ids(ACCOUNT) == ('1001',)
    scheduler.configure_all(author_ids=('1001', '1003'), interval_hours=24, startup_check=True,
                            expected_revision=now_state(scheduler).revision, now=datetime.now(timezone.utc), account_id=ACCOUNT)
    assert scheduler.favourites(ACCOUNT) == ('1001', '1003')
