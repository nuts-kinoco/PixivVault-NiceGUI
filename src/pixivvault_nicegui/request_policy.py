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
from weakref import WeakKeyDictionary

from .gate_refusal_state import (
    gate_file as _gate_file, gate_observation, gate_refusal_route,
    gate_request_route, record_gate_refusal, refusal_changed, refusal_snapshot,
)
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
_RATE_STOP_CAUSES = {'own_limit', 'shared_limit', 'cleanup_429', 'shared_cleanup_429'}


def is_pinned_notice(record):
    return record.get('stop_cause') in _GATE_NOTICE_CAUSES


def is_rate_stop_notice(record):
    return (record['phase'] == 'stopped' and record['status'] == 429
            and (record.get('stop_cause') in _RATE_STOP_CAUSES or record.get('stop_cause') is None))


def _gate_notice_key(record):
    cause, problem = record.get('stop_cause'), record.get('gate_problem')
    owners = record.get('gate_owners', ()) if problem == 'storage' or cause == 'gate_busy' else ()
    return cause, record['status'], problem, owners


def _reported_owners(error):
    authority = getattr(error, 'authority', None)
    if authority == 'both':
        return ('local', 'shared')
    return (authority,) if authority in ('local', 'shared') else ()


def _gate_files(gates):
    return tuple(map(_gate_file, gates))


def _route_owners(route):
    return {owner for owner, path in zip(('local', 'shared'), route[0]) if path is not None}


def _confirmed_route(record, route):
    origin = record.get('notice_route')
    owners = _route_owners(route)
    if origin is not None:
        return origin == route and set(record.get('gate_owners', ())) <= owners
    observed = record.get('observed_files', ())
    return (bool(owners) and set(record.get('gate_owners', ()) or ('local', 'shared')) <= owners
            and all(route[0][slot] == files for slot, files in observed))


def reconcile_gate_notices(records, snapshot, now, serial, *, watched_request=None):
    """Add newly observed stops without removing an existing notice."""
    origin = next((record for record in records.values() if is_pinned_notice(record)), None)
    if origin is None and watched_request is None:
        return records, serial

    observations = []

    def observed(state, cause, status=None, problem=None, owners=None):
        if owners is None:
            owners = ('local', 'shared') if state.authority == 'both' else (state.authority,)
        files = frozenset()
        if state.owner_gate is not None:
            slot = 0 if state.authority == 'local' else 1
            files = frozenset(((slot, _gate_file(state.owner_gate)),))
        observations.append((cause, status, problem, owners, files))

    for state in snapshot.states:
        if state.status is not None:
            observed(state, 'durable_gate', state.status)
        if state.state == 'unverified' or state.cleanup_unverified:
            observed(state, 'gate_unverified', problem='storage', owners=() if state.authority == 'both' else None)
        if state.authority in ('shared', 'both') and (
                state.state == 'uncertain' or (state.state == 'stopped' and state.inflight)):
            observed(state, 'gate_unverified', problem='uncertain', owners=('shared',))
    # Lock activity and inconsistent samples can invalidate an in-flight
    # confirmation, but only a refused acquisition publishes a busy notice.
    if not observations:
        return records, serial

    result = records.copy()
    attempt, limit = (origin['attempt'], origin['limit']) if origin is not None else watched_request
    for cause, status, problem, owners, observed_files in observations:
        record = dict(phase='stopped', attempt=attempt, limit=limit, status=status,
                      wait_seconds=0, until=now, stop_cause=cause, gate_owners=owners)
        if problem is not None:
            record['gate_problem'] = problem
        if observed_files:
            record['observed_files'] = observed_files
        matches = [(key, previous) for key, previous in result.items()
                   if _gate_notice_key(previous) == _gate_notice_key(record)
                   and (cause == 'durable_gate' or previous.get('gate_owners', ()) == owners)
                   and (previous.get('notice_route') is None
                        or (set(owners) <= _route_owners(previous['notice_route'])
                            and all(previous['notice_route'][0][slot] == files
                                    for slot, files in observed_files))
                        or (not observed_files and problem == 'storage'
                            and previous.get('gate_owners') == owners
                            and not set(owners) & _route_owners(previous['notice_route'])))
                   and not any(slot == old_slot and files != old_files
                               for slot, files in observed_files
                               for old_slot, old_files in previous.get('observed_files', ()))]
        if not matches:
            serial += 1
            result[serial] = record
        else:
            for key, previous in matches:
                if cause == 'durable_gate':
                    combined = tuple(sorted(set(previous.get('gate_owners', ())) | set(owners)))
                    if combined != previous.get('gate_owners'):
                        result[key] = {**previous, 'gate_owners': combined}
                if previous.get('notice_route') is None and observed_files:
                    files = previous.get('observed_files', frozenset()) | observed_files
                    if files != previous.get('observed_files'):
                        result[key] = {**result[key], 'observed_files': files}
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


@contextmanager
def request_gate_preflight(policy, gates, *, checks_selection=False):
    """Record the same admission denial before a reader has been registered."""
    from .managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkStopped

    with gate_request_route(gates):
        try:
            yield
        except (ManagedNetworkGateStorageError, ManagedNetworkStopped) as error:
            policy.report_preflight_refusal(error, gates, checks_selection)
            raise


def request_send_checkpoint():
    """Authorize this attempt against the current stop generation."""
    active = _SEND_ATTEMPT.get()
    if active is not None:
        active.policy._require_rate_open(active.generation)


def record_request_rate_stop(status, *, cleanup_failed=False):
    """Record confirmed stops at the protected response or cleanup boundary."""
    active = _SEND_ATTEMPT.get()
    terminal_rate = (status == 429 and active is not None and _BOUNDED_GET_RETRY.get()
                     and (active.attempt >= active.limit or cleanup_failed))
    if durable_stop_status(status) or terminal_rate:
        record_gate_refusal(stop=True)
    # The single fresh recovery already commits a durable STOP. Keep that
    # path owned by its gate so peers continue to see its durable authority.
    if terminal_rate:
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
                 check_gate=None, confirm_routes=None):
        if not all(callable(x) for x in (settings,clock,sleep,jitter)):raise TypeError('retry inputs must be callable')
        self.settings=settings;self.clock=clock;self.sleep=sleep;self.jitter=jitter
        self._status_lock=RLock();self._serial=0;self._records={};self._rate_stopped=False
        self._rate_stopped_by_cleanup = False
        self._rate_stop_generation = 0
        # Optional local observation of the existing owners. Never used to admit a GET.
        self._check_gate = check_gate
        self._gate_watch_context = None
        self._observation_denials = 0
        self._route_revision = 0
        self._observation_revision = 0
        self._notice_readers = WeakKeyDictionary()
        self._confirm_routes = confirm_routes
        self._recheck_running = False
        self._recheck_again = False

    def _replace_notice_route(self, old, new, *, completed=False):
        old_owners, new_owners = _route_owners(old), _route_owners(new)
        if old != new and (old_owners == new_owners or completed and old_owners < new_owners):
            for key, record in self._records.items():
                if record.get('notice_route') == old:
                    self._records[key] = {**record, 'notice_route': new}
                observed = record.get('observed_files')
                if observed and all(old[0][slot] == files for slot, files in observed):
                    files = frozenset((slot, new[0][slot]) for slot, _ in observed)
                    self._records[key] = {**self._records[key], 'observed_files': files}

    def forget_recovered_401(self, gates):
        """Retire only the 401 notices tied to these successfully opened gates."""
        files = _gate_files(gates)
        observed = frozenset((slot, file) for slot, file in enumerate(files) if file is not None)
        with self._status_lock:
            self._observation_revision += 1
            for key, record in tuple(self._records.items()):
                if record.get('stop_cause') != 'durable_gate' or record.get('status') != 401:
                    continue
                route = record.get('notice_route')
                recorded = (frozenset((slot, file) for slot, file in enumerate(route[0]) if file is not None)
                            if route is not None else record.get('observed_files', frozenset()))
                if recorded and recorded <= observed:
                    del self._records[key]

    def replace_notice_gates(self, old_gates, new_gates):
        """An owner explicitly installed a new gate for the same route."""
        with self._status_lock:
            for selected in (False, True):
                self._replace_notice_route((_gate_files(old_gates), selected),
                                           (_gate_files(new_gates), selected), completed=True)
            self._route_revision += 1
            self._observation_revision += 1

    def register_notice_reader(self, reader, *, replaces=None):
        gates = getattr(reader._transport, '_notice_gates', None)
        with self._status_lock:
            if replaces is not None and (replaces._retry_policy is not self or not replaces._closed):
                raise ValueError('the replaced notice reader must belong to this policy and be closed')
            old = (self._notice_readers.get(reader) if replaces is None else
                   getattr(replaces, '_registered_notice_route', None))
            if gates is None or not any(gates):
                reader._registered_notice_route = None
                self._notice_readers.pop(reader, None)
            else:
                route = _gate_files(gates), reader._transport._checks_notice_selection
                if (old is not None and old[1] == route[1]
                        and all(other is reader or other_route != old
                                for other, other_route in tuple(self._notice_readers.items()))):
                    self._replace_notice_route(old, route)
                if replaces is not None:
                    self._notice_readers.pop(replaces, None)
                reader._registered_notice_route = route
                self._notice_readers[reader] = route
                if self._check_gate is not None and self._gate_watch_context is None:
                    self._gate_watch_context = (1, 1)
            self._route_revision += 1
            self._observation_revision += 1

    def unregister_notice_reader(self, reader):
        with self._status_lock:
            if self._notice_readers.pop(reader, None) is not None:
                self._route_revision += 1

    def _confirmation_plan(self):
        if self._rate_stopped:
            return {}
        checks = {}
        # The Native profile supplies current routes even for per-run readers
        # already closed by the time the user opens the notice.
        if self._confirm_routes is not None:
            for gates, selected, check in self._confirm_routes():
                checks.setdefault((_gate_files(gates), selected), []).append(check)
        else:
            for reader, route in tuple(self._notice_readers.items()):
                if not reader._closed and reader._retry_policy is self:
                    checks.setdefault(route, []).append(reader._confirm_gate_admission)
        plan = {}
        for key, record in self._records.items():
            if not is_pinned_notice(record) or record.get('cleanup_unrecheckable'):
                continue
            # Native exposes two admission paths for the same gates. A
            # route-less observation has no saved credential requirement.
            gate_only = self._confirm_routes is not None and record.get('notice_route') is None
            plan[key] = tuple((route, check) for route, options in checks.items()
                              if (not gate_only or not route[1]) and _confirmed_route(record, route)
                              for check in options)
        return plan

    def can_confirm_gate_notices(self):
        with self._status_lock:
            return any(self._confirmation_plan().values())

    def confirm_gate_notices(self):
        """Admit without sending, then commit against physical refusal generations."""
        from .pixiv_read_client import PixivReadError

        with self._status_lock:
            plan = self._confirmation_plan()
            observations = self._observation_denials
            revision = self._route_revision
            checks = dict.fromkeys(check for options in plan.values() for check in options)
            start = refusal_snapshot()
            physical_gates = {files for route, _ in checks for files in route[0] if files is not None}

        passed, failures = set(), []
        for evidence in checks:
            route, check = evidence
            with gate_refusal_route(route[0]):
                try:
                    result = check()
                except (PixivReadError, OSError) as error:
                    failures.append((route, error))
                    continue
            if result is not None and (_gate_files(result[0]), result[1]) == route:
                passed.add(evidence)

        with self._status_lock:
            for route, error in failures:
                self._remember_confirmation_failure(route, error, plan)
            if (not checks or len(passed) != len(checks)
                    or self._rate_stopped or observations != self._observation_denials
                    or revision != self._route_revision):
                return False
            current = self._confirmation_plan()
            cleared = [key for key, options in plan.items()
                       if options and all(any(candidate == route for candidate, _ in current.get(key, ()))
                                          for route, _ in options)]
            if not cleared:
                return False
            preserved = self._records.copy()
            for key in cleared:
                del self._records[key]
            # Readers of the notice map also hold this lock. The final check
            # validates the tentative deletion before any reader can see it.
            commit = refusal_snapshot()
            if any(refusal_changed(files, start, commit) for files in physical_gates):
                self._records = preserved
                return False
            return True

    def _remember_confirmation_failure(self, route, error, plan):
        from .managed_network_stop_gate import ManagedNetworkGateStorageError, ManagedNetworkStopped

        for failure in (error, *getattr(error, 'gate_cleanup_errors', ())):
            if getattr(failure, 'cleanup_unrecheckable', False):
                for key, options in plan.items():
                    if (key in self._records and _confirmed_route(self._records[key], route)
                            and any(candidate == route for candidate, _ in options)):
                        self._records[key] = {**self._records[key], 'cleanup_unrecheckable': True}
            if getattr(failure, 'gate_cleanup_failure', False):
                self._publish_cleanup_notice(failure, 1, 1, notice_route=route)
            elif isinstance(failure, (ManagedNetworkGateStorageError, ManagedNetworkStopped)):
                self._serial += 1
                self._publish_gate_failure(self._serial, failure, 1, 1, notice_route=route)
            elif isinstance(failure, OSError):
                self._publish_cleanup_notice(failure, 1, 1, notice_route=route)

    def _is_transient_notice(self, record):
        return (record['phase'] in ('cancelled', 'stopped')
                and not is_pinned_notice(record)
                and not is_rate_stop_notice(record))

    def progress_snapshot(self):
        with self._status_lock:
            now = self.clock()
            self._records = {
                key: record for key, record in self._records.items()
                if not self._is_transient_notice(record) or now < record['until'] + NOTICE_LIFETIME_SECONDS
            }
            return tuple({**{field: value for field, value in record.items()
                             if field not in ('cleanup_unrecheckable', 'notice_route', 'observed_files')},
                          'notice_id': key,
                          'remaining_seconds': max(0, math.ceil(record['until'] - now))}
                         for key, record in self._records.items())

    def has_gate_notices(self):
        with self._status_lock:
            return any(is_pinned_notice(record) for record in self._records.values())

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
                    revision = self._observation_revision
                    self._recheck_again = False
                snapshot = self._observe_gate_notices()
                with self._status_lock:
                    current = revision == self._observation_revision
                    if current:
                        if not snapshot.consistent or any(state.status is not None or state.state in ('unverified', 'uncertain', 'busy', 'changed')
                               or state.inflight or state.admission_refused for state in snapshot.states):
                            self._observation_denials += 1
                        self._records, self._serial = reconcile_gate_notices(
                            self._records, snapshot, self.clock(), self._serial,
                            watched_request=self._gate_watch_context)
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
            with gate_observation():
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
                 gate_problem=None,gate_owners=(),cleanup_unrecheckable=False,
                 notice_gates=None,checks_selection=False,notice_route=None):
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
            if is_pinned_notice(record):
                if notice_route is not None:
                    record['notice_route'] = notice_route
                elif notice_gates is not None:
                    record['notice_route'] = _gate_files(notice_gates), checks_selection
                if self._check_gate is not None:
                    self._gate_watch_context = (attempt, limit)
                # Let the active sweep publish its evidence before it samples again.
                if self._recheck_running:
                    self._recheck_again = True
                for key, previous in tuple(self._records.items()):
                    if (_gate_notice_key(previous) != _gate_notice_key(record)
                            or previous.get('notice_route') != record.get('notice_route')):
                        continue
                    if previous.get('cleanup_unrecheckable') != record.get('cleanup_unrecheckable'):
                        continue
                    if stop_cause in ('durable_gate', 'gate_busy'):
                        record['gate_owners'] = tuple(sorted(set(record.get('gate_owners', ()))
                                                             | set(previous.get('gate_owners', ()))))
                    if key != token:
                        del self._records[key]
                    break
            self._records[token] = record
            if phase == 'cancelled' and self._is_transient_notice(record):
                cancelled = [key for key, record in self._records.items()
                             if record['phase'] == 'cancelled' and self._is_transient_notice(record)]
                if len(cancelled) > 32:
                    del self._records[cancelled[0]]

    def _publish_cleanup_notice(self, failure, attempt, limit, *, notice_gates=None,
                                checks_selection=False, notice_route=None):
        self._serial += 1
        self._publish(self._serial, 'stopped', attempt, limit, stop_cause='gate_unverified',
                      gate_problem='storage', gate_owners=_reported_owners(failure),
                      cleanup_unrecheckable=getattr(failure, 'cleanup_unrecheckable', False),
                      notice_gates=notice_gates, checks_selection=checks_selection, notice_route=notice_route)

    def report_preflight_refusal(self, failure, gates, checks_selection):
        with self._status_lock:
            self._serial += 1
            self._publish_gate_failure(self._serial, failure, 1, 1,
                                       notice_gates=gates, checks_selection=checks_selection)
            for cleanup in getattr(failure, 'gate_cleanup_errors', ()):
                self._publish_cleanup_notice(cleanup, 1, 1, notice_gates=gates,
                                             checks_selection=checks_selection)

    def _publish_gate_failure(self, token, failure, attempt, limit, *, notice_gates=None,
                              checks_selection=False, notice_route=None):
        from .managed_network_stop_gate import (
            ManagedNetworkGateBusy, ManagedNetworkGateStorageError, ManagedNetworkStopped,
        )
        from .managed_network_stop_shared_scope import SharedGetOutcomeUncertain

        owners, status, problem = _reported_owners(failure), None, None
        if isinstance(failure, ManagedNetworkStopped):
            phase, status, cause = 'stopped', failure.status_code, 'durable_gate'
        elif isinstance(failure, ManagedNetworkGateBusy):
            phase, cause = 'cancelled', 'gate_busy'
        elif isinstance(failure, ManagedNetworkGateStorageError):
            phase, cause = 'stopped', 'gate_unverified'
            if isinstance(failure, SharedGetOutcomeUncertain):
                problem, owners = 'uncertain', ('shared',)
            else:
                problem = 'storage'
        else:
            return False
        self._publish(token, phase, attempt, limit, status=status, stop_cause=cause,
                      gate_problem=problem, gate_owners=owners,
                      cleanup_unrecheckable=getattr(failure, 'cleanup_unrecheckable', False),
                      notice_gates=notice_gates, checks_selection=checks_selection, notice_route=notice_route)
        return True

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
    def execute(self,operation,*,deadline,notice_gates=None,checks_selection=False):
        token=_BOUNDED_GET_RETRY.set(True)
        try:
            with gate_request_route(notice_gates):
                return self._execute(operation, deadline=deadline, notice_gates=notice_gates,
                                     checks_selection=checks_selection)
        finally:
            _BOUNDED_GET_RETRY.reset(token)

    def _execute(self,operation,*,deadline,notice_gates,checks_selection):
        from .managed_network_stop_gate import _carry_gate_cleanup
        from .pixiv_read_client import HTTPStatusRejected, _PeerRateStopped
        if not callable(operation):raise TypeError('operation must be callable')
        request_checkpoint();_check_deadline(deadline,self.clock)
        count,rate_wait=self._settings() if _SCOPE.get().allow_retry else (1,0)
        with self._status_lock:
            if self._rate_stopped:raise HTTPStatusRejected(429)
            self._records={k:v for k,v in self._records.items() if v['phase'] != 'failed'}
            self._serial+=1;token=self._serial
        try:
            return self._attempts(operation, deadline, token, count, rate_wait)
        except BaseException as error:
            with self._status_lock:
                value = self._records.get(token)
                attempt = value['attempt'] if value else 1
                if isinstance(error, _PeerRateStopped):
                    self._publish_peer_rate_stop(token, attempt, count)
                elif value and value['phase'] in ('requesting','waiting'):
                    if not self._publish_gate_failure(token, error, attempt, count,
                                                      notice_gates=notice_gates, checks_selection=checks_selection):
                        self._publish(token, 'cancelled', attempt, count, status=value['status'])
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

    def _attempts(self, operation, deadline, token, count, rate_wait):
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
