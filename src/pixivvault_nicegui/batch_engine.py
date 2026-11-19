"""Batch run engine: the original's "実行 1 回" over the existing managed pipeline.

For every selected author, one after another: list the whole inventory, compare
update dates (new / updated / restored / missing), save through the existing
durable jobs, observe deletions, record the author in the run ledger, then wait
4-6 seconds before the next author. Differences from the original that are on
purpose: the wait wakes up at once on stop/pause, a closed network gate pauses
the run (never "fails" it), and work is handed to JobService a few at a time.

The engine never talks to the network itself. Everything it touches is behind a
`port` (see `ServicesPort`): the gate, the full author listing, the enqueue and
the job states. Tests supply a fake port; the app supplies `ServicesPort`.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, RLock, Thread
import random
import time
from uuid import uuid4

from .account_scope import AccountMismatch, AccountScope, OwnedListing, require_same_account
from .author_download import Difference
from .batch_runs import COUNTERS, BatchRunError

BETWEEN_AUTHORS = (4.0, 6.0)          # original core.py:2269 random.uniform(4.0, 6.0)
MAX_IN_FLIGHT = 16                    # unfinished jobs the engine itself keeps queued
RETIRE_EVERY = 500                    # finished jobs after which the capped ledgers are freed
RETIRE_WAIT_SECONDS = 60.0            # how long to hold back new work for that, at most
SETTLE_EVERY = 16                     # finished jobs between proof-binding passes (job window is 50)
_ADMISSION_PAUSES = {'admission_blocked': 'network_stopped',
                     'admission_unavailable': 'network_gate_unavailable'}
POLL_SECONDS = 0.25
_TERMINAL_JOBS = ('done', 'error', 'stopped')
_ACCOUNT_UNSET = object()


class RunStopped(Exception):
    """Internal unwind: the user (or app shutdown) asked the run to stop."""


class GateClosed(Exception):
    """Internal unwind: the network stop gate is closed; the run pauses, never fails."""

    def __init__(self, code='network_stopped'):
        super().__init__(code)
        self.code = code


class RunFailed(Exception):
    """Internal unwind: a run-level fault (not one author's) ends the whole run."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class RunNeedsConfirmation(Exception):
    """Stop a resumable run until its identity can be confirmed."""


def capacity_code(error):
    """Name a hard storage-capacity refusal; None for any other error."""
    text = str(error)
    if getattr(error, 'reason_code', None) == 'storage_capacity':
        return 'storage_capacity'  # saved-works ceiling or a novel ledger that is full
    if 'job operation history is full' in text:
        return 'job_operation_history_full'
    if type(error).__name__ == 'ManagedRequestError' and 'directory rejected' in text:
        return 'intent_store_full'
    return None


def _queue_full(error):
    """Nothing was accepted because the queue is at capacity: wait, do not fail the work."""
    if type(error).__name__ == 'DownloadQueueCapacityReached':
        return True
    text = str(error)
    return 'queue capacity reached' in text or text == 'job queue is full'


def _gate_error(error):
    return type(error).__name__ in ('ManagedNetworkStopped', 'ManagedNetworkGateStorageError',
                                    'ManagedNetworkGateBusy')


@dataclass(frozen=True, slots=True)
class JobRef:
    kind: str
    job_id: str
    operation_id: str


class ServicesPort:
    """Adapter over ApplicationServices (or anything shaped like it)."""

    def __init__(self, services):
        self.s = services

    @property
    def closing(self):
        return bool(getattr(self.s, 'closing', False))

    def require_allowed(self):
        integration = getattr(self.s, 'managed_network_stop', None)
        if integration is not None:
            integration.require_allowed()

    def author_target(self, author_id):
        media = getattr(self.s, 'author_media', None)
        return None if media is None else media.author_target(author_id).target_type

    def verified_author_account(self) -> str:
        reader = self.s.live_client
        if reader is None:
            raise AccountMismatch()
        return AccountScope(reader.verify_identity(), reader).verify_current(self.s)

    def list_author(self, author_id, target_type, *, expected_account: str) -> OwnedListing:
        AccountScope(expected_account)
        return self.s.author_media_preview_owned(author_id, target_type, expected_account=expected_account)

    def verify_author_scope(self, scope: AccountScope) -> None:
        scope.verify_current(self.s)

    def enqueue(self, candidate, operation_id):
        record = getattr(candidate, 'record', None)
        if record is not None:        # a bookmark: the managed illustration path, as the old screen does
            job, created = self.s.enqueue_managed_download(record, operation_id, return_created=True)
            return job.job_id, bool(created)
        job, created, _ = self.s.enqueue_author_media(candidate, operation_id, origin='follow')
        return job.job_id, bool(created)

    def list_bookmarks(self, visibility):
        """Candidates of the cached bookmark list for one visibility (no network: the caller refreshed it)."""
        from .bookmark_run import classify
        owner = self.s.managed_downloads

        def saved(work_id):
            try:
                return owner.verified_saved_receipt(work_id, verify_pages=False) is not None
            except Exception:
                return False
        records = [r for r in self.s.bookmark_download_candidates() if r.visibility == visibility]
        return classify(records, is_saved=saved)

    def _owner(self, kind):
        media = getattr(self.s, 'author_media', None)
        return media.owner_jobs(kind) if media is not None else self.s.jobs

    def job_states(self, refs):
        """{job_id: (state, error_code)}; an unknown id is reported as ('lost', '')."""
        result = {}
        owners = {}
        for ref in refs:
            owner = owners.setdefault(ref.kind, self._owner(ref.kind))
            job = next((j for j in owner.snapshot() if j.job_id == ref.job_id), None)
            if job is None and hasattr(owner, 'retained_job'):
                job = owner.retained_job(ref.job_id)
            result[ref.job_id] = ('lost', '') if job is None else (job.state, job.error_code)
        return result

    def stop_jobs(self, refs):
        for ref in refs:
            try:
                self._owner(ref.kind).control(ref.job_id, 'stop')
            except Exception:
                pass

    def pause_jobs(self, refs):
        """Hold the run's queued / running jobs while the run is paused by the user."""
        for ref in refs:
            try:
                self._owner(ref.kind).control(ref.job_id, 'pause')
            except Exception:
                pass

    def resume_jobs(self, refs):
        """Release only jobs this run paused. A job the network gate paused stays for recovery."""
        for ref in refs:
            try:
                owner = self._owner(ref.kind)
                job = next((j for j in owner.snapshot() if j.job_id == ref.job_id), None)
                if job is not None and job.state == 'paused' and job.error_code not in _ADMISSION_PAUSES:
                    owner.control(ref.job_id, 'resume')
            except Exception:
                pass

    def observe_deletion(self, author_id, remote, scan_id):
        """(pending, confirmed) from one recorded complete crawl; None without a legacy bridge."""
        bridge = getattr(self.s, 'legacy_author_bridge', None)
        if bridge is None:
            return None
        bridge.assert_source_unchanged()
        scan = bridge.observe_author(author_id, remote, scan_id=scan_id)
        return scan.remote_missing_pending, scan.remote_missing_confirmed

    def saved_headroom(self):
        """How many more works the managed store can hold, None if unknown.

        The smaller of the entry-count ceiling and what still fits in the index file. The
        engine refuses before queueing instead of discovering the wall one work at a time.
        """
        generations = getattr(getattr(self.s, 'managed_downloads', None), '_generations', None)
        if generations is None:
            return None
        try:
            return generations.capacity()['headroom']
        except Exception as error:
            return 0 if 'limit exceeded' in str(error) else None

    def settle(self, author_id):
        """Bind finished jobs to their proofs before the bounded job window forgets them."""
        media = getattr(self.s, 'author_media', None)
        if media is not None:
            media.reconcile(author_id=author_id)

    def retire(self, refs):
        """Free the capped ledgers: int = retired, None = not safe yet, 'unsupported' = never."""
        hook = getattr(self.s, 'retire_settled_author_jobs', None)
        return 'unsupported' if hook is None else hook(tuple(refs))


class BatchRunEngine:
    def __init__(self, port, ledger, *, following=None, account_id=None, on_progress=None,
                 between_authors=BETWEEN_AUTHORS, uniform=random.uniform, wait=None,
                 max_in_flight=MAX_IN_FLIGHT, poll_seconds=POLL_SECONDS, monotonic=time.monotonic,
                 retire_every=RETIRE_EVERY, retire_wait_seconds=RETIRE_WAIT_SECONDS):
        if type(max_in_flight) is not int or not 1 <= max_in_flight <= 40:
            raise ValueError('invalid in-flight limit')
        self.port, self.ledger = port, ledger
        self.following, self.account_id = following, account_id
        self.on_progress = on_progress
        self.between_authors, self._uniform = between_authors, uniform
        self._external_wait = wait
        self.max_in_flight, self.poll_seconds = max_in_flight, poll_seconds
        self.retire_every, self.retire_wait_seconds = retire_every, retire_wait_seconds
        self._settled = []
        self._retire_disabled = False
        self._next_drain_at = retire_every
        self._monotonic = monotonic
        self._cond = Condition(RLock())
        self._stop = False
        self._paused = False
        self._current = None
        self._thread = None
        self.run_id = None
        self.state = 'idle'
        self.pause_reason = ''
        self.totals = {name: 0 for name in COUNTERS}

    # -- control -----------------------------------------------------------
    @property
    def active(self):
        return self.state in ('running', 'paused', 'stopping')

    def pause(self, reason='user'):
        with self._cond:
            if self.active and not self._stop:
                self._paused, self.pause_reason, self.state = True, reason, 'paused'
                self._set_run('paused', reason if reason != 'user' else '')
                self._cond.notify_all()

    def resume(self):
        with self._cond:
            if self.state == 'paused':
                self._paused, self.pause_reason, self.state = False, '', 'running'
                self._set_run('running')
                self._cond.notify_all()
                return True
            return False

    def stop(self):
        """Ask the run to stop. Safe from any thread; wakes every wait at once."""
        with self._cond:
            if self.active and not self._stop:
                self._stop, self._paused, self.state = True, False, 'stopping'
                self._set_run('stopping')
                self._cond.notify_all()

    # -- after a stop or a restart ------------------------------------------
    def recover(self):
        """At application start: a run still marked active can no longer be running.

        Marks it `interrupted` (its mid-step author `stopped`, never stamped). Starts nothing and
        talks to nobody: whether to continue is the user's call (`resumable_run`).
        """
        with self._cond:
            if self.active:
                return ()
            return self.ledger._recover_interrupted()

    def resumable_run(self):
        """The run the screen offers as 「前回は途中で止まりました。再開／破棄」, or None."""
        with self._cond:
            return None if self.active else self.ledger.resumable_run()

    def _displayed_resume(self, run_id):
        run = self.resumable_run()
        if run is None or run.run_id != run_id:
            raise AccountMismatch()
        return run

    def discard_resumable(self, run_id, *, account_id):
        with self._cond:
            run = self._displayed_resume(run_id)
            self.ledger.discard_run(run.run_id, account_id=account_id)
            return run

    def start_unfinished(self, run_id, *, account_id=None, bookmark_refresh=None):
        """Resume the exact displayed run over its unfinished rows."""
        with self._cond:
            run = self._displayed_resume(run_id)
            verified = run.kind == 'bookmarks' and self.ledger.has_verified_bookmark_account(run.run_id)
            if run.kind == 'bookmarks':
                if not verified or not run.account_id:
                    raise RunNeedsConfirmation('account_unknown')
                if account_id is not None:
                    require_same_account(run.account_id, account_id)
                account_id = run.account_id
            else:
                AccountScope(account_id)
                require_same_account(run.account_id, account_id)
            authors = self.ledger.unfinished_authors(run.run_id)
            preflight = (lambda: bookmark_refresh(account_id)) if verified and bookmark_refresh else None
            return self.start(authors, kind=run.kind, origin=run.origin, target_type=run.target_type or None,
                              preflight=preflight, account_id=account_id, verified_bookmark_account=verified,
                              resume_from=run.run_id)

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def _set_run(self, state, error_code=''):
        if self.run_id is not None:
            try:
                self.ledger.set_run_state(self.run_id, state, error_code, account_id=self.account_id)
            except BatchRunError:
                pass

    def _check_state(self):
        """Original check_state(): raise on stop, block while paused."""
        with self._cond:
            while True:
                if self._stop or self.port.closing:
                    raise RunStopped()
                if not self._paused:
                    return
                self._cond.wait(0.5)

    def _sleep(self, seconds, *, between_authors=False):
        """Wait up to `seconds`; return early on stop or pause (the original could not)."""
        if between_authors and self._external_wait is not None:
            self._external_wait(seconds)
            return
        deadline = self._monotonic() + seconds
        with self._cond:
            while not self._stop and not self._paused and not self.port.closing:
                left = deadline - self._monotonic()
                if left <= 0:
                    return
                self._cond.wait(min(left, 0.5))

    def _emit(self, event, **values):
        if self.on_progress is not None:
            try:
                self.on_progress(dict(event=event, run_id=self.run_id, totals=dict(self.totals), **values))
            except Exception:
                pass

    # -- entry points ------------------------------------------------------
    def start(self, author_ids, *, kind='authors', origin='manual', target_type=None, preflight=None,
              account_id=_ACCOUNT_UNSET, verified_bookmark_account=False, resume_from=None, on_finish=None):
        """Begin a run on its own thread and return the run id."""
        run_id = self._begin(author_ids, kind, origin, target_type, account_id=account_id,
                             verified_bookmark_account=verified_bookmark_account, resume_from=resume_from)
        self._thread = Thread(target=self._execute, args=(run_id, tuple(self._authors), preflight, on_finish),
                              name='pixivvault-batch', daemon=True)
        self._thread.start()
        return run_id

    def run(self, author_ids, *, kind='authors', origin='manual', target_type=None):
        """Same as `start`, but on the calling thread (tests, scripts)."""
        run_id = self._begin(author_ids, kind, origin, target_type)
        self._execute(run_id, tuple(self._authors))
        return run_id

    def _begin(self, author_ids, kind, origin, target_type, *, account_id=_ACCOUNT_UNSET,
               verified_bookmark_account=False, resume_from=None):
        if kind not in ('authors', 'bookmarks'):
            raise ValueError('invalid run kind')
        if kind == 'bookmarks':
            from .bookmark_run import ROW_VISIBILITY, VISIBILITY_ROWS
            # The rows of a bookmarks run are its visibilities (names, or the row IDs of a continued run).
            author_ids = tuple(VISIBILITY_ROWS.get(v, v) for v in author_ids)
            if not author_ids or any(v not in ROW_VISIBILITY for v in author_ids):
                raise ValueError('invalid bookmark visibility')
        with self._cond:
            if self.active:
                raise RuntimeError('a batch run is already active')
            owner = self.account_id if account_id is _ACCOUNT_UNSET else account_id
            # The ledger has one process owner, so a run still marked active while this engine
            # is idle is left over from a crash or shutdown: close it as `interrupted` first.
            if resume_from is None:
                self.ledger._recover_interrupted()
            authors = tuple(dict.fromkeys(author_ids))
            run_id = self.ledger.begin_run(kind, origin, target_type or 'both', authors, account_id=owner,
                                           verified_bookmark_account=verified_bookmark_account, resume_from=resume_from)
            self.account_id, self._authors, self.run_id = owner, authors, run_id
            self._stop = self._paused = False
            self.state, self.pause_reason = 'running', ''
            self._settled, self._retire_disabled = [], False
            self._next_drain_at = self.retire_every
            self.totals = {name: 0 for name in COUNTERS}
            self.kind, self.target_type = kind, target_type
            self._started = self._monotonic()
            return self.run_id

    # -- the run -----------------------------------------------------------
    def _execute(self, run_id, authors, preflight=None, on_finish=None):
        total = len(authors)
        outcome, code = 'done', ''
        author_account = self.account_id
        self._emit('run_start', total=total)
        try:
            if preflight is not None:
                while True:
                    self._check_state()
                    try:
                        self._gate()
                        try:
                            preflight()
                        except Exception:
                            # A failed route can wrap a 429; the gate is the
                            # authoritative record of whether it stopped traffic.
                            self._gate()
                            raise
                        break
                    except GateClosed as closed:
                        self.pause(closed.code)
                        self._emit('run_paused', reason=closed.code, index=0, total=total,
                                   author_id=None)
            for index, author_id in enumerate(authors, 1):
                self._current = None
                if index > 1:
                    self._between_authors()
                self._current = author_id
                while True:
                    self._check_state()
                    try:
                        if self.kind == 'bookmarks':
                            self._process_bookmarks(run_id, index, total, author_id)
                        else:
                            if author_account is None:
                                self._gate()
                                try:
                                    author_account = self.port.verified_author_account()
                                except Exception:
                                    self._gate()
                                    raise
                            self._process_author(run_id, index, total, author_id, author_account)
                        break
                    except GateClosed as closed:
                        self._reset_author(run_id, author_id)
                        self.pause(closed.code)
                        self._emit('run_paused', reason=closed.code, index=index, total=total,
                                   author_id=author_id)
                    except (RunStopped, RunFailed, AccountMismatch):
                        raise
                    except Exception as error:
                        self._author_failed(run_id, index, total, author_id, error)
                        break
        except RunStopped:
            outcome = 'stopped'
            self._close_author(run_id, 'stopped', '')
        except AccountMismatch as error:
            outcome, code = 'failed', error.code
            if self._current is not None:
                self._reset_author(run_id, self._current)
                self._current = None
        except RunNeedsConfirmation:
            outcome, code = 'interrupted', 'bookmark_account_mismatch'
            self._close_author(run_id, 'stopped', code)
        except RunFailed as failed:
            outcome, code = 'failed', failed.code
            self._close_author(run_id, 'failed', failed.code)
        except Exception as error:  # a ledger fault etc.: end the run, never hang the thread
            outcome, code = 'failed', type(error).__name__
            self._close_author(run_id, 'failed', code)
        finally:
            with self._cond:
                self._set_run(outcome, code)
                try:
                    if on_finish is not None:
                        on_finish(outcome, code, dict(self.totals))
                finally:
                    self.state = outcome
                    self._paused = False
            self._emit('run_end', outcome=outcome, error_code=code, total=total,
                       elapsed=self._monotonic() - self._started)

    def _close_author(self, run_id, state, code):
        """The author that was mid-step when the run ended: never `done`, never stamped."""
        author_id, self._current = self._current, None
        if author_id is not None:
            try:
                self.ledger.finish_author(run_id, author_id, state, error_code=code, account_id=self.account_id)
            except BatchRunError:
                pass  # already settled

    def _between_authors(self):
        low, high = self.between_authors
        self._sleep(self._uniform(low, high), between_authors=True)
        self._check_state()

    def _reset_author(self, run_id, author_id):
        try:
            self.ledger.update_author(run_id, author_id, state='pending', account_id=self.account_id)
        except BatchRunError:
            pass

    def _author_failed(self, run_id, index, total, author_id, error):
        code = type(error).__name__
        try:
            self.ledger.finish_author(run_id, author_id, 'failed', error_code=code, account_id=self.account_id)
        except BatchRunError:
            pass
        self._emit('author_failed', index=index, total=total, author_id=author_id, error_code=code)

    def _gate(self):
        try:
            self.port.require_allowed()
        except Exception as error:
            if _gate_error(error):
                raise GateClosed('network_stopped' if type(error).__name__ == 'ManagedNetworkStopped'
                                 else 'network_gate_unavailable') from None
            raise

    def _target_for(self, author_id):
        # Original core.py:1185-1189: an author's own setting beats the run's toggle.
        own = self.port.author_target(author_id)
        return own or self.target_type

    def _process_author(self, run_id, index, total, author_id, expected_account):
        self._gate()
        self.ledger.update_author(run_id, author_id, state='listing', account_id=self.account_id)
        self._emit('author_start', index=index, total=total, author_id=author_id)
        target = self._target_for(author_id)
        try:
            listing = self.port.list_author(author_id, target, expected_account=expected_account)
        except Exception as error:
            if _gate_error(error):
                raise GateClosed('network_stopped' if type(error).__name__ == 'ManagedNetworkStopped'
                                 else 'network_gate_unavailable') from None
            raise
        self._check_state()
        if type(listing) is not OwnedListing or listing.author_id != author_id:
            raise AccountMismatch()
        require_same_account(expected_account, listing.account_id)
        self.port.verify_author_scope(listing.scope)
        rows = listing.rows
        keys = [(c.kind, c.work_id) for c in rows]
        if any(c.author_id != author_id for c in rows) or len(set(keys)) != len(keys):
            raise ValueError('author inventory identity changed')
        counters = {name: 0 for name in COUNTERS}
        names = {Difference.NEW: 'n_new', Difference.UPDATED: 'n_updated', Difference.RESTORED: 'n_restored',
                 Difference.MISSING: 'n_missing', Difference.SKIPPED: 'n_skipped', Difference.UNKNOWN: 'n_unknown'}
        for candidate in rows:
            counters[names[candidate.difference]] += 1
        self.port.verify_author_scope(listing.scope)
        self._observe_deletion(run_id, author_id, target, rows, counters)
        self._check_saved_capacity(rows)
        self.ledger.update_author(run_id, author_id, state='downloading', counters=counters, account_id=self.account_id)
        self._emit('author_listed', index=index, total=total, author_id=author_id, counters=dict(counters))
        queue = sorted((c for c in rows if c.eligible), key=lambda c: (-int(c.work_id), c.kind))
        failed = self._download(run_id, index, total, author_id, queue, listing=listing)
        counters['n_failed'] = failed
        self._check_state()
        self.port.verify_author_scope(listing.scope)
        self.ledger.complete_author(run_id, author_id, counters, listing=listing, following=self.following)
        for name in COUNTERS:
            self.totals[name] += counters[name]
        self._emit('author_done', index=index, total=total, author_id=author_id, counters=dict(counters))

    def _process_bookmarks(self, run_id, index, total, row_id):
        """One visibility of the bookmark run: list, save what is new, done. Never stamps."""
        from .bookmark_run import ROW_VISIBILITY
        self._gate()
        self.ledger.update_author(run_id, row_id, state='listing', account_id=self.account_id)
        self._emit('author_start', index=index, total=total, author_id=row_id)
        try:
            rows = self.port.list_bookmarks(ROW_VISIBILITY[row_id])
        except Exception as error:
            if _gate_error(error):
                raise GateClosed('network_stopped' if type(error).__name__ == 'ManagedNetworkStopped'
                                 else 'network_gate_unavailable') from None
            raise
        self._check_state()
        keys = [c.key for c in rows]
        if len(set(keys)) != len(keys):
            raise ValueError('bookmark listing identity changed')
        counters = {name: 0 for name in COUNTERS}
        names = {Difference.NEW: 'n_new', Difference.UPDATED: 'n_updated', Difference.RESTORED: 'n_restored',
                 Difference.MISSING: 'n_missing', Difference.SKIPPED: 'n_skipped', Difference.UNKNOWN: 'n_unknown'}
        for candidate in rows:
            counters[names[candidate.difference]] += 1
        self._check_saved_capacity(rows)
        self.ledger.update_author(run_id, row_id, state='downloading', counters=counters, account_id=self.account_id)
        self._emit('author_listed', index=index, total=total, author_id=row_id, counters=dict(counters))
        queue = sorted((c for c in rows if c.eligible), key=lambda c: (-int(c.work_id), c.kind))
        failed = self._download(run_id, index, total, row_id, queue, listing=None)
        counters['n_failed'] = failed
        self._check_state()
        self.ledger.complete_bookmark_row(run_id, row_id, counters, account_id=self.account_id)
        for name in COUNTERS:
            self.totals[name] += counters[name]
        self._emit('author_done', index=index, total=total, author_id=row_id, counters=dict(counters))

    def _check_saved_capacity(self, rows):
        """Refuse an author whose new works cannot fit the managed store (no partial, stamped run)."""
        headroom = getattr(self.port, 'saved_headroom', None)
        if headroom is None:
            return
        room = headroom()
        new = sum(1 for c in rows if c.eligible and c.kind == 'illustration' and c.prior_token is None)
        if room is not None and new > room:
            raise RunFailed('saved_works_limit')

    def _observe_deletion(self, run_id, author_id, target, rows, counters):
        """Original phase B. Skipped (never guessed) unless a full illustration list was read."""
        illust = [c for c in rows if c.kind in ('illustration', 'ugoira')]
        if target == 'novel':
            check = 'skipped_novel_only'
        elif not illust:
            check = 'skipped_no_illustration_list'
        else:
            scan_id = uuid4().hex
            remote = tuple(dict(id=c.work_id, user_id=c.author_id, type=c.work_type,
                                page_count=c.page_count, update_date=c.update_date) for c in illust)
            self.ledger.update_author(run_id, author_id, scan_id=scan_id, deletion_check='pending', account_id=self.account_id)
            try:
                result = self.port.observe_deletion(author_id, remote, scan_id)
            except Exception:
                result = False  # the download goes on; only the deletion check is skipped
            if result is None:
                check = 'skipped_no_legacy_db'
            elif result is False:
                check = 'failed'
            else:
                check = 'done'
                counters['n_absent_pending'], counters['n_absent_confirmed'] = result
        self.ledger.update_author(run_id, author_id, deletion_check=check, account_id=self.account_id)

    def _hold_jobs(self, refs):
        hook = getattr(self.port, 'pause_jobs', None)
        if hook is not None and refs and self.pause_reason in ('', 'user'):
            hook(refs)

    def _release_jobs(self, refs):
        hook = getattr(self.port, 'resume_jobs', None)
        if hook is not None and refs:
            hook(refs)

    def _retire(self):
        """Ask the port to free finished jobs; True when done (or never going to be possible)."""
        if not self._settled or self._retire_disabled:
            return True
        result = self.port.retire(tuple(self._settled))
        if result == 'unsupported':
            self._retire_disabled = True
            self._settled = []
            return True
        if result is None:
            return False
        self._settled = []
        self._next_drain_at = self.retire_every
        return True

    def _download(self, run_id, index, total, author_id, queue, *, listing):
        """Hand work to the durable jobs a few at a time; return how many did not save.

        The job ledger caps at 10,000 operations (job_service.MAX_OPERATION_IDS), so after
        `retire_every` finished jobs new work is held back until the port can free them.
        """
        inflight, failed, done_count, unsettled = {}, 0, 0, 0
        pending = list(queue)
        drain_since = None
        try:
            jobs_held = False
            while pending or inflight:
                if self._paused and inflight and not jobs_held:
                    self._hold_jobs([ref for ref, _ in inflight.values()])
                    jobs_held = True
                self._check_state()
                if listing is not None:
                    self.port.verify_author_scope(listing.scope)
                if jobs_held:
                    self._release_jobs([ref for ref, _ in inflight.values()])
                    jobs_held = False
                progressed = False
                draining = len(self._settled) >= self._next_drain_at and not self._retire_disabled
                if draining and drain_since is None:
                    drain_since = self._monotonic()
                if draining and not inflight and self._retire():
                    drain_since, draining = None, False
                holding = draining and self._monotonic() - drain_since <= self.retire_wait_seconds
                if draining and not holding:
                    # Gave up waiting: carry on, and try again after the next `retire_every` works.
                    drain_since, draining = None, False
                    self._next_drain_at = len(self._settled) + self.retire_every
                while pending and not holding and len(inflight) < self.max_in_flight:
                    self._gate()
                    if listing is not None:
                        self.port.verify_author_scope(listing.scope)
                    candidate = pending[0]
                    operation_id = uuid4().hex
                    try:
                        job_id, _created = self.port.enqueue(candidate, operation_id)
                    except AccountMismatch:
                        raise
                    except Exception as error:
                        if _gate_error(error):
                            raise GateClosed('network_stopped' if type(error).__name__ == 'ManagedNetworkStopped'
                                             else 'network_gate_unavailable') from None
                        code = capacity_code(error)
                        if code is not None:
                            raise RunFailed(code) from None
                        if _queue_full(error):
                            break  # wait for jobs to finish, then offer it again
                        pending.pop(0)
                        failed += 1
                        self.ledger.record_items(run_id, author_id, (
                            (candidate.key, candidate.kind, operation_id, None, 'error'),), account_id=self.account_id)
                        continue
                    pending.pop(0)
                    progressed = True
                    self._emit('work_info', index=index, total=total, author_id=author_id,
                               title=getattr(candidate, 'title', '') or '')
                    ref = JobRef(candidate.kind, job_id, operation_id)
                    inflight[job_id] = (ref, candidate)
                    self.ledger.record_items(run_id, author_id, (
                        (candidate.key, candidate.kind, operation_id, job_id, 'queued'),), account_id=self.account_id)
                if inflight:
                    states = self.port.job_states([ref for ref, _ in inflight.values()])
                    if listing is not None:
                        self.port.verify_author_scope(listing.scope)
                    finished = []
                    for job_id, (ref, candidate) in tuple(inflight.items()):
                        state, error_code = states.get(job_id, ('lost', ''))
                        if state == 'paused' and error_code in _ADMISSION_PAUSES:
                            raise GateClosed(_ADMISSION_PAUSES[error_code])
                        if state == 'error' and error_code == 'storage_capacity':
                            raise RunFailed('saved_works_limit')  # the store is full: never a per-work failure
                        if state in _TERMINAL_JOBS or state == 'lost':
                            finished.append((ref, candidate, state))
                            del inflight[job_id]
                    if finished:
                        progressed = True
                        unsettled += len(finished)
                        if unsettled >= SETTLE_EVERY:
                            self.port.settle(author_id)
                            unsettled = 0
                        rows = []
                        for ref, candidate, state in finished:
                            done_count += 1
                            if state != 'done':
                                failed += 1
                            rows.append((candidate.key, candidate.kind, ref.operation_id, ref.job_id,
                                         {'done': 'done', 'error': 'error', 'stopped': 'stopped'}.get(state, 'lost')))
                            self._settled.append(ref)
                        self.ledger.record_items(run_id, author_id, rows, account_id=self.account_id)
                        self.ledger.update_author(run_id, author_id, counters={'n_failed': failed}, account_id=self.account_id)
                        self._emit('work_progress', index=index, total=total, author_id=author_id,
                                   done=done_count, queued=len(queue), failed=failed,
                                   title=getattr(finished[-1][1], 'title', '') or '')
                if not progressed and (pending or inflight):
                    self._sleep(self.poll_seconds)
        except (RunStopped, RunFailed, AccountMismatch):
            if inflight:
                self.port.stop_jobs([ref for ref, _ in inflight.values()])
            raise
        if unsettled:
            self.port.settle(author_id)
        self._retire()  # best effort at the author boundary; carried over when not yet safe
        return failed
