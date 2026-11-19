import pytest
from pixivvault_nicegui.request_policy import RequestAdmission


def test_admission_uses_original_twenty_percent_interval():
    now = [0.0]
    waits = []
    bounds = []
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    def jitter(low, high):
        bounds.append((low, high))
        return high
    gate = RequestAdmission(lambda: 0.5, clock=lambda: now[0], sleep=sleep, jitter=jitter)
    gate(10)
    gate(10)
    assert bounds == [(0.4, 0.6), (0.4, 0.6)]
    assert sum(waits) == pytest.approx(0.6)   # the gate waits in short steps so pause/cancel stay responsive
