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
from threading import Lock, RLock
import time


@dataclass(frozen=True)
class _Scope:
    callbacks: tuple=()
    allow_retry: bool=True

_SCOPE=ContextVar('pixivvault_request_scope',default=_Scope())
_BOUNDED_GET_RETRY=ContextVar('pixivvault_bounded_get_retry',default=False)
_LANE=ContextVar('pixivvault_request_lane',default='background')
# Original rate_coordinator.py MAX_BACKGROUND_DEFER_SECONDS: background yields to a
# waiting interactive request, but never starves for longer than this.
MAX_BACKGROUND_DEFER_SECONDS=2.0


def interactive(function):
    """Wrap a callable so its requests are admitted in the interactive lane."""
    @wraps(function)
    def run(*args,**kwargs):
        with interactive_lane():return function(*args,**kwargs)
    return run


@contextmanager
def lane_scope(lane):
    """Run requests inside the named lane; an explicit lane overrides an enclosing one."""
    token=_LANE.set('interactive' if lane == 'interactive' else 'background')
    try:yield
    finally:_LANE.reset(token)


def current_lane():
    return _LANE.get()


@contextmanager
def interactive_lane():
    """Mark requests issued inside as screen-driven (admitted ahead of background)."""
    token=_LANE.set('interactive')
    try:yield
    finally:_LANE.reset(token)


def durable_stop_status(status):
    """401/403 remain durable; an admitted bounded GET owns its 429 retry."""
    return status in (401,403) or status==429 and not _BOUNDED_GET_RETRY.get()


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
    """Shared minimum-interval gate with the original rate_coordinator lanes.

    One lock decides, at the moment of issue, whether this request may go: it must be
    its lane's head ticket, no interactive request may be ahead of a background one
    (unless that background request has already yielded for MAX_BACKGROUND_DEFER_SECONDS),
    and the real interval since the previous issue must have elapsed. There are no slot
    reservations, so a late wake-up cannot shrink the real spacing and a cancelled
    request leaves nothing behind. Interactive tickets are served first-come first-served.
    """
    def __init__(self,interval,*,clock=time.monotonic,sleep=time.sleep):
        self.interval=interval;self.clock=clock;self.sleep=sleep
        self._lock=Lock();self._last=None
        self._interactive=[];self._background=[]
    @property
    def _interactive_waiting(self):
        with self._lock:return len(self._interactive)
    @staticmethod
    def _head(queue):
        """First ticket that is not parked in a (possibly blocking) pause/settings call."""
        for item in queue:
            if not item[3]:return item
        return None
    def _my_turn(self,ticket,interactive,now):
        if interactive:
            if self._head(self._interactive) is not ticket:return False
            head=self._head(self._background)
            # a background head that already yielded the full cap goes before more interactive ones
            return head is None or head[2] is None or now-head[2]<MAX_BACKGROUND_DEFER_SECONDS
        if self._head(self._background) is not ticket:return False
        if ticket[2] is None:ticket[2]=now          # the yield starts when it becomes the head, not while queued behind others
        return self._head(self._interactive) is None or now-ticket[2]>=MAX_BACKGROUND_DEFER_SECONDS
    def __call__(self,deadline):
        request_checkpoint();_check_deadline(deadline,self.clock)
        interactive=_LANE.get()=='interactive'
        ticket=[object(),self.clock(),None,False]       # token, queued at, yield start, parked
        queue=self._interactive if interactive else self._background
        with self._lock:queue.append(ticket)
        try:
            while True:
                ticket[3]=True                           # parked: a pause/settings call may block outside the lock
                try:
                    request_checkpoint();_check_deadline(deadline,self.clock)
                    seconds=float(self.interval())
                    if seconds not in (0.3,0.4,0.5,0.6,1.0,1.5):raise ValueError('invalid request interval')
                    request_checkpoint();_check_deadline(deadline,self.clock)   # cancelled during the settings read: issue nothing
                finally:ticket[3]=False
                with self._lock:
                    now=self.clock()                     # taken after every blocking call
                    if self._my_turn(ticket,interactive,now):
                        remaining=0 if self._last is None else self._last+seconds-now
                        if remaining<=0:
                            self._last=now
                            return
                        if now+remaining>=deadline:
                            from .pixiv_read_client import ReadDeadlineExceeded
                            raise ReadDeadlineExceeded()
                        pause=min(remaining,0.1)
                    else:pause=0.05
                self.sleep(pause)
        finally:
            with self._lock:queue.remove(ticket)


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
        self._status_lock=RLock();self._serial=0;self._records={};self._rate_stopped=False

    def progress_snapshot(self):
        with self._status_lock:
            return tuple({**value,'remaining_seconds':max(0,math.ceil(value.get('until',0)-self.clock()))}
                         for value in self._records.values())

    def _publish(self,token,phase,attempt,limit,*,status=None,delay=0,stop_cause=None):
        with self._status_lock:
            self._records[token]=dict(phase=phase,attempt=attempt,limit=limit,status=status,
                                      wait_seconds=delay,until=self.clock()+delay)
            if stop_cause is not None:
                self._records[token]['stop_cause']=stop_cause
            while len(self._records)>32:self._records.pop(next(iter(self._records)))

    def _settings(self):
        value=self.settings();count=value.retry_limit;wait=value.rate_limit_wait_seconds
        if type(count) is not int or count not in (1,3,5,10):raise ValueError('invalid retry count')
        if type(wait) not in (int,float) or wait not in (3.0,5.0,10.0,30.0):raise ValueError('invalid retry wait')
        return count,float(wait)
    def execute(self,operation,*,deadline):
        token=_BOUNDED_GET_RETRY.set(True)
        try:return self._execute(operation,deadline=deadline)
        finally:_BOUNDED_GET_RETRY.reset(token)

    def _execute(self,operation,*,deadline):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed
        if not callable(operation):raise TypeError('operation must be callable')
        request_checkpoint();_check_deadline(deadline,self.clock)
        count,rate_wait=self._settings() if _SCOPE.get().allow_retry else (1,0)
        with self._status_lock:
            if self._rate_stopped:raise HTTPStatusRejected(429)
            self._records={k:v for k,v in self._records.items() if v['phase'] in ('requesting','waiting')}
            self._serial+=1;token=self._serial
        try:return self._attempts(operation,deadline,token,count,rate_wait)
        except BaseException:
            with self._status_lock:
                value=self._records.get(token)
                if value and value['phase'] in ('requesting','waiting'):
                    self._publish(token,'cancelled',value['attempt'],count,status=value['status'])
            raise

    def _attempts(self,operation,deadline,token,count,rate_wait):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed
        for attempt in range(count):
            request_checkpoint();_check_deadline(deadline,self.clock)
            with self._status_lock:
                if self._rate_stopped:
                    self._publish(token,'stopped',attempt+1,count,status=429,stop_cause='shared_limit')
                    raise HTTPStatusRejected(429)
            self._publish(token,'requesting',attempt+1,count)
            try:
                result=operation()
                with self._status_lock:self._records.pop(token,None)
                return result
            except (HTTPStatusRejected,TransportFailed) as error:
                if isinstance(error,HTTPStatusRejected):
                    status=error.status_code
                    if status not in (408,425,429) and status<500:
                        self._publish(token,'failed',attempt+1,count,status=status);raise
                else:status=None
                if attempt+1>=count:
                    if status==429:
                        with self._status_lock:self._rate_stopped=True
                    self._publish(token,'stopped',attempt+1,count,status=status,
                                  stop_cause='own_limit' if status==429 else None);raise
                request_checkpoint();_check_deadline(deadline,self.clock)
                if status==429:delay=rate_wait
                else:
                    noise=self.jitter(0,1)
                    if type(noise) not in (int,float) or not math.isfinite(noise) or not 0<=noise<=1:raise ValueError('invalid retry jitter')
                    delay=2**attempt+noise
                self._publish(token,'waiting',attempt+1,count,status=status,delay=delay)
                try:_wait(delay,deadline,clock=self.clock,sleep=self.sleep)
                except BaseException:
                    self._publish(token,'cancelled',attempt+1,count,status=status);raise
            except BaseException:
                self._publish(token,'cancelled',attempt+1,count);raise
