"""App-shared explicit Pixiv actions, with borrowed GET and following owners.

Construction and cached getters do not perform HTTP. A click supplies one
immutable WriteIntent. Unknown outcomes require explicit GET reconciliation;
neither a new operation ID nor a vanished UI authorizes another POST.

Workers own completion, state publication and the optional local single-row
commit. UI cancellation never closes the shared reader. Synchronous close can
drain these futures without waiting for a callback on the UI event loop.

The operation/unknown ledger is in memory for this application lifetime. This
module does not claim durable unknown-operation recovery across process restarts.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import math
from threading import Event, RLock, get_ident
import time

from .bookmark_write_evidence import positive_id
from .pixiv_read_client import (
    LivePixivReadClient, MAX_FOLLOWED_AUTHORS, ReadDeadlineExceeded, read_deadline_scope,
)
from .pixiv_write_client import (
    PixivWriteClient, WriteIntent, WriteResult, WriteBusy, WriteOperationConflict,
    _validate as _validate_write_intent,
)
from .request_policy import request_scope

MAX_ACTION_SECONDS = 120.0
DEFAULT_ACTION_SECONDS = 30.0
DEFAULT_CLOSE_SECONDS = MAX_ACTION_SECONDS + 15.0


class ActionError(RuntimeError):
    code = 'pixiv_action_failed'
    def __init__(self):
        super().__init__(self.code)


class ActionBusy(ActionError):
    code = 'pixiv_action_busy'


class ActionConflict(ActionError):
    code = 'pixiv_action_conflict'


class ActionNeedsReconciliation(ActionError):
    code = 'pixiv_action_requires_reconciliation'


class ActionClosed(ActionError):
    code = 'pixiv_actions_closed'


class ActionCancelled(ActionError):
    code = 'pixiv_action_cancelled_before_dispatch'


class ActionTimedOut(ActionError):
    code = 'pixiv_action_deadline'


class ActionAccountChanged(ActionError):
    code = 'pixiv_action_account_changed'


@dataclass(frozen=True, slots=True, repr=False)
class ActionState:
    pending: bool = False
    unknown: bool = False
    observed: bool | None = None
    revision: int = 0
    operation_id: str | None = None

    def __repr__(self):
        return (f'ActionState(pending={self.pending!r}, unknown={self.unknown!r}, '
                f'observed={self.observed!r}, revision={self.revision!r})')


@dataclass(frozen=True, slots=True, repr=False)
class ActionOutcome:
    intent: WriteIntent
    status: str  # confirmed/already/observed/rejected/indeterminate/cancelled
    remote_state: bool | None
    reason: str
    write_result: WriteResult | None = None
    local_warning: bool = False

    @property
    def confirmed(self):
        return self.status == 'confirmed'

    @property
    def requires_reconciliation(self):
        return self.status == 'indeterminate'

    def matches(self, intent):
        # Include author, privacy, tags and comment, unlike WriteResult.matches.
        return isinstance(intent, WriteIntent) and self.intent == intent

    def __repr__(self):
        return f'ActionOutcome(status={self.status!r}, local_warning={self.local_warning!r})'


@dataclass(frozen=True, slots=True, repr=False)
class _Observation:
    present: bool
    restrict: int | None = None
    name: str = ''


@dataclass(slots=True, repr=False)
class _Control:
    deadline: float
    cancelled: object = None
    cancel_event: Event = field(default_factory=Event)
    dispatched: bool = False
    observation: _Observation | None = None
    write_result: WriteResult | None = None
    dispatch_error: ActionError | None = None
    local_revision: int | None = None
    local_snapshot_available: bool = False


def _identifier(value):
    if type(value) is not str or positive_id(value) != value or value.startswith('0'):
        raise ActionError()
    return value


def _name(value):
    if (type(value) is not str or len(value) > 256 or
            any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ActionError()
    return value


def _key(account_id, kind, target_id):
    if kind not in ('illust', 'novel', 'user'):
        raise ActionError()
    return _identifier(account_id), kind, _identifier(target_id)


def _intent_key(intent):
    kind = 'user' if intent.action in ('follow', 'unfollow') else intent.work_kind
    return _key(intent.account_id, kind, intent.target_id)


def _seconds(value, *, maximum, zero=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or value < 0 or (not zero and value == 0) or value > maximum):
        raise ActionError()
    return float(value)


class PixivActions:
    """One shared coordinator per ApplicationServices; never one per viewer."""

    def __init__(self, reader, *, writer=None, following_store=None):
        if not isinstance(reader, LivePixivReadClient):
            raise ActionError()
        if writer is not None and (not isinstance(writer, PixivWriteClient)
                                   or writer._reader is not reader):
            raise ActionError()
        self._reader = reader
        self._writer = writer if writer is not None else PixivWriteClient(reader)
        self._following = following_store
        self._lock = RLock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='pixiv-actions')
        self._futures = {}
        self._worker_ids = set()
        self._pending = set()
        self._intents = {}
        self._results = {}
        self._reconciled = {}
        self._blocked = {}
        self._writer_reconciled = set()
        self._states = {}
        self._closing = False
        self._shutdown_unverified = False
        # Read a cached scalar only: never acquire the reader's GET lock here.
        cached = getattr(reader, '_verified_identity', None)
        try:
            self._verified_account = _identifier(cached)
        except ActionError:
            self._verified_account = None

    @property
    def cached_verified_account_id(self):
        with self._lock:
            # S03 or another client may invalidate/replace the shared reader's
            # verified account. Read only cached scalars: no HTTP and no reader
            # identity lock on the UI event loop.
            if self._closing or getattr(self._reader, '_closed', False):
                self._verified_account = None
                return None
            try:
                account = _identifier(getattr(self._reader, '_verified_identity', None))
            except ActionError:
                account = None
            self._verified_account = account
            return account

    @property
    def active_count(self):
        with self._lock:
            return sum(not future.done() for future in self._futures)

    @property
    def shutdown_unverified(self):
        with self._lock:
            pending = self._shutdown_unverified
        return pending or bool(getattr(self._writer, 'shutdown_unverified', False))

    def state(self, account_id, kind, target_id):
        key = _key(account_id, kind, target_id)
        with self._lock:
            return self._states.get(key, ActionState())

    def result(self, operation_id):
        """Original outcome; an unknown POST stays unknown after observation."""
        with self._lock:
            return self._results.get(operation_id)

    def reconciliation_result(self, operation_id):
        with self._lock:
            return self._reconciled.get(operation_id)

    def _checkpoint(self, control):
        with self._lock:
            dispatched, closing = control.dispatched, self._closing
        if dispatched:
            # POST has its writer-owned deadline. Always drain its result/local
            # commit, even after UI cancellation or the preflight deadline.
            return
        if closing or control.cancel_event.is_set():
            raise ActionCancelled()
        if control.cancelled is not None and control.cancelled():
            raise ActionCancelled()
        if time.monotonic() >= control.deadline:
            raise ActionTimedOut()

    def _identity(self, control, expected=None):
        self._checkpoint(control)
        account = _identifier(self._reader.require_identity())
        self._checkpoint(control)
        with self._lock:
            if not self._closing:
                self._verified_account = account
        if expected is not None and account != expected:
            raise ActionAccountChanged()
        return account

    def _before_dispatch(self, control, intent):
        # Called by writer outside its lock, after all credential/receipt GETs.
        try:
            self._identity(control, intent.account_id)
            self._checkpoint(control)
            with self._lock:
                if self._closing or control.cancel_event.is_set():
                    raise ActionCancelled()
                if time.monotonic() >= control.deadline:
                    raise ActionTimedOut()
                control.dispatched = True
        except ActionError as error:
            control.dispatch_error = error
            raise
        return None

    def _submit(self, mode, intent, author_name, cancelled, timeout):
        duration = _seconds(timeout, maximum=MAX_ACTION_SECONDS)
        if cancelled is not None and not callable(cancelled):
            raise ActionError()
        author_name = _name(author_name)
        key = None
        if intent is not None:
            _validate_write_intent(intent)
            if (intent.action in ('follow', 'unfollow') and
                    intent.author_id is not None and intent.author_id != intent.target_id):
                raise ActionError()
            key = _intent_key(intent)
        control = _Control(time.monotonic() + duration, cancelled)
        with self._lock:
            if self._closing:
                raise ActionClosed()
            if key is not None:
                prior = self._intents.get(intent.operation_id)
                if prior is not None and prior != intent:
                    raise ActionConflict()
                if key in self._pending:
                    raise ActionBusy()
                if mode == 'execute':
                    if prior is not None:
                        cached = self._results.get(intent.operation_id)
                        if cached is None:
                            raise ActionConflict()
                        ready = Future()
                        ready.set_result(cached)
                        return ready, control
                    if key in self._blocked:
                        raise ActionNeedsReconciliation()
                    self._intents[intent.operation_id] = intent
                else:
                    if prior != intent:
                        raise ActionConflict()
                    if self._blocked.get(key) != intent.operation_id:
                        cached = self._reconciled.get(intent.operation_id)
                        if cached is None:
                            raise ActionConflict()
                        ready = Future()
                        ready.set_result(cached)
                        return ready, control
                self._pending.add(key)
                previous = self._states.get(key, ActionState())
                self._states[key] = ActionState(True, key in self._blocked,
                    previous.observed, previous.revision + 1, intent.operation_id)
            try:
                future = self._pool.submit(self._worker, mode, intent, author_name, control)
            except Exception:
                if key is not None:
                    outcome = ActionOutcome(intent, 'rejected', None, 'worker_unavailable')
                    self._finish(mode, outcome)
                raise ActionClosed() from None
            self._futures[future] = control
            future.add_done_callback(self._retire)
            return future, control

    def _retire(self, future):
        with self._lock:
            self._futures.pop(future, None)

    @staticmethod
    async def _await_owned(future, control):
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError as cancellation:
            control.cancel_event.set()
            # Repeated UI cancellation cannot orphan an admitted operation.
            while not wrapped.done():
                try:
                    await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            try:
                wrapped.result()
            except BaseException:
                pass
            raise cancellation

    async def verify_account(self, *, cancelled=None, timeout=DEFAULT_ACTION_SECONDS):
        return await self._await_owned(*self._submit('verify', None, '', cancelled, timeout))

    async def execute(self, intent, *, author_name='', cancelled=None, timeout=DEFAULT_ACTION_SECONDS):
        return await self._await_owned(*self._submit('execute', intent, author_name, cancelled, timeout))

    async def reconcile(self, intent, *, author_name='', cancelled=None, timeout=DEFAULT_ACTION_SECONDS):
        return await self._await_owned(*self._submit('reconcile', intent, author_name, cancelled, timeout))

    def _wait(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                pending = tuple(f for f in self._futures if not f.done())
            if not pending:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _, unfinished = wait(pending, timeout=remaining)
            if unfinished:
                return False

    async def drain(self, *, timeout=DEFAULT_CLOSE_SECONDS):
        duration = _seconds(timeout, maximum=DEFAULT_CLOSE_SECONDS, zero=True)
        return await asyncio.to_thread(self._wait, duration)

    def close(self, *, timeout=DEFAULT_CLOSE_SECONDS):
        """True only after workers committed/drained and owned writer closed.

        On False, the ApplicationServices owner must retain reader/store until
        a later successful drain. They are borrowed and never closed here.
        """
        duration = _seconds(timeout, maximum=DEFAULT_CLOSE_SECONDS, zero=True)
        with self._lock:
            self._closing = True
            for control in self._futures.values():
                control.cancel_event.set()
            in_worker = get_ident() in self._worker_ids
        complete = False if in_worker else self._wait(duration)
        if not complete:
            # Only writer-owned sockets may be interrupted; never shared GETs.
            try:
                self._writer.close()
            except Exception:
                pass
            with self._lock:
                self._shutdown_unverified = True
            return False
        try:
            self._writer.close()
            self._pool.shutdown(wait=False, cancel_futures=False)
            complete = not bool(getattr(self._writer, 'shutdown_unverified', False))
        except Exception:
            complete = False
        with self._lock:
            self._shutdown_unverified = not complete
        return complete

    def _observe(self, intent, control):
        self._identity(control, intent.account_id)
        kind = _intent_key(intent)[1]
        if kind == 'user':
            observation = _Observation(False)
            for restrict, visibility in enumerate(('show', 'hide')):
                self._checkpoint(control)
                rows = self._reader.get_following_users(intent.account_id, visibility)
                if type(rows) is not tuple or len(rows) > MAX_FOLLOWED_AUTHORS:
                    raise ActionError()
                seen = set()
                for row in rows:
                    self._checkpoint(control)
                    if type(row) is not dict:
                        raise ActionError()
                    uid, name = _identifier(row.get('user_id')), _name(row.get('name'))
                    if uid in seen:
                        raise ActionError()
                    seen.add(uid)
                    if uid == intent.target_id:
                        # The private list wins an overlap, matching S03's store.
                        observation = _Observation(True, restrict, name)
        else:
            body = (self._reader.get_artwork_metadata_raw(intent.target_id, intent.author_id)
                    if kind == 'illust' else
                    self._reader.get_novel_payload(intent.target_id, intent.author_id))
            if (type(body) is not dict or positive_id(body.get('id')) != intent.target_id
                    or positive_id(body.get('userId')) != intent.author_id
                    or 'bookmarkData' not in body):
                raise ActionError()
            bookmark = body['bookmarkData']
            if bookmark is None:
                observation = _Observation(False)
            elif type(bookmark) is dict:
                _identifier(bookmark.get('id'))
                private = bookmark.get('private')
                observation = _Observation(True, int(private) if type(private) is bool else None)
            else:
                raise ActionError()
        self._identity(control, intent.account_id)
        control.observation = observation
        return observation

    def _capture_local_revision(self, intent, control):
        """Worker-only admission snapshot; never read local SQLite on UI loop."""
        if intent.action not in ('follow', 'unfollow') or self._following is None:
            return
        try:
            snapshot = self._following.snapshot(intent.account_id)
            if (snapshot.account_id != intent.account_id or
                    type(snapshot.revision) is not int or snapshot.revision < 0):
                return
            control.local_revision = snapshot.revision
            control.local_snapshot_available = True
        except Exception:
            # Do not turn a failed revision read into an unguarded overwrite.
            return

    def _local_follow(self, intent, observation, author_name, control):
        """Only the selected account/author row; preserve existing local flags."""
        if intent.action not in ('follow', 'unfollow'):
            return False
        if self._following is None:
            # The remote outcome is still authoritative, but no local row
            # can be reflected without the separately owned following store.
            return True
        if not control.local_snapshot_available:
            return True
        try:
            if observation.present:
                if type(observation.restrict) is not int or observation.restrict not in (0, 1):
                    return True
                self._following.record_followed(
                    intent.account_id, intent.target_id,
                    observation.name or author_name, observation.restrict,
                    expected_revision=control.local_revision)
            else:
                self._following.record_unfollowed(intent.account_id, intent.target_id,
                    expected_revision=control.local_revision)
            return False
        except Exception:
            # Remote success/observation survives a failed local transaction.
            return True

    def _execute(self, intent, author_name, control):
        observed = self._observe(intent, control)
        desired = intent.action in ('bookmark_add', 'follow')
        self._checkpoint(control)
        if observed.present is desired:
            warning = self._local_follow(intent, observed, author_name, control)
            return ActionOutcome(intent, 'already', desired, 'desired_state_already_observed',
                                 local_warning=warning)
        key = _intent_key(intent)
        try:
            cached = self._writer.confirmed_state(*key)
            if type(cached) is bool and cached is not observed.present:
                # This removes stale memory only; blocked/opid ledgers survive.
                self._writer.invalidate_confirmed_state(*key)
            self._checkpoint(control)
            result = self._writer.execute(
                intent, before_dispatch=lambda: self._before_dispatch(control, intent))
        except WriteBusy:
            raise ActionBusy() from None
        except WriteOperationConflict:
            raise ActionNeedsReconciliation() from None
        control.write_result = result if isinstance(result, WriteResult) else None
        if control.dispatch_error is not None:
            raise control.dispatch_error
        if not control.dispatched:
            # The GET reader may have noticed cancellation before writer reached
            # before_dispatch and returned preflight_not_sent instead.
            self._checkpoint(control)
        if (not isinstance(result, WriteResult) or not result.matches(intent)
                or result.status not in ('confirmed', 'rejected', 'indeterminate')):
            raise ActionNeedsReconciliation()
        if result.status == 'confirmed':
            if (intent.action in ('bookmark_add', 'follow')
                    and result.restrict != intent.restrict):
                raise ActionNeedsReconciliation()
            final = _Observation(desired, intent.restrict if desired else None, author_name)
            warning = self._local_follow(intent, final, author_name, control)
            return ActionOutcome(intent, 'confirmed', desired, 'write_confirmed', result, warning)
        if result.status == 'rejected':
            return ActionOutcome(intent, 'rejected', observed.present, 'write_rejected', result)
        return ActionOutcome(intent, 'indeterminate', None, 'unknown_outcome', result)

    def _reconcile(self, intent, author_name, control):
        key = _intent_key(intent)
        self._identity(control, intent.account_id)
        with self._lock:
            already_unblocked_in_writer = key in self._writer_reconciled
        if already_unblocked_in_writer:
            # A previous bounded GET unblocked writer, but a later identity or
            # deadline check prevented coordinator publication. A new explicit
            # reconciliation still needs a fresh authoritative observation.
            observed = self._observe(intent, control)
        else:
            valid = self._writer.reconcile_from_reader(
                intent.account_id, key[1], intent.target_id, author_id=intent.author_id)
            if valid is not True:
                self._checkpoint(control)
                raise ActionNeedsReconciliation()
            with self._lock:
                self._writer_reconciled.add(key)
            present = self._writer.confirmed_state(*key)
            if type(present) is not bool:
                raise ActionNeedsReconciliation()
            self._identity(control, intent.account_id)
            observed = _Observation(present)

        warning = key[1] == 'user' and self._following is None
        if key[1] == 'user' and self._following is not None:
            try:
                # Writer's bool does not supply public/private visibility. Only
                # this explicit reconciliation may make this additional GET.
                if not already_unblocked_in_writer:
                    observed = self._observe(intent, control)
                cached = self._writer.confirmed_state(*key)
                if type(cached) is bool and cached is not observed.present:
                    self._writer.invalidate_confirmed_state(*key)
                warning = self._local_follow(intent, observed, author_name, control)
            except ActionAccountChanged:
                raise
            except Exception:
                # The first GET already proved remote state. Extra local detail
                # or its transaction failing cannot turn that fact into a POST.
                warning = True
        elif already_unblocked_in_writer:
            cached = self._writer.confirmed_state(*key)
            if type(cached) is bool and cached is not observed.present:
                self._writer.invalidate_confirmed_state(*key)
        with self._lock:
            original = self._results[intent.operation_id].write_result
        return ActionOutcome(intent, 'observed', observed.present, 'remote_state_observed',
                             original, warning)

    def _worker(self, mode, intent, author_name, control):
        worker_id = get_ident()
        with self._lock:
            self._worker_ids.add(worker_id)
        try:
            try:
                if mode != 'verify':
                    self._capture_local_revision(intent, control)
                with (
                    request_scope(checkpoint=lambda: self._checkpoint(control), allow_retry=False),
                    read_deadline_scope(control.deadline),
                ):
                    if mode == 'verify':
                        return self._identity(control)
                    outcome = (self._execute(intent, author_name, control) if mode == 'execute'
                               else self._reconcile(intent, author_name, control))
            except BaseException as error:
                if isinstance(error, ReadDeadlineExceeded):
                    error = ActionTimedOut()
                if mode == 'verify':
                    if isinstance(error, ActionError):
                        raise error from None
                    raise ActionError() from None
                if isinstance(error, ActionCancelled) and not control.dispatched:
                    status = 'cancelled'
                elif (mode == 'reconcile' or control.dispatched
                      or isinstance(error, ActionNeedsReconciliation)):
                    status = 'indeterminate'
                else:
                    status = 'rejected'
                reason = error.code if isinstance(error, ActionError) else (
                    'unknown_outcome' if status == 'indeterminate' else 'preflight_failed')
                observed = control.observation
                state = (observed.present if observed is not None
                         and status not in ('indeterminate',) and mode != 'reconcile' else None)
                outcome = ActionOutcome(intent, status, state, reason, control.write_result)
            self._finish(mode, outcome)
            return outcome
        finally:
            with self._lock:
                self._worker_ids.discard(worker_id)

    def _finish(self, mode, outcome):
        intent = outcome.intent
        key = _intent_key(intent)
        with self._lock:
            self._pending.discard(key)
            previous = self._states.get(key, ActionState())
            if mode == 'reconcile':
                if outcome.status == 'observed':
                    self._blocked.pop(key, None)
                    self._writer_reconciled.discard(key)
                    self._reconciled[intent.operation_id] = outcome
                # Never rewrite the original unknown POST result as success.
            else:
                self._results[intent.operation_id] = outcome
                if outcome.status == 'indeterminate':
                    self._blocked[key] = intent.operation_id
            unknown = key in self._blocked
            observed = (None if unknown else outcome.remote_state
                        if type(outcome.remote_state) is bool else previous.observed)
            self._states[key] = ActionState(False, unknown, observed,
                                            previous.revision + 1, intent.operation_id)
