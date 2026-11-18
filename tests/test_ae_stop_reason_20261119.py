from threading import Event
import time
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.egara_inference import EgaraInferenceError
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from pixivvault_nicegui.ui.similar_view import SimilarView


def test_failure_reason_survives_resume_until_success():
    entered, release = Event(), Event()
    state = {'fail': True}
    def analyze(*args, **kwargs):
        if state['fail']:
            raise EgaraInferenceError('private synthetic detail')
        entered.set()
        assert release.wait(5)
        return True
    worker = SimilarityWorker(lambda: [('1', 1)], analyze, auto=lambda: False)
    try:
        worker.start()
        deadline = time.monotonic() + 5
        while worker.status()['phase'] != 'failed' and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker.status()['phase'] == 'failed'
        assert worker.status()['last_stopped_reason'] == 'model_error'
        state['fail'] = False
        worker.resume()
        assert entered.wait(5)
        assert worker.status()['last_stopped_reason'] == 'model_error'
        release.set()
        deadline = time.monotonic() + 5
        while not worker.status()['indexed'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker.status()['last_stopped_reason'] is None
    finally:
        release.set()
        worker.stop()


@pytest.mark.asyncio
async def test_previous_stop_reason_is_shown_with_auto_state():
    status = dict(phase='idle', errors=1, indexed=0, resumed=0, examined=1,
                  started=True, auto=False, last_stopped_reason='model_error')
    client = Client(ui.page('/_ae_stop_reason'))
    try:
        with client:
            view = SimilarView(None, lambda *a: None, analysis_status=lambda: dict(status))
            view.dialog.open()
            await view.refresh_analysis_status()
            assert '前回は次の理由で止まりました' in view.analysis_text.text
            assert '再開できます' in view.analysis_text.text
            status['auto'] = True
            await view.refresh_analysis_status()
            assert '再挑戦しています' in view.analysis_text.text
            view.close()
    finally:
        client.delete()


def test_auto_wake_retries_failed_worker():
    state = {'fail': True}
    def analyze(*args, **kwargs):
        if state['fail']:
            raise EgaraInferenceError('synthetic')
        return True
    worker = SimilarityWorker(lambda: [('1', 1)], analyze, auto=lambda: True)
    try:
        worker.start()
        deadline = time.monotonic() + 5
        while worker.status()['phase'] != 'failed' and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker.status()['phase'] == 'failed'
        state['fail'] = False
        worker.wake()
        deadline = time.monotonic() + 1
        while not worker.status()['indexed'] and time.monotonic() < deadline:
            time.sleep(.01)
        assert worker.status()['indexed'] == 1
    finally:
        worker.stop()
