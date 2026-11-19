"""FL-Z10: interactive requests are admitted ahead of background ones (original
rate_coordinator.py:27-82). Generated clocks/threads only; no network."""
from threading import Thread
import time

import pytest

from pixivvault_nicegui.request_policy import (
    MAX_BACKGROUND_DEFER_SECONDS, RequestAdmission, current_lane, interactive, interactive_lane)
from pixivvault_nicegui.pixiv_read_client import ReadDeadlineExceeded


class _Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def _queued_interactive(gate):
    """Put a queued (not yet issued) interactive ticket in front, as another thread would."""
    ticket = [object(), 0.0, None]
    with gate._lock:
        gate._interactive.append(ticket)
    return ticket


def _gate(interval=0.6):
    clock = _Clock()
    return RequestAdmission(lambda: interval, clock=clock, sleep=clock.sleep), clock


def test_default_lane_is_background_and_context_restores():
    assert current_lane() == 'background'
    with interactive_lane():
        assert current_lane() == 'interactive'
        with interactive_lane():
            assert current_lane() == 'interactive'
    assert current_lane() == 'background'


def test_interactive_wrapper_runs_callable_in_interactive_lane():
    seen = []
    assert interactive(lambda x: (seen.append(current_lane()), x * 2)[1])(21) == 42
    assert seen == ['interactive'] and current_lane() == 'background'


def test_background_defers_exactly_the_cap_while_interactive_waits():
    gate, clock = _gate()
    gate(1000.0)                           # first slot at t=0 (next free at 0.6)
    clock.now = 10.0                       # long idle: no interval wait remains
    _queued_interactive(gate)              # an interactive request stays queued
    before = clock.now
    gate(1000.0)
    deferred = clock.now - before
    assert MAX_BACKGROUND_DEFER_SECONDS <= deferred <= MAX_BACKGROUND_DEFER_SECONDS + 0.06


def test_background_does_not_defer_without_interactive_and_keeps_interval():
    gate, clock = _gate()
    gate(1000.0)
    before = clock.now
    gate(1000.0)
    assert abs((clock.now - before) - 0.6) < 1e-9


def test_interactive_is_never_deferred_by_background_waiting():
    gate, clock = _gate()
    gate(1000.0)
    clock.now = 10.0
    before = clock.now
    with interactive_lane():
        gate(1000.0)
    assert clock.now == before            # slot free, no deferral, no extra wait


def test_late_wakeup_cannot_shrink_real_spacing():
    gate, clock = _gate(0.3)
    issued = []
    for late in (0.0, 0.25, 0.0):
        gate(1000.0)
        issued.append(clock.now)
        clock.now += late                  # the caller wakes up late
    assert all(b - a >= 0.3 - 1e-9 for a, b in zip(issued, issued[1:]))


def test_failed_wait_leaves_nothing_behind():
    gate, clock = _gate()
    gate(1000.0)
    with pytest.raises(ReadDeadlineExceeded):
        gate(clock.now + 0.1)              # needs 0.6 s -> deadline error
    assert gate._interactive == [] and gate._background == []
    before = clock.now
    gate(1000.0)                           # only the normal interval
    assert clock.now - before == pytest.approx(0.6)


def test_cancelled_earlier_request_does_not_make_later_ones_expire():
    gate, clock = _gate(0.6)
    gate(1000.0)
    for _ in range(2):
        with pytest.raises(ReadDeadlineExceeded):
            gate(clock.now + 0.2)
    gate(clock.now + 0.9)                  # a later request still gets the free slot


def test_interactive_counter_released_on_deadline_error():
    gate, clock = _gate()
    gate(1000.0)
    with pytest.raises(ReadDeadlineExceeded):
        with interactive_lane():
            gate(clock.now + 0.1)
    assert gate._interactive_waiting == 0 and gate._background == []


def test_real_threads_interactive_issues_before_waiting_background():
    gate = RequestAdmission(lambda: 0.3)
    gate(time.monotonic() + 30)            # occupy the gate: the next issue waits ~0.3 s
    issued = []

    def background():
        gate(time.monotonic() + 30)
        issued.append('background')

    def screen():
        with interactive_lane():
            gate(time.monotonic() + 30)
        issued.append('interactive')

    b = Thread(target=background); b.start()
    time.sleep(0.05)                       # background is already waiting for its slot
    s = Thread(target=screen); s.start()
    s.join(5); b.join(5)
    assert not s.is_alive() and not b.is_alive()
    assert issued == ['interactive', 'background']


def test_background_yielded_the_full_cap_goes_before_a_later_interactive_stream():
    gate, clock = _gate(0.3)
    gate(1000.0)
    clock.now = 10.0
    _queued_interactive(gate)              # an endless interactive stream is queued
    before = clock.now
    gate(1000.0)                           # background: yields the cap, then is served
    assert MAX_BACKGROUND_DEFER_SECONDS <= clock.now - before <= MAX_BACKGROUND_DEFER_SECONDS + 0.06


def test_lane_scope_and_reader_lane_kwarg_reach_the_gate():
    from pixivvault_nicegui.request_policy import lane_scope
    seen = []
    with lane_scope('background'):
        seen.append(current_lane())
    with lane_scope('interactive'):
        seen.append(current_lane())
    assert seen == ['background', 'interactive'] and current_lane() == 'background'


def test_discovery_reader_wrapper_honours_lane_kwarg():
    from pixivvault_nicegui.discovery_learning import _Reader
    seen = []

    class Target:
        def get_ranking(self, **kwargs):
            seen.append((kwargs, current_lane()))
            return []
        def get_user_works(self, *a, **k):
            seen.append((k, current_lane()))
            return []

    reader = _Reader(Target())
    reader.get_ranking(mode='day', lane='interactive')
    reader.get_user_works('1', lane='background')
    assert seen[0] == ({'mode': 'day'}, 'interactive')
    assert seen[1][1] == 'background' and 'lane' not in seen[1][0]


def test_cancel_during_interval_read_issues_nothing():
    from pixivvault_nicegui.request_policy import request_scope
    clock = _Clock()
    cancelled = []

    def interval():
        cancelled.append(True)             # the operation is cancelled while settings are read
        return 0.6
    gate = RequestAdmission(interval, clock=clock, sleep=clock.sleep)

    def checkpoint():
        if len(cancelled) >= 1:
            raise RuntimeError('cancelled')
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(RuntimeError):
            gate(1000.0)
    assert gate._last is None              # no slot consumed, nothing issued


def test_deadline_during_interval_read_does_not_consume_the_slot():
    clock = _Clock()

    def interval():
        clock.now += 0.15                  # settings read is slow
        return 0.6
    gate = RequestAdmission(interval, clock=clock, sleep=clock.sleep)
    with pytest.raises(ReadDeadlineExceeded):
        gate(clock.now + 0.07)
    assert gate._last is None
    gate(clock.now + 5.0)                  # a later request is not blocked by the failed one


def test_background_queued_behind_others_does_not_count_that_wait_as_yielding():
    gate, clock = _gate(1.5)
    gate(1000.0)
    heads = []
    for _ in range(3):
        heads.append([object(), clock.now, None])
    with gate._lock:
        gate._background.extend(heads)
    clock.now += 2.5                       # they waited a long time in the background queue
    _queued_interactive(gate)              # an interactive request arrives now
    with gate._lock:
        assert gate._my_turn(heads[0], False, clock.now) is False   # yields starts at its own head time
    assert heads[0][2] == clock.now


def test_explicit_background_lane_overrides_enclosing_interactive_lane():
    from pixivvault_nicegui.request_policy import lane_scope
    seen = []
    with interactive_lane():
        with lane_scope('background'):
            seen.append(current_lane())
        seen.append(current_lane())
    assert seen == ['background', 'interactive']
