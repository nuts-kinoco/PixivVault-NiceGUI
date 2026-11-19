"""FL-Z10: interactive requests are admitted ahead of background ones (original
rate_coordinator.py:27-82: slot reserved at call time, wait outside the lock, background
yields to a waiting interactive request for at most 2.0 s from its own call).
Generated clocks/threads only; no network."""
from threading import Event, Thread
import time

import pytest

from pixivvault_nicegui.request_policy import (
    MAX_BACKGROUND_DEFER_SECONDS, RequestAdmission, current_lane, interactive, interactive_lane,
    lane_scope, request_scope)
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


def test_lane_scope_explicit_background_overrides_enclosing_interactive_and_none_inherits():
    seen = []
    with interactive_lane():
        with lane_scope('background'):
            seen.append(current_lane())
        with lane_scope(None):
            seen.append(current_lane())
        seen.append(current_lane())
    with lane_scope('interactive'):
        seen.append(current_lane())
    with lane_scope(None):
        seen.append(current_lane())
    assert seen == ['background', 'interactive', 'interactive', 'interactive', 'background']


def test_background_keeps_the_minimum_interval_without_interactive():
    gate, clock = _gate()
    gate(1000.0)
    before = clock.now
    gate(1000.0)
    assert clock.now - before == pytest.approx(0.6)


def test_background_defers_exactly_the_cap_while_an_interactive_request_waits():
    gate, clock = _gate()
    gate(1000.0)
    clock.now = 10.0
    gate._interactive_waiting = 1          # an interactive request is still waiting for its slot
    before = clock.now
    gate(1000.0)
    deferred = clock.now - before
    assert MAX_BACKGROUND_DEFER_SECONDS <= deferred <= MAX_BACKGROUND_DEFER_SECONDS + 0.06


def test_interactive_is_never_deferred():
    gate, clock = _gate()
    gate(1000.0)
    clock.now = 10.0
    before = clock.now
    with interactive_lane():
        gate(1000.0)
    assert clock.now == before


def test_slots_are_reserved_at_call_time_so_a_later_background_cannot_overtake_an_earlier_one():
    gate, clock = _gate(0.3)
    first = gate._reserve(1000.0)          # (start, end, seconds)
    second = gate._reserve(1000.0)
    third = gate._reserve(1000.0)
    assert first[0] == 0 and second[0] == pytest.approx(0.3) and third[0] == pytest.approx(0.6)


def test_interactive_reserved_before_a_later_background_keeps_the_earlier_slot():
    gate, clock = _gate(0.3)
    gate(1000.0)
    clock.now = 0.05
    with interactive_lane():
        interactive_slot = gate._reserve(1000.0)      # reserved first
    background_slot = gate._reserve(1000.0)           # arrives later
    assert interactive_slot[0] < background_slot[0]


def test_slot_that_cannot_be_used_before_the_deadline_is_not_taken():
    gate, clock = _gate()
    gate(1000.0)                           # next free at 0.6
    with pytest.raises(ReadDeadlineExceeded):
        gate(clock.now + 0.1)
    assert gate._next == pytest.approx(0.6)
    before = clock.now
    gate(1000.0)
    assert clock.now - before == pytest.approx(0.6)


def test_cancel_while_waiting_gives_the_slot_back_when_nothing_is_queued_behind():
    gate, clock = _gate(0.6)
    gate(1000.0)
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) >= 4:                # cancelled during the slot wait
            raise RuntimeError('cancelled')
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(RuntimeError):
            gate(1000.0)
    assert gate._next == pytest.approx(0.6)


def test_cancel_during_interval_read_reserves_nothing():
    clock = _Clock()
    cancelled = []

    def interval():
        cancelled.append(True)
        return 0.6
    gate = RequestAdmission(interval, clock=clock, sleep=clock.sleep)

    def checkpoint():
        if cancelled:
            raise RuntimeError('cancelled')
    with request_scope(checkpoint=checkpoint):
        with pytest.raises(RuntimeError):
            gate(1000.0)
    assert gate._next is None


def test_interactive_counter_released_on_failure():
    gate, clock = _gate()
    gate(1000.0)
    with pytest.raises(ReadDeadlineExceeded):
        with interactive_lane():
            gate(clock.now + 0.1)
    assert gate._interactive_waiting == 0


def test_paused_checkpoint_does_not_hold_the_gate_lock():
    gate = RequestAdmission(lambda: 0.3)
    gate(time.monotonic() + 30)
    resume = Event()
    entered = Event()

    def checkpoint():
        entered.set()
        resume.wait(5)

    def paused_background():
        with request_scope(checkpoint=checkpoint):
            gate(time.monotonic() + 30)
    b = Thread(target=paused_background)
    b.start()
    assert entered.wait(5)
    started = time.monotonic()
    with interactive_lane():
        gate(time.monotonic() + 5)         # an unrelated screen request is not blocked by the paused job
    assert time.monotonic() - started < 1.0
    resume.set()
    b.join(5)
    assert not b.is_alive()


def test_real_threads_interactive_issues_before_a_background_that_arrived_after_it():
    gate = RequestAdmission(lambda: 0.3)
    gate(time.monotonic() + 30)            # occupy the gate
    issued = []

    def screen():
        with interactive_lane():
            gate(time.monotonic() + 30)
        issued.append('interactive')

    def background():
        gate(time.monotonic() + 30)
        issued.append('background')
    s = Thread(target=screen)
    s.start()
    time.sleep(0.05)
    b = Thread(target=background)
    b.start()
    s.join(5)
    b.join(5)
    assert issued == ['interactive', 'background']


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


def test_ranking_screen_fetch_runs_in_the_interactive_lane_but_tag_search_stays_background():
    from pixivvault_nicegui.ranking_source import RankingSource, RankingQuery
    seen = []

    class Reader:
        def require_identity(self):
            pass

        def get_ranking(self, **kwargs):
            seen.append(('ranking', current_lane()))
            return []

        def search_illusts(self, *a, **k):
            seen.append(('tag', current_lane()))
            return {'works': []}
    for query in (RankingQuery(), RankingQuery(tag='test')):
        try:
            RankingSource(Reader()).fetch(query)
        except Exception:
            pass                           # the response shape is irrelevant; only the lane at the GET matters
    assert seen == [('ranking', 'interactive'), ('tag', 'background')]


def test_background_yield_is_counted_from_the_call_even_when_the_entry_checkpoint_is_slow():
    clock = _Clock()
    gate = RequestAdmission(lambda: 0.3, clock=clock, sleep=clock.sleep)
    gate(1000.0)
    clock.now = 10.0
    gate._interactive_waiting = 1
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 1:
            clock.now += 2.5               # the entry checkpoint is slow (a pause)
    start = clock.now
    with request_scope(checkpoint=checkpoint):
        gate(1000.0)
    assert clock.now - start <= 2.5 + 0.35  # not another full cap after resuming


def test_real_read_client_default_lane_inherits_the_callers_interactive_lane():
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    import inspect
    assert inspect.signature(LivePixivReadClient.get_user_works).parameters['lane'].default is None
    assert inspect.signature(LivePixivReadClient.get_recommended_users).parameters['lane'].default is None


def test_follow_bookmark_csrf_home_get_runs_in_the_interactive_lane():
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient
    seen = []

    class Reader:
        def _get_once(self, *a, **k):
            seen.append(current_lane())
            return b''
    client = PixivWriteClient.__new__(PixivWriteClient)
    client._reader = Reader()
    try:
        client._csrf_token('1')
    except Exception:
        pass                               # the home page body is irrelevant; only the lane at the GET matters
    assert seen == ['interactive']


def test_real_spacing_is_kept_after_a_long_pause_inside_the_gate():
    clock = _Clock()
    gate = RequestAdmission(lambda: 0.3, clock=clock, sleep=clock.sleep)
    gate(1000.0)                           # issued at 0.0
    pause = {'n': 0}

    def checkpoint():
        pause['n'] += 1
        if pause['n'] == 3:                # request A pauses inside the gate until 0.55
            clock.now = 0.55
    with request_scope(checkpoint=checkpoint):
        gate(1000.0)
    a = clock.now
    gate(1000.0)                           # B was reserved for 0.6 but A really issued at 0.55
    b = clock.now
    assert b - a >= 0.3 - 1e-9


def test_background_keeps_yielding_when_an_interactive_request_queues_during_its_settings_read():
    clock = _Clock()
    state = {'reads': 0}
    gate = RequestAdmission(lambda: 0.3, clock=clock, sleep=clock.sleep)
    gate(1000.0)
    clock.now = 5.0

    def interval():
        state['reads'] += 1
        if state['reads'] == 1:
            gate._interactive_waiting = 1     # an interactive request queues while the background reads settings
        return 0.3
    gate.interval = interval
    start = clock.now
    gate(1000.0)
    assert clock.now - start >= MAX_BACKGROUND_DEFER_SECONDS - 0.06


def test_related_authors_explicit_lane_is_honoured_by_the_real_reader_signature():
    import inspect
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
    source = inspect.getsource(LivePixivReadClient.get_recommended_users)
    assert 'lane_scope(lane)' in source
