from contextlib import contextmanager
import os
import struct
import subprocess
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from pixivvault_nicegui import gpu_arbiter, visual_preference_worker as wiring
from pixivvault_nicegui.services import ApplicationServices
from test_visual_preference_shadow import store_at, wait_for
from test_visual_preference_store import generation, model


@pytest.fixture
def services(tmp_path):
    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    app = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                              discovery_root=tmp_path / 'discovery')
    app._egara_model_path = tmp_path / 'fake.onnx'
    try:
        yield app
    finally:
        app.close()


def test_busy_helper_uses_zero_wait_and_worker_can_stop(tmp_path):
    helper = tmp_path / 'lease.py'
    entered = helper.with_suffix('.ready')
    helper.write_text("""
from pathlib import Path
import sys
import time
wait = float(sys.argv[sys.argv.index('--wait') + 1])
Path(__file__).with_suffix('.ready').write_text(str(wait))
if wait:
    time.sleep(3)
raise SystemExit(2)
""", encoding='utf-8')
    worker = wiring.VisualPreferenceWorker(store_at(tmp_path / 'visual'), tmp_path / 'fake.onnx',
        gpu_arbiter=gpu_arbiter.GpuInferenceArbiter(), shared_lease=gpu_arbiter.SharedGpuLease(helper))
    worker.start()
    try:
        worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=b'image'))
        wait_for(entered.is_file)
        worker.stop(timeout=1, pause=True)
        assert entered.read_text() == '0.0'
        assert worker.status()['phase'] == 'paused'
    finally:
        worker.stop()


def test_acquire_timeout_leaves_no_live_owner(tmp_path, monkeypatch):
    owners = []
    launched = []
    cancel = Event()
    popen = subprocess.Popen

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        launched.append(process)
        return process

    def lost_receipt(args, **kwargs):
        owners.append(int(args[args.index('--pid') + 1]))
        cancel.set()
        raise subprocess.TimeoutExpired('fake', kwargs['timeout'])

    monkeypatch.setattr(subprocess, 'Popen', track)
    monkeypatch.setattr(subprocess, 'run', lost_receipt)
    with pytest.raises(gpu_arbiter.GpuInferenceStopped):
        with gpu_arbiter.SharedGpuLease(tmp_path / 'fake.py').acquire(cancel):
            pytest.fail('unexpected lease')
    assert len(owners) == 1 and owners[0] != os.getpid()
    assert len(launched) == 1 and launched[0].pid == owners[0] and launched[0].poll() is not None


def test_cancel_after_grant_releases_token_without_entering_body(tmp_path, monkeypatch):
    cancel = Event()
    calls = []
    lease = gpu_arbiter.SharedGpuLease(tmp_path / 'fake.py')

    def call(*args, **kwargs):
        calls.append((args, kwargs['timeout']))
        if args[0] == 'acquire':
            cancel.set()
            return SimpleNamespace(returncode=0, stdout='acquired {"token":"synthetic","expires":4102444800}')
        return SimpleNamespace(returncode=0, stdout='released')

    monkeypatch.setattr(lease, '_call', call)
    with pytest.raises(gpu_arbiter.GpuInferenceStopped):
        with lease.acquire(cancel):
            pytest.fail('started despite stop')
    assert [args[0] for args, _ in calls] == ['acquire', 'release']
    assert calls[1][0][1:] == ('--token', 'synthetic')
    assert sum(timeout for _, timeout in calls) < 30


def test_crossing_expiry_reserve_reacquires_without_invalidating(tmp_path, monkeypatch):
    clock = [0]
    history = []
    arbiter = gpu_arbiter.GpuInferenceArbiter()

    class Lease:
        @contextmanager
        def acquire(self, cancel):
            history.append('acquire')
            yield clock[0] + 100
            history.append('release')

    class Predictor:
        def __init__(self, *args, **kwargs):
            pass

        def infer_with_meta(self, data):
            clock[0] = 41
            with arbiter.acquire(self):
                history.append('infer')
                return {wiring.STYLE: struct.pack('<1024f', 1, *([0] * 1023))}, 'cuda'

        def close(self):
            history.append('vram')

    monkeypatch.setattr(wiring, 'EgaraInference', Predictor)
    monkeypatch.setattr(wiring, 'time', lambda: clock[0])
    store = store_at(tmp_path / 'visual')
    expected = generation(store)
    assert store.publish(expected, model(expected).to_payload())
    worker = wiring.VisualPreferenceWorker(store, tmp_path / 'fake.onnx',
        gpu_arbiter=arbiter, shared_lease=Lease())
    try:
        worker.start()
        worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=b'image'))
        wait_for(lambda: worker.status()['indexed'] or worker.status()['phase'] == 'failed')
        assert worker.status()['indexed'] == 1
        assert store.is_current(expected)
        assert history[:6] == ['acquire', 'vram', 'release', 'acquire', 'infer', 'vram']
    finally:
        worker.stop()


def test_sync_failure_visible_and_account_poll_retries(services, monkeypatch):
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    start = worker.start
    attempts = []

    def fail_once(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RuntimeError('synthetic release in progress')
        start(**kwargs)

    monkeypatch.setattr(worker, 'start', fail_once)
    snapshot = services.preferences.update({'visual_preference_enabled': '1', 'visual_preference_mode': 'idle'},
        expected_revision=services.preferences.snapshot().revision)
    services.sync_visual_preference(snapshot)
    assert services.visual_preference_status()['error'] == 'scope_failure'
    assert not services.visual_preference_enabled()
    services.sync_visual_preference(account_only=True)
    assert len(attempts) == 2
    assert services.visual_preference_status()['error'] is None
    assert services.visual_preference_enabled()


def test_manual_pause_survives_saving_correction_and_model_changes(services, tmp_path):
    services.preferences.set_setting('visual_preference_mode', 'idle')
    services.sync_visual_preference()
    first = services.visual_preference_worker
    services.pause_visual_preference()
    wait_for(lambda: first.status()['phase'] == 'paused')
    services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1'))
    assert first.status()['phase'] == 'paused'
    expected = generation(first.store)
    assert first.store.publish(expected, model(expected).to_payload())
    services._egara_model_path = tmp_path / 'replacement.onnx'
    services._egara_provider = 'cpu'
    services.sync_visual_preference(account_only=True)
    second = services.visual_preference_worker
    assert second is not first and second.provider == 'cpu' and second.status()['phase'] == 'paused'
    assert not first.store.is_current(expected)


def test_default_off_invalidates_prior_generation_on_first_sync(services):
    from pixivvault_nicegui.visual_preference_store import VisualPreferenceStore

    root, scope = services._visual_store_scope('123')
    store = VisualPreferenceStore(root, **scope)
    expected = generation(store)
    assert store.publish(expected, model(expected).to_payload())
    services.sync_visual_preference()
    assert not store.is_current(expected)
    assert services.visual_preference_worker.status()['phase'] == 'disabled'


def test_saved_projection_covers_new_managed_and_ugoira(services):
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    services.managed_downloads = SimpleNamespace(
        catalog_snapshot=lambda: SimpleNamespace(source_pages={'12': [0]}))
    services.ugoira = SimpleNamespace(library=lambda: [{'spec': {'work_id': '34'}}])
    try:
        worker.ranking_finalized(lambda: [{'work_id': wid, 'judgment': None, 'revision': 0}
                                          for wid in ('12', '34', '56')])
        worker.flush_events()
        rows = worker.store.events()[0].payload['candidates']
        assert [(row.get('saved', False), row['judgment']) for row in rows] == [
            (True, ''), (True, ''), (False, None),
        ]
    finally:
        services.managed_downloads = services.ugoira = None


def test_omitted_judgment_normalizes_only_first_saved_revision(tmp_path):
    store = store_at(tmp_path / 'visual')
    store.append_event('exposure', {'work_id': '1', 'saved': True})
    store.append_event('exposure', {'work_id': '2', 'saved': True, 'revision': 2})
    first, second = store.events()
    assert first.payload['judgment'] == ''
    assert 'judgment' not in second.payload


def test_close_with_unresolved_gap_finishes(services):
    services.sync_visual_preference()
    worker = services.visual_preference_worker

    def fail():
        raise ValueError('synthetic event capture failure')

    with pytest.raises(ValueError):
        worker.ranking_finalized(fail)
    services.close()
    assert services.closed


def test_close_reports_visual_and_other_shutdown_failures(monkeypatch):
    services = ApplicationServices()
    events = []

    class Visual:
        def retire(self):
            events.append('cancel visual')

        def stop(self, **kwargs):
            events.append('join visual')
            raise ValueError('synthetic visual failure')

        def flush_events(self, **kwargs):
            pass

    class Scheduler:
        def stop(self, **kwargs):
            events.append('join scheduler')
            raise RuntimeError('synthetic scheduler failure')

    services.visual_preference_worker = Visual()
    services.managed_schedule_runner = Scheduler()
    try:
        with pytest.raises(ExceptionGroup) as result:
            services.close()
        assert {type(error) for error in result.value.exceptions} == {ValueError, RuntimeError}
        assert events == ['cancel visual', 'join scheduler', 'join visual']
    finally:
        services.managed_schedule_runner = None
        services.close()



def test_helper_killed_after_publish_before_reply_expires_owner(tmp_path, monkeypatch):
    helper = tmp_path / 'lease.py'
    helper.write_text("""
from pathlib import Path
import sys
import time
Path(__file__).with_suffix('.published').write_text(sys.argv[sys.argv.index('--pid') + 1])
time.sleep(60)
""", encoding='utf-8')
    processes = []
    popen = subprocess.Popen

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', track)
    monkeypatch.setattr(gpu_arbiter, '_LEASE_CALL_TIMEOUT', 2)
    cancel = Event()

    def cancel_after_publish():
        wait_for(helper.with_suffix('.published').is_file)
        cancel.set()

    watcher = Thread(target=cancel_after_publish)
    watcher.start()
    try:
        with pytest.raises(gpu_arbiter.GpuInferenceStopped):
            with gpu_arbiter.SharedGpuLease(helper).acquire(cancel):
                pytest.fail('lease receipt was never sent')
    finally:
        cancel.set()
        watcher.join(10)
    owner, helper_process = processes
    assert int(helper.with_suffix('.published').read_text()) == owner.pid
    assert owner.poll() is not None and helper_process.poll() is not None


@pytest.mark.skipif(os.name != 'nt', reason='Windows process-handle check')
def test_forced_parent_exit_retires_owned_liveness_pid(tmp_path):
    import ctypes
    from ctypes import wintypes
    import sys

    helper = tmp_path / 'lease.py'
    helper.write_text("""
import json
import sys
import time
print('acquired', json.dumps({'token': 'synthetic', 'expires': time.time() + 100}))
""", encoding='utf-8')
    ready = tmp_path / 'ready'
    code = """
import subprocess, sys, time
from pathlib import Path
from threading import Event
from pixivvault_nicegui.gpu_arbiter import SharedGpuLease
lease = SharedGpuLease(Path(sys.argv[1]))
with lease.acquire(Event()):
    with Path(sys.argv[2]).open('x') as out:
        out.write(str(owners[0].pid))
    time.sleep(60)
"""
    # Record only the first Popen, the lease owner process, in the isolated parent.
    setup = """
import subprocess
owners = []
original = subprocess.Popen
def tracked(*args, **kwargs):
    process = original(*args, **kwargs)
    owners.append(process)
    return process
subprocess.Popen = tracked
"""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    with subprocess.Popen([sys._base_executable, '-B', '-c', setup + code, str(helper), str(ready)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as parent:
        owner = None
        try:
            wait_for(lambda: (ready.is_file() and ready.stat().st_size > 0) or parent.poll() is not None)
            assert parent.poll() is None
            owner = kernel.OpenProcess(0x00100000, False, int(ready.read_text()))
            assert owner and kernel.WaitForSingleObject(owner, 0) == 0x102
            parent.terminate()
            parent.wait(timeout=5)
            assert kernel.WaitForSingleObject(owner, 5_000) == 0
        finally:
            if parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=5)
            if owner:
                kernel.CloseHandle(owner)



def test_no_second_native_call_when_load_crosses_lease_deadline():
    from pixivvault_nicegui.egara_inference import EgaraInference

    arbiter = gpu_arbiter.GpuInferenceArbiter()
    engine = object.__new__(EgaraInference)
    engine._gpu_arbiter = arbiter
    engine._session = None
    engine._input_name = 'input'
    engine._backbone_index = 0
    engine.provider_requested = 'cuda'
    engine._check_model = lambda: None
    events = []
    elapsed = Event()

    class DeadlineReached(Exception):
        pass

    def checkpoint():
        if elapsed.is_set():
            raise DeadlineReached()

    def native_load(*args):
        events.append('load')
        elapsed.set()
        return SimpleNamespace(run=lambda *args: events.append('run'))

    engine._load = native_load
    with pytest.raises(DeadlineReached):
        with arbiter.stop_scope(checkpoint=checkpoint):
            with arbiter.acquire(engine) as stop:
                engine._on_provider('cuda', stop, None, infer=True)
    assert events == ['load']


def test_lease_checkpoint_wakes_while_local_arbiter_is_busy():
    arbiter = gpu_arbiter.GpuInferenceArbiter()
    expired = Event()
    stopped = Event()

    class DeadlineReached(Exception):
        pass

    def checkpoint():
        if expired.is_set():
            raise DeadlineReached()

    def waiting():
        try:
            with arbiter.stop_scope(checkpoint=checkpoint):
                with arbiter.acquire(object()):
                    pass
        except DeadlineReached:
            stopped.set()

    with arbiter.acquire(object()):
        thread = Thread(target=waiting)
        thread.start()
        try:
            wait_for(lambda: bool(arbiter._waiting))
            expired.set()
            assert stopped.wait(2.5)
        finally:
            expired.set()
    thread.join(5)
    assert not thread.is_alive()


def test_event_catalog_borrow_blocks_provider_close_until_retry(services, monkeypatch):
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    entered, release, closed = Event(), Event(), Event()

    def catalog_snapshot():
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(source_pages={})

    services.managed_downloads = SimpleNamespace(catalog_snapshot=catalog_snapshot, close=closed.set)
    flush = worker.flush_events
    monkeypatch.setattr(worker, 'flush_events', lambda **opts: flush(timeout=.15, **opts))
    try:
        worker.ranking_finalized(lambda: [{'work_id': '1'}])
        assert entered.wait(5)
        with pytest.raises((ExceptionGroup, TimeoutError)):
            services.close()
        assert not closed.is_set()
    finally:
        release.set()
        flush(require_complete=False)
        services.close()
    assert closed.is_set()


def test_bad_saved_catalog_keeps_core_shadow_event(tmp_path):
    def broken_catalog():
        raise OSError('synthetic damaged receipt')

    worker = wiring.VisualPreferenceWorker(store_at(tmp_path / 'visual'), tmp_path / 'fake.onnx',
                                            saved_ids=broken_catalog)
    worker.ranking_finalized(lambda: [{'work_id': '55', 'judgment': 'opened'}])
    worker.flush_events()
    event, = worker.store.events()
    assert event.payload['candidates'][0]['judgment'] == 'opened'


def test_transient_unverified_account_keeps_explicit_pause(services):
    services.preferences.set_setting('visual_preference_mode', 'idle')
    services.sync_visual_preference()
    services.pause_visual_preference()
    services.live_client._verified_identity = None
    services.sync_visual_preference(account_only=True)
    services.live_client._verified_identity = '123'
    services.sync_visual_preference(account_only=True)
    assert services.visual_preference_worker.status()['phase'] == 'paused'



def test_pause_remembers_bound_account_during_auth_race(services, monkeypatch):
    services.preferences.set_setting('visual_preference_mode', 'idle')
    services.sync_visual_preference()
    active = services._active_visual_worker

    def clear_identity_after_lookup():
        worker = active()
        services.live_client._verified_identity = None
        return worker

    monkeypatch.setattr(services, '_active_visual_worker', clear_identity_after_lookup)
    services.pause_visual_preference()
    services.sync_visual_preference(account_only=True)
    services.live_client._verified_identity = '123'
    services.sync_visual_preference(account_only=True)
    assert services.visual_preference_worker.status()['phase'] == 'paused'


def test_acquire_timeout_retries_with_fresh_owner(tmp_path, monkeypatch):
    acquired = []
    processes = []
    popen = subprocess.Popen

    def track(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    def complete_on_second(args, **kwargs):
        if 'release' in args:
            assert processes[-1].poll() is None
            return SimpleNamespace(returncode=0, stdout='released')
        acquired.append(int(args[args.index('--pid') + 1]))
        if len(acquired) == 1:
            raise subprocess.TimeoutExpired('fake', kwargs['timeout'])
        assert processes[0].poll() is not None
        return SimpleNamespace(returncode=0, stdout='acquired {"token":"synthetic","expires":4102444800}')

    monkeypatch.setattr(subprocess, 'Popen', track)
    monkeypatch.setattr(subprocess, 'run', complete_on_second)
    monkeypatch.setattr(gpu_arbiter, '_LEASE_RETRY_SECONDS', 0)
    with gpu_arbiter.SharedGpuLease(tmp_path / 'fake.py').acquire(Event()):
        assert len(set(acquired)) == 2
    assert all(process.poll() is not None for process in processes)
