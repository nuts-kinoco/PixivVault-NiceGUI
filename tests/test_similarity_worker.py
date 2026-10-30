"""Event-driven offline acceptance for one-owner Egara analysis work queue.

Never loads a model, opens a DB, reads original images or uses the network.
"""
from __future__ import annotations

from threading import Event, Lock, current_thread
from time import monotonic, sleep

import pytest

from pixivvault_nicegui.egara_inference import EgaraInferenceError, EgaraBackendUnavailable
from pixivvault_nicegui.similarity_worker import SimilarityWorker


def eventually(predicate, *, seconds=2.0):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    assert predicate(), 'event deadline exceeded'


def test_constructor_never_runs_and_single_thread_sequential_new_vs_resumed():
    observed=[]
    done=Event()
    def pages():
        observed.append(('enumerated',current_thread().name))
        return [('101',1),('101',2),('102',1)]
    def analyze(wid,page,*,is_current):
        observed.append(('analyzed',wid,page,current_thread().ident,is_current()))
        if len([r for r in observed if r[0]=='analyzed'])==3:
            done.set()
        return (wid,page)!=('101',2)
    worker=SimilarityWorker(pages,analyze,idle_seconds=120)
    assert observed==[]
    assert worker.status()==dict(phase='idle',indexed=0,resumed=0,errors=0,
                                 examined=0,error_code=None)
    worker.start(); worker.start()
    try:
        assert done.wait(2)
        eventually(lambda:worker.status()['indexed']==2 and worker.status()['resumed']==1)
        data=[r for r in observed if r[0]=='analyzed']
        assert len(data)==3 and len({r[3] for r in data})==1
        assert all(r[4] is True for r in data)
        assert worker.status()['examined']==3
        assert worker.status()['errors']==0
    finally:
        worker.stop(timeout=2)
    assert worker.status()['phase']=='stopped'
    with pytest.raises(RuntimeError):worker.start()


def test_idle_event_wait_does_not_spin_and_wake_causes_scan():
    calls=0
    visited=Event()
    def pages():
        nonlocal calls
        calls+=1
        visited.set()
        return []
    worker=SimilarityWorker(pages,lambda *a,**k:True,idle_seconds=120)
    worker.start()
    try:
        assert visited.wait(2)
        eventually(lambda:worker.status()['phase']=='idle')
        sleep(0.04)
        assert calls==1
        visited.clear();worker.wake()
        assert visited.wait(2)
        assert calls==2
    finally:worker.stop(timeout=2)


def test_pause_blocks_new_page_and_old_inflight_token_stays_invalid_after_resume():
    entered=Event();release=Event();calls=[];captured=[]
    scan_calls=0
    def pages():
        nonlocal scan_calls
        scan_calls+=1
        return [('21',1),('21',2)] if scan_calls==1 else []
    def analyze(wid,page,*,is_current):
        calls.append((wid,page))
        captured.append(is_current)
        entered.set()
        assert release.wait(2)
        return True
    worker=SimilarityWorker(pages,analyze,idle_seconds=120)
    worker.start()
    try:
        assert entered.wait(2)
        assert captured[0]() is True
        worker.pause();worker.pause()
        assert worker.status()['phase']=='paused'
        assert captured[0]() is False
        worker.wake()  # paused worker must not enumerate next work.
        release.set()
        eventually(lambda:worker.status()['phase']=='paused')
        sleep(0.04)
        assert calls==[('21',1)] and worker.status()['indexed']==0
        worker.resume()
        assert captured[0]() is False  # generation-bound even after resume
        eventually(lambda:scan_calls>=2)
        assert len(calls)==1
    finally:worker.stop(timeout=2)


def test_stop_invalidates_inflight_then_refuses_restarts_and_double_stop_safe():
    started=Event();release=Event();observed=[]
    def analyze(wid,page,*,is_current):
        observed.append(is_current)
        started.set();assert release.wait(2)
        return True
    worker=SimilarityWorker(lambda:[('77',1),('77',2)],analyze,idle_seconds=120)
    worker.start()
    assert started.wait(2)
    assert observed[0]() is True
    try:
        with pytest.raises(TimeoutError):worker.stop(timeout=0.01)
        assert observed[0]() is False
    finally:release.set()
    worker.stop(timeout=2)
    worker.stop(timeout=2)
    assert worker.status()['phase']=='stopped'
    assert len(observed)==1 and worker.status()['indexed']==0
    with pytest.raises(RuntimeError):worker.start()
    worker.wake();worker.resume()
    assert worker.status()['phase']=='stopped'


@pytest.mark.parametrize('error',[
    EgaraInferenceError('PRIVATE GPU path'),
    EgaraBackendUnavailable('PRIVATE CUDA device'),
])
def test_model_fault_immediately_fails_and_requires_explicit_resume(error):
    entered=Event();failed=Event();calls=0
    def analyze(wid,page,*,is_current):
        nonlocal calls
        calls+=1
        entered.set()
        if calls==1:raise error
        return False
    worker=SimilarityWorker(lambda:[('123',1)],analyze,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['phase']=='failed')
        assert calls==1 and worker.status()['error_code']=='model_error'
        assert worker.status()['errors']==1
        assert 'PRIVATE' not in repr(worker.status())
        worker.wake();sleep(.03)
        assert calls==1 and worker.status()['phase']=='failed'
        worker.resume()
        eventually(lambda:worker.status()['resumed']==1)
        assert calls==2 and worker.status()['error_code'] is None
    finally:worker.stop(timeout=2)


def test_five_consecutive_analysis_failures_park_until_resume():
    calls=0
    def analyze(wid,page,*,is_current):
        nonlocal calls
        calls+=1
        if calls<=5:raise ValueError('PRIVATE path/token')
        return False
    worker=SimilarityWorker(lambda:[(str(i),1) for i in range(1,9)],analyze,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['phase']=='failed')
        assert calls==5
        state=worker.status()
        assert state['examined']==5 and state['errors']==5 and state['error_code']=='analysis_error'
        assert state['indexed']==state['resumed']==0
        assert 'PRIVATE' not in repr(state)
        worker.wake();sleep(.04)
        assert calls==5
        worker.resume()
        eventually(lambda:worker.status()['resumed']>=1)
        assert worker.status()['phase']!='failed'
    finally:worker.stop(timeout=2)


def test_success_resets_consecutive_failure_streak():
    progress=Event();calls=0
    def analyze(wid,page,*,is_current):
        nonlocal calls
        calls+=1
        if calls==8:progress.set()
        if calls in (1,2,3,5,6,7):raise ValueError('bad frame')
        return calls%2==0
    worker=SimilarityWorker(lambda:[(str(i),1) for i in range(1,9)],analyze,idle_seconds=120)
    worker.start()
    try:
        assert progress.wait(2)
        eventually(lambda:worker.status()['resumed']+worker.status()['indexed']==2)
        assert worker.status()['errors']==6
        assert worker.status()['phase']!='failed'
    finally:worker.stop(timeout=2)


@pytest.mark.parametrize('invalid',[(12,1),('12',0),('12',-1),('12',True),('12',1.5),('12',1,2)])
def test_malformed_iterator_page_is_error_never_forwarded(invalid):
    calls=[]
    def analyze(*args,**kwargs):
        calls.append(args)
        return True
    worker=SimilarityWorker(lambda:[invalid],analyze,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['errors']==1)
        assert calls==[]
        assert worker.status()['error_code']=='invalid_page'
    finally:worker.stop(timeout=2)


def test_iterator_error_classified_without_exposing_original_exception():
    def pages():raise RuntimeError('PRIVATE original path')
    worker=SimilarityWorker(pages,lambda *a,**k:True,idle_seconds=120)
    worker.start()
    try:
        eventually(lambda:worker.status()['errors']==1)
        assert worker.status()['error_code']=='iterator_error'
        assert 'PRIVATE' not in repr(worker.status())
    finally:worker.stop(timeout=2)


def test_invalid_constructor_does_not_spawn_thread():
    for iterator,analyze,interval in [(None,lambda *a,**k:True,60),
                                      (lambda:[],None,60),
                                      (lambda:[],lambda *a,**k:True,0),
                                      (lambda:[],lambda *a,**k:True,-1),
                                      (lambda:[],lambda *a,**k:True,True)]:
        with pytest.raises((TypeError,ValueError)):
            SimilarityWorker(iterator,analyze,idle_seconds=interval)
