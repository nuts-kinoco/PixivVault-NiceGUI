"""Dispatch cancellation and cache invalidation never loosen the unknown fence."""
import threading

import pytest

from pixivvault_nicegui.pixiv_write_client import (
    WriteHTTPResponse, WriteBusy, WriteOperationConflict,
)
from test_pixiv_write_client import writer, intent


def test_cancel_gate_runs_once_after_csrf_and_before_any_post(writer):
    client, transport, read, *_ = writer
    gates = []
    def cancel():
        assert read.calls.count('https://www.pixiv.net/') == 2
        gates.append('cancel')
        raise RuntimeError('synthetic cancellation')
    result = client.execute(intent('cancel-before-post'), before_dispatch=cancel)
    assert gates == ['cancel'] and transport.calls == []
    assert result.status == 'rejected' and result.reason == 'preflight_not_sent'
    assert client.confirmed_state('55', 'illust', '123') is None


def test_success_gate_is_once_and_closing_inside_gate_does_not_send(writer):
    client, transport, read, *_ = writer
    gates = []
    original = client._transport
    def after_gate(*args):
        assert gates == ['dispatch']
        return original(*args)
    client._transport = after_gate
    def permit():
        gates.append('dispatch')
    assert client.execute(intent('permitted'), before_dispatch=permit).confirmed
    assert gates == ['dispatch'] and len(transport.calls) == 1
    result = client.execute(intent('closed', target='124'), before_dispatch=client.close)
    assert result.requires_reconciliation and len(transport.calls) == 1


def test_async_gate_is_not_silently_skipped(writer):
    client, transport, read, *_ = writer
    entered = []
    async def incorrect_async_gate():
        entered.append(True)
    result = client.execute(intent('wrong-gate'), before_dispatch=incorrect_async_gate)
    assert result.status == 'rejected' and result.reason == 'preflight_not_sent'
    assert entered == [] and transport.calls == []


def test_invalidation_preserves_unknown_target_and_consumed_operation_ids(writer):
    client, transport, read, *_ = writer
    known = client.execute(intent('known'))
    assert known.confirmed
    assert client.invalidate_confirmed_state('55', 'illust', '123') is True
    assert client.confirmed_state('55', 'illust', '123') is None
    assert client.result('known') is known
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('known'))
    transport.answers = [TimeoutError('synthetic unknown')]
    uncertain = client.execute(intent('unknown'))
    assert uncertain.requires_reconciliation
    assert client.invalidate_confirmed_state('55', 'illust', '123') is False
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('new-id'))
    assert client.result('unknown') is uncertain and len(transport.calls) == 2


@pytest.mark.parametrize('phase', ['post', 'reconcile'])
def test_invalidation_cannot_cross_pending_or_reconciling_target(writer, phase):
    client, transport, read, *_ = writer
    entered, release = threading.Event(), threading.Event()
    answers, errors = [], []
    if phase == 'post':
        def delayed(*args):
            entered.set()
            assert release.wait(5)
            return WriteHTTPResponse(200, b'{"error":false,"body":{"last_bookmark_id":"888"}}')
        client._transport = delayed
        operation = lambda: client.execute(intent('pending'))
    else:
        transport.answers = [TimeoutError('synthetic unknown')]
        assert client.execute(intent('unknown')).requires_reconciliation
        def delayed(target, author):
            entered.set()
            assert release.wait(5)
            return {'id': target, 'userId': author, 'bookmarkData': None}
        client._reader.get_artwork_metadata_raw = delayed
        operation = lambda: client.reconcile_from_reader('55', 'illust', '123', author_id='91')
    def run():
        try:
            answers.append(operation())
        except BaseException as error:
            errors.append(type(error).__name__)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(WriteBusy):
            client.invalidate_confirmed_state('55', 'illust', '123')
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive() and not errors and len(answers) == 1
    assert client.confirmed_state('55', 'illust', '123') is (phase == 'post')
