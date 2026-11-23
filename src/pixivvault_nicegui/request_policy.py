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

from .request_notice_gates import GateNoticeState, GateObservationSnapshot, check_request_notice_gates


@dataclass(frozen=True)
class _Scope:
    callbacks: tuple=()
    allow_retry: bool=True

_SCOPE=ContextVar('pixivvault_request_scope',default=_Scope())
_BOUNDED_GET_RETRY=ContextVar('pixivvault_bounded_get_retry',default=False)
_FRESH_GET=ContextVar('pixivvault_fresh_get',default=False)
_SEND_POLICY = ContextVar('pixivvault_send_policy', default=None)
_LANE=ContextVar('pixivvault_request_lane',default='background')
# Original rate_coordinator.py MAX_BACKGROUND_DEFER_SECONDS: background yields to a
# waiting interactive request, but never starves for longer than this.
MAX_BACKGROUND_DEFER_SECONDS=2.0
NOTICE_LIFETIME_SECONDS = 10 * 60
_GATE_NOTICE_CAUSES = {'gate_busy', 'gate_unverified', 'durable_gate'}
_PINNED_NOTICE_CAUSES = {'durable_gate', 'gate_unverified'}
_RATE_STOP_CAUSES = {'own_limit', 'shared_limit', 'cleanup_429', 'shared_cleanup_429'}


def is_pinned_notice(record):
    return record.get('stop_cause') in _PINNED_NOTICE_CAUSES


def is_rate_stop_notice(record):
    return (record['phase'] == 'stopped' and record['status'] == 429
            and (record.get('stop_cause') in _RATE_STOP_CAUSES or record.get('stop_cause') is None))


def _gate_notice_key(record):
    problem = record.get('gate_problem')
    return (record.get('stop_cause'), record['status'], problem,
            record.get('gate_owners', ()) if problem == 'storage' else ())


def _reported_owners(error):
    authority = getattr(error, 'authority', None)
    if authority == 'both':
        return ('local', 'shared')
    return (authority,) if authority in ('local', 'shared') else ()


def reconcile_gate_notices(records, snapshot, now, serial, *, watched_request=None, selection_generation=None):
    """Project observations, preserving each pin until its cause clears twice."""
    pending = {key: record for key, record in records.items()
               if record.get('stop_cause') in _GATE_NOTICE_CAUSES}
    if not pending and watched_request is None:
        return records, serial

    states = snapshot.states
    stopped = {}
    for state in states:
        if state.status is not None:
            stopped.setdefault(state.status, set()).add(state.authority)

    def covered_states(owners):
        observed = [state for state in states if not owners or state.authority in owners
                    or state.authority == 'both']
        covered = {state.authority for state in observed}
        return observed if set(owners) <= covered or 'both' in covered else ()

    def verified(owners, *, marker_clear=False):
        observed = covered_states(owners)
        return bool(observed) and all(state.state in ('open', 'unselected', 'stopped')
                                      and not state.cleanup_unverified
                                      and (not marker_clear or state.inflight is False) for state in observed)

    result = records.copy()
    for key, record in pending.items():
        cause = record['stop_cause']
        owners = record.get('gate_owners', ())
        if cause == 'gate_busy':
            if (snapshot.consistent and covered_states(owners)
                    and all(state.state in ('open', 'unselected', 'stopped', 'unverified', 'uncertain') for state in states)):
                del result[key]
            elif now >= record['until'] + NOTICE_LIFETIME_SECONDS:
                result[key] = {**record, 'until': now}
            continue

        if cause == 'durable_gate':
            evidence = stopped.get(record['status'], set())
            known = tuple(sorted(set(owners) | (evidence - {'both'})))
            pending_owners = None
            if evidence and 'both' not in evidence and snapshot.consistent:
                remaining = tuple(owner for owner in known if owner in evidence or not verified((owner,)))
                if remaining != known:
                    if record.get('pending_gate_owners') == remaining:
                        known = remaining
                    else:
                        pending_owners = remaining
            record = {**record, 'pending_gate_owners': pending_owners}
            if known != owners:
                record['gate_owners'] = known
            resolved = not evidence and verified(owners)
        else:
            if record.get('gate_problem') == 'uncertain':
                owners = ('shared',)
            expected = record.get('expected_selection')
            selection_verified = (
                expected is None or record.get('selection_generation') is not selection_generation
                or any(state.selection_proof == expected for state in covered_states(owners))
            )
            resolved = (not record.get('cleanup_unrecheckable')
                        and verified(owners, marker_clear=True) and selection_verified)

        streak = record.get('clear_streak', 0) + 1 if snapshot.consistent and resolved else 0
        if streak >= 2:
            del result[key]
        else:
            result[key] = {**record, 'clear_streak': streak}

    pinned = {_gate_notice_key(record) for record in result.values() if is_pinned_notice(record)}
    observed = [('durable_gate', status, None, tuple(sorted(owners - {'both'})))
                for status, owners in sorted(stopped.items())]
    for authority in sorted({state.authority for state in states
                             if state.state == 'unverified' or state.cleanup_unverified}):
        observed.append(('gate_unverified', None, 'storage',
                         () if authority == 'both' else (authority,)))
    if any(state.authority in ('shared', 'both')
           and (state.state == 'uncertain' or (state.state == 'stopped' and state.inflight))
           for state in states):
        observed.append(('gate_unverified', None, 'uncertain', ('shared',)))

    origin = next(iter(pending.values()), None)
    attempt, limit = (origin['attempt'], origin['limit']) if origin is not None else watched_request
    for cause, status, problem, owners in observed:
        record = dict(phase='stopped', attempt=attempt, limit=limit, status=status,
                      wait_seconds=0, until=now, stop_cause=cause, gate_owners=owners)
        if problem is not None:
            record['gate_problem'] = problem
        if _gate_notice_key(record) not in pinned:
            serial += 1
            result[serial] = record
    return result, serial



def interactive(function):
    """Wrap a callable so its requests are admitted in the interactive lane."""
    @wraps(function)
    def run(*args,**kwargs):
        with interactive_lane():return function(*args,**kwargs)
    return run


@contextmanager
def lane_scope(lane):
    """Run requests inside the named lane. None keeps the enclosing lane; an explicit lane overrides it."""
    if lane is None:
        yield
        return
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


def request_send_checkpoint():
    """Recheck the active GET's shared stop before any request bytes are sent."""
    policy = _SEND_POLICY.get()
    if policy is not None:
        policy._require_rate_open()


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
    """Shared minimum-interval gate: the original rate_coordinator.py algorithm.

    The next issue slot is reserved at call time under a short lock and the wait happens
    outside it, so a request that arrived earlier owns an earlier slot and a later
    background request cannot overtake it. An interactive request reserves immediately;
    a background request first yields while an interactive one is still waiting for its
    slot, for at most MAX_BACKGROUND_DEFER_SECONDS counted from its own call, so it is
    never starved. Pause/cancel checkpoints and the deadline are honoured outside the lock.
    A request that fails before issuing gives its slot back when nothing was queued behind it.
    """
    def __init__(self,interval,*,clock=time.monotonic,sleep=time.sleep,jitter=random.uniform):
        self.interval=interval;self.clock=clock;self.sleep=sleep;self.jitter=jitter
        self._lock=Lock();self._next=None;self._issued=None;self._spacing=0;self._interactive_waiting=0
    def _defer_for_interactive(self,deadline,start):
        while True:
            with self._lock:
                if self._interactive_waiting==0:return
            if self.clock()-start>=MAX_BACKGROUND_DEFER_SECONDS:return
            request_checkpoint();_check_deadline(deadline,self.clock)
            request_send_checkpoint()
            self.sleep(0.05)
    def _reserve(self,deadline,called=None,interactive=True):
        seconds=float(self.interval())
        if seconds not in (0.3,0.4,0.5,0.6,1.0,1.5):raise ValueError('invalid request interval')
        request_checkpoint();_check_deadline(deadline,self.clock)       # cancelled/expired during the settings read: reserve nothing
        request_send_checkpoint()
        with self._lock:
            now=self.clock()
            if (not interactive and self._interactive_waiting and now-called<MAX_BACKGROUND_DEFER_SECONDS):
                return None                                            # an interactive request queued meanwhile: keep yielding (original order)
            start=now if self._next is None else max(now,self._next)
            if start>=deadline:
                from .pixiv_read_client import ReadDeadlineExceeded
                raise ReadDeadlineExceeded()                           # the slot cannot be used before the deadline: do not take it
            spacing=self.jitter(seconds*0.8,seconds*1.2)                # original: wait interval +-20% random (Z05)
            if not math.isfinite(spacing) or not seconds*0.8<=spacing<=seconds*1.2:raise ValueError('invalid interval jitter')
            gap=self._spacing                                          # the spacing the previous request assigned: the real distance it must keep from that issue
            self._spacing=spacing
            self._next=start+spacing
            return start,start+spacing,gap
    def _release(self,start,end):
        with self._lock:
            if self._next==end:self._next=start
    def __call__(self,deadline):
        called=self.clock()
        request_checkpoint();_check_deadline(deadline,self.clock)
        interactive=_LANE.get()=='interactive'
        if interactive:
            with self._lock:self._interactive_waiting+=1
        try:
            while True:
                if not interactive:self._defer_for_interactive(deadline,called)
                reserved=self._reserve(deadline,called,interactive)
                if reserved is not None:break
            start,end,seconds=reserved
            try:
                while True:
                    request_checkpoint();_check_deadline(deadline,self.clock)
                    request_send_checkpoint()
                    with self._lock:
                        now=self.clock()
                        # the reserved slot AND the real spacing since the previous actual issue (e.g. after a long pause)
                        left=max(start-now,0 if self._issued is None else self._issued+seconds-now)
                        if left<=0:
                            self._issued=now
                            return
                    self.sleep(min(left,0.1))
            except BaseException:
                self._release(start,end);raise
        finally:
            if interactive:
                with self._lock:self._interactive_waiting-=1


def _recover_on_fresh_connection(operation, deadline, *, clock=time.monotonic):
    """Re-enter the complete read once, with fresh admission and connection."""
    request_checkpoint()
    _check_deadline(deadline, clock)
    request_send_checkpoint()
    fresh = _FRESH_GET.set(True)
    # The recovery is a single GET. A 429 now goes to the durable gate.
    bounded = _BOUNDED_GET_RETRY.set(False)
    try:
        return operation()
    finally:
        _BOUNDED_GET_RETRY.reset(bounded)
        _FRESH_GET.reset(fresh)


def execute_single_get(operation, *, deadline):
    from .pixiv_read_client import _PooledConnectionLost
    try:
        return operation()
    except _PooledConnectionLost as error:
        if not _SCOPE.get().allow_retry or getattr(error, 'gate_cleanup_errors', ()):
            raise
        return _recover_on_fresh_connection(operation, deadline)


class RequestRetryPolicy:
    """Read configured values once per GET; share no retry counters across calls.

    settings() returns RequestPreferences (retry_limit, rate_limit_wait_seconds).
    Transient transport failures, 408/425, 429 and 5xx use the configured
    budget. A disconnected pooled GET without response bytes can spend one
    remaining attempt on a fresh connection, with no further retries. Identity,
    auth 401/403, 404, redirects, invalid payload/destination/limits, operation
    deadlines and checkpoint cancellation escape immediately. A 404 is raised
    to the caller without a shared progress record. The last original
    typed failure escapes when the configured total HTTP tries are exhausted.
    """
    def __init__(self,settings,*,clock=time.monotonic,sleep=time.sleep,jitter=random.uniform,check_gate=None):
        if not all(callable(x) for x in (settings,clock,sleep,jitter)):raise TypeError('retry inputs must be callable')
        self.settings=settings;self.clock=clock;self.sleep=sleep;self.jitter=jitter
        self._status_lock=RLock();self._serial=0;self._records={};self._rate_stopped=False
        self._rate_stopped_by_cleanup = False
        # Optional local observation of the existing owners. Never used to admit a GET.
        self._check_gate = check_gate
        self._gate_watch_context = None
        self._gate_notice_generation = 0
        self._selection_binding = None
        self._selection_generation = None
        self._recheck_running = False
        self._recheck_again = False

    def bind_selection_reader(self, binding):
        """Track the active gate and selection shared by installed saved readers."""
        with self._status_lock:
            if binding != self._selection_binding:
                gates_changed = self._selection_binding is None or binding[:2] != self._selection_binding[:2]
                self._selection_binding = binding
                self._selection_generation = object()
                self._gate_notice_generation += 1
                for key, record in self._records.items():
                    if gates_changed and record.get('stop_cause') in _GATE_NOTICE_CAUSES:
                        self._records[key] = {**record, 'clear_streak': 0, 'pending_gate_owners': None}
                    elif record.get('expected_selection') is not None:
                        self._records[key] = {**record, 'clear_streak': 0}
            return self._selection_generation

    def _is_transient_notice(self, record):
        cause = record.get('stop_cause')
        return (record['phase'] in ('cancelled', 'stopped')
                and not is_pinned_notice(record)
                and not is_rate_stop_notice(record)
                and cause not in _GATE_NOTICE_CAUSES)

    def progress_snapshot(self):
        with self._status_lock:
            now = self.clock()
            self._records = {
                key: record for key, record in self._records.items()
                if not self._is_transient_notice(record) or now < record['until'] + NOTICE_LIFETIME_SECONDS
            }
            return tuple({**{field: value for field, value in record.items()
                             if field not in ('clear_streak', 'pending_gate_owners', 'expected_selection',
                                              'selection_generation', 'cleanup_unrecheckable')},
                          'notice_id': key,
                          'remaining_seconds': max(0, math.ceil(record['until'] - now))}
                         for key, record in self._records.items())

    def has_gate_notices(self):
        if self._check_gate is None:
            return False
        with self._status_lock:
            return any(record.get('stop_cause') in _GATE_NOTICE_CAUSES for record in self._records.values())

    def should_watch_gate(self):
        """Keep an activated watch running through the lifetime of its view."""
        with self._status_lock:
            return self._gate_watch_context is not None

    def recheck_gate_notices(self):
        with self._status_lock:
            if self._gate_watch_context is None:
                return
            if self._recheck_running:
                self._recheck_again = True
                return
            self._recheck_running = True
        try:
            # One follow-up is enough to coalesce a parallel request or
            # retry a fenced result. Further work can use the next UI poll.
            for _ in range(2):
                with self._status_lock:
                    generation = self._gate_notice_generation
                    self._recheck_again = False
                snapshot = self._observe_gate_notices()
                with self._status_lock:
                    current = generation == self._gate_notice_generation
                    if current:
                        self._records, self._serial = reconcile_gate_notices(
                            self._records, snapshot, self.clock(), self._serial,
                            watched_request=self._gate_watch_context,
                            selection_generation=self._selection_generation)
                    if current and not self._recheck_again:
                        return
        finally:
            with self._status_lock:
                self._recheck_running = self._recheck_again = False

    def _observe_gate_notices(self):
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain

        try:
            observed = self._check_gate()
            if isinstance(observed, GateObservationSnapshot):
                return observed
            states = ((GateNoticeState('both', 'open'),) if observed is None
                      else tuple(observed))
        except ManagedNetworkStopped as error:
            states = (GateNoticeState(getattr(error, 'authority', None) or 'both',
                                      'stopped', error.status_code, inflight=None),)
        except ManagedNetworkGateBusy:
            states = (GateNoticeState('both', 'busy'),)
        except SharedGetOutcomeUncertain:
            states = (GateNoticeState('shared', 'uncertain'),)
        except (ManagedNetworkGateStorageError, OSError) as error:
            states = (GateNoticeState(getattr(error, 'authority', None) or 'both',
                                      'unverified', inflight=None),)
        return GateObservationSnapshot(states or (GateNoticeState('both', 'unverified'),))


    def _finish_success(self, token):
        with self._status_lock:
            self._records = {key: record for key, record in self._records.items()
                             if key != token and not self._is_transient_notice(record)}

    def _publish(self,token,phase,attempt,limit,*,status=None,delay=0,stop_cause=None,
                 gate_problem=None,gate_owners=(),expected_selection=None,selection_generation=None,
                 cleanup_unrecheckable=False):
        with self._status_lock:
            record = dict(phase=phase, attempt=attempt, limit=limit, status=status,
                          wait_seconds=delay, until=self.clock()+delay)
            if stop_cause is not None:
                record['stop_cause'] = stop_cause
            if gate_problem is not None:
                record['gate_problem'] = gate_problem
            if gate_owners:
                record['gate_owners'] = gate_owners
            if cleanup_unrecheckable:
                record['cleanup_unrecheckable'] = True
            if expected_selection is not None and selection_generation is self._selection_generation:
                record['expected_selection'] = expected_selection
                record['selection_generation'] = selection_generation
            if stop_cause in _GATE_NOTICE_CAUSES:
                if self._check_gate is not None:
                    self._gate_watch_context = (attempt, limit)
                self._gate_notice_generation += 1
                for key, previous in tuple(self._records.items()):
                    if _gate_notice_key(previous) == _gate_notice_key(record):
                        if (record.get('expected_selection') is None
                                and previous.get('expected_selection') is not None
                                and previous.get('selection_generation') is self._selection_generation):
                            record['expected_selection'] = previous['expected_selection']
                            record['selection_generation'] = self._selection_generation
                        if previous.get('cleanup_unrecheckable'):
                            record['cleanup_unrecheckable'] = True
                        if stop_cause == 'durable_gate' and previous.get('gate_owners'):
                            record['gate_owners'] = tuple(sorted(set(record.get('gate_owners', ()))
                                                                 | set(previous['gate_owners'])))
                        del self._records[key]
                    elif previous.get('clear_streak') or previous.get('pending_gate_owners'):
                        self._records[key] = {**previous, 'clear_streak': 0, 'pending_gate_owners': None}
            self._records[token] = record
            if phase == 'cancelled' and self._is_transient_notice(self._records[token]):
                cancelled = [key for key, record in self._records.items()
                             if record['phase'] == 'cancelled' and self._is_transient_notice(record)]
                if len(cancelled) > 32:
                    del self._records[cancelled[0]]

    def _publish_cleanup_notice(self, failure, attempt, limit):
        self._serial += 1
        self._publish(self._serial, 'stopped', attempt, limit, stop_cause='gate_unverified',
                      gate_problem='storage', gate_owners=_reported_owners(failure),
                      cleanup_unrecheckable=getattr(failure, 'cleanup_unrecheckable', False))

    def report_preflight_cleanup(self, failure):
        """A failed lifetime check can occur before a retry record exists."""
        with self._status_lock:
            self._publish_cleanup_notice(failure, 1, 1)

    def _require_rate_open(self):
        from .pixiv_read_client import _PeerRateStopped
        with self._status_lock:
            if self._rate_stopped:
                raise _PeerRateStopped()

    def _settings(self):
        value=self.settings();count=value.retry_limit;wait=value.rate_limit_wait_seconds
        if type(count) is not int or count not in (1,3,5,10):raise ValueError('invalid retry count')
        if type(wait) not in (int,float) or wait not in (3.0,5.0,10.0,30.0):raise ValueError('invalid retry wait')
        return count,float(wait)
    def execute(self,operation,*,deadline,selection_generation=None):
        token=_BOUNDED_GET_RETRY.set(True)
        try:return self._execute(operation,deadline=deadline,selection_generation=selection_generation)
        finally:_BOUNDED_GET_RETRY.reset(token)

    def _execute(self,operation,*,deadline,selection_generation):
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped, _carry_gate_cleanup,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain
        from .pixiv_read_client import HTTPStatusRejected, _PeerRateStopped
        if not callable(operation):raise TypeError('operation must be callable')
        request_checkpoint();_check_deadline(deadline,self.clock)
        count,rate_wait=self._settings() if _SCOPE.get().allow_retry else (1,0)
        with self._status_lock:
            if self._rate_stopped:raise HTTPStatusRejected(429)
            self._records={k:v for k,v in self._records.items() if v['phase'] != 'failed'}
            self._serial+=1;token=self._serial
        active_policy = _SEND_POLICY.set(self)
        try:
            return self._attempts(operation, deadline, token, count, rate_wait)
        except BaseException as error:
            with self._status_lock:
                value = self._records.get(token)
                attempt = value['attempt'] if value else 1
                if isinstance(error, _PeerRateStopped):
                    self._publish_peer_rate_stop(token, attempt, count)
                elif value and value['phase'] in ('requesting','waiting'):
                    phase, status, cause = 'cancelled', value['status'], None
                    problem = None
                    if isinstance(error, ManagedNetworkStopped):
                        phase, status, cause = 'stopped', error.status_code, 'durable_gate'
                    elif isinstance(error, ManagedNetworkGateBusy):
                        cause = 'gate_busy'
                    elif isinstance(error, ManagedNetworkGateStorageError):
                        phase, status, cause = 'stopped', None, 'gate_unverified'
                        problem = 'uncertain' if isinstance(error, SharedGetOutcomeUncertain) else 'storage'
                    owners = ()
                    if cause in _GATE_NOTICE_CAUSES:
                        owners = ('shared',) if problem == 'uncertain' else _reported_owners(error)
                    self._publish(token, phase, value['attempt'], count, status=status,
                                  stop_cause=cause, gate_problem=problem, gate_owners=owners,
                                  expected_selection=getattr(error, 'expected_selection', None),
                                  selection_generation=selection_generation,
                                  cleanup_unrecheckable=getattr(error, 'cleanup_unrecheckable', False))
                for failure in getattr(error, 'gate_cleanup_errors', ()):
                    self._publish_cleanup_notice(failure, attempt, count)
            if isinstance(error, _PeerRateStopped):
                raise _carry_gate_cleanup(error, HTTPStatusRejected(429)) from None
            raise
        finally:
            _SEND_POLICY.reset(active_policy)

    def _stop_on_rate_limit(self, token, attempt, count):
        early = attempt < count
        with self._status_lock:
            if not self._rate_stopped:
                self._rate_stopped_by_cleanup = early
            self._rate_stopped = True
            self._publish(token, 'stopped', attempt, count, status=429,
                          stop_cause='cleanup_429' if early else 'own_limit')

    def _publish_peer_rate_stop(self, token, attempt, count):
        self._publish(token, 'stopped', attempt, count, status=429,
                      stop_cause='shared_cleanup_429' if self._rate_stopped_by_cleanup else 'shared_limit')

    def _attempts(self,operation,deadline,token,count,rate_wait):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed,_PooledConnectionLost
        for attempt in range(count):
            request_checkpoint();_check_deadline(deadline,self.clock)
            with self._status_lock:
                if self._rate_stopped:
                    self._publish_peer_rate_stop(token, attempt+1, count)
                    raise HTTPStatusRejected(429)
            self._publish(token,'requesting',attempt+1,count)
            try:
                result=operation()
                self._finish_success(token)
                return result
            except _PooledConnectionLost as error:
                if attempt+1>=count or getattr(error, 'gate_cleanup_errors', ()):
                    self._publish(token,'failed',attempt+1,count)
                    raise
                with self._status_lock:
                    if self._rate_stopped:
                        self._publish_peer_rate_stop(token, attempt+2, count)
                        raise HTTPStatusRejected(429)
                    self._publish(token,'requesting',attempt+2,count)
                try:
                    result=_recover_on_fresh_connection(operation,deadline,clock=self.clock)
                except HTTPStatusRejected as error:
                    if error.status_code == 429 and getattr(error, 'gate_cleanup_errors', ()):
                        self._stop_on_rate_limit(token, attempt+2, count)
                    elif error.status_code == 404:
                        with self._status_lock:self._records.pop(token,None)
                    else:
                        self._publish(token,'failed',attempt+2,count,status=error.status_code)
                    raise
                except TransportFailed:
                    self._publish(token,'failed',attempt+2,count)
                    raise
                self._finish_success(token)
                return result
            except (HTTPStatusRejected,TransportFailed) as error:
                if isinstance(error,HTTPStatusRejected):
                    status=error.status_code
                    if status not in (408,425,429) and status<500:
                        if status == 404:
                            with self._status_lock:
                                self._records.pop(token, None)
                        else:
                            self._publish(token,'failed',attempt+1,count,status=status)
                        raise
                else:
                    if not error.retryable:
                        self._publish(token,'failed',attempt+1,count)
                        raise
                    status=None
                if attempt+1>=count or getattr(error, 'gate_cleanup_errors', ()):
                    if status == 429:
                        self._stop_on_rate_limit(token, attempt+1, count)
                    else:
                        self._publish(token, 'stopped', attempt+1, count, status=status)
                    raise
                request_checkpoint();_check_deadline(deadline,self.clock)
                if status==429:delay=rate_wait
                else:
                    noise=self.jitter(0,1)
                    if type(noise) not in (int,float) or not math.isfinite(noise) or not 0<=noise<=1:raise ValueError('invalid retry jitter')
                    delay=2**attempt+noise
                self._publish(token,'waiting',attempt+1,count,status=status,delay=delay)
                _wait(delay,deadline,clock=self.clock,sleep=self.sleep)
