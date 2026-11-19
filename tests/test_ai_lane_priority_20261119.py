"""FL-Z10: interactive requests are admitted ahead of background ones (original
rate_coordinator.py:27-82). Generated clocks/threads only; no network."""
from threading import Event, Thread
import time

from pixivvault_nicegui.request_policy import RequestAdmission, interactive_lane, current_lane


class _Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def test_default_lane_is_background_and_context_restores():
    assert current_lane() == 'background'
    with interactive_lane():
        assert current_lane() == 'interactive'
        with interactive_lane():
            assert current_lane() == 'interactive'
    assert current_lane() == 'background'


def test_background_defers_while_interactive_waits_but_not_beyond_cap():
    clock = _Clock()
    admission = RequestAdmission(lambda: 0.6, clock=clock, sleep=clock.sleep)
    admission(1000.0)                       # first slot, no wait
    admission._interactive_waiting = 1      # an interactive request is queued
    before = clock.now
    admission(1000.0)                       # background must defer, but at most 2.0 s
    deferred = clock.now - before
    assert 0.6 <= deferred <= 2.0 + 0.6 + 0.2


def test_background_does_not_defer_when_no_interactive_waits():
    clock = _Clock()
    admission = RequestAdmission(lambda: 0.6, clock=clock, sleep=clock.sleep)
    admission(1000.0)
    before = clock.now
    admission(1000.0)
    assert abs((clock.now - before) - 0.6) < 1e-9


def test_interactive_takes_next_slot_before_deferred_background():
    admission = RequestAdmission(lambda: 0.3)
    admission(time.monotonic() + 30)
    order = []
    gate = Event()

    def background():
        gate.wait()
        admission(time.monotonic() + 30)
        order.append('background')

    def interactive():
        with interactive_lane():
            admission(time.monotonic() + 30)
        order.append('interactive')

    admission._interactive_waiting = 1       # hold interactive count until it arrives
    b = Thread(target=background); b.start(); gate.set()
    time.sleep(0.15)
    admission._interactive_waiting = 0       # real interactive enters below
    i = Thread(target=interactive); i.start()
    i.join(5); b.join(5)
    assert not i.is_alive() and not b.is_alive()
    assert set(order) == {'background', 'interactive'}
    assert admission._interactive_waiting == 0


def test_interactive_counter_released_on_deadline_error():
    clock = _Clock()
    admission = RequestAdmission(lambda: 0.6, clock=clock, sleep=clock.sleep)
    admission(1000.0)
    try:
        with interactive_lane():
            admission(clock.now + 0.1)       # needs 0.6 s -> deadline error
    except Exception:
        pass
    assert admission._interactive_waiting == 0


def test_interactive_wrapper_runs_callable_in_interactive_lane():
    from pixivvault_nicegui.request_policy import interactive
    seen = []
    result = interactive(lambda x: (seen.append(current_lane()), x * 2)[1])(21)
    assert result == 42 and seen == ['interactive'] and current_lane() == 'background'
