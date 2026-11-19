"""WU-2: batch run engine over a fake port. No network, no real jobs."""
import time
from threading import Event

import pytest

from pixivvault_nicegui.account_scope import AccountScope, OwnedListing, require_same_account
from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.batch_engine import BatchRunEngine
from pixivvault_nicegui.batch_runs import BatchRunLedger
from pixivvault_nicegui.following_sync import FollowingSyncStore
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopped

ACCOUNT = '55'
D = Difference


def cand(author, wid, difference=D.NEW, kind='illustration', date='2026-01-01T00:00:00+00:00'):
    return AuthorMediaCandidate(kind, str(wid), author, date, 1, 't' + str(wid), difference,
                                work_type=2 if kind == 'ugoira' else 0)


class OwnedInventoryPort:
    """Synthetic port with its own observed account and the real listing contract."""

    def verified_author_account(self):
        self._verified_identity = ACCOUNT
        return self._verified_identity

    def verify_run_account(self, account_id):
        require_same_account(account_id, getattr(self, '_verified_identity', ACCOUNT))

    def owned(self, aid, target, rows, expected_account):
        observed = self.verified_author_account()
        require_same_account(expected_account, observed)
        return OwnedListing(AccountScope(observed, self), aid, target or 'both', tuple(rows))

    def verify_author_scope(self, scope):
        require_same_account(self, scope.reader)
        scope.verify_observed()


class FakePort(OwnedInventoryPort):
    closing = False

    def __init__(self, inventories, *, targets=None):
        self.inventories = inventories          # author -> tuple of candidates, or Exception
        self.targets = targets or {}
        self.gate_closed = False
        self.calls = []                          # ordered log
        self.enqueued = []
        self.job_result = {}                     # work_id -> 'error' | 'stopped' (default 'done')
        self.enqueue_error = {}                  # work_id -> Exception
        self.deletion = {}                       # author -> (pending, confirmed) | None | Exception
        self.deletion_calls = []
        self.stopped_jobs = []
        self.ledger = None
        self.hold_jobs = False                   # jobs never finish (stop tests)

    def require_allowed(self):
        if self.gate_closed:
            raise ManagedNetworkStopped(429)

    def author_target(self, aid):
        return self.targets.get(aid)

    def list_author(self, aid, target, *, expected_account):
        self.calls.append(('list', aid, target))
        value = self.inventories[aid]
        if isinstance(value, Exception):
            raise value
        return self.owned(aid, target, value, expected_account)

    def enqueue(self, candidate, operation_id):
        self.calls.append(('enqueue', candidate.author_id, candidate.work_id))
        error = self.enqueue_error.get(candidate.work_id)
        if error is not None:
            raise error
        job_id = '%032x' % (len(self.enqueued) + 1)
        self.enqueued.append((candidate, operation_id, job_id))
        return job_id, True

    def job_states(self, refs):
        by_job = {job_id: c for c, _, job_id in self.enqueued}
        out = {}
        for ref in refs:
            if self.hold_jobs:
                out[ref.job_id] = ('queued', '')
            else:
                out[ref.job_id] = (self.job_result.get(by_job[ref.job_id].work_id, 'done'), '')
        return out

    def stop_jobs(self, refs):
        self.stopped_jobs.extend(ref.job_id for ref in refs)

    def observe_deletion(self, aid, remote, scan_id):
        author = self.ledger.authors(self.ledger.active_run().run_id)
        self.deletion_calls.append((aid, remote, scan_id,
                                    next(a.scan_id for a in author if a.author_id == aid)))
        value = self.deletion.get(aid)
        if isinstance(value, Exception):
            raise value
        return value

    def settle(self, aid, *, scope):
        self.calls.append(('settle', aid))

    def retire(self, refs):
        self.calls.append(('retire', len(refs)))


@pytest.fixture
def env(tmp_path):
    ledger = BatchRunLedger(tmp_path / 'runs')
    following = FollowingSyncStore(tmp_path / 'follow')

    class Reader:
        def require_identity(self):
            return ACCOUNT

        def get_following_users_detailed(self, uid, rest):
            return () if rest == 'hide' else tuple(
                dict(user_id=u, name='n' + u, account='', profile_img='') for u in ('41', '42', '43'))
    following.sync(Reader())
    yield ledger, following
    ledger.close()
    following.close()


def engine_for(port, env, **kwargs):
    ledger, following = env
    port.ledger = ledger
    kwargs.setdefault('poll_seconds', 0.001)
    kwargs.setdefault('wait', lambda seconds: None)
    return BatchRunEngine(port, ledger, following=following, account_id=ACCOUNT, **kwargs)


def stamps(following):
    return {a.user_id: a.last_downloaded for a in following.snapshot(ACCOUNT).authors}


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---- ordering, waits, stamps ---------------------------------------------------

def test_three_authors_run_in_order_each_done_and_stamped(env):
    port = FakePort({'41': (cand('41', 1), cand('41', 2)), '42': (cand('42', 3),), '43': ()})
    engine = engine_for(port, env)
    run_id = engine.run(('41', '42', '43'))
    ledger, following = env
    assert [c for c in port.calls if c[0] == 'list'] == [
        ('list', '41', None), ('list', '42', None), ('list', '43', None)]
    authors = ledger.authors(run_id)
    assert [a.state for a in authors] == ['done'] * 3
    assert [a.n_new for a in authors] == [2, 1, 0]
    assert ledger.run(run_id).state == 'done' and engine.state == 'done'
    assert all(stamps(following)[u] for u in ('41', '42', '43'))
    # items are recorded with their job and result
    assert sorted((i.work_key, i.state) for i in ledger.items(run_id, '41')) == [('1', 'done'), ('2', 'done')]


def test_wait_is_4_to_6_seconds_between_authors_and_not_after_the_last(env):
    port = FakePort({u: () for u in ('41', '42', '43')})
    asked, waited = [], []
    engine = engine_for(port, env, uniform=lambda low, high: asked.append((low, high)) or 5.0,
                        wait=waited.append)
    engine.run(('41', '42', '43'))
    assert asked == [(4.0, 6.0)] * 2 and waited == [5.0, 5.0]
    engine = engine_for(FakePort({'41': ()}), env, wait=waited.append)
    engine.run(('41',))
    assert waited == [5.0, 5.0]  # a single author never waits


def test_listing_failure_moves_on_and_never_stamps_that_author(env):
    port = FakePort({'41': ValueError('private-detail'), '42': (cand('42', 3),), '43': ()})
    ledger, following = env
    run_id = engine_for(port, env).run(('41', '42', '43'))
    states = {a.author_id: a for a in ledger.authors(run_id)}
    assert (states['41'].state, states['41'].error_code) == ('failed', 'ValueError')
    assert states['42'].state == states['43'].state == 'done'
    stamped = stamps(following)
    assert stamped['41'] is None and stamped['42'] and stamped['43']
    assert ledger.run(run_id).state == 'done'
    assert 'private-detail' not in repr(ledger.authors(run_id))


def test_work_failures_do_not_fail_the_author(env):
    port = FakePort({'41': (cand('41', 1), cand('41', 2), cand('41', 3))})
    port.job_result['2'] = 'error'
    port.enqueue_error['3'] = ValueError('author receipt changed')
    ledger, following = env
    run_id = engine_for(port, env).run(('41',))
    author = ledger.authors(run_id)[0]
    assert (author.state, author.n_failed, author.n_new) == ('done', 2, 3)
    assert stamps(following)['41']  # original core.py:2254: failures still stamp
    assert {i.work_key: i.state for i in ledger.items(run_id)} == {'1': 'done', '2': 'error', '3': 'error'}


def test_difference_counts_and_only_eligible_works_are_enqueued(env):
    rows = (cand('41', 1, D.NEW), cand('41', 2, D.UPDATED), cand('41', 3, D.RESTORED),
            cand('41', 4, D.MISSING), cand('41', 5, D.SKIPPED), cand('41', 6, D.UNKNOWN))
    port = FakePort({'41': rows})
    ledger, _ = env
    run_id = engine_for(port, env).run(('41',))
    a = ledger.authors(run_id)[0]
    assert (a.n_new, a.n_updated, a.n_restored, a.n_missing, a.n_skipped, a.n_unknown) == (1, 1, 1, 1, 1, 1)
    assert sorted(c.work_id for c, _, _ in port.enqueued) == ['1', '2', '3', '4']


def test_author_setting_beats_run_target_and_run_target_beats_default(env):
    port = FakePort({'41': (), '42': ()}, targets={'41': 'novel'})
    engine_for(port, env).run(('41', '42'), target_type='illust')
    assert [c for c in port.calls if c[0] == 'list'] == [('list', '41', 'novel'), ('list', '42', 'illust')]


def test_inventory_identity_mismatch_fails_the_author(env):
    port = FakePort({'41': (cand('99', 1),)})
    ledger, following = env
    run_id = engine_for(port, env).run(('41',))
    assert ledger.authors(run_id)[0].state == 'failed' and stamps(following)['41'] is None


def test_a_second_run_cannot_start_while_one_is_active(env):
    port = FakePort({'41': (cand('41', 1),)})
    port.hold_jobs = True
    engine = engine_for(port, env)
    engine.start(('41',))
    assert wait_for(lambda: engine.state == 'running' and port.enqueued)
    with pytest.raises(RuntimeError):
        engine.start(('42',))
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(5)


# ---- progress ------------------------------------------------------------------

def test_progress_callback_reports_each_step_in_order(env):
    events = []
    port = FakePort({'41': (cand('41', 1),), '42': ()})
    engine_for(port, env, on_progress=events.append).run(('41', '42'))
    kinds = [e['event'] for e in events]
    assert kinds == ['run_start', 'author_start', 'author_listed', 'work_info', 'work_progress', 'author_done',
                     'author_start', 'author_listed', 'author_done', 'run_end']
    assert events[1]['index'] == 1 and events[1]['total'] == 2 and events[1]['author_id'] == '41'
    assert events[-1]['outcome'] == 'done' and events[-1]['totals']['n_new'] == 1


def test_progress_callback_errors_never_break_the_run(env):
    def broken(event):
        raise RuntimeError('ui gone')
    port = FakePort({'41': (cand('41', 1),)})
    ledger, _ = env
    assert ledger.authors(engine_for(port, env, on_progress=broken).run(('41',)))[0].state == 'done'


# ---- stop / pause --------------------------------------------------------------

def test_stop_during_the_wait_between_authors_is_immediate(env):
    port = FakePort({'41': (), '42': (), '43': ()})
    ledger, following = env
    engine = BatchRunEngine(port, ledger, following=following, account_id=ACCOUNT,
                            between_authors=(30.0, 30.0), poll_seconds=0.001)
    port.ledger = ledger
    run_id = engine.start(('41', '42', '43')).run_id
    assert wait_for(lambda: ledger.authors(run_id)[0].state == 'done')
    time.sleep(0.2)  # now inside the 30 s wait
    began = time.monotonic()
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(3) and time.monotonic() - began < 2
    assert engine.state == 'stopped' and ledger.run(run_id).state == 'stopped'
    assert [a.state for a in ledger.authors(run_id)] == ['done', 'pending', 'pending']
    assert stamps(following)['41'] and stamps(following)['42'] is None
    assert ('list', '42', None) not in port.calls


def test_stop_in_the_middle_of_an_author_never_stamps_and_stops_its_jobs(env):
    port = FakePort({'41': (cand('41', 1), cand('41', 2))})
    port.hold_jobs = True
    ledger, following = env
    engine = engine_for(port, env)
    run_id = engine.start(('41',)).run_id
    assert wait_for(lambda: len(port.enqueued) == 2)
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(5)
    assert ledger.authors(run_id)[0].state == 'stopped' and ledger.run(run_id).state == 'stopped'
    assert stamps(following)['41'] is None
    assert sorted(port.stopped_jobs) == sorted(j for _, _, j in port.enqueued)


def test_pause_blocks_the_next_step_and_resume_continues(env):
    port = FakePort({'41': (), '42': (), '43': ()})
    ledger, following = env
    gate = Event()
    engine = engine_for(port, env, wait=lambda seconds: gate.wait(5))  # parked in the first wait
    run_id = engine.start(('41', '42', '43')).run_id
    assert wait_for(lambda: ledger.authors(run_id)[0].state == 'done')
    engine.pause(engine.active_run(ACCOUNT))
    gate.set()
    assert wait_for(lambda: ledger.run(run_id).state == 'paused')
    time.sleep(0.3)
    assert ledger.authors(run_id)[1].state == 'pending'  # nothing runs while paused
    assert engine.resume(engine.active_run(ACCOUNT))
    assert engine.join(5) and ledger.run(run_id).state == 'done'
    assert all(stamps(following)[u] for u in ('41', '42', '43'))


def test_app_shutdown_counts_as_stop(env):
    port = FakePort({'41': (cand('41', 1),)})
    port.hold_jobs = True
    ledger, following = env
    engine = engine_for(port, env)
    run_id = engine.start(('41',)).run_id
    assert wait_for(lambda: port.enqueued)
    port.closing = True
    assert engine.join(5)
    assert ledger.run(run_id).state == 'stopped' and stamps(following)['41'] is None


# ---- network stop gate ---------------------------------------------------------

def test_closed_gate_pauses_the_run_without_traffic_or_failure(env):
    port = FakePort({'41': (cand('41', 1),), '42': ()})
    port.gate_closed = True
    ledger, following = env
    engine = engine_for(port, env)
    run_id = engine.start(('41', '42')).run_id
    assert wait_for(lambda: ledger.run(run_id).state == 'paused')
    assert ledger.run(run_id).error_code == 'network_stopped'
    assert port.calls == [] and port.enqueued == []          # no listing, no enqueue
    assert ledger.authors(run_id)[0].state == 'pending' and stamps(following)['41'] is None
    port.gate_closed = False
    assert engine.resume(engine.active_run(ACCOUNT))
    assert engine.join(5)
    assert ledger.run(run_id).state == 'done'
    assert [a.state for a in ledger.authors(run_id)] == ['done', 'done']
    assert all(stamps(following)[u] for u in ('41', '42'))


def test_gate_closing_mid_author_pauses_and_the_author_is_retried_on_resume(env):
    port = FakePort({'41': (cand('41', 1), cand('41', 2))})
    original = port.enqueue
    def closing_enqueue(candidate, operation_id):
        if candidate.work_id == '2':  # newest first: the next candidate meets a closed gate
            port.gate_closed = True
        return original(candidate, operation_id)
    port.enqueue = closing_enqueue
    ledger, following = env
    engine = engine_for(port, env)
    run_id = engine.start(('41',)).run_id
    assert wait_for(lambda: ledger.run(run_id).state == 'paused')
    assert stamps(following)['41'] is None and ledger.authors(run_id)[0].state == 'pending'
    port.gate_closed = False
    port.enqueue = original
    assert engine.resume(engine.active_run(ACCOUNT)) and engine.join(5)
    assert ledger.authors(run_id)[0].state == 'done'


def test_jobs_paused_by_admission_block_pause_the_run(env):
    port = FakePort({'41': (cand('41', 1),)})
    port.job_states = lambda refs: {r.job_id: ('paused', 'admission_blocked') for r in refs}
    ledger, following = env
    engine = engine_for(port, env)
    run_id = engine.start(('41',)).run_id
    assert wait_for(lambda: ledger.run(run_id).state == 'paused')
    assert stamps(following)['41'] is None and port.stopped_jobs == []  # jobs stay for the recovery screen
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(5)


# ---- deletion observation ------------------------------------------------------

def test_deletion_scan_uses_a_recorded_scan_id_and_only_the_illustration_list(env):
    rows = (cand('41', 1), cand('41', 2, kind='ugoira'), cand('41', 3, D.SKIPPED))
    port = FakePort({'41': rows + (cand('41', 9, kind='novel'),)})
    port.deletion['41'] = (2, 1)
    ledger, _ = env
    run_id = engine_for(port, env).run(('41',))
    (aid, remote, scan_id, ledger_scan_id), = port.deletion_calls
    assert ledger_scan_id == scan_id                       # persisted BEFORE the call
    assert sorted(r['id'] for r in remote) == ['1', '2', '3']
    assert all(set(r) == {'id', 'user_id', 'type', 'page_count', 'update_date'} for r in remote)
    a = ledger.authors(run_id)[0]
    assert (a.deletion_check, a.n_absent_pending, a.n_absent_confirmed) == ('done', 2, 1)


@pytest.mark.parametrize('case,rows,target,value,expected', [
    ('novel only', (cand('41', 9, kind='novel'),), 'novel', None, 'skipped_novel_only'),
    ('empty list', (), None, None, 'skipped_no_illustration_list'),
    ('no legacy db', (cand('41', 1),), None, None, 'skipped_no_legacy_db'),
    ('scan raises', (cand('41', 1),), None, ValueError('x'), 'failed'),
])
def test_deletion_scan_is_skipped_or_failed_without_failing_the_author(env, case, rows, target, value, expected):
    port = FakePort({'41': rows}, targets={'41': target} if target else None)
    port.deletion['41'] = value
    ledger, following = env
    run_id = engine_for(port, env).run(('41',))
    a = ledger.authors(run_id)[0]
    assert a.deletion_check == expected and a.state == 'done' and stamps(following)['41']
    if expected.startswith('skipped_n') and expected != 'skipped_no_legacy_db':
        assert port.deletion_calls == []   # never even asked: a partial list must not advance streaks


def test_zero_authors_finishes_immediately(env):
    ledger, _ = env
    run_id = engine_for(FakePort({}), env).run(())
    assert ledger.run(run_id).state == 'done' and ledger.run(run_id).authors_total == 0


# ---- the real adapter over a services-shaped object ----------------------------

def test_services_port_maps_services_onto_the_engine_port():
    from types import SimpleNamespace as NS
    from pixivvault_nicegui.batch_engine import JobRef, ServicesPort
    job = NS(job_id='a' * 32, state='error', error_code='boom')
    retained = NS(job_id='b' * 32, state='done', error_code='')
    owner = NS(snapshot=lambda: (job,), retained_job=lambda jid: retained if jid == retained.job_id else None,
               control=lambda jid, action: calls.append((jid, action)))
    calls, gate = [], NS(require_allowed=lambda: calls.append('gate'))
    media = NS(author_target=lambda aid: NS(target_type='novel'), owner_jobs=lambda kind: owner,
               reconcile=lambda author_id: calls.append(('reconcile', author_id)))
    services = NS(closing=False, managed_network_stop=gate, author_media=media,
                  author_media_preview_owned=lambda aid, target, expected_account:
                      calls.append(('preview', aid, target)) or OwnedInventoryPort().owned(aid, target, (), expected_account),
                  enqueue_author_media=lambda c, op, origin: (job, True, 'fp'), legacy_author_bridge=None)
    port = ServicesPort(services)
    port.require_allowed()
    assert port.author_target('41') == 'novel' and port.list_author('41', 'both', expected_account=ACCOUNT).rows == ()
    assert port.enqueue(object(), 'c' * 32) == ('a' * 32, True)
    refs = [JobRef('illustration', 'a' * 32, 'x'), JobRef('illustration', 'b' * 32, 'y'),
            JobRef('illustration', 'c' * 32, 'z')]
    assert port.job_states(refs) == {'a' * 32: ('error', 'boom'), 'b' * 32: ('done', ''), 'c' * 32: ('lost', '')}
    port.stop_jobs(refs[:1])
    port.settle('41')
    assert port.observe_deletion('41', (), 'd' * 32) is None and port.retire(refs) == 'unsupported'
    assert calls == ['gate', ('preview', '41', 'both'), ('a' * 32, 'stop'), ('reconcile', '41')]


def test_a_run_left_active_by_a_crash_is_closed_as_interrupted_before_the_next_run(env):
    ledger, following = env
    stale = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=ACCOUNT).run_id
    ledger.update_author(stale, '41', state='downloading', account_id=ledger.run(stale).account_id)
    run_id = engine_for(FakePort({'41': ()}), env).run(('41',))
    assert ledger.run(stale).state == 'interrupted' and ledger.run(run_id).state == 'done'
    assert [a.state for a in ledger.authors(stale)] == ['stopped', 'pending']
