from urllib.parse import parse_qs, urlsplit

import pytest

from pixivvault_nicegui.pixiv_read_client import (
    InvalidPayload, LivePixivReadClient, MAX_AUTHOR_WORKS,
    ResponseLimitExceeded, _validate_destination, DestinationRejected,
)


def fake_reader(work_count=50, *, bad=None):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '999'
    ids = [str(1000 + index) for index in range(work_count)]
    requests = []

    def response(kind, url, *, deadline):
        _validate_destination(url, kind)
        requests.append((kind, url))
        if kind == 'author_all':
            return {'illusts': {wid: None for wid in ids[::2]},
                    'manga': {wid: None for wid in ids[1::2]}}
        query = parse_qs(urlsplit(url).query)
        chunk = query['ids[]']
        rows = {wid: {'id': wid, 'userId': '123', 'title': 'Title',
                      'userName': 'Name', 'illustType': 0, 'pageCount': 2,
                      'createDate': '2025', 'updateDate': '2026'} for wid in chunk}
        if bad == 'missing':
            rows.pop(chunk[0])
        if bad == 'author':
            rows[chunk[0]]['userId'] = '777'
        return {'works': rows}

    reader._json = response
    return reader, requests


def test_author_details_paged_and_newest_first():
    reader, requests = fake_reader()
    rows = reader.get_author_artworks('123')
    assert len(rows) == 50
    assert rows[0]['id'] == '1049'
    assert rows[-1]['id'] == '1000'
    assert all(row['update_date'] == '2026' for row in rows)
    assert [kind for kind, _ in requests] == ['author_all', 'author_details', 'author_details']
    assert len(parse_qs(urlsplit(requests[1][1]).query)['ids[]']) == 48
    assert len(parse_qs(urlsplit(requests[2][1]).query)['ids[]']) == 2


@pytest.mark.parametrize('bad', ['missing', 'author'])
def test_incomplete_or_cross_author_payload_fails_closed(bad):
    reader, _ = fake_reader(bad=bad)
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks('123')


def test_invalid_author_rejected_before_request():
    reader, calls = fake_reader()
    with pytest.raises(DestinationRejected):
        reader.get_author_artworks('0')
    assert calls == []


def test_author_size_cap():
    reader, _ = fake_reader(MAX_AUTHOR_WORKS + 1)
    with pytest.raises(ResponseLimitExceeded):
        reader.get_author_artworks('123')


@pytest.mark.parametrize('invalid', [[], 0, '', False])
def test_malformed_empty_author_bucket_is_not_zero_works(invalid):
    reader, _ = fake_reader()
    reader._json = lambda kind, url, *, deadline: {
        'illusts': invalid, 'manga': {},
    }
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks('123')


def test_empty_update_date_remains_unknown_not_current():
    reader, _ = fake_reader(1)
    original = reader._json
    def without_update(kind, url, *, deadline):
        body = original(kind, url, deadline=deadline)
        if kind == 'author_details':
            next(iter(body['works'].values())).pop('updateDate')
        return body
    reader._json = without_update
    from pixivvault_nicegui.author_download import decide_difference, Difference
    entry, = reader.get_author_artworks('123')
    assert entry['update_date'] == ''
    assert decide_difference(saved=True, remote_update_date=entry['update_date'],
                             saved_update_date='2025', files_present=True) is Difference.UNKNOWN
