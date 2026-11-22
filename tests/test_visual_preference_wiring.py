from threading import Event

import pytest

from pixivvault_nicegui.preferences import PreferencesError, PreferencesStore
from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker
from test_visual_preference_shadow import store_at, wait_for


def test_visual_preference_round_trip_and_legacy_defaults(tmp_path):
    path = tmp_path / 'preferences.sqlite3'
    store = PreferencesStore(path)
    store.set_setting('grid_card_size_mode', 'medium')
    store.close()
    store = PreferencesStore(path)
    try:
        existing = store.snapshot()
        assert existing.values['grid_card_size_mode'] == 'medium'
        assert existing.values['visual_preference_enabled'] == '0'
        assert existing.values['visual_preference_mode'] == 'manual'
        assert existing.values['visual_preference_thumb_prefetch'] == '0'
        changes = {'visual_preference_enabled': '1', 'visual_preference_mode': 'idle',
                   'visual_preference_thumb_prefetch': '1'}
        store.update(changes, expected_revision=existing.revision)
        with pytest.raises(PreferencesError):
            store.set_setting('unknown_visual_option', '1')
    finally:
        store.close()
    store = PreferencesStore(path)
    try:
        assert {key: store.snapshot().values[key] for key in changes} == changes
    finally:
        store.close()


def test_first_capture_failure_after_purge_is_recorded(tmp_path):
    store = store_at(tmp_path / 'visual')
    worker = VisualPreferenceWorker(store, tmp_path / 'unused.onnx')
    worker.ranking_finalized(lambda: [{'work_id': 'before'}])
    worker.flush_events()
    store.purge()

    def failed_snapshot():
        raise ValueError('synthetic snapshot failure')

    with pytest.raises(ValueError):
        worker.ranking_finalized(failed_snapshot)
    worker.ranking_finalized(lambda: [{'work_id': 'after'}])
    worker.flush_events()
    assert [event.kind for event in store.events()] == ['capture_gap', 'ranking']
    assert store.events()[0].payload == {'count': 1}


def test_saved_unjudged_events_normalize_both_existing_spellings(tmp_path):
    store = store_at(tmp_path / 'visual')
    store.append_event('ranking', {'candidates': [
        {'work_id': '1', 'saved': True, 'judgment': None},
        {'work_id': '2', 'saved': True, 'judgment': ''},
        {'work_id': '3', 'saved': False, 'judgment': None},
        {'work_id': '4', 'saved': True, 'judgment': None, 'revision': 2},
        {'work_id': '5', 'bookmarked': True, 'judgment': None, 'revision': 0},
    ]})
    assert [r['judgment'] for r in store.events()[0].payload['candidates']] == ['', '', None, None, '']


@pytest.mark.parametrize('message', [
    'CUDA failure 2: out of memory',
    'CUDNN_STATUS_ALLOC_FAILED',
    'cudaErrorMemoryAllocation',
    'BFCArena::AllocateRawInternal Failed to allocate memory for requested buffer of size 15728896',
    'Available memory of 1024 is smaller than requested bytes of 4096',
    'Failed to find a free memory block despite calling Extend. rounded_bytes=4096',
])
def test_cuda_memory_pressure_from_ort(message):
    from pixivvault_nicegui.gpu_arbiter import cuda_memory_pressure

    assert cuda_memory_pressure(RuntimeError(message))
    assert not cuda_memory_pressure(RuntimeError('Failed to allocate output tensor sequence'))


@pytest.mark.parametrize('raise_inside', [False, True])
def test_shared_lease_releases_only_its_token(tmp_path, raise_inside):
    from contextlib import nullcontext
    from pixivvault_nicegui.gpu_arbiter import SharedGpuLease

    script = tmp_path / 'lease.py'
    script.write_text("""
import argparse
import json
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('command')
parser.add_argument('--owner', required=True)
parser.add_argument('--pid')
parser.add_argument('--vram-gib', type=int)
parser.add_argument('--minutes', type=int)
parser.add_argument('--wait', type=float)
parser.add_argument('--token')
args = parser.parse_args()
assert args.owner == 'PV'
state = Path(__file__).with_suffix('.state')
if args.command == 'acquire':
    assert int(args.pid) > 0 and args.vram_gib == 6 and 0 < args.minutes <= 30 and args.wait > 0
    state.write_text('first')
    print('acquired', json.dumps(dict(token='first', expires=time.time() + 60)))
elif args.command == 'release':
    assert args.token == 'first'
    with state.with_suffix('.attempts').open('a') as log:
        log.write('release\\n')
    if state.read_text() != args.token:
        print('token mismatch (the lease was re-acquired by someone else)')
        raise SystemExit(1)
    state.write_text('released')
    print('released')
""", encoding='utf-8')
    lease = SharedGpuLease(script)
    with (pytest.raises(ValueError) if raise_inside else nullcontext()):
        with lease.acquire(Event()):
            if raise_inside:
                script.with_suffix('.state').write_text('other')
                raise ValueError('synthetic inference failure')
    assert script.with_suffix('.attempts').read_text().splitlines() == ['release']
    assert script.with_suffix('.state').read_text() == ('other' if raise_inside else 'released')



def test_service_starts_manually_and_switches_account_scopes(tmp_path):
    from types import SimpleNamespace

    from pixivvault_nicegui.services import ApplicationServices

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    try:
        services.sync_visual_preference()
        first = services.visual_preference_worker
        assert services.discovery.shadow_observer is first
        assert first.status()['phase'] == 'disabled'
        assert not services.visual_preference_enabled()
        services.start_visual_preference()
        assert first.status()['phase'] == 'running'
        services.pause_visual_preference()
        wait_for(lambda: first.status()['phase'] == 'paused')
        reader._verified_identity = '456'
        assert not first.scope_is_current()
        services.sync_visual_preference()
        second = services.visual_preference_worker
        assert second is not first and second.store.root != first.store.root
        assert second.status()['phase'] == 'disabled'
        assert services.discovery.shadow_observer is second
        with pytest.raises(RuntimeError):
            services.clear_visual_preference(first)
    finally:
        services.close()


def test_service_clear_preserves_other_data_and_pauses(tmp_path):
    from types import SimpleNamespace

    from pixivvault_nicegui.services import ApplicationServices

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    try:
        services.sync_visual_preference()
        worker = services.visual_preference_worker
        from pixivvault_nicegui.managed_similarity import ManagedSimilarityIndex
        services.similarity_index = ManagedSimilarityIndex(tmp_path / 'similar')
        services.library_image_root = tmp_path / 'saved'
        services.library_image_root.mkdir()
        saved_image = services.library_image_root / 'saved-image.png'
        saved_image.write_bytes(b'synthetic image')
        services.discovery._submit(services.discovery._database).result(5)
        protected = {path: path.read_bytes() for path in (
            services.preferences.path, services.similarity_index.path,
            services.discovery.sandbox_root / 'learning.sqlite3', saved_image)}
        worker.ranking_finalized(lambda: [{'work_id': 'before'}])
        worker.flush_events()
        root = worker.store.root
        assert worker.store.events()
        services.clear_visual_preference(worker)
        replacement = services.visual_preference_worker
        assert replacement.store.root == root and replacement.store.events() == ()
        assert replacement.status()['phase'] == 'paused'
        assert {path: path.read_bytes() for path in protected} == protected
        assert not worker.scope_is_current()
    finally:
        services.close()


def test_visual_timeout_does_not_strand_other_shutdown_steps(monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices

    services = ApplicationServices()
    calls = []

    class Stalled:
        def retire(self):
            calls.append('retire')

        def stop(self, **kwargs):
            calls.append('stop')
            raise TimeoutError()

        def flush_events(self, **kwargs):
            calls.append('events')

    services.visual_preference_worker = Stalled()
    close_resources = services._close_resources

    def close():
        calls.append('resources')
        close_resources()

    monkeypatch.setattr(services, '_close_resources', close)
    with pytest.raises(TimeoutError):
        services.close()
    assert calls == ['retire', 'stop', 'events', 'resources']
    assert services.closed
    with pytest.raises(TimeoutError):
        services.close()
    assert calls == ['retire', 'stop', 'events', 'resources', 'stop', 'events']


def test_status_label_describes_waiting_lease():
    from pixivvault_nicegui.ui.preferences_view import visual_status_text

    text = visual_status_text(phase='running', error=None, indexed=4, pending=2,
                              generation=9, worker_available=True, waiting_for_lease=True)
    for fragment in ('待機中', '解析済み 4 件（残り 2 件）', '学習世代 9', 'GPU（CUDA）', '追加 GET 0 件',
                     '共有 GPU の空き待ち'):
        assert fragment in text



def test_busy_shared_gpu_waits_and_global_stop_cancels(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pixivvault_nicegui import visual_preference_worker as wiring
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, SharedGpuLease

    script = tmp_path / 'lease.py'
    script.write_text("import sys; raise SystemExit(2 if sys.argv[1] == 'acquire' else 1)")
    arbiter = GpuInferenceArbiter()
    called = Event()

    def unexpected_inference(*args, **kwargs):
        called.set()
        raise AssertionError('prediction while another application holds the lease')

    monkeypatch.setattr(wiring, 'EgaraInference', unexpected_inference)
    worker = VisualPreferenceWorker(store_at(tmp_path / 'visual'), tmp_path / 'fake.onnx',
                                    gpu_arbiter=arbiter, shared_lease=SharedGpuLease(script))
    try:
        worker.start()
        assert worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=b'image'))
        wait_for(lambda: worker.status()['waiting_for_lease'])
        arbiter.release_all()
        wait_for(lambda: worker.status()['phase'] == 'paused')
        assert not called.is_set()
    finally:
        worker.stop()


def test_visual_lease_releases_after_vram_and_before_runtime_close(tmp_path, monkeypatch):
    import struct
    from contextlib import contextmanager
    from time import time
    from types import SimpleNamespace

    from pixivvault_nicegui import gpu_arbiter, visual_preference_worker as wiring
    from pixivvault_nicegui.services import ApplicationServices

    calls = []
    entered, resume = Event(), Event()

    class Lease:
        @contextmanager
        def acquire(self, cancel):
            calls.append('acquire')
            try:
                yield time() + 120
            finally:
                calls.append('release')

    class Predictor:
        def __init__(self, model, *, provider, **kwargs):
            assert provider == 'cuda'
            calls.append('load')

        def infer_with_meta(self, image):
            entered.set()
            assert resume.wait(5)
            return {wiring.STYLE: struct.pack('<1024f', 1, *([0] * 1023))}, 'cuda'

        def close(self):
            calls.append('vram')

    monkeypatch.setattr(gpu_arbiter, 'SharedGpuLease', Lease)
    monkeypatch.setattr(wiring, 'EgaraInference', Predictor)
    reader = SimpleNamespace(_verified_identity='123', close=lambda: calls.append('reader'))
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    from threading import Thread
    closing = Thread(target=services.close)
    try:
        services.start_visual_preference()
        worker = services.visual_preference_worker
        worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=b'image'))
        assert entered.wait(5)
        closing.start()
        wait_for(lambda: not worker.scope_is_current())
    finally:
        resume.set()
        if closing.ident is not None:
            closing.join(10)
        services.close()
    assert calls == ['acquire', 'load', 'vram', 'release', 'reader']
    assert worker.store.progress()[0] == 0


@pytest.mark.asyncio
async def test_settings_status_and_actions_through_nicegui(tmp_path):
    from types import SimpleNamespace
    from nicegui import Client, ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.preferences_view import PreferencesView

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    client = Client(ui.page('/_visual_settings'))
    services.sync_visual_preference()
    try:
        with client:
            view = PreferencesView(services.preferences, enabled_keys=services.preference_consumers(),
                                   on_change=services.apply_preferences, visual_service=services)
            view.open()
            await view._refresh_visual_status(view._epoch)
            label = view.visual_status_label
            assert label.props['data-testid'] == 'preferences-visual-status'
            assert label.props['role'] == 'status' and label.props['aria-live'] == 'polite'
            assert '追加 GET 0 件' in label.text
            assert view.visual_start.props['data-testid'] == 'preferences-visual-start'
            assert view.visual_pause.props['data-testid'] == 'preferences-visual-pause'
            assert view.visual_clear.props['aria-haspopup'] == 'dialog'
            await view._start_visual()
            assert view.visual_pause.enabled
            await view._pause_visual()
            assert '一時停止中' in label.text
            view._confirm_visual_clear()
            confirmation = view._visual_confirm
            assert confirmation.props['data-testid'] == 'preferences-visual-clear-dialog'
            assert any('保存した作品・判定履歴・「似た絵を探す」の解析台帳は削除しません' in getattr(e, 'text', '')
                       for e in confirmation.descendants())
            view._on_dialog_key(SimpleNamespace(args={'key': 'Escape'}))
            assert view.opened
    finally:
        view.close()
        client.delete()
        services.close()


def test_off_setting_revokes_generation_and_preserves_base_ranking(tmp_path):
    from types import SimpleNamespace
    import asyncio
    from pixivvault_nicegui.services import ApplicationServices
    from test_visual_preference_store import generation, model

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    try:
        candidates = [{'work_id': str(i), 'author_id': str(i), 'tags': ['known' if i % 2 else 'unknown']}
                      for i in (39, 15, 29, 18, 19)]
        services.discovery._candidates = {row['work_id']: row for row in candidates}

        def rank():
            return asyncio.run(services.discovery.rank_pending(tuple(services.discovery._candidates), ('15',)))

        base = rank()
        assert set(base[0]) == set(services.discovery._candidates)
        services.sync_visual_preference()
        worker = services.visual_preference_worker
        expected = generation(worker.store)
        assert worker.store.publish(expected, model(expected).to_payload())
        services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1'))
        assert services.visual_preference_enabled()
        services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '0'))
        assert not services.visual_preference_enabled() and not worker.store.is_current(expected)
        assert rank() == base
    finally:
        services.close()



def test_owned_visual_database_can_be_cleared_after_open_fails(tmp_path):
    from types import SimpleNamespace

    from pixivvault_nicegui.services import ApplicationServices

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    try:
        services.sync_visual_preference()
        path = services.visual_preference_worker.store.path
        services._retire_visual_worker()
        path.write_bytes(b'broken owned db')
        services.sync_visual_preference()
        assert services.visual_preference_worker is None
        state = services.visual_preference_status()
        assert state['can_clear'] and state['error'] == 'scope_failure'
        services.clear_visual_preference(state['clear_target'])
        assert services.visual_preference_worker.store.events() == ()
    finally:
        services.close()


def test_lease_failure_is_reported_with_no_subprocess_output(tmp_path):
    from pixivvault_nicegui.gpu_arbiter import SharedGpuLease, SharedGpuLeaseReleaseError

    script = tmp_path / 'lease.py'
    script.write_text('''
import json
import sys
import time
if sys.argv[1] == 'acquire':
    print('acquired', json.dumps({'token': 'fake', 'expires': time.time() + 100}))
else:
    print('fake')
    raise SystemExit(3)
''', encoding='utf-8')
    with pytest.raises(SharedGpuLeaseReleaseError, match='^shared GPU lease release unconfirmed$'):
        with SharedGpuLease(script).acquire(Event()):
            pass



def test_idle_mode_opts_into_shadow_analysis_without_enabling_correction(tmp_path):
    from types import SimpleNamespace

    from pixivvault_nicegui.services import ApplicationServices

    reader = SimpleNamespace(_verified_identity='123', close=lambda: None)
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'fake.onnx'
    try:
        services.preferences.set_setting('visual_preference_mode', 'idle')
        services.sync_visual_preference()
        worker = services.visual_preference_worker
        assert worker.status()['phase'] == 'running' and worker.status()['automatic']
        assert not services.visual_preference_enabled()
        services.pause_visual_preference()
        wait_for(lambda: worker.status()['phase'] == 'paused')
        services.sync_visual_preference()
        assert worker.status()['phase'] == 'paused'
        services.start_visual_preference()
        assert worker.status()['phase'] == 'running' and not worker.status()['automatic']
        services.apply_preferences(services.preferences.set_setting('visual_preference_mode', 'manual'))
        wait_for(lambda: worker.status()['phase'] == 'paused')
    finally:
        services.close()



def test_start_rechecks_release_outcome_after_join(tmp_path, monkeypatch):
    import struct
    from contextlib import contextmanager
    from threading import Thread
    from time import time
    from types import SimpleNamespace

    from pixivvault_nicegui import visual_preference_worker as wiring
    from pixivvault_nicegui.gpu_arbiter import GpuInferenceArbiter, SharedGpuLeaseReleaseError

    releasing, resume, joining = Event(), Event(), Event()

    class Lease:
        @contextmanager
        def acquire(self, stop):
            yield time() + 120
            releasing.set()
            assert resume.wait(5)
            raise SharedGpuLeaseReleaseError('synthetic release failure')

    predictor = SimpleNamespace(close=lambda: None,
        infer_with_meta=lambda _data: ({wiring.STYLE: struct.pack('<1024f', 1, *([0] * 1023))}, 'cuda'))
    monkeypatch.setattr(wiring, 'EgaraInference', lambda *args, **kwargs: predictor)
    arbiter = GpuInferenceArbiter()
    worker = VisualPreferenceWorker(store_at(tmp_path / 'visual'), tmp_path / 'fake.onnx',
                                    gpu_arbiter=arbiter, shared_lease=Lease())
    join = worker._join
    errors = []

    def report_join(thread, timeout):
        joining.set()
        join(thread, timeout)

    def restart():
        try:
            worker.start()
        except RuntimeError as error:
            errors.append(error)

    monkeypatch.setattr(worker, '_join', report_join)
    thread = Thread(target=restart)
    worker.start()
    try:
        worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=b'image'))
        assert releasing.wait(5)
        arbiter.release_all()
        thread.start()
        assert joining.wait(5)
        resume.set()
        thread.join(5)
        assert not thread.is_alive()
        assert errors and worker.status()['phase'] != 'running'
    finally:
        resume.set()
        if thread.ident is not None:
            thread.join(5)
        with pytest.raises(SharedGpuLeaseReleaseError):
            worker.stop()
