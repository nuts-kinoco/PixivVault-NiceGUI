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
    gate._interactive_waiting = 1          # an interactive request stays queued
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


def test_slot_is_reserved_atomically_so_later_interactive_cannot_overtake():
    gate, clock = _gate()
    first = gate._reserve(1000.0)          # (wait, previous, reserved)
    second = gate._reserve(1000.0)
    assert first[0] == 0 and second[0] == pytest.approx(0.6)
    third = gate._reserve(1000.0)          # a later request (any lane) queues behind both
    assert third[0] == pytest.approx(1.2)


def test_failed_wait_gives_the_slot_back():
    gate, clock = _gate()
    gate(1000.0)                           # next free at 0.6
    with pytest.raises(ReadDeadlineExceeded):
        gate(clock.now + 0.1)              # needs 0.6 s -> deadline error
    assert gate._next == pytest.approx(0.6)
    before = clock.now
    gate(1000.0)                           # still only the normal interval
    assert clock.now - before == pytest.approx(0.6)


def test_interactive_counter_released_on_deadline_error():
    gate, clock = _gate()
    gate(1000.0)
    with pytest.raises(ReadDeadlineExceeded):
        with interactive_lane():
            gate(clock.now + 0.1)
    assert gate._interactive_waiting == 0


def test_real_threads_interactive_issues_before_deferred_background():
    gate = RequestAdmission(lambda: 0.3)
    gate(time.monotonic() + 30)
    issued = []
    gate._interactive_waiting = 1          # an interactive request is queued

    def background():
        gate(time.monotonic() + 30)
        issued.append('background')

    def screen():
        with interactive_lane():
            gate(time.monotonic() + 30)
        issued.append('interactive')

    b = Thread(target=background); b.start()
    time.sleep(0.15)                       # background is now deferring
    s = Thread(target=screen); s.start(); s.join(5)
    gate._interactive_waiting -= 1         # the queued interactive request is served
    b.join(5)
    assert not s.is_alive() and not b.is_alive()
    assert issued == ['interactive', 'background']
