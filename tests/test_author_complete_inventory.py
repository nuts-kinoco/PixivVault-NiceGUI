"""Strict author inventory requires every GET bucket and detail batch."""

from threading import RLock
from urllib.parse import parse_qs, urlsplit

import pytest

from pixivvault_nicegui.pixiv_read_client import InvalidPayload, LivePixivReadClient


def reader_with(respond):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '999'
    reader._identity_lock = RLock()
    requested = []

    def fake_json(kind, url, *, deadline):
        requested.append((kind, url))
        return respond(kind, url)

    reader._json = fake_json
    return reader, requested


def valid(kind, url):
    if kind == 'author_all':
        return {'illusts': {'100': None}, 'manga': {'101': None}}
    ids = parse_qs(urlsplit(url).query)['ids[]']
    return {'works': {wid: {'id': wid, 'userId': '12', 'title': '作品',
                             'userName': '作者', 'pageCount': 2, 'illustType': 0,
                             'updateDate': '2026-10-23'} for wid in ids}}


def test_complete_inventory_reuses_existing_get_routes_and_order():
    reader, requests = reader_with(valid)
    rows = reader.get_author_artworks_complete('12')
    assert [item['id'] for item in rows] == ['101', '100']
    assert [kind for kind, _ in requests] == ['author_all', 'author_details']
    assert requests[0][1].endswith('/ajax/user/12/profile/all')
    assert '/ajax/user/12/profile/illusts?' in requests[1][1]


@pytest.mark.parametrize('bad', [
    {'illusts': {}}, {'manga': {}}, {'illusts': None, 'manga': {}},
    {'illusts': {}, 'manga': None}, {'illusts': ['100'], 'manga': {}},
    {'illusts': {}, 'manga': False},
])
def test_partial_or_non_dict_inventory_rejected_before_detail_requests(bad):
    reader, requests = reader_with(lambda kind, url: bad)
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks_complete('12')
    assert [kind for kind, _ in requests] == ['author_all']


def test_explicit_empty_inventory_is_valid_and_legacy_tolerates_missing_bucket():
    reader, requests = reader_with(lambda kind, url: {'illusts': {}, 'manga': {}})
    assert reader.get_author_artworks_complete('12') == ()
    assert len(requests) == 1
    legacy, _ = reader_with(lambda kind, url: {'illusts': {}})
    assert legacy.get_author_artworks('12') == ()


@pytest.mark.parametrize('buckets', [
    {'illusts': [], 'manga': {}},
    {'illusts': {}, 'manga': []},
    {'illusts': [], 'manga': []},
])
def test_explicit_empty_list_bucket_proves_zero_works(buckets):
    reader, requests = reader_with(lambda kind, url: buckets)
    assert reader.get_author_artworks_complete('12') == ()
    assert [kind for kind, _ in requests] == ['author_all']


def test_legacy_reader_accepts_explicit_empty_list_bucket():
    legacy, requests = reader_with(lambda kind, url: {'illusts': [], 'manga': []})
    assert legacy.get_author_artworks('12') == ()
    assert [kind for kind, _ in requests] == ['author_all']


@pytest.mark.parametrize('buckets', [
    {'illusts': [None], 'manga': {}},
    {'illusts': {}, 'manga': ['101']},
])
def test_nonempty_list_bucket_does_not_prove_complete_inventory(buckets):
    reader, requests = reader_with(lambda kind, url: buckets)
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks_complete('12')
    assert [kind for kind, _ in requests] == ['author_all']


@pytest.mark.parametrize('failure', ['missing', 'masked', 'malformed'])
def test_any_incomplete_detail_aborts_entire_inventory(failure):
    def respond(kind, url):
        data = valid(kind, url)
        if kind == 'author_details':
            if failure == 'missing':
                data['works'].pop('100')
            elif failure == 'masked':
                data['works']['101']['isMasked'] = True
            else:
                data['works']['100']['pageCount'] = 0
        return data

    reader, _ = reader_with(respond)
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks_complete('12')


def test_complete_inventory_cannot_succeed_after_later_detail_page_failed():
    all_ids = [str(1000 + i) for i in range(49)]
    def respond(kind, url):
        if kind == 'author_all':
            return {'illusts': {wid: None for wid in all_ids}, 'manga': {}}
        if parse_qs(urlsplit(url).query).get('is_first_page') == ['0']:
            return {'works': {}}
        ids = parse_qs(urlsplit(url).query)['ids[]']
        return {'works': {wid: {'id': wid, 'userId': '12', 'title': '作品',
                                 'userName': '作者', 'pageCount': 1,
                                 'illustType': 0} for wid in ids}}

    reader, calls = reader_with(respond)
    with pytest.raises(InvalidPayload):
        reader.get_author_artworks_complete('12')
    assert [kind for kind, _ in calls] == ['author_all', 'author_details', 'author_details']
