"""Original GET retry behavior without external API or Cookie access."""
from dataclasses import replace
from threading import Event,Thread
from types import SimpleNamespace
import time
import pytest
from pixivvault_nicegui.request_policy import RequestRetryPolicy,RequestAdmission,request_scope,job_request_runner
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui import pixiv_read_client as reader
from pixivvault_nicegui.job_service import JobService,JobCancelled
from test_job_service import wait,find
from test_pixiv_read_client import make_client,MockTransport,packet,home_page

URL='https://i.pximg.net/img-master/img/2026/01/01/00/00/00/123_p0_master1200.jpg'

class Clock:
    def __init__(self):self.now=0.0;self.sleeps=[];self.on_sleep=None
    def __call__(self):return self.now
    def sleep(self,seconds):
        self.sleeps.append(seconds);self.now+=seconds
        if self.on_sleep:self.on_sleep()

def policy(clock,tries=3,wait=5,jitter=0):
    return RequestRetryPolicy(lambda:RequestPreferences(.6,tries,wait),clock=clock,sleep=clock.sleep,jitter=lambda lo,hi:jitter)

def fail(error):
    def operation():raise error
    return operation

@pytest.mark.parametrize('tries',[1,3,5,10])
def test_count_is_total_http_tries_429_wait_is_original_setting(tries):
    clock=Clock();retry=policy(clock,tries=tries,wait=3);calls=[];error=reader.HTTPStatusRejected(429)
    def operation():calls.append(1);raise error
    with pytest.raises(reader.HTTPStatusRejected) as caught:retry.execute(operation,deadline=1000)
    assert caught.value is error and len(calls)==tries
    assert sum(clock.sleeps)==3*(tries-1)

def test_transient_backoff_jitter_and_success():
    clock=Clock();retry=policy(clock,jitter=.25);calls=[]
    def operation():
        calls.append(1)
        if len(calls)==1:raise reader.TransportFailed()
        if len(calls)==2:raise reader.HTTPStatusRejected(503)
        return b'ok'
    assert retry.execute(operation,deadline=100)==b'ok'
    assert clock.sleeps==[1.25,2.25]

@pytest.mark.parametrize('error',[
    reader.AuthenticationMissing(),reader.CookieSourceRejected(),reader.HTTPStatusRejected(401),reader.HTTPStatusRejected(403),
    reader.HTTPStatusRejected(404),reader.HTTPStatusRejected(400),reader.HTTPStatusRejected(302),
    reader.ReadDeadlineExceeded(),reader.InvalidPayload(),reader.ApiRejected(),reader.ResponseRejected(),
    reader.ResponseLimitExceeded(),reader.DestinationRejected(),reader.ClientClosed(),JobCancelled('stopped'),
])
def test_auth_absence_deadline_and_invalid_payload_never_retry(error):
    clock=Clock();calls=[]
    def operation():calls.append(1);raise error
    with pytest.raises(type(error)) as caught:policy(clock).execute(operation,deadline=100)
    assert caught.value is error and calls==[1] and clock.sleeps==[]

def test_no_second_get_when_configured_wait_cannot_fit_original_deadline():
    clock=Clock();calls=[]
    def operation():calls.append(1);raise reader.HTTPStatusRejected(429)
    with pytest.raises(reader.ReadDeadlineExceeded):policy(clock,wait=30).execute(operation,deadline=30)
    assert calls==[1] and clock.sleeps==[]

def test_nested_strict_scope_isolation_and_settings_snapshot():
    clock=Clock();calls=[];snapshots=[]
    def settings():snapshots.append(1);return RequestPreferences(.6,3,5)
    retry=RequestRetryPolicy(settings,clock=clock,sleep=clock.sleep,jitter=lambda *_:0)
    def operation():calls.append(1);raise reader.HTTPStatusRejected(429)
    with request_scope(allow_retry=False):
        with request_scope(allow_retry=True):
            with pytest.raises(reader.HTTPStatusRejected):retry.execute(operation,deadline=100)
    assert calls==[1] and snapshots==[]
    with pytest.raises(reader.HTTPStatusRejected):retry.execute(operation,deadline=100)
    assert len(calls)==4 and snapshots==[1]

def test_checkpoint_cancel_interrupts_429_wait_without_second_get():
    clock=Clock();calls=[];cancelled=[False]
    def checkpoint():
        if cancelled[0]:raise JobCancelled('stop')
    clock.on_sleep=lambda:cancelled.__setitem__(0,True)
    def operation():calls.append(1);raise reader.HTTPStatusRejected(429)
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(JobCancelled):policy(clock,wait=30).execute(operation,deadline=100)
    assert calls==[1] and clock.now==pytest.approx(.1)

def test_request_admission_is_interruptible_and_lock_released():
    clock=Clock();admit=RequestAdmission(lambda:1.5,clock=clock,sleep=clock.sleep);admit(100);cancelled=[False]
    clock.on_sleep=lambda:cancelled.__setitem__(0,True)
    def checkpoint():
        if cancelled[0]:raise JobCancelled('stop')
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(JobCancelled):admit(100)
    assert clock.now==pytest.approx(.1)
    clock.on_sleep=None;admit(100);assert clock.now==pytest.approx(1.5)

def test_public_image_retry_remains_cookie_free_and_same_destination(monkeypatch):
    clock=Clock();monkeypatch.setattr(reader.time,'monotonic',clock);calls=[]
    def transport(request,timeout,limit,deadline):
        calls.append((request,timeout,limit,deadline))
        if len(calls)==1:raise reader.HTTPStatusRejected(429)
        return b'fixture'
    client=reader.PublicImageReadClient(transport=transport);client.set_retry_policy(policy(clock))
    try:
        assert client.get_image_bytes(URL)==b'fixture'
        assert len(calls)==2 and clock.now==pytest.approx(5)
        assert all(call[0].full_url==URL and call[0].get_method()=='GET' and not call[0].has_header('Cookie') for call in calls)
        assert all(call[1]<=10 and call[3]<=120 for call in calls)
    finally:client.close()

def test_client_close_during_retry_wait_prevents_retry(monkeypatch):
    clock=Clock();monkeypatch.setattr(reader.time,'monotonic',clock);calls=[]
    def transport(*args):calls.append(1);raise reader.HTTPStatusRejected(429)
    client=reader.PublicImageReadClient(transport=transport);client.set_retry_policy(policy(clock));clock.on_sleep=client.close
    with pytest.raises(reader.ClientClosed):client.get_image_bytes(URL)
    assert calls==[1] and clock.now==pytest.approx(.1)

def test_raw_metadata_is_immediate_failure_even_with_policy(tmp_path,monkeypatch):
    clock=Clock();monkeypatch.setattr(reader.time,'monotonic',clock);mock=MockTransport()
    mock.when('https://www.pixiv.net/',home_page());mock.when('/ajax/user/extra',packet({'userId':'55'}))
    client=make_client(tmp_path,mock);client.verify_identity();client.set_retry_policy(policy(clock));calls=[]
    def unavailable(*args):calls.append(1);raise reader.HTTPStatusRejected(429)
    client._transport=unavailable
    try:
        with pytest.raises(reader.HTTPStatusRejected):client.get_artwork_metadata_raw('123','10')
        assert calls==[1] and clock.sleeps==[]
    finally:client.close()

def test_job_http_retries_keep_attempt_operation_and_one_durable_receipt(tmp_path):
    clock=Clock();retry=policy(clock);calls=[]
    def runner(job,context):
        def read():
            calls.append((job.attempt,service.operation_ids(job.job_id)))
            if len(calls)%3:raise reader.HTTPStatusRejected(503)
            return 'receipt'
        result=retry.execute(read,deadline=1000);context.progress(1,1);return context.publish(result.__str__)
    service=JobService(job_request_runner(runner),tmp_path/'jobs.json')
    try:
        job=service.enqueue('demo','one',operation_id='original-operation')
        done=wait(service,job.job_id,'done')
        assert done.attempt==1 and done.result=='receipt' and calls==[(1,('original-operation',))]*3
        assert service.enqueue('demo','one',operation_id='original-operation').job_id==done.job_id
    finally:service.close()

def test_stop_while_retrying_cannot_publish_receipt_or_requeue(tmp_path):
    entered,release=Event(),Event();clock=Clock();calls=[]
    def sleep(seconds):entered.set();assert release.wait(3);clock.now+=seconds
    retry=RequestRetryPolicy(lambda:RequestPreferences(.6,3,30),clock=clock,sleep=sleep,jitter=lambda *_:0)
    def runner(job,context):
        def read():calls.append(1);raise reader.HTTPStatusRejected(429)
        retry.execute(read,deadline=1000);return context.publish(lambda:'must-not-publish')
    service=JobService(job_request_runner(runner),tmp_path/'stop-jobs.json')
    try:
        job=service.enqueue('demo','one',operation_id='stop-operation');assert entered.wait(3)
        running=find(service,job.job_id);assert service.control(job.job_id,'stop',running.revision)
        release.set();stopped=wait(service,job.job_id,'stopped')
        assert stopped.attempt==1 and stopped.result is None and calls==[1]
    finally:release.set();service.close()

def test_thread_local_strict_scope_does_not_disable_other_operation():
    barrier=Event();release=Event();results=[]
    def strict():
        with request_scope(allow_retry=False):
            barrier.set();assert release.wait(3)
            c=Clock()
            with pytest.raises(reader.HTTPStatusRejected):policy(c).execute(fail(reader.HTTPStatusRejected(429)),deadline=100)
            results.append(sum(c.sleeps))
    worker=Thread(target=strict);worker.start();assert barrier.wait(3)
    try:
        c=Clock()
        with pytest.raises(reader.HTTPStatusRejected):policy(c).execute(fail(reader.HTTPStatusRejected(429)),deadline=100)
        assert sum(c.sleeps)==10
    finally:release.set();worker.join(3)
    assert not worker.is_alive() and results==[0]

def test_injected_http_auth_failure_is_not_misclassified_as_transport(monkeypatch):
    from urllib.error import HTTPError
    clock=Clock();monkeypatch.setattr(reader.time,'monotonic',clock);calls=[]
    def transport(*args):calls.append(1);raise HTTPError(URL,403,'private response',{},None)
    client=reader.PublicImageReadClient(transport=transport);client.set_retry_policy(policy(clock))
    try:
        with pytest.raises(reader.HTTPStatusRejected) as caught:client.get_image_bytes(URL)
        assert caught.value.status_code==403 and calls==[1] and clock.sleeps==[] and URL not in str(caught.value)
    finally:client.close()

def test_explicit_job_retry_alone_increments_durable_attempt(tmp_path):
    clock=Clock();retry=policy(clock);calls=[]
    def runner(job,context):
        def read():
            calls.append(job.attempt)
            if job.attempt==1:raise reader.HTTPStatusRejected(503)
            return 'receipt'
        result=retry.execute(read,deadline=1000);return context.publish(lambda:result)
    service=JobService(job_request_runner(runner),tmp_path/'retry-jobs.json')
    try:
        job=service.enqueue('demo','one',operation_id='retry-operation');failed=wait(service,job.job_id,'error')
        assert failed.attempt==1 and calls==[1,1,1]
        assert service.control(job.job_id,'retry',failed.revision)
        done=wait(service,job.job_id,'done');assert done.attempt==2 and calls==[1,1,1,2]
        assert service.operation_ids(job.job_id)==('retry-operation',)
    finally:service.close()

def test_pause_during_429_wait_resumes_same_job_attempt(tmp_path):
    entered,release,second=Event(),Event(),Event();clock=Clock();calls=[]
    def sleep(seconds):
        if not entered.is_set():entered.set();assert release.wait(3)
        clock.now+=seconds
    retry=RequestRetryPolicy(lambda:RequestPreferences(.6,3,5),clock=clock,sleep=sleep,jitter=lambda *_:0)
    def runner(job,context):
        def read():
            calls.append(job.attempt)
            if len(calls)==1:raise reader.HTTPStatusRejected(429)
            second.set();return 'receipt'
        result=retry.execute(read,deadline=1000);return context.publish(lambda:result)
    service=JobService(job_request_runner(runner),tmp_path/'pause-jobs.json')
    try:
        job=service.enqueue('demo','one');assert entered.wait(3)
        running=find(service,job.job_id);assert service.control(job.job_id,'pause',running.revision)
        paused=find(service,job.job_id);release.set();assert not second.wait(.1)
        assert service.control(job.job_id,'resume',paused.revision)
        done=wait(service,job.job_id,'done');assert done.attempt==1 and calls==[1,1]
    finally:release.set();service.close()
