"""Original request pacing and bounded GET retries, scoped to one operation.

api_retry_count is the total number of HTTP tries (original range(count)), not
additional JobService attempts. Phase10 can suppress retries in a local scope.
No URL, response, Cookie, or exception text is persisted by this policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import math
import random
from threading import Lock, RLock
import time
from weakref import WeakValueDictionary

from .request_notice_gates import GateNoticeState, GateObservationSnapshot, check_request_notice_gates


@dataclass(frozen=True)
class _Scope:
    callbacks: tuple=()
    allow_retry: bool=True

_SCOPE=ContextVar('pixivvault_request_scope',default=_Scope())
_BOUNDED_GET_RETRY=ContextVar('pixivvault_bounded_get_retry',default=False)
_FRESH_GET=ContextVar('pixivvault_fresh_get',default=False)
_SEND_ATTEMPT = ContextVar('pixivvault_send_attempt', default=None)
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


def _gate_authorities(gates):
    return frozenset(authority for authority, gate in zip(('local', 'shared'), gates) if gate is not None)


def _gate_files(gates):
    return tuple((gate._database_identity, gate._lock_identity) if gate is not None else None for gate in gates)


def _confirmed_route(record, proof, *, unknown_owners=('local', 'shared')):
    gates, checks_selection = proof
    if record.get('requires_selected_reader') and not checks_selection:
        return False
    origin = record.get('notice_gates')
    if origin is not None:
        # A request-origin failure needs its entire real route. A healthy
        # shared-only CDN cannot authorize a failing dual-owner reader.
        return all(source is None or source == target for source, target in zip(origin, _gate_files(gates)))
    owners = frozenset(record.get('gate_owners') or unknown_owners)
    return owners <= _gate_authorities(gates)


def reconcile_gate_notices(records, snapshot, now, serial, *, watched_request=None,
                           selection_generations=frozenset((None,)), recovered_sql=frozenset(),
                           require_admission=False):
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
                expected is None or record.get('selection_generation') not in selection_generations
                or any(state.selection_proof == expected for state in covered_states(owners))
            )
            sql_verified = all(failure in recovered_sql for failure in record.get('acquisition_sql_failures', ()))
            resolved = (not record.get('cleanup_unrecheckable') and sql_verified
                        and verified(owners, marker_clear=True) and selection_verified)
        resolved = resolved and not record.get('admission_required')

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
        if require_admission:
            record['admission_required'] = True
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


@dataclass(frozen=True, slots=True)
class _SendAttempt:
    policy: RequestRetryPolicy
    attempt: int
    limit: int
    generation: int


def request_send_checkpoint():
    """Authorize this attempt against the current stop generation."""
    active = _SEND_ATTEMPT.get()
    if active is not None:
        active.policy._require_rate_open(active.generation)


def record_request_rate_stop(status, *, cleanup_failed=False):
    """Latch a terminal 429 at the protected response or cleanup boundary."""
    active = _SEND_ATTEMPT.get()
    # The single fresh recovery already commits a durable STOP. Keep that
    # path owned by its gate so peers continue to see its durable authority.
    if (status == 429 and active is not None and _BOUNDED_GET_RETRY.get()
            and (active.attempt >= active.limit or cleanup_failed)):
        active.policy._latch_rate_stop(by_cleanup=cleanup_failed and active.attempt < active.limit)


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
    def __init__(self,settings,*,clock=time.monotonic,sleep=time.sleep,jitter=random.uniform,
                 check_gate=None, confirm_reader=None):
        if not all(callable(x) for x in (settings,clock,sleep,jitter)):raise TypeError('retry inputs must be callable')
        self.settings=settings;self.clock=clock;self.sleep=sleep;self.jitter=jitter
        self._status_lock=RLock();self._serial=0;self._records={};self._rate_stopped=False
        self._rate_stopped_by_cleanup = False
        self._rate_stop_generation = 0
        # Optional local observation of the existing owners. Never used to admit a GET.
        self._check_gate = check_gate
        self._gate_watch_context = None
        self._gate_notice_generation = 0
        self._selection_bindings = {}
        self._selection_generation = None
        self._notice_readers = WeakValueDictionary()
        self._confirm_reader = confirm_reader
        self._recheck_running = False
        self._recheck_again = False

    def _active_selection_generations(self):
        return frozenset(generation for _, generation in self._selection_bindings.values()) | {None}

    def bind_selection_reader(self, binding, reader=None):
        """Keep independent local, shared, and dual-owner reader bindings."""
        owners = _gate_authorities(binding[:2])
        with self._status_lock:
            previous, generation = self._selection_bindings.get(owners, (None, None))
            # An unreadable/absent optional Draft proof does not supersede a
            # saved selection already registered on these physical gates.
            if previous is not None and binding[:2] == previous[:2] and binding[2] is None:
                binding = previous
            if binding != previous:
                gates_changed = previous is None or binding[:2] != previous[:2]
                generation = object()
                self._selection_bindings[owners] = binding, generation
                self._gate_notice_generation += 1
                for key, record in self._records.items():
                    if gates_changed and record.get('stop_cause') in _GATE_NOTICE_CAUSES:
                        self._records[key] = {**record, 'clear_streak': 0, 'pending_gate_owners': None}
                    elif record.get('expected_selection') is not None:
                        self._records[key] = {**record, 'clear_streak': 0}
            if reader is not None:
                self._register_notice_reader(binding[:2], reader)
            self._selection_generation = generation
            return generation

    def _register_notice_reader(self, gates, reader):
        checks_selection = getattr(reader._transport, '_checks_notice_selection', False)
        route = _gate_files(gates), checks_selection
        if self._notice_readers.get(route) is not reader:
            self._notice_readers[route] = reader
            self._gate_notice_generation += 1

    def can_confirm_gate_notices(self):
        with self._status_lock:
            return bool(self._confirm_reader or self._notice_readers) and any(
                is_pinned_notice(record) for record in self._records.values())

    def confirm_gate_notices(self):
        """Clear eligible notices only after the current acquisition check passes."""
        from .pixiv_read_client import PixivReadError

        with self._status_lock:
            pending = {key for key, record in self._records.items() if is_pinned_notice(record)}
            if not pending or self._rate_stopped:
                return False
            generation = self._gate_notice_generation
            readers = tuple(self._notice_readers.values())
        checks = ([self._confirm_reader] if self._confirm_reader is not None else
                  [reader._confirm_gate_admission for reader in readers if reader._retry_policy is self])
        confirmed = []
        failed = False
        for check in checks:
            try:
                gates = check()
            except (PixivReadError, OSError):
                failed = True
                continue
            if gates is not None:
                confirmed.append(gates)
        with self._status_lock:
            if self._rate_stopped or generation != self._gate_notice_generation:
                return False
            covered = frozenset().union(*(_gate_authorities(gates) for gates, _ in confirmed))
            cleared = [key for key, record in self._records.items()
                       if key in pending and (record.get('notice_gates') is not None or not failed)
                       and not record.get('cleanup_unrecheckable')
                       and all(failure.recovered() for failure in record.get('acquisition_sql_failures', ()))
                       and any(_confirmed_route(record, proof, unknown_owners=covered) for proof in confirmed)]
            for key in cleared:
                del self._records[key]
            if cleared:
                self._gate_notice_generation += 1
            return bool(cleared)

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
                                              'selection_generation', 'cleanup_unrecheckable', 'acquisition_sql_failures',
                                              'notice_gates', 'admission_required', 'requires_selected_reader')},
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
                        if any(state.status is not None or state.state in ('busy', 'uncertain')
                               or state.inflight is True or state.admission_refused for state in snapshot.states):
                            # A later definite refusal can reuse an existing
                            # notice ID. Fence an earlier admission proof even
                            # when the observer has nothing new to display.
                            self._gate_notice_generation += 1
                        recovered_sql = frozenset(
                            failure for record in self._records.values()
                            for failure in record.get('acquisition_sql_failures', ()) if failure.recovered()
                        )
                        self._records, self._serial = reconcile_gate_notices(
                            self._records, snapshot, self.clock(), self._serial,
                            watched_request=self._gate_watch_context,
                            selection_generations=self._active_selection_generations(), recovered_sql=recovered_sql,
                            require_admission=bool(self._selection_bindings or self._confirm_reader))
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


    def _finish_success(self, token, notice_gates, checks_selection, gate_generation):
        with self._status_lock:
            self._records = {key: record for key, record in self._records.items()
                             if key != token and not self._is_transient_notice(record)}
            if notice_gates is not None and gate_generation == self._gate_notice_generation:
                for key, record in self._records.items():
                    if (key < token and record.get('admission_required')
                            and _confirmed_route(record, (notice_gates, checks_selection))):
                        self._records[key] = {**record, 'admission_required': False, 'clear_streak': 0}

    def _publish(self,token,phase,attempt,limit,*,status=None,delay=0,stop_cause=None,
                 gate_problem=None,gate_owners=(),expected_selection=None,selection_generation=None,
                 cleanup_unrecheckable=False, acquisition_sql_failure=None, notice_gates=None,
                 checks_selection=False):
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
            if expected_selection is not None:
                record['requires_selected_reader'] = True
                if selection_generation in self._active_selection_generations():
                    record['expected_selection'] = expected_selection
                    record['selection_generation'] = selection_generation
            if acquisition_sql_failure is not None:
                record['acquisition_sql_failures'] = (acquisition_sql_failure,)
            if notice_gates is not None and is_pinned_notice(record):
                record['notice_gates'] = _gate_files(notice_gates)
                if checks_selection:
                    record['requires_selected_reader'] = True
                if acquisition_sql_failure is None:
                    record['admission_required'] = True
            if stop_cause in _GATE_NOTICE_CAUSES:
                if self._check_gate is not None:
                    self._gate_watch_context = (attempt, limit)
                self._gate_notice_generation += 1
                for key, previous in tuple(self._records.items()):
                    previous_route, route = previous.get('notice_gates'), record.get('notice_gates')
                    same_route = previous_route is None or route is None or previous_route == route
                    if same_route and _gate_notice_key(previous) == _gate_notice_key(record):
                        if (record.get('expected_selection') is None
                                and previous.get('expected_selection') is not None
                                and previous.get('selection_generation') in self._active_selection_generations()):
                            record['expected_selection'] = previous['expected_selection']
                            record['selection_generation'] = previous['selection_generation']
                        if previous.get('admission_required'):
                            record['admission_required'] = True
                        if previous.get('requires_selected_reader'):
                            record['requires_selected_reader'] = True
                        if 'notice_gates' not in record and 'notice_gates' in previous:
                            record['notice_gates'] = previous['notice_gates']
                        if previous.get('cleanup_unrecheckable'):
                            record['cleanup_unrecheckable'] = True
                        if previous.get('acquisition_sql_failures'):
                            proofs = {failure.proof: failure for failure in previous['acquisition_sql_failures']
                                      if not failure.recovered()}
                            for failure in record.get('acquisition_sql_failures', ()):
                                older = proofs.get(failure.proof)
                                if older is None or failure.generation > older.generation:
                                    proofs[failure.proof] = failure
                            record['acquisition_sql_failures'] = tuple(proofs.values())
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

    def _publish_cleanup_notice(self, failure, attempt, limit, *, notice_gates=None, checks_selection=False):
        self._serial += 1
        self._publish(self._serial, 'stopped', attempt, limit, stop_cause='gate_unverified',
                      gate_problem='storage', gate_owners=_reported_owners(failure),
                      cleanup_unrecheckable=getattr(failure, 'cleanup_unrecheckable', False),
                      acquisition_sql_failure=getattr(failure, 'acquisition_sql_failure', None),
                      notice_gates=notice_gates, checks_selection=checks_selection)

    def report_preflight_cleanup(self, failure, *, notice_gates=None, checks_selection=False):
        """A failed lifetime check can occur before a retry record exists."""
        with self._status_lock:
            self._publish_cleanup_notice(failure, 1, 1, notice_gates=notice_gates, checks_selection=checks_selection)

    def _require_rate_open(self, generation):
        from .pixiv_read_client import _PeerRateStopped
        with self._status_lock:
            if self._rate_stopped or generation != self._rate_stop_generation:
                raise _PeerRateStopped()

    def _latch_rate_stop(self, *, by_cleanup):
        with self._status_lock:
            if not self._rate_stopped:
                self._rate_stopped_by_cleanup = by_cleanup
                self._rate_stop_generation += 1
                self._rate_stopped = True

    def _call_attempt(self, operation, *, attempt, limit, generation):
        token = _SEND_ATTEMPT.set(_SendAttempt(self, attempt, limit, generation))
        try:
            return operation()
        finally:
            _SEND_ATTEMPT.reset(token)

    def _settings(self):
        value=self.settings();count=value.retry_limit;wait=value.rate_limit_wait_seconds
        if type(count) is not int or count not in (1,3,5,10):raise ValueError('invalid retry count')
        if type(wait) not in (int,float) or wait not in (3.0,5.0,10.0,30.0):raise ValueError('invalid retry wait')
        return count,float(wait)
    def execute(self,operation,*,deadline,selection_generation=None,notice_gates=None,checks_selection=False):
        token=_BOUNDED_GET_RETRY.set(True)
        try:return self._execute(operation,deadline=deadline,selection_generation=selection_generation,
                                 notice_gates=notice_gates,checks_selection=checks_selection)
        finally:_BOUNDED_GET_RETRY.reset(token)

    def _execute(self,operation,*,deadline,selection_generation,notice_gates,checks_selection):
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
            gate_generation = self._gate_notice_generation
        try:
            return self._attempts(operation, deadline, token, count, rate_wait,
                                  notice_gates, checks_selection, gate_generation)
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
                                  cleanup_unrecheckable=getattr(error, 'cleanup_unrecheckable', False),
                                  acquisition_sql_failure=getattr(error, 'acquisition_sql_failure', None),
                                  notice_gates=notice_gates, checks_selection=checks_selection)
                for failure in getattr(error, 'gate_cleanup_errors', ()):
                    self._publish_cleanup_notice(failure, attempt, count,
                                                 notice_gates=notice_gates, checks_selection=checks_selection)
            if isinstance(error, _PeerRateStopped):
                raise _carry_gate_cleanup(error, HTTPStatusRejected(429)) from None
            raise

    def _stop_on_rate_limit(self, token, attempt, count):
        early = attempt < count
        with self._status_lock:
            self._latch_rate_stop(by_cleanup=early)
            self._publish(token, 'stopped', attempt, count, status=429,
                          stop_cause='cleanup_429' if early else 'own_limit')

    def _publish_peer_rate_stop(self, token, attempt, count):
        self._publish(token, 'stopped', attempt, count, status=429,
                      stop_cause='shared_cleanup_429' if self._rate_stopped_by_cleanup else 'shared_limit')

    def _attempts(self, operation, deadline, token, count, rate_wait, notice_gates, checks_selection, gate_generation):
        from .pixiv_read_client import HTTPStatusRejected,TransportFailed,_PooledConnectionLost
        for attempt in range(count):
            request_checkpoint();_check_deadline(deadline,self.clock)
            with self._status_lock:
                if self._rate_stopped:
                    self._publish_peer_rate_stop(token, attempt+1, count)
                    raise HTTPStatusRejected(429)
                generation = self._rate_stop_generation
                self._publish(token,'requesting',attempt+1,count)
            try:
                result = self._call_attempt(operation, attempt=attempt+1, limit=count, generation=generation)
                self._finish_success(token, notice_gates, checks_selection, gate_generation)
                return result
            except _PooledConnectionLost as error:
                if attempt+1>=count or getattr(error, 'gate_cleanup_errors', ()):
                    self._publish(token,'failed',attempt+1,count)
                    raise
                with self._status_lock:
                    if self._rate_stopped:
                        self._publish_peer_rate_stop(token, attempt+2, count)
                        raise HTTPStatusRejected(429)
                    generation = self._rate_stop_generation
                    self._publish(token,'requesting',attempt+2,count)
                try:
                    result = self._call_attempt(
                        lambda: _recover_on_fresh_connection(operation, deadline, clock=self.clock),
                        attempt=attempt+2, limit=count, generation=generation,
                    )
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
                self._finish_success(token, notice_gates, checks_selection, gate_generation)
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
