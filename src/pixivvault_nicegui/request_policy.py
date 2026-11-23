"""Original request pacing and bounded GET retries, scoped to one operation.

api_retry_count is the total number of HTTP tries (original range(count)), not
additional JobService attempts. Phase10 can suppress retries in a local scope.
No URL, response, Cookie, or exception text is persisted by this policy.
"""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import math
from pathlib import Path
import random
import sqlite3
from threading import Lock, RLock
import time


@dataclass(frozen=True)
class _Scope:
    callbacks: tuple=()
    allow_retry: bool=True

_SCOPE=ContextVar('pixivvault_request_scope',default=_Scope())
_BOUNDED_GET_RETRY=ContextVar('pixivvault_bounded_get_retry',default=False)
_FRESH_GET=ContextVar('pixivvault_fresh_get',default=False)
_LANE=ContextVar('pixivvault_request_lane',default='background')
# Original rate_coordinator.py MAX_BACKGROUND_DEFER_SECONDS: background yields to a
# waiting interactive request, but never starves for longer than this.
MAX_BACKGROUND_DEFER_SECONDS=2.0
NOTICE_LIFETIME_SECONDS = 10 * 60
_GATE_NOTICE_CAUSES = {'gate_busy', 'gate_unverified', 'durable_gate'}
_PINNED_NOTICE_CAUSES = {'durable_gate', 'gate_unverified'}
_RATE_STOP_CAUSES = {'own_limit', 'shared_limit'}
# The sealed database has one schema page and one table page, at most 64 KiB each.
_MAX_NOTICE_GATE_BYTES = 2 * 65536


def is_pinned_notice(record):
    return record.get('stop_cause') in _PINNED_NOTICE_CAUSES


def is_rate_stop_notice(record):
    return (record['phase'] == 'stopped' and record['status'] == 429
            and (record.get('stop_cause') in _RATE_STOP_CAUSES or record.get('stop_cause') is None))


def _gate_notice_key(record):
    problem = record.get('gate_problem')
    return (record.get('stop_cause'), record['status'], problem,
            record.get('gate_owners', ()) if problem == 'storage' else ())


@dataclass(frozen=True)
class GateNoticeState:
    authority: str
    state: str
    status: int | None = None
    inflight: bool | None = False


def check_request_notice_gates(*scopes):
    """Observe each bound authority, without taking a dispatch lock."""
    from .managed_network_stop_gate import (
        ManagedNetworkGateStorageError, _GATE_SCHEMA, _file_identity, _reject_links,
    )
    from .managed_network_stop_history_readonly import (
        _DB_NAME, _SIDECARS, _Unverified, _check_marker, _fingerprint, _read_gate_immutable,
    )
    from .managed_network_stop_shared_scope import _file_identity_directory
    from .native_profile import PROFILE_DB

    owners = tuple(scope for scope in scopes if scope is not None)
    shared_owners = tuple(getattr(scope, 'shared_owner', None) for scope in owners)
    owners = tuple(dict.fromkeys(scope for scope in (*owners, *shared_owners) if scope is not None))
    failures = (ManagedNetworkGateStorageError, _Unverified, OSError, ValueError, sqlite3.Error)
    observations = []

    def exists(path):
        _reject_links(path)
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    def observe(authority, path, lock, db_id, lock_id, verify_owner, *, gate=None):
        marker = getattr(gate, 'inflight_path', None)

        def fingerprint():
            verify_owner()
            if any(exists(path.with_name(path.name + suffix)) for suffix in _SIDECARS):
                raise _Unverified()
            if _file_identity(path) != db_id or path.stat().st_size > _MAX_NOTICE_GATE_BYTES:
                raise _Unverified()
            state = _fingerprint(path)
            if state[0] != db_id or _file_identity(lock) != lock_id:
                raise _Unverified()
            # Windows denies reading the byte while another handle holds its exclusive lock.
            with lock.open('rb') as stream:
                try:
                    token = stream.read(2)
                except PermissionError:
                    token = None
            if token not in (None, b'0'):
                raise _Unverified()
            return state, marker is not None and exists(marker), token

        try:
            before = fingerprint()
            blocked, status, _ = _read_gate_immutable(path)
            if gate is not None and gate._observed_stop_status not in (None, status):
                raise _Unverified()
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
                objects = conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master LIMIT 2').fetchall()
                if objects != [('table', 'gate', 'gate', _GATE_SCHEMA)]:
                    raise _Unverified()
            _, inflight, token = before
            state = ('busy' if token is None else 'stopped' if blocked
                     else 'uncertain' if inflight else 'open')
            return GateNoticeState(authority, state, status, inflight), lambda: fingerprint() == before
        except failures:
            return GateNoticeState(authority, 'unverified'), None

    def observe_owner(scope):
        gate = scope.gate
        shared = hasattr(gate, 'inflight_path') or scope in shared_owners
        authority = 'shared' if shared else 'local'

        def verify_owner():
            if hasattr(gate, 'inflight_path'):
                scope._check_owner()
            if hasattr(scope, 'profile_identity'):
                _check_marker(scope.root, 'native')
                if _file_identity(scope.root / PROFILE_DB) != scope.profile_identity:
                    raise _Unverified()

        yield observe(authority, gate.path, gate.lock_path,
                      gate._database_identity, gate._lock_identity, verify_owner, gate=gate)
        for name, (kind, root_id, db_id, lock_id) in getattr(scope, '_startup_histories', ()):
            root = Path(name)

            def verify_history(root=root, kind=kind, root_id=root_id):
                verify_owner()
                _check_marker(root, kind)
                if _file_identity_directory(root) != root_id:
                    raise _Unverified()

            yield observe(authority, root / _DB_NAME, root / (_DB_NAME + '.lock'),
                          db_id, lock_id, verify_history)

    measured = [read for scope in owners for read in observe_owner(scope)]
    # Revalidate after reading both sides so an earlier open read cannot clear
    # a notice when the other side changed during the same poll.
    for state, unchanged in measured:
        try:
            if unchanged is not None and not unchanged():
                raise _Unverified()
        except failures:
            state = GateNoticeState(state.authority, 'unverified')
        observations.append(state)
    return tuple(observations) or (GateNoticeState('both', 'unverified'),)


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
            self.sleep(0.05)
    def _reserve(self,deadline,called=None,interactive=True):
        seconds=float(self.interval())
        if seconds not in (0.3,0.4,0.5,0.6,1.0,1.5):raise ValueError('invalid request interval')
        request_checkpoint();_check_deadline(deadline,self.clock)       # cancelled/expired during the settings read: reserve nothing
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
    except _PooledConnectionLost:
        if not _SCOPE.get().allow_retry:
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
        # Optional local observation of the existing owners. Never used to admit a GET.
        self._check_gate = check_gate
        self._gate_notice_generation = 0
        self._gate_checks_in_progress = 0
        self._pending_gate_result = None
        self._earlier_gate_states = set()

    def _is_transient_notice(self, record):
        cause = record.get('stop_cause')
        return (record['phase'] in ('cancelled', 'stopped')
                and not is_pinned_notice(record)
                and not is_rate_stop_notice(record)
                and (self._check_gate is None or cause not in _GATE_NOTICE_CAUSES))

    def progress_snapshot(self):
        with self._status_lock:
            now = self.clock()
            self._records = {
                key: record for key, record in self._records.items()
                if not self._is_transient_notice(record) or now < record['until'] + NOTICE_LIFETIME_SECONDS
            }
            return tuple({**record, 'notice_id': key,
                          'remaining_seconds': max(0, math.ceil(record['until'] - now))}
                         for key, record in self._records.items())

    def has_gate_notices(self):
        if self._check_gate is None:
            return False
        with self._status_lock:
            return any(record.get('stop_cause') in _GATE_NOTICE_CAUSES for record in self._records.values())

    def recheck_gate_notices(self):
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain
        if self._check_gate is None:
            return
        with self._status_lock:
            pending = {key: record for key, record in self._records.items()
                       if record.get('stop_cause') in _GATE_NOTICE_CAUSES}
            if not pending:
                return
            # A newer observation supersedes this one even if it leaves the notices unchanged.
            self._gate_notice_generation += 1
            generation = self._gate_notice_generation
            self._gate_checks_in_progress += 1
        states = (GateNoticeState('both', 'unverified'),)
        try:
            states = self._check_gate()
            if states is None:
                states = (GateNoticeState('both', 'open'),)
        except ManagedNetworkStopped as error:
            states = (GateNoticeState('both', 'stopped', error.status_code, inflight=None),)
        except ManagedNetworkGateBusy:
            states = (GateNoticeState('both', 'busy'),)
        except SharedGetOutcomeUncertain:
            states = (GateNoticeState('shared', 'uncertain'),)
        except (ManagedNetworkGateStorageError, OSError):
            pass
        finally:
            self._complete_gate_check(generation, pending, states)

    def _complete_gate_check(self, generation, pending, states):
        with self._status_lock:
            self._gate_checks_in_progress -= 1
            if generation == self._gate_notice_generation:
                self._pending_gate_result = generation, pending, states
            else:
                # Start order can differ from read order. Retain adverse
                # evidence for its authority, without masking another side.
                self._earlier_gate_states.update(state for state in states if state.state != 'open')
            if self._gate_checks_in_progress:
                return
            result = self._pending_gate_result
            self._pending_gate_result = None
            earlier = self._earlier_gate_states
            self._earlier_gate_states = set()
            if result is None or result[0] != self._gate_notice_generation:
                return
            _, pending, states = result
            self._reconcile_gate_notices(pending, states, earlier)

    def _reconcile_gate_notices(self, pending, states, earlier):
        evidence = (*states, *earlier)
        kinds = {state.state for state in evidence}
        stopped = {}
        for state in states:
            if state.status is not None:
                stopped.setdefault(state.status, set()).add(state.authority)
        unresolved = {'busy', 'uncertain', 'unverified'}

        def verified(owners, *, require_marker_clear=False):
            observed = [state for state in evidence if not owners or state.authority in owners
                        or state.authority == 'both']
            covered = {state.authority for state in observed}
            return (bool(observed) and (set(owners) <= covered or 'both' in covered)
                    and all(state.state not in unresolved
                            and (not require_marker_clear or state.inflight is False) for state in observed))

        for key, record in pending.items():
            if self._records.get(key) is not record:
                continue
            cause = record['stop_cause']
            if cause == 'durable_gate':
                known = set(record.get('gate_owners', ()))
                owners = known | (stopped.get(record['status'], set()) - {'both'})
                if owners != known:
                    self._records[key] = {**record, 'gate_owners': tuple(sorted(owners))}
                resolved = (all(state.status != record['status'] for state in evidence)
                            and verified(owners))
            elif cause == 'gate_unverified':
                owners = ('shared',) if record.get('gate_problem') == 'uncertain' else record.get('gate_owners', ())
                resolved = verified(owners, require_marker_clear=True)
            else:
                if 'busy' in kinds:
                    resolved = (kinds <= {'open', 'busy'}
                                and all(state.status is None for state in evidence)
                                and self.clock() >= record['until'] + NOTICE_LIFETIME_SECONDS)
                else:
                    resolved = not earlier or any(state.state != 'open' for state in states)
            if resolved:
                del self._records[key]

        pinned = {_gate_notice_key(record) for record in self._records.values() if is_pinned_notice(record)}
        observed_stops = [('durable_gate', status, None, tuple(sorted(owners - {'both'})))
                          for status, owners in sorted(stopped.items())]
        for authority in sorted({state.authority for state in states if state.state == 'unverified'}):
            observed_stops.append(('gate_unverified', None, 'storage',
                                   () if authority == 'both' else (authority,)))
        if any(state.state == 'uncertain' and state.authority in ('shared', 'both') for state in states):
            observed_stops.append(('gate_unverified', None, 'uncertain', ()))
        origin = next(iter(pending.values()))
        for cause, status, problem, owners in observed_stops:
            if (cause, status, problem, owners if problem == 'storage' else ()) not in pinned:
                self._serial += 1
                self._publish(self._serial, 'stopped', origin['attempt'], origin['limit'],
                              status=status, stop_cause=cause, gate_problem=problem, gate_owners=owners)

    def _finish_success(self, token):
        with self._status_lock:
            self._records = {key: record for key, record in self._records.items()
                             if key != token and not self._is_transient_notice(record)}

    def _publish(self,token,phase,attempt,limit,*,status=None,delay=0,stop_cause=None,
                 gate_problem=None,gate_owners=()):
        with self._status_lock:
            record = dict(phase=phase, attempt=attempt, limit=limit, status=status,
                          wait_seconds=delay, until=self.clock()+delay)
            if stop_cause is not None:
                record['stop_cause'] = stop_cause
            if gate_problem is not None:
                record['gate_problem'] = gate_problem
            if gate_owners:
                record['gate_owners'] = gate_owners
            if stop_cause in _GATE_NOTICE_CAUSES:
                self._gate_notice_generation += 1
                for key, previous in tuple(self._records.items()):
                    if _gate_notice_key(previous) == _gate_notice_key(record):
                        del self._records[key]
            self._records[token] = record
            if phase == 'cancelled' and self._is_transient_notice(self._records[token]):
                cancelled = [key for key, record in self._records.items()
                             if record['phase'] == 'cancelled' and self._is_transient_notice(record)]
                if len(cancelled) > 32:
                    del self._records[cancelled[0]]

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
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain
        from .pixiv_read_client import HTTPStatusRejected
        if not callable(operation):raise TypeError('operation must be callable')
        request_checkpoint();_check_deadline(deadline,self.clock)
        count,rate_wait=self._settings() if _SCOPE.get().allow_retry else (1,0)
        with self._status_lock:
            if self._rate_stopped:raise HTTPStatusRejected(429)
            self._records={k:v for k,v in self._records.items() if v['phase'] != 'failed'}
            self._serial+=1;token=self._serial
        try:return self._attempts(operation,deadline,token,count,rate_wait)
        except BaseException as error:
            with self._status_lock:
                value=self._records.get(token)
                if value and value['phase'] in ('requesting','waiting'):
                    phase, status, cause = 'cancelled', value['status'], None
                    problem = None
                    if isinstance(error, ManagedNetworkStopped):
                        phase, status, cause = 'stopped', error.status_code, 'durable_gate'
                    elif isinstance(error, ManagedNetworkGateBusy):
                        cause = 'gate_busy'
                    elif isinstance(error, ManagedNetworkGateStorageError):
                        phase, status, cause = 'stopped', None, 'gate_unverified'
                        problem = 'uncertain' if isinstance(error, SharedGetOutcomeUncertain) else 'storage'
                    self._publish(token, phase, value['attempt'], count, status=status,
                                  stop_cause=cause, gate_problem=problem)
            raise

    def _attempts(self,operation,deadline,token,count,rate_wait):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed,_PooledConnectionLost
        for attempt in range(count):
            request_checkpoint();_check_deadline(deadline,self.clock)
            with self._status_lock:
                if self._rate_stopped:
                    self._publish(token,'stopped',attempt+1,count,status=429,stop_cause='shared_limit')
                    raise HTTPStatusRejected(429)
            self._publish(token,'requesting',attempt+1,count)
            try:
                result=operation()
                self._finish_success(token)
                return result
            except _PooledConnectionLost:
                if attempt+1>=count:
                    self._publish(token,'failed',attempt+1,count)
                    raise
                with self._status_lock:
                    if self._rate_stopped:
                        self._publish(token,'stopped',attempt+2,count,status=429,stop_cause='shared_limit')
                        raise HTTPStatusRejected(429)
                    self._publish(token,'requesting',attempt+2,count)
                try:
                    result=_recover_on_fresh_connection(operation,deadline,clock=self.clock)
                except HTTPStatusRejected as error:
                    if error.status_code==404:
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
                _wait(delay,deadline,clock=self.clock,sleep=self.sleep)
