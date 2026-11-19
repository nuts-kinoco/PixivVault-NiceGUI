"""WU-5: stop / pause / resume, `interrupted` after a restart, the gate pause, the stop message."""
import threading

import pytest

from pixivvault_nicegui import unified_download_state as ui
from pixivvault_nicegui.batch_runs import BatchRunLedger, RunHandle
from test_t_dl_engine_20261117 import (ACCOUNT, FakePort, cand, engine_for, env, stamps,  # noqa: F401
                                       wait_for)


def run_and_stop_after_first(port, env, authors=('41', '42', '43')):
    """Run until the first author is done, then stop inside the wait before the second."""
    ledger, following = env
    gate = threading.Event()
    engine = engine_for(port, env, wait=lambda seconds: gate.wait(5))
    run_id = engine.start(authors).run_id
    assert wait_for(lambda: ledger.authors(run_id)[0].state == 'done')
    engine.stop(engine.active_run(ACCOUNT))
    gate.set()
    assert engine.join(5)
    return engine, run_id


def test_a_stopped_run_is_resumed_over_the_unfinished_authors_only(env):
    port = FakePort({'41': (cand('41', 1),), '42': (cand('42', 2),), '43': ()})
    ledger, following = env
    engine, first = run_and_stop_after_first(port, env)
    assert ledger.run(first).state == 'stopped'
    offer = engine.resumable_run(ACCOUNT)
    assert offer.run_id == first and ledger.unfinished_authors(first) == ('42', '43')
    port.calls.clear()
    second = engine.start_unfinished(ledger.run(first).handle).run_id
    assert engine.join(5) and ledger.run(second).state == 'done'
    assert [a.author_id for a in ledger.authors(second)] == ['42', '43']
    assert ('list', '41', 'both') not in port.calls and ('list', '42', 'both') in port.calls
    assert all(stamps(following)[u] for u in ('41', '42', '43'))
    assert engine.resumable_run(ACCOUNT) is None                      # a newer run ends the offer


def test_a_finished_run_is_never_offered_even_with_a_failed_author(env):
    port = FakePort({'41': (cand('41', 1),), '42': RuntimeError('boom'), '43': ()})
    ledger, _ = env
    engine = engine_for(port, env)
    first = engine.run(('41', '42', '43'))
    assert [a.state for a in ledger.authors(first)] == ['done', 'failed', 'done']
    assert ledger.run(first).state == 'done' and engine.resumable_run(ACCOUNT) is None


def test_resume_without_an_offer_is_refused(env):
    engine = engine_for(FakePort({}), env)
    with pytest.raises(RuntimeError, match='account changed'):
        engine.start_unfinished(RunHandle('1' * 32, ACCOUNT))


def test_a_run_left_active_by_a_crash_becomes_interrupted_without_any_traffic(env):
    ledger, _ = env
    port = FakePort({'41': (cand('41', 1),)})
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=ACCOUNT).run_id
    ledger.update_author(run_id, '41', state='listing', account_id=ledger.run(run_id).account_id)
    engine = engine_for(port, env)
    assert engine._recover_on_startup() == (run_id,)
    assert port.calls == [] and port.enqueued == []             # nothing talks to Pixiv at start-up
    assert ledger.run(run_id).state == 'interrupted'
    assert [a.state for a in ledger.authors(run_id)] == ['stopped', 'pending']
    assert engine.resumable_run(ACCOUNT).run_id == run_id and engine.state == 'idle'


def test_discard_ends_the_offer_and_survives_a_reopen(tmp_path):
    ledger = BatchRunLedger(tmp_path / 'runs')
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41',), account_id=ACCOUNT).run_id
    ledger._recover_interrupted()
    assert ledger.resumable_run(account_id=ACCOUNT).run_id == run_id
    ledger.discard_run(ledger.run(run_id).handle)
    assert ledger.resumable_run(account_id=ACCOUNT) is None
    ledger.close()
    reopened = BatchRunLedger(tmp_path / 'runs')
    assert reopened.resumable_run(account_id=ACCOUNT) is None and reopened.run(run_id).state == 'interrupted'
    reopened.close()


def test_a_version_1_ledger_is_migrated_in_place(tmp_path):
    import sqlite3
    ledger = BatchRunLedger(tmp_path / 'runs')
    run_id = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=None).run_id
    ledger._recover_interrupted()
    ledger.close()
    database = sqlite3.connect(str(tmp_path / 'runs' / 'batch-runs.sqlite3'))
    database.executescript('DROP TABLE run_flags; PRAGMA user_version=1;')
    database.close()
    reopened = BatchRunLedger(tmp_path / 'runs')
    assert reopened.run(run_id).state == 'interrupted' and reopened.resumable_run(account_id=None).run_id == run_id
    reopened.close()


def test_only_the_newest_run_can_be_resumed(env):
    ledger, _ = env
    old = ledger.begin_run('authors', 'manual', 'both', ('41',), account_id=None).run_id
    ledger._recover_interrupted()
    newer = ledger.begin_run('authors', 'manual', 'both', ('42',), account_id=None).run_id
    ledger.set_run_state(ledger.run(newer).handle, 'done')
    assert ledger.resumable_run(account_id=None) is None and old != newer


class JobPort(FakePort):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.job_calls = []
        self.hold_jobs = True

    def pause_jobs(self, refs):
        self.job_calls.append(('pause', sorted(r.job_id for r in refs)))

    def resume_jobs(self, refs):
        self.job_calls.append(('resume', sorted(r.job_id for r in refs)))


def test_user_pause_holds_the_queued_jobs_and_resume_releases_them(env, monkeypatch):
    port = JobPort({'41': (cand('41', 1), cand('41', 2))})
    ledger, _ = env
    engine = engine_for(port, env)
    checking, proceed = threading.Event(), threading.Event()
    check_state = engine._check_state

    def at_pause_boundary(*args, **kwargs):
        if len(port.enqueued) == 2 and not checking.is_set():
            checking.set()
            assert proceed.wait(5)
        return check_state(*args, **kwargs)

    monkeypatch.setattr(engine, '_check_state', at_pause_boundary)
    handle = engine.start(('41',))
    try:
        assert checking.wait(5)
        assert engine.pause(handle)
        proceed.set()
        assert wait_for(lambda: any(c[0] == 'pause' for c in port.job_calls))
        ids = sorted(j for _, _, j in port.enqueued)
        assert port.job_calls[0] == ('pause', ids)
        port.hold_jobs = False
        assert engine.resume(handle)
        assert engine.join(5) and ledger.run(handle.run_id).state == 'done'
        assert ('resume', ids) in port.job_calls
    finally:
        proceed.set()
        assert engine._shutdown(5)


def test_a_gate_pause_does_not_touch_the_jobs(env):
    port = JobPort({'41': (cand('41', 1),)})
    engine = engine_for(port, env)
    engine.start(('41',))
    assert wait_for(lambda: port.enqueued)
    engine.pause(engine.active_run(ACCOUNT), 'network_stopped')
    assert wait_for(lambda: engine.state == 'paused')
    assert not any(c[0] == 'pause' for c in port.job_calls)   # the admission gate owns those jobs
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(5)


def test_stop_message_and_status_texts():
    assert ui.status_text('stopping') == '停止しています（いまの作者が終わるまで待っています）…'
    assert ui.status_text('stopped') == '停止しました。'
    assert ui.status_text('done') == '完了しました。'
    assert ui.status_text('running', index=2, total=5) == '2 / 5 人目…'
    assert ui.status_text('idle') == '待機中'
    assert '失敗' not in ui.status_text('stopped')
    assert ui.status_text('paused', pause_reason='network_stopped') != ui.status_text('paused')


def test_the_final_event_of_a_stop_maps_to_the_stopped_message(env):
    port = FakePort({'41': (cand('41', 1),)})
    port.hold_jobs = True
    events = []
    engine = engine_for(port, env, on_progress=events.append)
    engine.start(('41',))
    assert wait_for(lambda: port.enqueued)
    engine.stop(engine.active_run(ACCOUNT))
    assert engine.join(5)
    progress = ui.RunProgress(['41'])
    for event in events:
        progress.apply(event)
    assert progress.state == 'stopped' and ui.end_message(progress.state) == '停止しました。'


def test_resume_after_a_restart_stamps_with_the_account_that_began_the_run(env):
    from pixivvault_nicegui.batch_engine import BatchRunEngine
    port = FakePort({'41': (cand('41', 1),), '42': (cand('42', 2),), '43': ()})
    ledger, following = env
    first_engine, first = run_and_stop_after_first(port, env)
    assert ledger.run(first).account_id == ACCOUNT
    # a new process: its engine starts without any account
    fresh = BatchRunEngine(port, ledger, following=following, account_id=None,
                           poll_seconds=0.001, wait=lambda s: None)
    second = fresh.start_unfinished(ledger.run(first).handle).run_id
    assert fresh.join(5) and ledger.run(second).state == 'done'
    assert all(stamps(following)[u] for u in ('41', '42', '43'))


def test_real_jobservice_pause_and_resume_through_the_services_port(tmp_path):
    """pause_jobs / resume_jobs swallow errors, so only a real JobService proves the action names."""
    from types import SimpleNamespace as NS
    from pixivvault_nicegui.batch_engine import JobRef, ServicesPort
    from pixivvault_nicegui.job_service import JobService
    release = threading.Event()
    started = threading.Event()

    def runner(job, context):
        started.set()
        release.wait(10)
        return 'ok'
    jobs = JobService(runner, tmp_path / 'jobs.sqlite3')
    try:
        port = ServicesPort(NS(jobs=jobs, closing=False))
        running = jobs.enqueue('pixiv-illust', '1', 't', operation_id='%032x' % 1)
        queued = jobs.enqueue('pixiv-illust', '2', 't', operation_id='%032x' % 2)
        assert started.wait(5)
        refs = [JobRef('illustration', running.job_id, '%032x' % 1), JobRef('illustration', queued.job_id, '%032x' % 2)]
        port.pause_jobs(refs)
        assert wait_for(lambda: {j.job_id: j.state for j in jobs.snapshot()}.get(queued.job_id) == 'paused')
        port.resume_jobs(refs)
        assert wait_for(lambda: {j.job_id: j.state for j in jobs.snapshot()}.get(queued.job_id) in ('queued', 'running'))
        release.set()
        assert wait_for(lambda: all(j.state == 'done' for j in jobs.snapshot()), 10)
    finally:
        release.set()
        jobs.close()


def test_d_an_automatic_check_does_not_hide_the_interrupted_manual_run(env):
    ledger, _ = env
    manual = ledger.begin_run('authors', 'manual', 'both', ('41', '42'), account_id=None).run_id
    ledger._recover_interrupted()
    auto = ledger.begin_run('authors', 'auto', 'both', ('43',), account_id=None).run_id
    ledger.set_run_state(ledger.run(auto).handle, 'done')
    assert ledger.resumable_run(account_id=None).run_id == manual           # the offer survives the timer's run
    newer = ledger.begin_run('authors', 'manual', 'both', ('44',), account_id=None).run_id
    ledger.set_run_state(ledger.run(newer).handle, 'done')
    assert ledger.resumable_run(account_id=None) is None                    # a newer manual run does end it


def test_d_an_interrupted_automatic_run_is_not_offered(env):
    ledger, _ = env
    ledger.begin_run('authors', 'auto', 'both', ('41',), account_id=None)
    ledger._recover_interrupted()
    assert ledger.resumable_run(account_id=None) is None
