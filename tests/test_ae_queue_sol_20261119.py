import json
import time
import sqlite3
from threading import Event, RLock
from types import SimpleNamespace

from nicegui import ui


def test_completion_log_observes_committed_done_not_running(tmp_path):
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.queue_log import QueueLog
    gate = Event(); entered = Event(); seen = []
    log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner._lock = RLock()
    owner.services = SimpleNamespace(queue_log=log)
    def execute(*_):
        entered.set(); assert gate.wait(5); return 'receipt'
    owner._run_command = execute
    append = log.append
    def capture(message, **kw):
        if 'ダウンロード完了' in message:
            with sqlite3.connect(tmp_path / 'jobs.sqlite') as db:
                persisted = json.loads(db.execute('SELECT payload FROM jobs').fetchone()[0])['state']
            seen.append((owner.jobs.snapshot()[0].state, persisted))
        append(message, **kw)
    log.append = capture
    owner.jobs = JobService(owner._run, tmp_path / 'jobs.sqlite', terminal_callback=owner._terminal_log)
    try:
        job = owner.jobs.enqueue('extension-command', json.dumps({'type':'work','work_id':'123'}), 'work')
        assert entered.wait(5)
        gate.set()
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline: time.sleep(.01)
        assert seen == [('done', 'done')]
        assert owner.jobs.snapshot()[0].state == 'done'
    finally:
        gate.set(); owner.jobs.close()


def test_stopped_before_runner_returns_never_logs_completed(tmp_path):
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.queue_log import QueueLog
    gate = Event(); entered = Event(); log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner._lock = RLock()
    owner.services = SimpleNamespace(queue_log=log)
    def execute(*_):
        entered.set(); assert gate.wait(5); return 'receipt'
    owner._run_command = execute
    owner.jobs = JobService(owner._run, tmp_path / 'jobs.sqlite', terminal_callback=owner._terminal_log)
    try:
        job = owner.jobs.enqueue('extension-command', json.dumps({'type':'work','work_id':'123'}), 'work')
        assert entered.wait(5)
        assert owner.jobs.control(job.job_id, 'stop')
        gate.set()
        deadline = time.monotonic() + 5
        while owner.jobs.snapshot()[0].state != 'stopped' and time.monotonic() < deadline: time.sleep(.01)
        assert owner.jobs.snapshot()[0].state == 'stopped'
        assert not any('ダウンロード完了' in row.message for row in log.snapshot())
    finally:
        gate.set(); owner.jobs.close()


def test_reopening_log_requests_scroll_after_hidden_update(monkeypatch):
    from nicegui import core
    from pixivvault_nicegui.queue_log import QueueLog
    from pixivvault_nicegui.ui.job_views import QueueView
    log = QueueLog()
    with ui.column():
        view = QueueView(SimpleNamespace(jobs=SimpleNamespace(snapshot=lambda: (), faulted=False), artworks=(), queue_log=log))
    calls = []
    monkeypatch.setattr(core, 'loop', object())
    monkeypatch.setattr(view.client, 'run_javascript', calls.append)
    try:
        view.toggle_log()
        log.append('hidden update'); view.refresh()
        calls.clear()
        view.toggle_log()
        assert calls and 'scrollHeight' in calls[-1]
    finally:
        view.close()


def test_failed_terminal_persistence_never_logs_completion(tmp_path):
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.job_service import JobService, JobStorageError
    from pixivvault_nicegui.queue_log import QueueLog
    gate = Event(); entered = Event(); log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner._lock = RLock()
    owner.services = SimpleNamespace(queue_log=log)
    def execute(*_):
        entered.set(); assert gate.wait(5); return 'receipt'
    owner._run_command = execute
    owner.jobs = JobService(owner._run, tmp_path / 'jobs.sqlite', terminal_callback=owner._terminal_log)
    real_write = owner.jobs._store.write
    def reject_done(jobs, operations, retired=frozenset()):
        if any(item.state == 'done' for item in jobs.values()):
            raise JobStorageError('synthetic write failure')
        return real_write(jobs, operations, retired)
    owner.jobs._store.write = reject_done
    try:
        owner.jobs.enqueue('extension-command', json.dumps({'type':'work','work_id':'123'}), 'work')
        assert entered.wait(5)
        gate.set()
        deadline = time.monotonic() + 5
        while not owner.jobs.faulted and time.monotonic() < deadline: time.sleep(.01)
        assert owner.jobs.faulted
        assert owner.jobs.snapshot()[0].state == 'running'
        assert not any('ダウンロード完了' in row.message for row in log.snapshot())
    finally:
        gate.set(); owner.jobs.close()


def test_duplicate_acceptance_precedes_terminal_and_names_existing_job(tmp_path):
    from threading import RLock
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.extension_bridge import ExtensionDownload
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.queue_log import QueueLog
    gate = Event(); entered = Event(); log = QueueLog()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner.services = SimpleNamespace(queue_log=log, closing=False)
    owner._lock = RLock()
    def execute(*_):
        entered.set(); assert gate.wait(5); return 'receipt'
    owner._run_command = execute
    owner.jobs = JobService(owner._run, tmp_path / 'jobs.sqlite', terminal_callback=owner._terminal_log)
    try:
        command = ExtensionDownload('work', '123')
        first = owner.enqueue(command)
        assert entered.wait(5)
        assert owner.enqueue(command) == first
        gate.set()
        deadline = time.monotonic() + 5
        while len(log.snapshot()) < 3 and time.monotonic() < deadline: time.sleep(.01)
        messages = [item.message for item in log.snapshot()]
        assert messages == ['[拡張機能連携] DL命令を受信: work (id=123)',
                            '[拡張機能連携] DL(既存ジョブ)命令を受信: work (id=123)',
                            '[拡張機能連携] ダウンロード完了: work (id=123)']
    finally:
        gate.set(); owner.jobs.close()


def test_fast_job_cannot_log_terminal_before_acceptance_or_deadlock(tmp_path):
    from threading import Thread
    from pixivvault_nicegui.extension_application import ExtensionApplication
    from pixivvault_nicegui.extension_bridge import ExtensionDownload
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.queue_log import QueueLog
    log = QueueLog(); entered = Event(); release = Event()
    owner = ExtensionApplication.__new__(ExtensionApplication)
    owner._lock = RLock(); owner.services = SimpleNamespace(queue_log=log, closing=False)
    owner._run_command = lambda *_: 'receipt'
    original_append = log.append
    def slow_acceptance(message, **kwargs):
        if '命令を受信' in message:
            entered.set(); assert release.wait(5)
        original_append(message, **kwargs)
    log.append = slow_acceptance
    owner.jobs = JobService(owner._run, tmp_path / 'jobs.sqlite', terminal_callback=owner._terminal_log)
    thread = Thread(target=lambda: owner.enqueue(ExtensionDownload('work', '123')))
    try:
        thread.start(); assert entered.wait(5)
        deadline = time.monotonic() + 5
        while (not owner.jobs.snapshot() or owner.jobs.snapshot()[0].state != 'done') and time.monotonic() < deadline:
            time.sleep(.01)
        assert owner.jobs.snapshot()[0].state == 'done'
        assert not log.snapshot()
        release.set(); thread.join(5); assert not thread.is_alive()
        deadline = time.monotonic() + 5
        while len(log.snapshot()) < 2 and time.monotonic() < deadline: time.sleep(.01)
        assert ['命令を受信' in log.snapshot()[0].message, 'ダウンロード完了' in log.snapshot()[1].message] == [True, True]
    finally:
        release.set(); thread.join(5); owner.jobs.close()
