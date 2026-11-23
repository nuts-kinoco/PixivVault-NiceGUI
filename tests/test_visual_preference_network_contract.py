"""Work 8 contracts with isolated data and synthetic transports."""

import asyncio
from threading import Event, Thread, current_thread
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui import gpu_arbiter, visual_preference_worker as wiring
from pixivvault_nicegui.pixiv_read_client import PublicImageReadClient
from pixivvault_nicegui.recommendation_state import RecommendationController, RecommendationGroup, RecommendationSnapshot
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_gpuarb_remote_media import png
from test_ranking_source import Reader, row
from test_recommendation_visual import _load_rendered, _publish_fixture_model, _rendered, _suffix, _work
from test_visual_preference_shadow import wait_for
from test_x_ctx_recommend_20261118 import open_view


def _services(root, transport):
    reader = PublicImageReadClient(transport=transport)
    reader._verified_identity = '101'
    services = ApplicationServices(live_client=reader, preferences_root=root / 'preferences',
                                   discovery_root=root / 'discovery')
    services._egara_model_path = root / 'unused.onnx'
    services.sync_visual_preference()
    return services


def _shelf(services, count):
    works = tuple(_work(i) for i in range(1, count + 1))
    services.recommendations = RecommendationController(SimpleNamespace(load=lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('group', 'Certain', works),)),), unavailable=())))


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['shutdown', 'switch', 'off'])
async def test_rendered_exposure_drains_at_service_boundary(tmp_path, monkeypatch, boundary):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = _services(tmp_path, lambda *_: pytest.fail('unexpected dispatch'))
    _shelf(services, 1)
    worker = services.visual_preference_worker
    if boundary == 'off':
        services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1'))

    client = Client(ui.page('/_gpu8_exposure_' + boundary))
    try:
        with client:
            view = await open_view(services)
            assert len(_rendered(view)) == 1
            visual = view._visual_binding()
            stamp = visual.exposure_stamp()
            # Queue another render and synchronously cross the service boundary.
            # No event-loop cycle is available to rescue a view-local task.
            view.render()
            if boundary == 'shutdown':
                services.close()
            elif boundary == 'switch':
                services.live_client._verified_identity = '102'
                services.sync_visual_preference(account_only=True)
                assert services.visual_preference_worker is not worker
                assert services.visual_preference_worker.store.events() == ()
            else:
                services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '0'))
            events = worker.store.events()
            assert [event.kind for event in events] == ['recommendation_exposure'] * 2
            assert events[0].payload == events[1].payload
            visual.queue_exposure(events[0].payload, stamp)
            worker.flush_events()
            assert worker.store.events() == events
            if boundary == 'off':
                view.render()
                worker.flush_events()
                assert len(worker.store.events()) == 3  # Fresh passive observation after OFF.
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_off_matches_unbound_recommendations_and_rankings(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    sent = []
    image = png()

    def transport(request, *_):
        sent.append((request.get_method(), request.full_url))
        return image

    def unexpected(*_, **__):
        pytest.fail('analysis started in the default OFF settings')

    monkeypatch.setattr(wiring.EgaraInference, '__init__', unexpected)
    monkeypatch.setattr(gpu_arbiter.SharedGpuLease, 'acquire', unexpected)
    services = _services(tmp_path, transport)
    _shelf(services, 6)
    reader = Reader()
    reader.rows = [row(202), row(201)]
    reader.rows[0].update(rank=2, view_count=50)
    services.ranking_source = RankingSource(reader)
    worker = services.visual_preference_worker
    client = Client(ui.page('/_gpu8_off_ranks'))
    try:
        with client:
            services.discovery.shadow_observer = None
            recommendation = await open_view(services)
            ranking = RankingView(services)
            ranking.active = True
            await ranking.load()
            original = tuple(_suffix(node) for node in _rendered(recommendation))
            original_rank = tuple(ranking.gallery.cards)
            assert original == tuple('123456') and len(original_rank) == 2
            _load_rendered(services.images, _rendered(recommendation))
            requests = tuple(sent)
            ranking_requests = tuple(reader.calls)
            assert len(requests) == 6 and all(method == 'GET' for method, _ in requests)

            services.discovery.shadow_observer = worker
            _publish_fixture_model(worker.store, worker._model_sha)
            await recommendation.refresh()
            ranking.render()
            _load_rendered(services.images, _rendered(recommendation))
            assert tuple(_suffix(node) for node in _rendered(recommendation)) == original
            assert tuple(ranking.gallery.cards) == original_rank
            assert tuple(sent) == requests and tuple(reader.calls) == ranking_requests
            assert not services.visual_preference_enabled()
            assert worker.status()['phase'] == 'disabled' and worker._thread is None
            assert [event.kind for event in await asyncio.to_thread(_flush_and_read, worker)] == [
                'recommendation_exposure']
            recommendation.close()
            ranking.close()
    finally:
        client.delete()
        services.close()


def _flush_and_read(worker):
    worker.flush_events()
    return worker.store.events()


def test_old_learning_capture_cannot_erase_new_exposure_gap(tmp_path, monkeypatch):
    from pixivvault_nicegui.recommendation_media import RecommendationVisual

    services = _services(tmp_path, lambda *_: pytest.fail('unexpected dispatch'))
    worker = services.visual_preference_worker
    visual = RecommendationVisual(worker)
    store = worker.store
    captured, resume = Event(), Event()
    capture, append = store.capture_event_epoch, store.append_event
    learning = Thread(target=worker.ranking_finalized, args=(lambda: [],))

    def delayed_capture():
        epoch = capture()
        if current_thread() is learning:
            captured.set()
            assert resume.wait(5)
        return epoch

    def failed_append(kind, *_):
        assert kind == 'recommendation_exposure'
        raise OSError('synthetic append failure')

    monkeypatch.setattr(store, 'capture_event_epoch', delayed_capture)
    monkeypatch.setattr(store, 'append_event', failed_append)
    learning.start()
    try:
        assert captured.wait(5)
        store.purge()
        visual.record(())
        with pytest.raises(RuntimeError, match='unresolved gap'):
            worker.flush_events()
        monkeypatch.setattr(store, 'append_event', append)
        resume.set()
        learning.join(5)
        assert not learning.is_alive()
        visual.record(())
        events = _flush_and_read(worker)
        assert [event.kind for event in events] == ['capture_gap', 'recommendation_exposure']
        assert events[0].payload == {'count': 1}
    finally:
        resume.set()
        learning.join(5)
        services.close()


@pytest.mark.parametrize('failure', [False, True], ids=['success', 'inference_failure'])
def test_gpu_worker_uses_token_lease_without_pixiv_dispatch(tmp_path, monkeypatch, failure):
    from struct import pack

    trace = tmp_path / 'lease.trace'
    script = tmp_path / 'lease.py'
    script.write_text("""
import argparse
import json
from pathlib import Path
from time import time

parser = argparse.ArgumentParser()
parser.add_argument('command', choices=('acquire', 'release'))
parser.add_argument('--owner', required=True)
parser.add_argument('--pid', required=True, type=int)
parser.add_argument('--vram-gib')
parser.add_argument('--minutes')
parser.add_argument('--wait')
parser.add_argument('--token')
args = parser.parse_args()
assert args.owner == 'PV' and args.pid > 0
if args.command == 'acquire':
    assert args.token is None
    print('acquired', json.dumps({'token': 'synthetic-grant', 'expires': time() + 120}))
else:
    assert args.token == 'synthetic-grant'
with Path(__file__).with_suffix('.trace').open('a') as output:
    output.write(args.command + '\\n')
""", encoding='utf-8')
    lease = gpu_arbiter.SharedGpuLease(script)
    monkeypatch.setattr(gpu_arbiter, 'SharedGpuLease', lambda: lease)
    dispatched = []

    def transport(request, *_):
        dispatched.append(request.get_method())
        raise RuntimeError('unexpected Pixiv transport')

    class Predictor:
        def __init__(self, *_, provider, **__):
            assert provider == 'cuda'
            assert trace.read_text().splitlines() == ['acquire']

        def infer_with_meta(self, _image):
            assert trace.read_text().splitlines() == ['acquire']
            if failure:
                raise wiring.EgaraInferenceError('synthetic prediction failure')
            return {wiring.STYLE: pack('<1024f', 1., *([0.] * 1023))}, 'cuda'

        def close(self):
            with trace.open('a') as output:
                output.write('close\n')

    monkeypatch.setattr(wiring, 'EgaraInference', Predictor)
    services = _services(tmp_path, transport)
    worker = services.visual_preference_worker
    try:
        assert not trace.exists() and worker._thread is None
        services.start_visual_preference()
        assert worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=png()))
        if failure:
            wait_for(lambda: worker.status()['phase'] == 'failed')
            assert worker.status()['error'] == 'cuda_failure'
        else:
            wait_for(lambda: worker.store.progress()[0] == 1)
    finally:
        services.close()
    assert trace.read_text().splitlines() == ['acquire', 'close', 'release']
    assert dispatched == []


@pytest.mark.asyncio
async def test_busy_gpu_keeps_base_recommendations_without_extra_get(tmp_path, monkeypatch):
    from contextlib import contextmanager

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    waiting, predictor_called = Event(), Event()

    class BusyLease:
        @contextmanager
        def acquire(self, cancel):
            waiting.set()
            assert cancel.wait(8)
            gpu_arbiter.check_gpu_stop(cancel)
            yield

    def predictor(*_, **__):
        predictor_called.set()
        raise AssertionError('predictor loaded on a busy GPU')

    monkeypatch.setattr(gpu_arbiter, 'SharedGpuLease', BusyLease)
    monkeypatch.setattr(wiring, 'EgaraInference', predictor)
    sent = []
    image = png()

    def transport(request, *_):
        sent.append((request.get_method(), request.full_url))
        return image

    services = _services(tmp_path, transport)
    _shelf(services, 6)
    worker = services.visual_preference_worker
    client = Client(ui.page('/_gpu8_busy_ranks'))
    try:
        with client:
            view = await open_view(services)
            baseline = tuple(_suffix(node) for node in _rendered(view))
            _load_rendered(services.images, _rendered(view))
            requests = tuple(sent)
            assert baseline == tuple('123456') and len(requests) == 6
            services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1'))
            _publish_fixture_model(worker.store, worker._model_sha)
            services.start_visual_preference()
            assert worker.submit_image('1', 0, SimpleNamespace(variant='regular_view', data=image))
            assert await asyncio.to_thread(waiting.wait, 5)
            assert worker.status()['waiting_for_lease']
            await view.refresh()
            assert tuple(_suffix(node) for node in _rendered(view)) == baseline
            _load_rendered(services.images, _rendered(view))
            assert tuple(sent) == requests and not predictor_called.is_set()
            services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '0'))
            assert worker.status()['phase'] == 'paused'
            assert worker._thread is None and not predictor_called.is_set()
            assert not worker.status()['waiting_for_lease']
            view.close()
    finally:
        client.delete()
        services.close()
