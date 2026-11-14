import threading,time
from types import SimpleNamespace
from multiprocessing.reduction import ForkingPickler
import pytest
from pixivvault_nicegui.job_service import JobService,JobServiceError
from pixivvault_nicegui.media_jobs import MediaJobs
from pixivvault_nicegui.native_lifecycle import NativeTrayBridge

class Native:
    def __init__(self):self.start_args={};self.main_window=object();self.events={}
    def on(self,name,callback):self.events[name]=callback

def make(jobs,monkeypatch,send):
    app=SimpleNamespace(native=Native())
    bridge=NativeTrayBridge(app,SimpleNamespace(get_setting=lambda *a:'1'),lambda *a:SimpleNamespace(stop=lambda:None),SimpleNamespace(jobs=jobs))
    bridge.shown();bridge._test_sender=ForkingPickler.loads(ForkingPickler.dumps(bridge._sender));bridge._test_sender.send('ready')
    for _ in range(1000):
        if bridge._child_ready:break
        time.sleep(.001)
    monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',send)
    return bridge,app

def wait_result(bridge,expected):
    for _ in range(1000):
        if bridge.last_exit_result==expected:return
        time.sleep(.001)
    pytest.fail('result not received')

def test_idle_skips_confirmation_and_failure_reopens_admission(monkeypatch):
    jobs=JobService(lambda job,ctx:'done'); calls=[]
    bridge,app=make(jobs,monkeypatch,lambda app,token,confirm:calls.append(confirm) or True)
    try:
        assert bridge.controller.exit_from_tray() and calls==[False]
        with pytest.raises(JobServiceError):jobs.enqueue('synthetic','1')
        bridge._test_sender.send('failed');wait_result(bridge,'failed')
        assert not jobs._native_exit_freeze
        assert jobs.enqueue('synthetic','1')
    finally:app.native.events['closed']();bridge._test_sender.close();jobs.close()

def test_all_queues_fenced_atomically_and_failure_releases(monkeypatch):
    one,two=JobService(lambda j,c:'done'),JobService(lambda j,c:'done')
    bridge,app=make(MediaJobs(one,two),monkeypatch,lambda *a:True)
    try:
        assert bridge.controller.exit_from_tray()
        for jobs in (one,two):
            with pytest.raises(JobServiceError):jobs.enqueue('synthetic','1')
        bridge._test_sender.send('failed');wait_result(bridge,'failed')
        assert not one._native_exit_freeze and not two._native_exit_freeze
    finally:app.native.events['closed']();bridge._test_sender.close();one.close();two.close()

def test_active_and_unknown_confirm_without_freezing(monkeypatch):
    started=threading.Event();release=threading.Event()
    def runner(job,ctx):
        started.set();release.wait(2);ctx.checkpoint();return 'done'
    jobs=JobService(runner);jobs.enqueue('synthetic','1');assert started.wait(1)
    calls=[];bridge,app=make(jobs,monkeypatch,lambda app,token,confirm:calls.append(confirm) or True)
    try:
        assert bridge.controller.exit_from_tray() and calls==[True] and not jobs._native_exit_freeze
        bridge._test_sender.send('cancelled');wait_result(bridge,'cancelled');assert jobs.snapshot()[0].state=='running'
    finally:release.set();app.native.events['closed']();bridge._test_sender.close();jobs.close()

def test_enqueue_and_idle_request_cannot_both_win(monkeypatch):
    release=threading.Event()
    def runner(job,ctx):
        release.wait(2);ctx.checkpoint();return 'done'
    jobs=JobService(runner); bridge,app=make(jobs,monkeypatch,lambda *a:True)
    try:
        barrier=threading.Barrier(2);result=[]
        def submit():
            barrier.wait()
            try:jobs.enqueue('synthetic','race');result.append('accepted')
            except JobServiceError:result.append('fenced')
        t=threading.Thread(target=submit);t.start();barrier.wait()
        assert bridge.controller.exit_from_tray();t.join(2);assert not t.is_alive()
        if bridge._idle_lease:assert result==['fenced']
        else:assert result==['accepted']
        bridge._test_sender.send('failed');wait_result(bridge,'failed')
    finally:release.set();app.native.events['closed']();bridge._test_sender.close();jobs.close()

def test_idle_lease_blocks_retry_and_releases_on_dispatch_failure(monkeypatch):
    failed=threading.Event()
    def runner(job,ctx):failed.set();raise ValueError('synthetic')
    jobs=JobService(runner); original=jobs.enqueue('synthetic','1');assert failed.wait(1)
    for _ in range(1000):
        if jobs.snapshot()[0].state=='error':break
        time.sleep(.001)
    assert jobs.snapshot()[0].state=='error'
    attempts=[]
    bridge,app=make(jobs,monkeypatch,lambda app,token,confirm:attempts.append((confirm,jobs.control(original.job_id,'retry'))) or False)
    try:
        assert not bridge.controller.exit_from_tray()
        assert attempts==[(False,False)] and not jobs._native_exit_freeze
        assert jobs.control(original.job_id,'retry')
    finally:app.native.events['closed']();bridge._test_sender.close();jobs.close()

def test_deleted_row_with_worker_still_active_requires_confirmation(monkeypatch):
    started=threading.Event();release=threading.Event()
    def runner(job,ctx):
        started.set();release.wait(2);ctx.checkpoint();return 'done'
    jobs=JobService(runner);item=jobs.enqueue('synthetic','1');assert started.wait(1)
    assert jobs.control(item.job_id,'delete') and jobs.snapshot()==()
    calls=[];bridge,app=make(jobs,monkeypatch,lambda app,token,confirm:calls.append(confirm) or True)
    try:
        assert bridge.controller.exit_from_tray() and calls==[True] and not jobs._native_exit_freeze
        bridge._test_sender.send('cancelled');wait_result(bridge,'cancelled')
    finally:release.set();app.native.events['closed']();bridge._test_sender.close();jobs.close()

def test_channel_loss_preserves_idle_fence_until_closed(monkeypatch):
    jobs=JobService(lambda j,c:'done');stopped=[]
    bridge,app=make(jobs,monkeypatch,lambda *a:True)
    bridge.controller.icon=SimpleNamespace(stop=lambda:stopped.append(True))
    try:
        assert bridge.controller.exit_from_tray() and jobs._native_exit_freeze
        bridge._test_sender.close()
        for _ in range(2000):
            if bridge.failed:break
            time.sleep(.001)
        assert bridge.failed and bridge.last_exit_result=='unconfirmed' and bridge.exit_unconfirmed
        assert jobs._native_exit_freeze and bridge.controller.exit_pending and not stopped
        with pytest.raises(JobServiceError):jobs.enqueue('synthetic','after-channel-loss')
    finally:app.native.events['closed']();jobs.close()
    assert stopped==[True]

def test_channel_eof_after_child_closed_preserves_fence_until_event(monkeypatch):
    jobs=JobService(lambda j,c:'done');stopped=[]
    bridge,app=make(jobs,monkeypatch,lambda *a:True)
    bridge.controller.icon=SimpleNamespace(stop=lambda:stopped.append(True))
    try:
        assert bridge.controller.exit_from_tray() and jobs._native_exit_freeze
        bridge._test_sender.send('closed');bridge._test_sender.close()
        wait_result(bridge,'closed');time.sleep(.05)
        assert not bridge.failed and bridge.controller.exit_pending and jobs._native_exit_freeze and not stopped
        with pytest.raises(JobServiceError):jobs.enqueue('synthetic','after-closed')
    finally:app.native.events['closed']();jobs.close()
    assert stopped==[True]

def synthetic_child_channel(sender,stop):
    sender.send('ready')
    stop.wait(10)
    sender.close()

def test_spawned_child_loss_preserves_idle_until_closed(monkeypatch):
    from multiprocessing import get_context
    ctx=get_context('spawn');stop=ctx.Event();jobs=JobService(lambda j,c:'done');stopped=[]
    class Native:
        def __init__(self):self.start_args={};self.main_window=object();self.events={}
        def on(self,name,callback):self.events[name]=callback
    app=SimpleNamespace(native=Native())
    bridge=NativeTrayBridge(app,SimpleNamespace(get_setting=lambda *a:'1'),lambda *a:SimpleNamespace(stop=lambda:stopped.append(True)),SimpleNamespace(jobs=jobs))
    bridge.shown();monkeypatch.setattr('pixivvault_nicegui.native_lifecycle.send_owned_tray_exit',lambda *a:True)
    child=ctx.Process(target=synthetic_child_channel,args=(bridge._sender,stop));child.start()
    try:
        for _ in range(2000):
            if bridge._child_ready:break
            time.sleep(.001)
        assert bridge._child_ready and bridge.controller.exit_from_tray() and jobs._native_exit_freeze
        stop.set();child.join(10);assert child.exitcode==0
        for _ in range(2000):
            if bridge.failed:break
            time.sleep(.001)
        assert bridge.failed and bridge.last_exit_result=='unconfirmed' and bridge.controller.exit_pending
        assert jobs._native_exit_freeze and not stopped
        with pytest.raises(JobServiceError):jobs.enqueue('synthetic','after-child-loss')
    finally:
        stop.set();child.join(10);app.native.events['closed']();jobs.close()
    assert stopped==[True]
