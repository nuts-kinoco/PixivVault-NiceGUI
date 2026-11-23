"""Review regressions for the real retry policy, with no request dispatch."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkGateStorageError
from pixivvault_nicegui.managed_network_stop_shared_scope import SharedGetOutcomeUncertain
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, ReadDeadlineExceeded
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.ui.request_status_view import retry_status_text


def make_policy(now, **kwargs):
    return RequestRetryPolicy(
        lambda: SimpleNamespace(retry_limit=3, rate_limit_wait_seconds=3),
        clock=lambda: now[0], **kwargs,
    )


def refuse(policy, failure, deadline):
    def operation():
        raise failure

    with pytest.raises(type(failure)) as caught:
        policy.execute(operation, deadline=deadline)
    assert caught.value is failure


def test_old_open_check_cannot_clear_reobserved_uncertainty():
    old_read_open = Event()
    allow_old_return = Event()
    blocked = True

    def check_gate():
        if blocked:
            raise SharedGetOutcomeUncertain()
        old_read_open.set()
        assert allow_old_return.wait(5)

    policy = make_policy([0.0], check_gate=check_gate)
    refuse(policy, SharedGetOutcomeUncertain(), deadline=1)
    previous = policy.progress_snapshot()

    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            blocked = False
            old_check = worker.submit(policy.recheck_gate_notices)
            assert old_read_open.wait(5)
            blocked = True
            policy.recheck_gate_notices()
            assert policy.progress_snapshot() == previous
        finally:
            allow_old_return.set()
        old_check.result(timeout=5)

    assert policy.progress_snapshot() == previous
    assert policy.has_gate_notices()
    blocked = False
    policy.recheck_gate_notices()
    assert policy.progress_snapshot() == previous
    policy.recheck_gate_notices()
    assert policy.progress_snapshot() == previous


@pytest.mark.parametrize(
    ("first_uncertain", "second_uncertain"),
    [(True, False), (False, True), (False, False)],
    ids=["uncertain-then-open", "open-then-uncertain", "both-open"],
)
def test_overlapping_requests_coalesce_into_two_serial_observations(first_uncertain, second_uncertain):
    first_started = Event()
    second_started = Event()
    let_first_return = Event()
    let_second_return = Event()
    confirmed_open = False
    calls = []

    def check_gate():
        if confirmed_open:
            return
        calls.append(True)
        first = len(calls) == 1
        (first_started if first else second_started).set()
        assert (let_first_return if first else let_second_return).wait(5)
        uncertain = first_uncertain if first else second_uncertain
        if uncertain:
            raise SharedGetOutcomeUncertain()

    policy = make_policy([0.0], check_gate=check_gate)
    refuse(policy, SharedGetOutcomeUncertain(), deadline=1)
    previous = policy.progress_snapshot()

    with ThreadPoolExecutor(max_workers=2) as worker:
        try:
            first = worker.submit(policy.recheck_gate_notices)
            assert first_started.wait(5)
            second = worker.submit(policy.recheck_gate_notices)
            second.result(timeout=5)
            assert not second_started.is_set() and calls == [True]
            let_first_return.set()
            assert second_started.wait(5)
            assert policy.progress_snapshot() == previous
        finally:
            let_first_return.set()
            let_second_return.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert calls == [True, True]
    if first_uncertain or second_uncertain:
        assert policy.progress_snapshot() == previous
        assert policy.has_gate_notices()
        confirmed_open = True
        policy.recheck_gate_notices()
        if second_uncertain:
            assert policy.progress_snapshot() == previous
            policy.recheck_gate_notices()
    assert policy.progress_snapshot() == previous


@pytest.mark.parametrize("error_type", [ManagedNetworkGateStorageError, SharedGetOutcomeUncertain])
def test_storage_stop_remains_past_timeout_even_when_observation_opens(error_type):
    now = [0.0]
    blocked = True

    def check_gate():
        if blocked:
            raise error_type()

    policy = make_policy(now, check_gate=check_gate)
    refuse(policy, error_type(), deadline=1)
    now[0] = 601.0
    assert policy.execute(lambda: "synthetic success", deadline=602) == "synthetic success"
    policy.recheck_gate_notices()
    record, = policy.progress_snapshot()
    assert record["phase"] == "stopped" and record["stop_cause"] == "gate_unverified"
    assert policy.has_gate_notices()

    blocked = False
    policy.recheck_gate_notices()
    assert policy.has_gate_notices()
    policy.recheck_gate_notices()
    assert policy.progress_snapshot() and policy.has_gate_notices()


def test_storage_stop_without_an_observer_cannot_expire():
    now = [0.0]
    policy = make_policy(now)
    refuse(policy, ManagedNetworkGateStorageError(), deadline=1)
    now[0] = 601.0
    record, = policy.progress_snapshot()
    assert record["phase"] == "stopped" and record["stop_cause"] == "gate_unverified"


def test_cancelled_notice_limit_keeps_uncertainty_and_its_gate_check():
    policy = make_policy([0.0], check_gate=lambda: None)
    refuse(policy, SharedGetOutcomeUncertain(), deadline=1)
    stop, = policy.progress_snapshot()
    for _ in range(40):
        refuse(policy, ReadDeadlineExceeded(), deadline=1)

    records = policy.progress_snapshot()
    assert stop in records and policy.has_gate_notices()
    assert sum(record["phase"] == "cancelled" for record in records) == 32
    policy.recheck_gate_notices()
    assert stop in policy.progress_snapshot()
    policy.recheck_gate_notices()
    assert len(policy.progress_snapshot()) == 33 and stop in policy.progress_snapshot()


def test_429_wait_and_stop_explain_the_action_without_internal_terms():
    now = [0.0]
    waiting_texts = []

    def sleep(seconds):
        waiting_texts.append(retry_status_text(policy.progress_snapshot()))
        now[0] += seconds

    policy = make_policy(now, sleep=sleep)
    refuse(policy, HTTPStatusRejected(429), deadline=10)
    stopped = retry_status_text(policy.progress_snapshot())

    assert len(waiting_texts) == 2
    for message in [*waiting_texts, stopped]:
        assert "STOP" not in message and "ポリシー" not in message
        assert "試行" in message and "429" in message
    assert all("初回を含む" in message and "再試行" in message for message in waiting_texts)
    assert "この操作自身が429の試行上限に達した" in stopped
    assert "起動し直した後" in stopped and "自動再送は行いません" in stopped
