"""F1: synthetic broken legacy images are per-page; backend faults still stop.

No real images, model, database, network, or GUI is involved in these tests.
"""
from threading import Event
from time import sleep

import pytest

from pixivvault_nicegui.egara_inference import (
    EgaraBackendUnavailable, EgaraInferenceError, EgaraInvalidImage,
)
from pixivvault_nicegui.local_media_adapter import (
    MediaAmbiguous, MediaMissing, MediaUnavailable,
)
from pixivvault_nicegui.similarity_worker import SimilarityWorker
from test_similarity_worker import eventually


@pytest.mark.parametrize('error,code', [
    (EgaraInvalidImage('synthetic truncated JPEG'), 'invalid_image'),
    (MediaMissing('synthetic missing legacy page'), 'missing_input'),
    (MediaAmbiguous('synthetic duplicate image'), 'missing_input'),
    (MediaUnavailable('synthetic unreadable legacy image'), 'missing_input'),
])
def test_six_bad_legacy_pages_cannot_block_two_later_good_pages(error, code):
    calls=[]
    completed=Event()
    def pages():
        return [(str(i),1) for i in range(1,9)]
    def analyze(wid,page,*,is_current):
        assert page==1 and is_current()
        calls.append(wid)
        if int(wid)<=6:
            raise error
        if wid=='8':completed.set()
        return wid=='7'
    worker=SimilarityWorker(pages,analyze,idle_seconds=120)
    worker.start()
    try:
        assert completed.wait(2)
        eventually(lambda: worker.status()['indexed']==1 and worker.status()['resumed']==1)
        status=worker.status()
        assert calls==[str(i) for i in range(1,9)]
        assert status['examined']==8 and status['errors']==6
        assert status['phase']=='idle'
        assert status['error_code'] is None
        sleep(.03)
        assert len(calls)==8, 'finite scan must not busy-spin on invalid pages'
    finally:worker.stop(timeout=2)


@pytest.mark.parametrize('error,expected',[
    (EgaraInvalidImage('invalid'), 'invalid_image'),
    (MediaMissing('missing'), 'missing_input'),
    (MediaUnavailable('unreadable'), 'missing_input'),
])
def test_only_bad_pages_report_nonfatal_classification_and_wait(error,expected):
    calls=[];finished=Event()
    def pages():return [(str(i),1) for i in range(1,7)]
    def analyze(wid,page,*,is_current):
        calls.append(wid)
        if wid=='6':finished.set()
        raise error
    worker=SimilarityWorker(pages,analyze,idle_seconds=120)
    worker.start()
    try:
        assert finished.wait(2)
        eventually(lambda:worker.status()['phase']=='idle')
        result=worker.status()
        assert result['examined']==result['errors']==6
        assert result['error_code']==expected
        assert result['indexed']==result['resumed']==0
        assert calls==[str(i) for i in range(1,7)]
        sleep(.04)
        assert len(calls)==6, 'bad-page scan must not retry continuously'
        worker.wake()
        eventually(lambda:len(calls)==12)
        assert worker.status()['phase']!='failed'
    finally:worker.stop(timeout=2)


@pytest.mark.parametrize('error',[
    EgaraBackendUnavailable('synthetic CUDA backend unavailable'),
    EgaraInferenceError('synthetic wrong model outputs'),
])
def test_explicit_backend_fault_stays_fail_stop_and_blocks_later_page(error):
    calls=[]
    def analyze(wid,page,*,is_current):
        calls.append(wid)
        if wid=='1':raise error
        return True
    worker=SimilarityWorker(lambda:[('1',1),('2',1)],analyze,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['phase']=='failed')
        assert calls==['1']
        assert worker.status()['error_code']=='model_error'
        worker.wake()
        sleep(.04)
        assert calls==['1'] and worker.status()['phase']=='failed'
    finally:worker.stop(timeout=2)


def test_pause_during_bad_page_invalidates_generation_without_count_or_late_page():
    entered=Event();release=Event();calls=[];captured=[];enumerations=0
    def pages():
        nonlocal enumerations
        enumerations+=1
        return [('broken',1),('healthy',1)] if enumerations==1 else [('healthy',1)]
    def analyze(wid,page,*,is_current):
        calls.append(wid)
        captured.append(is_current)
        if wid=='broken':
            entered.set()
            assert release.wait(2)
            raise EgaraInvalidImage('synthetic late image failure')
        return True
    worker=SimilarityWorker(pages,analyze,idle_seconds=120)
    worker.start()
    try:
        assert entered.wait(2)
        worker.pause()
        assert captured[0]() is False
        release.set()
        eventually(lambda:worker.status()['phase']=='paused')
        sleep(.03)
        assert calls==['broken']
        assert worker.status()['errors']==worker.status()['indexed']==0
        worker.resume()
        eventually(lambda:worker.status()['indexed']==1)
        assert calls==['broken','healthy']
        assert captured[0]() is False
    finally:
        release.set()
        worker.stop(timeout=2)


def test_stop_during_bad_page_keeps_join_fail_closed_and_no_following_page():
    entered=Event();release=Event();calls=[];captured=[]
    def analyze(wid,page,*,is_current):
        calls.append(wid)
        captured.append(is_current)
        entered.set()
        assert release.wait(2)
        raise MediaMissing('synthetic late missing input')
    worker=SimilarityWorker(lambda:[('broken',1),('healthy',1)],analyze,idle_seconds=120)
    worker.start()
    assert entered.wait(2)
    try:
        with pytest.raises(TimeoutError):
            worker.stop(timeout=.01)
        assert captured[0]() is False
    finally:
        release.set()
        worker.stop(timeout=2)
    assert calls==['broken']
    assert worker.status()['phase']=='stopped'
    assert worker.status()['errors']==0


@pytest.mark.parametrize('reason',[
    'catalog closed', 'library index not yet prepared', 'obsolete library index',
])
def test_persistent_systemic_catalog_error_uses_existing_five_failure_guard(reason):
    calls=[]
    def analyze(wid,page,*,is_current):
        calls.append(wid)
        raise MediaUnavailable(reason)
    worker=SimilarityWorker(lambda:[(str(i),1) for i in range(1,10)],
                            analyze,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['phase']=='failed')
        assert calls==[str(i) for i in range(1,6)]
        assert worker.status()['errors']==5
        assert worker.status()['error_code']=='analysis_error'
        worker.wake();sleep(.03)
        assert len(calls)==5
    finally:worker.stop(timeout=2)
