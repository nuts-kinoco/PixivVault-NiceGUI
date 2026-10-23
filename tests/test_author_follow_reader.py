from urllib.parse import parse_qs, urlsplit
from threading import RLock

import pytest

from pixivvault_nicegui.pixiv_read_client import (
    LivePixivReadClient, DestinationRejected, InvalidPayload,
    _validate_destination,
)


_NORMAL = object()


def mock_reader(*, malformed=_NORMAL):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '999'
    reader._identity_lock = RLock()
    seen = []

    def respond(kind, url, *, deadline):
        _validate_destination(url, kind)
        seen.append(url)
        params = parse_qs(urlsplit(url).query)
        offset = int(params['offset'][0])
        if malformed is not _NORMAL:
            return {'users': malformed}
        if offset == 0:
            return {'users': [{'userId': str(i + 100), 'userName': '作者'} for i in range(24)]}
        if offset == 24:
            return {'users': [{'userId': '200', 'userName': '作者'}]}
        return {'users': []}

    reader._json = respond
    return reader, seen


def test_following_paginated_and_identity_restricted():
    reader, urls = mock_reader()
    authors = reader.get_following_users('999')
    assert len(authors) == 25
    assert authors[-1]['user_id'] == '200'
    assert len(urls) == 3
    assert all('/following?' in url for url in urls)
    with pytest.raises(Exception):
        reader.get_following_users('123')
    assert len(urls) == 3


@pytest.mark.parametrize('payload', [{}, None, 'bad', 0, [{'userId': '0', 'userName': 'A'}],
                                     [{'userId': '123', 'userName': 42}]])
def test_bad_following_payload_fails_closed(payload):
    reader, _ = mock_reader(malformed=payload)
    with pytest.raises(InvalidPayload):
        reader.get_following_users('999')


def test_following_query_allowlist():
    valid = 'https://www.pixiv.net/ajax/user/999/following?offset=0&limit=24&rest=show&lang=ja'
    _validate_destination(valid, 'following')
    for tampered in ('limit=25', 'rest=all', 'lang=en', 'offset=-1'):
        with pytest.raises(DestinationRejected):
            _validate_destination(valid.replace(tampered.split('=')[0] + '=' +
                {'limit':'24', 'rest':'show', 'lang':'ja', 'offset':'0'}[tampered.split('=')[0]], tampered),
                'following')
