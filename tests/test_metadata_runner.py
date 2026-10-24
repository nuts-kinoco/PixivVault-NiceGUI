"""Offline single-pass metadata runner acceptance; fake transport, no network."""
from collections import deque
from datetime import datetime
import hashlib
import json
from threading import Event

import pytest

from pixivvault_nicegui.managed_downloads import IDENTITY, MARKER
from pixivvault_nicegui.metadata_ledger import ManagedMetadataLedger
from pixivvault_nicegui.metadata_policy import MetadataCandidate
from pixivvault_nicegui.metadata_runner import (
    ManagedMetadataBackfillRunner, MetadataRunSummary,
)
from pixivvault_nicegui.metadata_transport import MetadataFetchResult, MetadataPayloadInvalid
from pixivvault_nicegui.pixiv_read_client import (
    AuthenticationMissing, HTTPStatusRejected, ReadDeadlineExceeded,
)

A, B = 'a' * 64, 'b' * 64
AUTHOR = '91'


def digest(work_id, salt='a'):
    return hashlib.sha256(f'{salt}:{work_id}'.encode()).hexdigest()


def cand(work_id='123', user='91', **kw):
    args = dict(work_id=work_id, user_id=user, content_type='illust', is_deleted=False,
                bookmark_count=None, meta_attempts=0)
    args.update(kw)
    return MetadataCandidate(**args)


def ok(work_id='123', user='91', count=0, **extra):
    meta = {'meta_source': 'illust_detail', 'bookmark_count': count}
    meta.update(extra)
    return MetadataFetchResult(work_id, user, meta, False)


def missing(work_id='123', user='91'):
    return MetadataFetchResult(work_id, user, None, True)


class FakeTransport:
    def __init__(self, results=(), identity='555'):
        self.results = deque(results)
        self.identity = identity
        self.identity_calls = 0
        self.calls = []
        self.before_return = None

    def verify_identity(self):
        self.identity_calls += 1
        if isinstance(self.identity, BaseException):
            raise self.identity
        return self.identity

    def fetch_detail(self, work_id, expected_author_id):
        self.calls.append((work_id, expected_author_id))
        if self.before_return is not None:
            self.before_return(work_id)
        item = self.results.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def env(tmp_path):
    root = tmp_path / 'isolated-managed'
    root.mkdir()
    (root / MARKER).write_text(json.dumps(IDENTITY), encoding='utf-8')
    active = {'123': A, '124': B}
    ledger = ManagedMetadataLedger(root, active_receipt_sha256=active.get)
    yield ledger, active
    ledger.close()


def make(env, results=(), *, busy=lambda: False, enabled=True, identity='555'):
    ledger, active = env
    if enabled:
        ledger.configure(enabled=True, expected_revision=1)
    transport = FakeTransport(results, identity)
    runner = ManagedMetadataBackfillRunner(
        ledger, transport, active_receipt_sha256=active.get, is_download_busy=busy)
    return runner, transport, ledger, active


def many_active(active, count, start=200):
    ids = [str(start + i) for i in range(count)]
    for work_id in ids:
        active[work_id] = digest(work_id)
    return ids


# ---------------------------------------------------------------- construction

def test_constructor_validates_dependencies(env):
    ledger, active = env
    t = FakeTransport()
    with pytest.raises(TypeError):
        ManagedMetadataBackfillRunner(object(), t, active_receipt_sha256=active.get,
                                      is_download_busy=lambda: False)
    with pytest.raises(TypeError):
        ManagedMetadataBackfillRunner(ledger, object(), active_receipt_sha256=active.get,
                                      is_download_busy=lambda: False)
    with pytest.raises(TypeError):
        ManagedMetadataBackfillRunner(ledger, t, active_receipt_sha256=None,
                                      is_download_busy=lambda: False)
    with pytest.raises(TypeError):
        ManagedMetadataBackfillRunner(ledger, t, active_receipt_sha256=active.get,
                                      is_download_busy=None)


def test_construction_makes_no_calls(env):
    runner, transport, ledger, _ = make(env)
    assert transport.identity_calls == 0 and transport.calls == []
    assert ledger.entry('123') is None


@pytest.mark.parametrize('kwargs,error', [
    ({'wait_for_idle': 1}, ValueError),
    ({'poll_seconds': 0}, ValueError),
    ({'poll_seconds': -1}, ValueError),
    ({'poll_seconds': 6}, ValueError),
    ({'poll_seconds': '0.1'}, ValueError),
    ({'stop_event': object()}, TypeError),
])
def test_run_options_validated_before_any_call(env, kwargs, error):
    runner, transport, _, _ = make(env)
    with pytest.raises(error):
        runner.run_once((cand(),), **kwargs)
    assert transport.identity_calls == 0 and transport.calls == []


def test_candidates_must_be_immutable_tuple(env):
    runner, transport, _, _ = make(env)
    with pytest.raises(ValueError):
        runner.run_once([cand()])
    assert transport.identity_calls == 0


# ---------------------------------------------------------------- gating

def test_disabled_ledger_makes_no_transport_or_busy_calls(env):
    busy_calls = []
    runner, transport, ledger, _ = make(
        env, [ok()], enabled=False, busy=lambda: busy_calls.append(1) or False)
    summary = runner.run_once((cand(),))
    assert summary == MetadataRunSummary(stopped='disabled')
    assert transport.identity_calls == 0 and transport.calls == [] and busy_calls == []
    assert ledger.entry('123') is None


def test_no_candidates_means_no_identity_call(env):
    runner, transport, ledger, _ = make(env)
    assert runner.run_once(()) == MetadataRunSummary()
    assert transport.identity_calls == 0 and transport.calls == []


@pytest.mark.parametrize('row', [
    dict(bookmark_count=0), dict(bookmark_count=5), dict(is_deleted=True),
    dict(content_type='manga'), dict(meta_attempts=3),
])
def test_ineligible_candidates_are_not_targets(env, row):
    runner, transport, ledger, _ = make(env)
    assert runner.run_once((cand(**row),)) == MetadataRunSummary()
    assert transport.identity_calls == 0 and transport.calls == []
    assert ledger.entry('123') is None


@pytest.mark.parametrize('identity', ['', 'abc', '0', None, 555, '01'])
def test_unverified_identity_stops_before_any_fetch(env, identity):
    runner, transport, ledger, _ = make(env, [ok()], identity=identity)
    summary = runner.run_once((cand(),))
    assert summary.stopped == 'authentication_unverified'
    assert summary.target == 1 and summary.filled == 0
    assert transport.identity_calls == 1 and transport.calls == []
    assert ledger.entry('123') is None


@pytest.mark.parametrize('error', [AuthenticationMissing(), ReadDeadlineExceeded(), RuntimeError('x')])
def test_identity_exception_stops_before_any_fetch(env, error):
    runner, transport, ledger, _ = make(env, [ok()], identity=error)
    summary = runner.run_once((cand(),))
    assert summary.stopped == 'authentication_or_state_unavailable'
    assert transport.calls == []
    assert ledger.entry('123') is None


def test_identity_verified_once_before_fetches(env):
    ids = many_active(env[1], 3)
    runner, transport, _, _ = make(env, [ok(i) for i in ids])
    order = []
    original = transport.verify_identity
    transport.verify_identity = lambda: order.append('identity') or original()
    transport.before_return = lambda w: order.append('fetch')
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.filled == 3
    assert order == ['identity', 'fetch', 'fetch', 'fetch']
    assert transport.identity_calls == 1


def test_inactive_work_is_skipped_without_get(env):
    runner, transport, ledger, active = make(env, [ok()])
    del active['123']
    summary = runner.run_once((cand(),))
    assert summary.skipped == 1 and summary.filled == 0
    assert transport.calls == [] and ledger.entry('123') is None


def test_unreadable_active_pointer_stops_managed_state_changed(env):
    ledger, active = env
    ledger.configure(enabled=True, expected_revision=1)
    transport = FakeTransport([ok()])

    def broken(work_id):
        raise OSError('boom')
    runner = ManagedMetadataBackfillRunner(
        ledger, transport, active_receipt_sha256=broken, is_download_busy=lambda: False)
    summary = runner.run_once((cand(),))
    assert summary.stopped == 'managed_state_changed'
    assert transport.calls == []


# ---------------------------------------------------------------- success

def test_zero_bookmark_success_persists_and_overlays_active_generation(env):
    runner, transport, ledger, _ = make(env, [ok(count=0, x_restrict=0, view_count=9)])
    summary = runner.run_once((cand(),))
    assert summary == MetadataRunSummary(target=1, filled=1)
    assert transport.calls == [('123', '91')]
    entry = ledger.entry('123')
    assert entry.state == 'done' and entry.attempts == 0
    overlay = ledger.verified_overlay('123', A)
    assert overlay['bookmark_count'] == 0
    assert overlay['x_restrict'] == 0 and overlay['view_count'] == 9
    assert overlay['meta_source'] == 'illust_detail'
    assert ledger.verified_overlay('123', B) is None


def test_success_records_timezone_aware_fetch_timestamp(env):
    runner, _, ledger, _ = make(env, [ok(meta_fetched_at='1999-01-01T00:00:00+00:00')])
    runner.run_once((cand(),))
    stamp = ledger.verified_overlay('123', A)['meta_fetched_at']
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert parsed.year >= 2026  # transport-supplied stamp never wins


def test_transport_result_not_mutated_by_runner(env):
    result = ok()
    runner, _, _, _ = make(env, [result])
    runner.run_once((cand(),))
    assert 'meta_fetched_at' not in result.metadata


def test_one_get_per_work_in_policy_order(env):
    ids = many_active(env[1], 3)
    rows = (cand(ids[2], user='5'), cand(ids[0], user='7'), cand(ids[1], user='5'),
            cand('999', bookmark_count=1))
    results = [ok(ids[1], '5'), ok(ids[2], '5'), ok(ids[0], '7')]
    runner, transport, _, _ = make(env, results)
    summary = runner.run_once(rows)
    assert summary.target == 3 and summary.filled == 3
    assert transport.calls == [(ids[1], '5'), (ids[2], '5'), (ids[0], '7')]


def test_second_run_over_done_work_does_not_get_again(env):
    runner, transport, ledger, _ = make(env, [ok()])
    runner.run_once((cand(),))
    summary = runner.run_once((cand(),))
    assert summary.skipped == 1 and summary.filled == 0
    assert len(transport.calls) == 1
    assert ledger.entry('123').state == 'done'


def test_candidate_author_is_passed_for_verification(env):
    runner, transport, _, _ = make(env, [ok(user='77')])
    runner.run_once((cand(user='77'),))
    assert transport.calls == [('123', '77')]


# ---------------------------------------------------------------- work failures

def test_missing_bookmark_count_increments_attempts_and_stays_pending(env):
    runner, transport, ledger, _ = make(env, [missing()])
    summary = runner.run_once((cand(),))
    assert summary.no_value == 1 and summary.filled == 0 and summary.stopped == ''
    entry = ledger.entry('123')
    assert (entry.state, entry.attempts) == ('pending', 1)
    assert ledger.verified_overlay('123', A) is None


def test_missing_bookmark_does_not_stop_next_work(env):
    ids = many_active(env[1], 2)
    runner, transport, ledger, _ = make(env, [missing(ids[0]), ok(ids[1])])
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.no_value == 1 and summary.filled == 1
    assert ledger.entry(ids[1]).state == 'done'


def test_http_404_counts_attempt_retries_once_per_run_then_gives_up(env):
    runner, transport, ledger, _ = make(env, [HTTPStatusRejected(404)] * 5)
    first = runner.run_once((cand(),))
    assert first.work_error == 1 and first.stopped == ''
    assert (ledger.entry('123').state, ledger.entry('123').attempts) == ('pending', 1)
    assert len(transport.calls) == 1

    runner.run_once((cand(),))
    assert len(transport.calls) == 2
    assert (ledger.entry('123').state, ledger.entry('123').attempts) == ('pending', 2)

    runner.run_once((cand(),))
    assert len(transport.calls) == 3
    assert (ledger.entry('123').state, ledger.entry('123').attempts) == ('gave_up', 3)

    for _ in range(2):
        summary = runner.run_once((cand(),))
        assert summary.skipped == 1 and summary.work_error == 0
    assert len(transport.calls) == 3  # no automatic retry after gave_up
    assert ledger.entry('123').state == 'gave_up'


def test_missing_bookmark_also_gives_up_after_three(env):
    runner, transport, ledger, _ = make(env, [missing()] * 5)
    for _ in range(5):
        runner.run_once((cand(),))
    assert len(transport.calls) == 3
    assert ledger.entry('123').state == 'gave_up'


def test_work_failure_then_success_on_retry(env):
    runner, transport, ledger, _ = make(env, [HTTPStatusRejected(404), ok(count=2)])
    runner.run_once((cand(),))
    summary = runner.run_once((cand(),))
    assert summary.filled == 1
    assert ledger.entry('123').state == 'done'
    assert ledger.verified_overlay('123', A)['bookmark_count'] == 2


# ---------------------------------------------------------------- global / unknown

@pytest.mark.parametrize('error', [
    HTTPStatusRejected(429), HTTPStatusRejected(401), HTTPStatusRejected(403),
    HTTPStatusRejected(500), HTTPStatusRejected(503),
    AuthenticationMissing(), ReadDeadlineExceeded(),
])
def test_global_failure_blocks_without_attempt_and_stops_run(env, error):
    runner, transport, ledger, _ = make(env, [error, ok('124')])
    summary = runner.run_once((cand('123'), cand('124')))
    assert summary.stopped == 'global_failure'
    assert summary.work_error == 0 and summary.filled == 0
    assert transport.calls == [('123', '91')]
    entry = ledger.entry('123')
    assert (entry.state, entry.attempts) == ('blocked', 0)
    assert ledger.entry('124') is None  # never offered
    # A blocked work is not hot-retried by the next run.
    again = runner.run_once((cand('123'),))
    assert again.skipped == 1 and len(transport.calls) == 1


def test_resume_blocked_allows_explicit_retry_without_consumed_attempt(env):
    runner, transport, ledger, _ = make(env, [HTTPStatusRejected(429), ok()])
    runner.run_once((cand(),))
    settings = ledger.settings()
    ledger.resume_blocked('123', expected_revision=settings.revision)
    assert ledger.entry('123').attempts == 0
    assert runner.run_once((cand(),)).filled == 1


def test_five_unknown_failures_stop_exactly_at_five_without_attempts(env):
    ids = many_active(env[1], 7)
    runner, transport, ledger, _ = make(env, [RuntimeError('x')] * 7)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stopped == 'five_consecutive_unknown_failures'
    assert len(transport.calls) == 5
    assert summary.work_error == 0 and summary.filled == 0
    for work_id in ids[:5]:
        entry = ledger.entry(work_id)
        assert (entry.state, entry.attempts) == ('blocked', 0)
    assert ledger.entry(ids[5]) is None and ledger.entry(ids[6]) is None


def test_four_unknowns_do_not_stop(env):
    ids = many_active(env[1], 4)
    runner, transport, ledger, _ = make(env, [RuntimeError('x')] * 4)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stopped == '' and len(transport.calls) == 4
    assert all(ledger.entry(i).attempts == 0 for i in ids)


def test_success_resets_unknown_streak(env):
    ids = many_active(env[1], 9)
    results = ([RuntimeError('x')] * 4 + [ok(ids[4])] + [RuntimeError('x')] * 4)
    runner, transport, ledger, _ = make(env, results)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stopped == '' and summary.filled == 1
    assert len(transport.calls) == 9


def test_work_failure_resets_unknown_streak(env):
    ids = many_active(env[1], 9)
    results = ([RuntimeError('x')] * 4 + [HTTPStatusRejected(404)] + [RuntimeError('x')] * 4)
    runner, transport, ledger, _ = make(env, results)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stopped == '' and summary.work_error == 1
    assert ledger.entry(ids[4]).attempts == 1


@pytest.mark.parametrize('bad', [
    'not-a-result',
    None,
    MetadataFetchResult('999', '91', {'bookmark_count': 1}, False),
    MetadataFetchResult('123', '92', {'bookmark_count': 1}, False),
    MetadataFetchResult('123', '91', None, False),
    MetadataFetchResult('123', '91', {'meta_source': 'illust_detail'}, False),
    MetadataFetchResult('123', '91', {'bookmark_count': -1}, False),
    MetadataFetchResult('123', '91', {'bookmark_count': True}, False),
    MetadataFetchResult('123', '91', {'bookmark_count': '3'}, False),
    MetadataFetchResult('123', '91', ['bookmark_count'], False),
])
def test_invalid_transport_result_blocks_without_attempt(env, bad):
    runner, transport, ledger, _ = make(env, [bad])
    summary = runner.run_once((cand(),))
    assert summary.filled == 0 and summary.work_error == 0 and summary.stopped == ''
    entry = ledger.entry('123')
    assert (entry.state, entry.attempts) == ('blocked', 0)
    assert ledger.verified_overlay('123', A) is None


def test_payload_invalid_exception_blocks_without_attempt(env):
    runner, _, ledger, _ = make(env, [MetadataPayloadInvalid('x')])
    runner.run_once((cand(),))
    entry = ledger.entry('123')
    assert (entry.state, entry.attempts) == ('blocked', 0)


def test_metadata_rejected_by_ledger_is_unknown_not_attempt(env):
    forbidden = ok(userIllusts={'a': 1})
    runner, _, ledger, _ = make(env, [forbidden])
    summary = runner.run_once((cand(),))
    assert summary.filled == 0
    assert ledger.entry('123').attempts == 0
    assert ledger.verified_overlay('123', A) is None


# ---------------------------------------------------------------- busy / cancel

def test_busy_without_wait_returns_download_busy_and_no_calls(env):
    runner, transport, ledger, _ = make(env, [ok()], busy=lambda: True)
    summary = runner.run_once((cand(),), wait_for_idle=False)
    assert summary.stopped == 'download_busy' and summary.target == 1
    assert transport.identity_calls == 0 and transport.calls == []
    assert ledger.entry('123') is None


def test_busy_waits_until_idle_then_runs(env):
    states = deque([True, True, False])
    runner, transport, ledger, _ = make(env, [ok()], busy=lambda: states.popleft() if states else False)
    summary = runner.run_once((cand(),), poll_seconds=0.01)
    assert summary.filled == 1 and summary.stopped == ''
    assert summary.skipped_busy == 1
    assert len(transport.calls) == 1


def test_busy_checked_before_each_fetch(env):
    ids = many_active(env[1], 2)
    seen = []
    runner, transport, ledger, _ = make(env, [ok(ids[0]), ok(ids[1])],
                                        busy=lambda: seen.append(1) or False)
    runner.run_once(tuple(cand(i) for i in ids))
    assert len(seen) == 3  # once before identity, once per work


def test_download_becomes_busy_between_works_pauses_before_next_get(env):
    ids = many_active(env[1], 2)
    state = {'busy': False}
    polls = deque([False, False, True, True, False])  # start, work1, work2(wait,wait)->idle
    runner, transport, ledger, _ = make(env, [ok(ids[0]), ok(ids[1])],
                                        busy=lambda: polls.popleft() if polls else False)
    summary = runner.run_once(tuple(cand(i) for i in ids), poll_seconds=0.01)
    assert summary.filled == 2 and summary.skipped_busy == 1
    assert state['busy'] is False


def test_busy_probe_exception_is_state_unavailable(env):
    def boom():
        raise RuntimeError('x')
    runner, transport, _, _ = make(env, [ok()], busy=boom)
    summary = runner.run_once((cand(),))
    assert summary.stopped == 'authentication_or_state_unavailable'
    assert transport.identity_calls == 0 and transport.calls == []


def test_cancel_while_waiting_busy_stops_without_any_call(env):
    stop = Event()
    polls = []

    def busy():
        polls.append(1)
        stop.set()
        return True
    runner, transport, ledger, _ = make(env, [ok()], busy=busy)
    summary = runner.run_once((cand(),), stop_event=stop, poll_seconds=0.01)
    assert summary.stopped == 'cancelled'
    assert transport.identity_calls == 0 and transport.calls == []
    assert ledger.entry('123') is None


def test_preset_stop_event_cancels_before_first_get(env):
    stop = Event()
    stop.set()
    runner, transport, ledger, _ = make(env, [ok()])
    summary = runner.run_once((cand(),), stop_event=stop)
    assert summary.stopped == 'cancelled' and summary.filled == 0
    assert transport.calls == [] and transport.identity_calls == 0
    assert ledger.entry('123') is None


def test_cancel_after_first_work_skips_the_rest(env):
    ids = many_active(env[1], 3)
    stop = Event()
    runner, transport, ledger, _ = make(env, [ok(i) for i in ids])
    transport.before_return = lambda work_id: stop.set()
    summary = runner.run_once(tuple(cand(i) for i in ids), stop_event=stop)
    assert summary.stopped == 'cancelled' and summary.filled == 1
    assert len(transport.calls) == 1
    assert ledger.entry(ids[0]).state == 'done'
    assert ledger.entry(ids[1]) is None


# ---------------------------------------------------------------- generation switch

def test_generation_switch_during_fetch_discards_without_overlay(env):
    runner, transport, ledger, active = make(env, [ok(count=4)])
    transport.before_return = lambda work_id: active.__setitem__('123', B)
    summary = runner.run_once((cand(),))
    assert summary.stale == 1 and summary.filled == 0 and summary.stopped == ''
    assert ledger.verified_overlay('123', A) is None
    assert ledger.verified_overlay('123', B) is None
    entry = ledger.entry('123')
    assert entry.base_receipt_sha256 == A
    assert (entry.state, entry.attempts) == ('unknown', 0)
    # Stale lease is retired: it can neither be claimed nor completed again.
    assert ledger.claim('123', A) is None


def test_generation_switch_then_next_run_backfills_new_generation(env):
    runner, transport, ledger, active = make(env, [ok(count=4), ok(count=8)])
    transport.before_return = lambda work_id: active.__setitem__('123', B)
    runner.run_once((cand(),))
    transport.before_return = None
    summary = runner.run_once((cand(),))
    assert summary.filled == 1
    entry = ledger.entry('123')
    assert entry.base_receipt_sha256 == B and entry.state == 'done'
    assert ledger.verified_overlay('123', B)['bookmark_count'] == 8
    assert ledger.verified_overlay('123', A) is None


def test_generation_switch_during_failed_fetch_is_stale_not_attempt(env):
    runner, transport, ledger, active = make(env, [HTTPStatusRejected(404)])
    transport.before_return = lambda work_id: active.__setitem__('123', B)
    summary = runner.run_once((cand(),))
    assert summary.stale == 1 and summary.work_error == 0
    entry = ledger.entry('123')
    assert (entry.base_receipt_sha256, entry.state, entry.attempts) == (A, 'unknown', 0)


def test_global_rate_limit_stops_even_if_generation_switches_during_fetch(env):
    ids = many_active(env[1], 2)
    runner, transport, ledger, active = make(env, [HTTPStatusRejected(429), ok(ids[1])])
    transport.before_return = lambda work_id: active.__setitem__(ids[0], B)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stopped == 'global_failure'
    assert summary.stale == 1
    assert transport.calls == [(ids[0], AUTHOR)]
    assert ledger.entry(ids[0]).attempts == 0


def test_per_work_busy_honors_no_wait_option(env, monkeypatch):
    busy = iter([False, True])
    runner, transport, ledger, _ = make(env, [ok()], busy=lambda: next(busy, True))

    def unexpected_sleep(_duration):
        raise AssertionError('wait_for_idle=False must not sleep')

    monkeypatch.setattr('pixivvault_nicegui.metadata_runner.time.sleep', unexpected_sleep)
    summary = runner.run_once((cand(),), wait_for_idle=False)
    assert summary.stopped == 'download_busy'
    assert transport.calls == []
    assert ledger.entry('123') is None


def test_generation_removed_during_fetch_is_stale(env):
    runner, transport, ledger, active = make(env, [ok()])
    transport.before_return = lambda work_id: active.pop('123')
    summary = runner.run_once((cand(),))
    assert summary.stale == 1 and summary.filled == 0
    assert ledger.verified_overlay('123', A) is None
    assert ledger.entry('123').state == 'unknown'


def test_stale_work_does_not_stop_following_work(env):
    ids = many_active(env[1], 2)
    runner, transport, ledger, active = make(env, [ok(ids[0]), ok(ids[1])])
    transport.before_return = lambda w: (
        active.__setitem__(ids[0], B) if w == ids[0] else None)
    summary = runner.run_once(tuple(cand(i) for i in ids))
    assert summary.stale == 1 and summary.filled == 1
    assert ledger.entry(ids[1]).state == 'done'


def test_disable_during_fetch_fences_result(env):
    runner, transport, ledger, active = make(env, [ok()])
    transport.before_return = lambda w: ledger.configure(
        enabled=False, expected_revision=ledger.settings().revision)
    summary = runner.run_once((cand(),))
    assert summary.filled == 0
    assert ledger.verified_overlay('123', A) is None
    assert ledger.entry('123').state == 'unknown'


def test_previous_generation_done_is_superseded_by_new_active_generation(env):
    runner, transport, ledger, active = make(env, [ok(count=1), ok(count=2)])
    runner.run_once((cand(),))
    active['123'] = B
    summary = runner.run_once((cand(),))
    assert summary.filled == 1
    assert ledger.verified_overlay('123', A) is None
    assert ledger.verified_overlay('123', B)['bookmark_count'] == 2
