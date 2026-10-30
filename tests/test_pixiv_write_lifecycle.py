"""Writer lifecycle regressions; no external API or real account data."""
import io
import http.client
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pixivvault_nicegui import pixiv_write_client as mod
from pixivvault_nicegui.pixiv_write_client import WriteHTTPResponse, WriteOperationConflict
from test_pixiv_write_client import writer, intent


@pytest.mark.parametrize('status', [200, 400])
def test_default_response_drip_obeys_absolute_deadline_and_closes_alias(monkeypatch, status):
    clock = [0.0]
    prefix = (f'HTTP/1.1 {status} Synthetic\r\nContent-Length: 32\r\n\r\n').encode()

    class Drip(io.RawIOBase):
        def __init__(self):
            super().__init__()
            self.prefix = bytearray(prefix)
            self.body_reads = 0
        def readable(self):
            return True
        def readinto(self, target):
            if self.prefix:
                count = min(len(target), len(self.prefix))
                target[:count] = self.prefix[:count]
                del self.prefix[:count]
                return count
            if self.body_reads == 32:
                return 0
            self.body_reads += 1
            clock[0] += .25
            target[0] = ord('x')
            return 1

    raw = Drip()
    class Socket:
        def makefile(self, *args, **kwargs):
            return io.BufferedReader(raw)
        def settimeout(self, value):
            pass
    sock = Socket()
    raw._sock = sock
    response = http.client.HTTPResponse(sock)
    response.begin()
    request = Request('https://www.pixiv.net/bookmark_add.php', data=b'synthetic', method='POST')
    opens = []
    class Opener:
        def open(self, received, timeout):
            opens.append(received)
            assert received is request
            if status == 400:
                raise HTTPError(request.full_url, status, 'Synthetic', response.headers, response)
            return response
    monkeypatch.setattr(mod, 'build_opener', lambda *handlers: Opener())
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    with pytest.raises(TimeoutError):
        mod._default_write_transport(request, 1.0, 128, 1.0)
    assert raw.body_reads <= 5, 'whole-body read overran the absolute deadline'
    assert raw.closed, 'the HTTPResponse buffering alias must be closed'
    assert opens == [request]


@pytest.mark.parametrize(('kind','target','add','remove'), [
    ('user', '91', 'follow', 'unfollow'),
    ('illust', '123', 'bookmark_add', 'bookmark_delete'),
])
def test_unknown_removal_invalidates_prior_confirmed_state_until_reconcile(writer, kind, target, add, remove):
    client, transport, read, *_ = writer
    transport.answers = [
        WriteHTTPResponse(200, b'{"error":false,"body":{"last_bookmark_id":"888"}}'),
        TimeoutError('synthetic unknown outcome'),
    ]
    original = client.execute(intent('known-present', action=add, target=target))
    assert original.confirmed and client.confirmed_state('55', kind, target) is True
    uncertain = client.execute(intent('uncertain-remove', action=remove, target=target))
    assert uncertain.requires_reconciliation
    assert client.confirmed_state('55', kind, target) is None
    assert client.result('known-present') is original
    with pytest.raises(WriteOperationConflict):
        client.execute(intent('must-not-retry', action=remove, target=target))
    assert len(transport.calls) == 2
    if kind == 'illust':
        read.by_work[target] = {'id': target, 'userId': '91', 'bookmarkData': None}
    assert client.reconcile_from_reader('55', kind, target, author_id='91')
    assert client.confirmed_state('55', kind, target) is False
    assert client.result('uncertain-remove').requires_reconciliation
    assert len(transport.calls) == 2


def test_response_alias_close_failure_poison_is_sticky_and_reader_is_borrowed(writer, monkeypatch):
    from email.message import Message
    from pixivvault_nicegui.pixiv_write_client import PixivWriteClient
    reader = writer[-1]
    client = PixivWriteClient(reader)
    opens = []
    class Response:
        headers = Message()
        sent = False
        def getcode(self):
            return 200
        def read1(self, size):
            if self.sent:
                return b''
            self.sent = True
            return b'{"error":false,"body":{"last_bookmark_id":"888"}}'
        def close(self):
            raise OSError('synthetic response-alias close failure')
    class Opener:
        def open(self, request, timeout):
            opens.append(request)
            return Response()
    monkeypatch.setattr(mod, 'build_opener', lambda *handlers: Opener())
    try:
        outcome = client.execute(intent('alias-close-failed'))
        assert outcome.requires_reconciliation and not outcome.confirmed
        assert client.shutdown_unverified
        assert client._owned_transport.active_count == 0
        assert client.confirmed_state('55', 'illust', '123') is None
        later = client.execute(intent('new-target', target='124'))
        assert later.requires_reconciliation and len(opens) == 1
        client.close()
        assert client.shutdown_unverified and reader.require_identity() == '55'
    finally:
        client.close()
