"""WU-3: the capped job / intent ledgers and the engine's bounded job hand-off.

The real limits are 10,000 (job_service.MAX_OPERATION_IDS and
managed_download_requests.MAX_INTENTS). The wall was also measured at full scale once
(10,000 enqueues on a real JobService -> "job operation history is full", about 200 s);
the tests here patch the caps down so the same code paths run in seconds.
"""
import time
from threading import Event
from types import SimpleNamespace as NS

import pytest

from pixivvault_nicegui import job_service, managed_download_requests as requests_module
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate, AuthorMediaService
from pixivvault_nicegui.batch_engine import BatchRunEngine, JobRef, ServicesPort
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.job_service import JobService, JobServiceError
from pixivvault_nicegui.managed_download_requests import ManagedDownloadRequests, ManagedRequestError
from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.services import ApplicationServices

import json


def wait_for(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def op(number):
    return '%032x' % number


def finish_all(jobs, ids):
    return wait_for(lambda: all(jobs.retained_job(i).state in ('done', 'error', 'stopped') for i in ids))


# ---- JobService: the wall and the release ---------------------------------------

def test_job_ledger_wall_exists_and_retiring_finished_jobs_lifts_it(tmp_path, monkeypatch):
    monkeypatch.setattr(job_service, 'MAX_OPERATION_IDS', 40)
    jobs = JobService(lambda job, ctx: 'ok', tmp_path / 'jobs.sqlite3')
    try:
        made = []
        for n in range(1, 41):
            made.append(jobs.enqueue('pixiv-illust', str(n), 't', operation_id=op(n)).job_id)
        assert finish_all(jobs, made)
        with pytest.raises(JobServiceError, match='operation history is full'):
            jobs.enqueue('pixiv-illust', '41', 't', operation_id=op(41))
        retired = jobs.retire_jobs(made[:30])
        assert sorted(retired) == sorted(made[:30]) and jobs.operation_count == 10
        for n in range(41, 71):  # the run goes on
            made.append(jobs.enqueue('pixiv-illust', str(n), 't', operation_id=op(n)).job_id)
        assert finish_all(jobs, made[30:])
        assert jobs.operation_count == 40
        assert jobs.retained_job(made[0]) is None and jobs.retained_job(made[35]).state == 'done'
        assert all(o not in [a[0] for a in jobs.accepted_operations()] for o in (op(1), op(30)))
    finally:
        jobs.close()
    reopened = JobService(lambda job, ctx: 'ok', tmp_path / 'jobs.sqlite3')  # storage still loads
    try:
        assert reopened.operation_count == 40
        assert reopened.retained_job(made[35]).state == 'done'
        assert reopened.retained_job(made[0]) is None
    finally:
        reopened.close()


def test_retire_leaves_unfinished_unknown_and_active_jobs_alone(tmp_path):
    release = Event()
    jobs = JobService(lambda job, ctx: release.wait(10) and 'ok' or 'late', tmp_path / 'jobs.sqlite3')
    try:
        running = jobs.enqueue('pixiv-illust', '1', 't', operation_id=op(1)).job_id
        queued = jobs.enqueue('pixiv-illust', '2', 't', operation_id=op(2)).job_id
        assert wait_for(lambda: jobs.retained_job(running).state == 'running')
        assert jobs.retire_jobs([running, queued, 'f' * 32]) == ()
        assert jobs.operation_count == 2
        with pytest.raises(JobServiceError):
            jobs.retire_jobs(['not-an-id'])
        release.set()
        assert finish_all(jobs, [running, queued])
        assert jobs.retire_jobs([running]) == (running,)
        assert jobs.retained_job(queued).state == 'done' and jobs.operation_count == 1
        assert jobs.retire_jobs([running]) == ()  # already gone
    finally:
        release.set()
        jobs.close()


def test_retired_operation_may_be_accepted_again_as_a_new_job(tmp_path):
    jobs = JobService(lambda job, ctx: 'ok', tmp_path / 'jobs.sqlite3')
    try:
        first = jobs.enqueue('pixiv-illust', '1', 't', operation_id=op(1)).job_id
        assert finish_all(jobs, [first])
        jobs.retire_jobs([first])
        second = jobs.enqueue('pixiv-illust', '1', 't', operation_id=op(1)).job_id
        assert second != first
    finally:
        jobs.close()


# ---- intents --------------------------------------------------------------------

def bookmark(n):
    return RemoteBookmark(str(n), 'illustration', 'show', 'Generated', '11', 'Generated author',
                          (), '', '', 1, 0, 0, page_count_known=True)


@pytest.fixture
def intents(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY))
    return ManagedDownloadRequests(root)


def test_intent_wall_exists_and_release_lifts_it(intents, monkeypatch):
    monkeypatch.setattr(requests_module, 'MAX_INTENTS', 20)
    for n in range(1, 21):
        intents.store(op(n), bookmark(n))
    with pytest.raises(ManagedRequestError):
        intents.store(op(99), bookmark(99))
    assert intents.release([op(n) for n in range(1, 16)] + [op(777)]) == 15  # unknown id is ignored
    intents.store(op(99), bookmark(99))
    assert intents.load(op(99)).remote_id == '99' and intents.load(op(16)).remote_id == '16'
    with pytest.raises(ManagedRequestError):
        intents.load(op(1))
    assert intents.release([]) == 0


# ---- author-media journal --------------------------------------------------------

def test_forget_admissions_drops_only_static_rows(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    media = AuthorMediaService(NS(managed_downloads=NS(root=root), closing=False))
    with media._db() as db:
        for n, key in ((1, '100'), (2, '101'), (3, 'novel:102'), (4, 'ugoira:103')):
            db.execute('INSERT INTO admissions(operation_id,media_key,spec) VALUES(?,?,?)', (op(n), key, '{}'))
    assert media.forget_admissions([op(1), op(3), op(4), 'not-an-op', op(9)]) == 1
    with media._db() as db:
        assert sorted(r[0] for r in db.execute('SELECT media_key FROM admissions')) == ['101', 'novel:102', 'ugoira:103']


# ---- ApplicationServices.retire_settled_author_jobs ------------------------------

@pytest.fixture
def world(tmp_path):
    root = tmp_path / 'managed'
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY))
    release = Event()
    release.set()
    jobs = JobService(lambda job, ctx: release.wait(10) and 'ok' or 'late', root / 'jobs.sqlite3')
    store = ManagedDownloadRequests(root)
    forgotten = []
    exports = NS(scans_completed=0)
    services = NS(closing=False, static_jobs=jobs, jobs=jobs, managed_downloads=NS(), _managed_requests=store,
                  author_media=NS(forget_admissions=lambda ops: forgotten.extend(ops) or len(ops)),
                  save_exports=exports, _media_admission_lock=__import__('threading').RLock(),
                  _managed_job_records={})
    services.retire = lambda refs, **kw: ApplicationServices.retire_settled_author_jobs(services, refs, **kw)
    services.release = release
    services.forgotten = forgotten
    services.store = store
    yield services
    release.set()
    jobs.close()


def accept(world, n):
    world.store.store(op(n), bookmark(n))
    job = world.static_jobs.enqueue('pixiv-illust', str(n), 't', operation_id=op(n))
    world._managed_job_records[job.job_id] = 'record'
    return JobRef('illustration', job.job_id, op(n))


def test_retire_frees_done_static_jobs_their_intents_and_journal_rows(world):
    refs = [accept(world, n) for n in (1, 2, 3)]
    assert finish_all(world.static_jobs, [r.job_id for r in refs])
    world.save_exports = None  # no exporter: nothing to wait for
    assert world.retire(refs + [JobRef('novel', 'e' * 32, op(9))]) == 3
    assert world.static_jobs.operation_count == 0
    assert sorted(world.forgotten) == [op(1), op(2), op(3)]
    assert world._managed_job_records == {}
    with pytest.raises(ManagedRequestError):
        world.store.load(op(1))
    assert world.retire(refs) == 0  # nothing left


def test_retire_keeps_failed_jobs_for_the_retry_button(world):
    world.static_jobs._runner = lambda job, ctx: (_ for _ in ()).throw(ValueError('x'))
    ref = accept(world, 1)
    assert finish_all(world.static_jobs, [ref.job_id])
    assert world.static_jobs.retained_job(ref.job_id).state == 'error'
    world.save_exports = None
    assert world.retire([ref]) == 0
    assert world.static_jobs.retained_job(ref.job_id) is not None and world.store.load(op(1)).remote_id == '1'


def test_retire_waits_for_quiescence_and_for_the_export_scanner(world):
    refs = [accept(world, n) for n in (1, 2)]
    assert finish_all(world.static_jobs, [r.job_id for r in refs])
    world.release.clear()
    pending = accept(world, 3)
    assert world.retire(refs) is None                       # a job is still unfinished
    world.release.set()
    assert finish_all(world.static_jobs, [pending.job_id])
    assert world.retire(refs, settle_timeout=0.2) is None   # scanner never completed two passes
    assert world.static_jobs.retained_job(refs[0].job_id) is not None
    def scanner():
        for _ in range(10):
            time.sleep(0.02)
            world.save_exports.scans_completed += 1
    import threading
    threading.Thread(target=scanner, daemon=True).start()
    assert world.retire(refs, settle_timeout=5) == 2
    assert world.static_jobs.retained_job(refs[0].job_id) is None


def test_retire_refuses_when_the_scan_queued_new_work(world):
    import threading
    refs = [accept(world, 1)]
    assert finish_all(world.static_jobs, [refs[0].job_id])
    original = world.static_jobs.snapshot
    calls = {'n': 0}

    def snapshot():
        calls['n'] += 1  # quiet before the exporter pass, busy after it (it queued a ZIP)
        return original() if calls['n'] <= 1 else (NS(state='queued'),)
    world.jobs = NS(snapshot=snapshot)
    world.save_exports = NS(scans_completed=0)

    def tick():
        for _ in range(5):
            time.sleep(0.01)
            world.save_exports.scans_completed += 1
    threading.Thread(target=tick, daemon=True).start()
    assert world.retire(refs, settle_timeout=5) is None
    assert world.static_jobs.retained_job(refs[0].job_id) is not None


def test_services_port_retire_distinguishes_unsupported_not_ready_and_done():
    refs = (JobRef('illustration', 'a' * 32, op(1)),)
    assert ServicesPort(NS()).retire(refs) == 'unsupported'
    assert ServicesPort(NS(retire_settled_author_jobs=lambda r: None)).retire(refs) is None
    assert ServicesPort(NS(retire_settled_author_jobs=lambda r: 1)).retire(refs) == 1


# ---- the engine over the capped ledger (fake port; the cap is the real 10,000) ----

class CappedPort:
    """Enqueue costs one ledger slot until retire(); job state needs `latency` polls."""
    closing = False
    CAP = 10_000

    def __init__(self, latency=0, quiet_after=0, retire_supported=True):
        self.live = 0
        self.max_live = 0
        self.unreported = {}
        self.polls = {}
        self.max_inflight = 0
        self.retire_calls = 0
        self.refused = quiet_after       # retire() says "not yet" this many times
        self.latency, self.retire_supported = latency, retire_supported
        self.n = 0
        self.queue_full_every = 0

    def require_allowed(self): pass
    def author_target(self, aid): return None
    def list_author(self, aid, target): return self.inventory[aid]
    def settle(self, aid): pass
    def stop_jobs(self, refs): pass
    def observe_deletion(self, aid, remote, scan_id): return None

    def enqueue(self, candidate, operation_id):
        self.n += 1
        if self.queue_full_every and self.n % self.queue_full_every == 0:
            from pixivvault_nicegui.legacy_retry_errors import DownloadQueueCapacityReached
            raise DownloadQueueCapacityReached('download queue capacity reached')
        if self.live >= self.CAP:
            raise JobServiceError('job operation history is full')
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        job_id = '%032x' % self.n
        self.unreported[job_id] = 0
        self.max_inflight = max(self.max_inflight, len(self.unreported))
        return job_id, True

    def job_states(self, refs):
        out = {}
        for ref in refs:
            self.unreported[ref.job_id] += 1
            if self.unreported[ref.job_id] > self.latency:
                out[ref.job_id] = ('done', '')
                del self.unreported[ref.job_id]
            else:
                out[ref.job_id] = ('running', '')
        return out

    def retire(self, refs):
        self.retire_calls += 1
        if not self.retire_supported:
            return 'unsupported'
        if self.unreported:
            return None
        if self.refused > 0:
            self.refused -= 1
            return None
        self.live -= len(refs)
        return len(refs)


def works(author, count):
    return tuple(AuthorMediaCandidate('illustration', str(1000 + i), author, 'd', 1, 't', Difference.NEW)
                 for i in range(count))


@pytest.fixture
def ledger(tmp_path):
    value = BatchRunLedger(tmp_path / 'runs', synchronous='NORMAL')
    yield value
    value.close()


def run_engine(port, ledger, authors, **kwargs):
    kwargs.setdefault('poll_seconds', 0.0001)
    kwargs.setdefault('wait', lambda s: None)
    return BatchRunEngine(port, ledger, **kwargs).run(authors)


def test_first_run_of_12000_works_does_not_overflow_the_operation_ledger(ledger):
    port = CappedPort(latency=1)
    port.inventory = {'41': works('41', 7000), '42': works('42', 5000)}
    run_id = run_engine(port, ledger, ('41', '42'))
    authors = ledger.authors(run_id)
    assert ledger.run(run_id).state == 'done'
    assert [a.state for a in authors] == ['done', 'done'] and sum(a.n_new for a in authors) == 12_000
    assert sum(a.n_failed for a in authors) == 0
    assert port.max_live <= port.CAP and port.retire_calls >= 24   # freed in steps, not once
    assert port.max_inflight <= 16                                  # never more than 16 in flight


def test_work_is_held_back_until_retire_is_possible_and_the_wall_is_never_hit(ledger):
    port = CappedPort(latency=3, quiet_after=4)
    port.inventory = {'41': works('41', 1500)}
    port.CAP = 600
    run_id = run_engine(port, ledger, ('41',), retire_every=500)
    assert ledger.run(run_id).state == 'done' and ledger.authors(run_id)[0].n_new == 1500
    assert port.max_live <= 600 and port.max_inflight <= 16


def test_without_a_retire_hook_the_run_fails_at_the_wall_with_a_clear_code(ledger):
    port = CappedPort(retire_supported=False)
    port.inventory = {'41': works('41', 80), '42': works('42', 5)}
    port.CAP = 50
    run_id = run_engine(port, ledger, ('41', '42'))
    assert ledger.run(run_id).state == 'failed'
    assert ledger.run(run_id).error_code == 'job_operation_history_full'
    assert [a.state for a in ledger.authors(run_id)] == ['failed', 'pending']  # never stamped, never `done`


def test_retire_that_never_becomes_possible_does_not_hang_the_run(ledger):
    port = CappedPort(quiet_after=10 ** 9)
    port.inventory = {'41': works('41', 60)}
    run_id = run_engine(port, ledger, ('41',), retire_every=20, retire_wait_seconds=0.01)
    assert ledger.run(run_id).state == 'done' and ledger.authors(run_id)[0].n_new == 60


def test_queue_full_waits_and_is_not_counted_as_a_failed_work(ledger):
    port = CappedPort(latency=2)
    port.queue_full_every = 3
    port.inventory = {'41': works('41', 40)}
    run_id = run_engine(port, ledger, ('41',))
    a = ledger.authors(run_id)[0]
    assert (a.state, a.n_new, a.n_failed) == ('done', 40, 0)
    assert {i.state for i in ledger.items(run_id)} == {'done'} and len(ledger.items(run_id)) == 40


def test_retire_is_carried_over_to_the_next_author_when_not_yet_safe(ledger):
    port = CappedPort(quiet_after=1)  # the first boundary is refused, the next one succeeds
    port.inventory = {'41': works('41', 3), '42': works('42', 3)}
    run_id = run_engine(port, ledger, ('41', '42'))
    assert ledger.run(run_id).state == 'done' and port.live == 0 and port.retire_calls == 2


# ---- real ApplicationServices: retire, then restart --------------------------------

@pytest.mark.asyncio
async def test_real_services_retire_keeps_the_saved_work_and_restarts_cleanly(tmp_path):
    import asyncio
    from test_managed_download_services import FakeSource, terminal
    root = tmp_path / 'managed'
    services = ApplicationServices(live_client=FakeSource(), pixiv_download_root=root)
    try:
        await services.refresh_bookmark_cache()
        candidate = services.download_candidates()[0]
        job = await asyncio.to_thread(services.enqueue_managed_download, candidate, 'first')
        finished = await asyncio.to_thread(terminal, services.jobs, job.job_id)
        ref = JobRef('illustration', job.job_id, 'first')
        assert services.static_jobs.operation_count == 1
        assert await asyncio.to_thread(services.retire_settled_author_jobs, (ref,)) == 1
        assert services.static_jobs.operation_count == 0 and services.static_jobs.snapshot() == ()
        with pytest.raises(ManagedRequestError):
            services._managed_requests.load('first')
        assert (root / finished.result).is_file()            # the saved work is untouched
        assert [w.work_id for w in (await services.refresh_library_snapshot()).works] == ['202']
    finally:
        await asyncio.to_thread(services.close)
    reopened = ApplicationServices(live_client=FakeSource(), pixiv_download_root=root)  # startup re-checks every job
    try:
        assert reopened.static_jobs.snapshot() == () and reopened.static_jobs.operation_count == 0
        assert [w.work_id for w in reopened.library_snapshot().works] == ['202']
    finally:
        await asyncio.to_thread(reopened.close)


def retire_when_quiet(services, ref, timeout=30):
    """What the engine does at its boundaries: ask again until the exporter has nothing in flight."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = services.retire_settled_author_jobs((ref,))
        if result is not None:
            return result
        time.sleep(0.1)
    return None


@pytest.mark.asyncio
async def test_real_services_and_real_exporter_zip_then_retire_then_restart(tmp_path):
    import asyncio
    from test_managed_download_services import FakeSource, terminal
    root, exports, prefs = tmp_path / 'managed', tmp_path / 'exports', tmp_path / 'prefs'

    def build():
        services = ApplicationServices(live_client=FakeSource(), pixiv_download_root=root, preferences_root=prefs)
        services.configure_save_exports(exports, '12')
        return services
    services = build()
    try:
        services.preferences.set_setting('zip_all_after_download', '1')
        services.save_exports.start()
        await services.refresh_bookmark_cache()
        job = await asyncio.to_thread(services.enqueue_managed_download, services.download_candidates()[0], 'first')
        await asyncio.to_thread(terminal, services.static_jobs, job.job_id)
        # The exporter notices the finished download and publishes the author's ZIP.
        assert await asyncio.to_thread(
            wait_for, lambda: any(j.kind == 'auto-zip' and j.state == 'done'
                                  for j in services.save_exports.jobs.snapshot()), 30)
        ref = JobRef('illustration', job.job_id, 'first')
        assert await asyncio.to_thread(retire_when_quiet, services, ref) == 1
        assert services.static_jobs.operation_count == 0
        zips = [j for j in services.save_exports.jobs.snapshot() if j.kind == 'auto-zip']
        assert zips and all(j.state == 'done' for j in zips)       # the exporter's own ledger is untouched
    finally:
        await asyncio.to_thread(services.close)
    reopened = build()
    try:
        assert reopened.static_jobs.snapshot() == ()
        assert [w.work_id for w in reopened.library_snapshot().works] == ['202']
    finally:
        await asyncio.to_thread(reopened.close)


# ---- review fixes: hard store cap, hold-back retry, admission pauses, settle cadence ----

def test_author_that_cannot_fit_the_managed_store_is_refused_before_any_work_is_queued(ledger):
    port = CappedPort()
    port.inventory = {'41': works('41', 5)}
    port.saved_headroom = lambda: 4
    run_id = run_engine(port, ledger, ('41',))
    assert ledger.run(run_id).state == 'failed' and ledger.run(run_id).error_code == 'saved_works_limit'
    assert port.n == 0 and ledger.authors(run_id)[0].state == 'failed'   # nothing queued, never `done`


def test_updates_do_not_count_against_the_store_cap_and_unknown_headroom_is_allowed(ledger):
    updates = tuple(AuthorMediaCandidate('illustration', str(2000 + i), '41', 'd', 1, 't', Difference.UPDATED,
                                         prior_token='tok') for i in range(5))
    for headroom in (0, None):
        port = CappedPort()
        port.inventory = {'41': updates}
        port.saved_headroom = lambda headroom=headroom: headroom
        run_id = run_engine(port, ledger, ('41',))
        assert ledger.run(run_id).state == 'done' and port.n == 5


def test_services_port_saved_headroom_reads_the_generation_count():
    from pixivvault_nicegui.managed_generations import _MAX_ENTRIES
    def port(entries):
        return ServicesPort(NS(managed_downloads=NS(_generations=NS(entries=entries))))
    assert port(lambda: {str(i): {} for i in range(30)}).saved_headroom() == _MAX_ENTRIES - 30
    assert port(lambda: (_ for _ in ()).throw(ValueError('generation limit exceeded'))).saved_headroom() == 0
    assert port(lambda: (_ for _ in ()).throw(OSError('x'))).saved_headroom() is None
    assert ServicesPort(NS()).saved_headroom() is None


def test_hold_back_that_gave_up_tries_again_after_the_next_batch(ledger):
    port = CappedPort(quiet_after=10 ** 9)
    port.inventory = {'41': works('41', 90)}
    run_id = run_engine(port, ledger, ('41',), retire_every=20, retire_wait_seconds=0.01)
    assert ledger.run(run_id).state == 'done' and ledger.authors(run_id)[0].n_new == 90
    assert port.retire_calls >= 5   # about every 20 works, not once


@pytest.mark.parametrize('code', ['admission_blocked', 'admission_unavailable'])
def test_jobs_paused_by_admission_pause_the_run_instead_of_hanging(ledger, code):
    port = CappedPort()
    port.inventory = {'41': works('41', 2)}
    port.job_states = lambda refs: {r.job_id: ('paused', code) for r in refs}
    engine = BatchRunEngine(port, ledger, poll_seconds=0.001, wait=lambda s: None)
    run_id = engine.start(('41',))
    assert wait_for(lambda: ledger.run(run_id).state == 'paused')
    assert ledger.run(run_id).error_code == ('network_stopped' if code == 'admission_blocked'
                                             else 'network_gate_unavailable')
    engine.stop()
    assert engine.join(5)


def test_proof_binding_runs_every_16_finished_jobs_and_at_the_author_end(ledger):
    port = CappedPort()
    port.inventory = {'41': works('41', 100)}
    settles = []
    port.settle = lambda aid: settles.append(aid)
    run_engine(port, ledger, ('41',))
    assert 6 <= len(settles) <= 9
