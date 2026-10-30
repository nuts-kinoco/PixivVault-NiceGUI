"""Original request pacing and bounded GET retries, scoped to one operation.

api_retry_count is the total number of HTTP tries (original range(count)), not
additional JobService attempts. Phase10 can suppress retries in a local scope.
No URL, response, Cookie, or exception text is persisted by this policy.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import math
import random
from threading import Lock
import time


@dataclass(frozen=True)
class _Scope:
    callbacks: tuple=()
    allow_retry: bool=True

_SCOPE=ContextVar('pixivvault_request_scope',default=_Scope())


@contextmanager
def request_scope(*,checkpoint=None,allow_retry=True):
    """Compose pause/stop guards; a nested scope cannot override no-retry."""
    if checkpoint is not None and not callable(checkpoint):raise TypeError('checkpoint must be callable')
    if type(allow_retry) is not bool:raise TypeError('allow_retry must be boolean')
    parent=_SCOPE.get()
    callbacks=parent.callbacks+((checkpoint,) if checkpoint is not None else ())
    token=_SCOPE.set(_Scope(callbacks,parent.allow_retry and allow_retry))
    try:yield
    finally:_SCOPE.reset(token)


def request_checkpoint():
    for callback in _SCOPE.get().callbacks:callback()


def job_request_runner(runner):
    """Keep HTTP retries inside the existing job/attempt and receipt CAS fences."""
    if not callable(runner):raise TypeError('runner must be callable')
    @wraps(runner)
    def run(job,context):
        with request_scope(checkpoint=context.checkpoint):return runner(job,context)
    return run


def _check_deadline(deadline,clock):
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or clock()>=deadline:
        from .pixiv_read_client import ReadDeadlineExceeded
        raise ReadDeadlineExceeded()


def _wait(seconds,deadline,*,clock,sleep):
    """Interruptible waits never consume the deadline and then issue another GET."""
    request_checkpoint();_check_deadline(deadline,clock)
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0:raise ValueError('invalid wait')
    end=clock()+seconds
    if end>=deadline:
        from .pixiv_read_client import ReadDeadlineExceeded
        raise ReadDeadlineExceeded()
    if not _SCOPE.get().callbacks:
        if seconds:sleep(seconds)
        _check_deadline(deadline,clock)
        return
    while True:
        request_checkpoint();_check_deadline(deadline,clock)
        left=end-clock()
        if left<=0:return
        sleep(min(left,0.1))


class RequestAdmission:
    def __init__(self,interval,*,clock=time.monotonic,sleep=time.sleep):
        self.interval=interval;self.clock=clock;self.sleep=sleep
        self._lock=Lock();self._last=None
    def __call__(self,deadline):
        request_checkpoint();_check_deadline(deadline,self.clock)
        while not self._lock.acquire(timeout=0.1):
            request_checkpoint();_check_deadline(deadline,self.clock)
        try:
            seconds=float(self.interval())
            if seconds not in (0.3,0.4,0.5,0.6,1.0,1.5):raise ValueError('invalid request interval')
            now=self.clock();wait=0 if self._last is None else max(0,self._last+seconds-now)
            _wait(wait,deadline,clock=self.clock,sleep=self.sleep)
            self._last=self.clock()
        finally:self._lock.release()


class RequestRetryPolicy:
    """Read configured values once per GET; share no retry counters across calls.

    settings() returns RequestPreferences (retry_limit, rate_limit_wait_seconds).
    Transport failures, 408/425, 429 and 5xx are transient candidates. Identity,
    auth 401/403, 404, redirects, invalid payload/destination/limits, operation
    deadlines and checkpoint cancellation escape immediately. The last original
    typed failure escapes when the configured total HTTP tries are exhausted.
    """
    def __init__(self,settings,*,clock=time.monotonic,sleep=time.sleep,jitter=random.uniform):
        if not all(callable(x) for x in (settings,clock,sleep,jitter)):raise TypeError('retry inputs must be callable')
        self.settings=settings;self.clock=clock;self.sleep=sleep;self.jitter=jitter
    def _settings(self):
        value=self.settings();count=value.retry_limit;wait=value.rate_limit_wait_seconds
        if type(count) is not int or count not in (1,3,5,10):raise ValueError('invalid retry count')
        if type(wait) not in (int,float) or wait not in (3.0,5.0,10.0,30.0):raise ValueError('invalid retry wait')
        return count,float(wait)
    def execute(self,operation,*,deadline):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed
        if not callable(operation):raise TypeError('operation must be callable')
        request_checkpoint();_check_deadline(deadline,self.clock)
        count,rate_wait=self._settings() if _SCOPE.get().allow_retry else (1,0)
        for attempt in range(count):
            request_checkpoint();_check_deadline(deadline,self.clock)
            try:return operation()
            except (HTTPStatusRejected,TransportFailed) as error:
                if isinstance(error,HTTPStatusRejected):
                    status=error.status_code
                    if status not in (408,425,429) and status<500:raise
                else:status=None
                if attempt+1>=count:raise
                request_checkpoint();_check_deadline(deadline,self.clock)
                if status==429:delay=rate_wait
                else:
                    noise=self.jitter(0,1)
                    if type(noise) not in (int,float) or not math.isfinite(noise) or not 0<=noise<=1:raise ValueError('invalid retry jitter')
                    delay=2**attempt+noise
                _wait(delay,deadline,clock=self.clock,sleep=self.sleep)
