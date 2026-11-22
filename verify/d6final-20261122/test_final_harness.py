"""Offline checks for the final paired ranking harness."""

import pytest

import live_gate
from live_gate import Ledger
from rank_adapters import RankingWire
from timing import DispatchObservation, StopRun


def test_startup_auth_tuple_latches_stop(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_network_stop_shared_transport import (
        SharedProfileStopTransport, _DraftAuthSharedTransport,
    )
    from pixivvault_nicegui.native_auth_result import NativeAuthResult
    from pixivvault_nicegui.native_startup import NativeProfileBinding
    from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient, _OwnedReadTransport

    result = NativeAuthResult('uncertain'), None
    monkeypatch.setattr(NativeProfileBinding, '_verify_auth_result', lambda *_: result)
    for owner, method in ((SharedProfileStopTransport, '__call__'),
                          (_DraftAuthSharedTransport, '__call__'),
                          (LivePixivReadClient, 'get_image_bytes'),
                          (_OwnedReadTransport, '__call__')):
        monkeypatch.setattr(owner, method, getattr(owner, method))
    wire = object.__new__(RankingWire)
    wire.ledger = Ledger.create(tmp_path)
    wire.install_nice()
    assert NativeProfileBinding._verify_auth_result(None) == result
    with pytest.raises(StopRun, match='^product_stop$'):
        wire.ledger.attempt('original', 'ranking', lambda: pytest.fail('sent after stop'))


def test_final_cap_is_85_gets_above_prior_count(tmp_path, monkeypatch):
    now = 100.0

    def advance(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(live_gate, 'perf_counter', lambda: now)
    monkeypatch.setattr(live_gate, 'sleep', advance)
    ledger = Ledger.create(tmp_path, prior_attempts=83)
    for _ in range(85):
        ledger.attempt('nice', 'ranking', lambda: (200, False, None))
    with pytest.raises(StopRun, match='^budget$'):
        ledger.attempt('original', 'ranking', lambda: pytest.fail('sent past cap'))
    assert ledger.snapshot()['count'] == 168


def test_unexpected_server_error_is_latched_with_status(tmp_path):
    ledger = Ledger.create(tmp_path)
    with pytest.raises(StopRun, match='^http_5xx$'):
        ledger.attempt('nice', 'ranking', lambda: (503, False, None))
    with pytest.raises(StopRun, match='^http_5xx$'):
        ledger.attempt('original', 'ranking', lambda: pytest.fail('sent after 503'))
    assert [event['status'] for event in ledger.snapshot()['events']] == [503]


def test_unexpected_success_status_stops_before_further_get():
    with pytest.raises(StopRun, match='^transport$'):
        DispatchObservation(204).validate()


def test_non_image_200_cannot_keep_the_wire_open():
    wire = object.__new__(RankingWire)
    wire.side = 'nice'
    with pytest.raises(StopRun, match='^transport$'):
        wire.inspect('image', 200, b'\xef\xbb\xbf <!--placeholder--> <html>action required</html>')


def test_original_holds_product_request_during_transport(tmp_path, monkeypatch):
    from contextlib import nullcontext
    import sys
    from types import SimpleNamespace

    sequence = []
    wire = object.__new__(RankingWire)
    wire.ledger = Ledger.create(tmp_path)
    wire.side, wire.fake, wire.image = 'original', True, None
    wire.fence = type('Fence', (), {'admitted': lambda *_: nullcontext()})()
    wire.inspect = lambda *_: (200, False, None)

    def fake_response(*args, **kwargs):
        assert sequence == ['gate entered']
        sequence.append('sent')
        return SimpleNamespace(status_code=200, iter_content=lambda **_: [b'{}'],
                               content=b'{}', close=lambda: None)

    def product_request(_url, dispatch):
        sequence.append('gate entered')
        reply = dispatch()
        sequence.append('gate left')
        return reply

    monkeypatch.setattr('fake_wire.original_response', fake_response)
    adapter = type('Adapter', (), {'send': fake_response})
    requests = SimpleNamespace(sessions=SimpleNamespace(Session=type('Session', (), {})),
                               adapters=SimpleNamespace(HTTPAdapter=adapter))
    monkeypatch.setitem(sys.modules, 'requests', requests)
    wire.install_original(product_request)
    request = SimpleNamespace(url='https://www.pixiv.net/', method='GET', body=None,
                              headers={'Cookie': 'fixture'})
    assert adapter().send(request).status_code == 200
    assert sequence == ['gate entered', 'sent', 'gate left']
