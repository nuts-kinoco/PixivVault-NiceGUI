"""Offline metadata transport parsing/classification; synthetic bodies only."""
import copy

import pytest

from pixivvault_nicegui.metadata_policy import (
    FailureEvidence, MetadataFailureClass, classify_failure,
)
from pixivvault_nicegui.metadata_transport import (
    LivePixivMetadataTransport, MetadataFetchResult, MetadataPayloadInvalid,
    failure_evidence, parse_illust_detail,
)
from pixivvault_nicegui.pixiv_read_client import (
    ApiRejected, AuthenticationMissing, ClientClosed, CookieSourceRejected,
    DestinationRejected, HTTPStatusRejected, InvalidPayload, LivePixivReadClient,
    ReadDeadlineExceeded, ResponseLimitExceeded, ResponseRejected, TransportFailed,
)

WID, AUTHOR = '123', '91'


def body(**overrides):
    result = {
        'id': WID, 'userId': AUTHOR, 'illustTitle': 'synthetic', 'bookmarkCount': 7,
        'isMasked': False,
    }
    result.update(overrides)
    return result


def parse(b):
    return parse_illust_detail(WID, AUTHOR, b)


def nest(depth):
    value = {}
    for _ in range(depth):
        value = {'a': value}
    return value


# ---------------------------------------------------------------- identity

@pytest.mark.parametrize('body_id,body_user', [
    ('123', '91'), (123, 91), ('123', 91), (123, '91'),
])
def test_identity_accepts_matching_str_or_int(body_id, body_user):
    result = parse(body(id=body_id, userId=body_user))
    assert isinstance(result, MetadataFetchResult)
    assert (result.work_id, result.author_id) == (WID, AUTHOR)
    assert result.missing_bookmark_count is False


@pytest.mark.parametrize('overrides', [
    {'id': '124'}, {'id': 124}, {'userId': '92'}, {'userId': 92},
    {'id': None}, {'userId': None}, {'id': True}, {'userId': True},
    {'id': 123.0}, {'id': '0123'}, {'id': '0'}, {'id': 'abc'}, {'id': ''},
    {'userId': ''}, {'userId': '-91'}, {'id': ['123']}, {'userId': {'x': 1}},
    {'id': '1' * 21},
])
def test_identity_mismatch_or_malformed_is_invalid(overrides):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**overrides))


@pytest.mark.parametrize('missing', ['id', 'userId'])
def test_identity_keys_required(missing):
    b = body()
    del b[missing]
    with pytest.raises(MetadataPayloadInvalid):
        parse(b)


@pytest.mark.parametrize('arg', ['0', '01', 'x', '', None, True, 1.5])
def test_expected_ids_validated(arg):
    with pytest.raises(MetadataPayloadInvalid):
        parse_illust_detail(arg, AUTHOR, body())
    with pytest.raises(MetadataPayloadInvalid):
        parse_illust_detail(WID, arg, body())


@pytest.mark.parametrize('not_a_body', [None, [], [body()], 'text', 5, b'{}', ()])
def test_non_dict_body_rejected(not_a_body):
    with pytest.raises(MetadataPayloadInvalid):
        parse(not_a_body)


@pytest.mark.parametrize('masked', [True, None, 1, 0, 'false', []])
def test_masked_or_non_false_is_invalid(masked):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(isMasked=masked))


def test_masked_false_and_absent_are_accepted():
    assert parse(body(isMasked=False)).metadata is not None
    b = body()
    del b['isMasked']
    assert parse(b).metadata is not None


def test_error_messages_do_not_leak_body_or_ids():
    with pytest.raises(MetadataPayloadInvalid) as info:
        parse(body(id='999', illustTitle='SECRET-TITLE'))
    text = str(info.value)
    assert 'SECRET-TITLE' not in text and '999' not in text and WID not in text


# ---------------------------------------------------------------- bookmarkCount

def test_missing_bookmark_count_is_work_outcome_without_metadata():
    b = body()
    del b['bookmarkCount']
    result = parse(b)
    assert result == MetadataFetchResult(WID, AUTHOR, None, True)


def test_null_bookmark_count_is_work_outcome_without_metadata():
    result = parse(body(bookmarkCount=None))
    assert result.metadata is None and result.missing_bookmark_count is True
    assert (result.work_id, result.author_id) == (WID, AUTHOR)


def test_zero_bookmark_count_is_valid_value():
    result = parse(body(bookmarkCount=0))
    assert result.missing_bookmark_count is False
    assert result.metadata['bookmark_count'] == 0
    assert result.metadata['meta_source'] == 'illust_detail'


def test_missing_bookmark_still_checks_identity_first():
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(bookmarkCount=None, userId='92'))


@pytest.mark.parametrize('value', [True, False, '5', 5.0, -1, 2_147_483_648, [], {}])
def test_bookmark_count_wrong_type_or_range_is_invalid(value):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(bookmarkCount=value))


def test_bookmark_count_upper_bound_accepted():
    assert parse(body(bookmarkCount=2_147_483_647)).metadata['bookmark_count'] == 2_147_483_647


# ---------------------------------------------------------------- promoted fields

def test_xrestrict_zero_is_preserved():
    meta = parse(body(xRestrict=0)).metadata
    assert 'x_restrict' in meta and meta['x_restrict'] == 0


def test_null_xrestrict_is_omitted_not_zeroed():
    meta = parse(body(xRestrict=None)).metadata
    assert 'x_restrict' not in meta


def test_promoted_mapping_is_snake_case():
    meta = parse(body(
        bookmarkCount=3, likeCount=4, viewCount=5, commentCount=6, xRestrict=1,
        aiType=2, width=800, height=600, uploadDate='2026-01-02T03:04:05+00:00',
        userAccount='synthetic_user',
    )).metadata
    assert meta['bookmark_count'] == 3
    assert meta['like_count'] == 4
    assert meta['view_count'] == 5
    assert meta['comment_count'] == 6
    assert meta['x_restrict'] == 1
    assert meta['ai_type'] == 2
    assert meta['illust_width'] == 800
    assert meta['illust_height'] == 600
    assert meta['upload_date'] == '2026-01-02T03:04:05+00:00'
    assert meta['user_account'] == 'synthetic_user'
    assert meta['meta_source'] == 'illust_detail'
    assert set(meta) == {
        'bookmark_count', 'like_count', 'view_count', 'comment_count', 'x_restrict',
        'ai_type', 'illust_width', 'illust_height', 'upload_date', 'user_account',
        'meta_source', 'work_meta',
    }


def test_absent_promoted_fields_are_omitted():
    meta = parse(body()).metadata
    assert set(meta) == {'bookmark_count', 'meta_source', 'work_meta'}


def test_empty_string_fields_are_omitted():
    meta = parse(body(uploadDate='', userAccount='')).metadata
    assert 'upload_date' not in meta and 'user_account' not in meta


def test_fetched_timestamp_is_not_set_by_transport():
    assert 'meta_fetched_at' not in parse(body()).metadata


@pytest.mark.parametrize('key', [
    'likeCount', 'viewCount', 'commentCount', 'xRestrict', 'aiType', 'width', 'height',
])
@pytest.mark.parametrize('bad', [True, '3', 1.5, -1, 2_147_483_648, [1], {}])
def test_promoted_numeric_wrong_type_or_range_invalid(key, bad):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**{key: bad}))


@pytest.mark.parametrize('value', [3, 4, -2])
def test_xrestrict_out_of_enum_invalid(value):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(xRestrict=value))


@pytest.mark.parametrize('key', ['width', 'height'])
def test_zero_dimensions_invalid(key):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**{key: 0}))


@pytest.mark.parametrize('key,limit', [('uploadDate', 128), ('userAccount', 64)])
def test_string_length_bounds(key, limit):
    assert parse(body(**{key: 'x' * limit})).metadata
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**{key: 'x' * (limit + 1)}))


@pytest.mark.parametrize('key', ['uploadDate', 'userAccount'])
@pytest.mark.parametrize('bad', [5, True, ['x'], {'a': 1}, 'a\nb', 'a\x00b', 'a\x7fb', 'a\tb'])
def test_string_fields_type_and_control_chars_invalid(key, bad):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**{key: bad}))


# ---------------------------------------------------------------- work_meta

def test_work_meta_excludes_promoted_and_blob_keys():
    b = body(
        likeCount=1, viewCount=2, commentCount=3, xRestrict=0, aiType=1, width=10,
        height=20, uploadDate='d', userAccount='u',
        userIllusts={'1': {'title': 'blob'}}, zoneConfig={'ad': 1}, extraData={'meta': 1},
        title='kept', tags={'tags': [{'tag': 'a'}]}, pageCount=2,
    )
    work_meta = parse(b).metadata['work_meta']
    for gone in ('bookmarkCount', 'likeCount', 'viewCount', 'commentCount', 'xRestrict',
                 'aiType', 'width', 'height', 'uploadDate', 'userAccount',
                 'userIllusts', 'zoneConfig', 'extraData'):
        assert gone not in work_meta
    assert work_meta['title'] == 'kept'
    assert work_meta['pageCount'] == 2
    assert work_meta['tags'] == {'tags': [{'tag': 'a'}]}
    assert work_meta['id'] == WID and work_meta['userId'] == AUTHOR


def test_excluded_blobs_are_not_validated_so_cannot_break_parse():
    huge = {'x': 'y' * 9000}
    meta = parse(body(userIllusts=huge, zoneConfig=huge, extraData=huge)).metadata
    assert 'userIllusts' not in meta['work_meta']


def test_promoted_keys_with_null_are_also_not_in_work_meta():
    work_meta = parse(body(likeCount=None, width=None)).metadata['work_meta']
    assert 'likeCount' not in work_meta and 'width' not in work_meta


def test_parse_does_not_mutate_input_body():
    b = body(userIllusts={'a': 1}, title='t', tags=[{'x': 1}])
    before = copy.deepcopy(b)
    parse(b)
    assert b == before


def test_nested_work_meta_within_bounds_is_preserved():
    nested = nest(11)
    work_meta = parse(body(nested=nested)).metadata['work_meta']
    assert work_meta['nested'] == nested


@pytest.mark.parametrize('bad_extra', [
    {'nested': nest(12)},
    {'nested': nest(5000)},
    {'big': 'x' * 8193},
    {'lst': list(range(513))},
    {'obj': {str(i): i for i in range(257)}},
    {'bad': float('nan')},
    {'bad': float('inf')},
    {'bad': 2 ** 70},
    {'bad': 'a\ud800b'},
    {'bad': {'': 1}},
    {'bad': {'k\n': 1}},
    {'bad': {'k' * 129: 1}},
    {'bad': {1: 'non-str-key'}},
    {'bad': object()},
    {'bad': (1, 2)},
    {'bad': b'bytes'},
    {'bad': {'set': {1}}},
])
def test_invalid_work_meta_is_payload_invalid(bad_extra):
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**bad_extra))


def test_work_meta_too_many_top_level_keys_invalid():
    extra = {f'k{i}': i for i in range(260)}
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**extra))


def test_work_meta_oversize_total_payload_invalid():
    extra = {f'chunk{i}': 'z' * 8000 for i in range(10)}
    with pytest.raises(MetadataPayloadInvalid):
        parse(body(**extra))


def test_work_meta_at_limit_boundaries_accepted():
    ok = parse(body(big='x' * 8192, lst=list(range(512)))).metadata['work_meta']
    assert len(ok['big']) == 8192 and len(ok['lst']) == 512


# ---------------------------------------------------------------- failure_evidence

@pytest.mark.parametrize('status', [400, 404, 401, 403, 429, 500, 502, 503, 599, 301])
def test_http_status_preserved(status):
    assert failure_evidence(HTTPStatusRejected(status)) == FailureEvidence('http', status)


@pytest.mark.parametrize('exc,reason', [
    (AuthenticationMissing(), 'authentication'),
    (CookieSourceRejected(), 'authentication'),
    (ReadDeadlineExceeded(), 'timeout'),
    (TransportFailed(), 'transport'),
    (ClientClosed(), 'transport'),
    (ResponseRejected(), 'unknown'),
    (InvalidPayload(), 'unknown'),
    (ApiRejected(), 'unknown'),
    (DestinationRejected(), 'unknown'),
    (ResponseLimitExceeded(), 'unknown'),
    (MetadataPayloadInvalid('x'), 'unknown'),
    (RuntimeError('boom 404'), 'unknown'),
    (ValueError('HTTP 429'), 'unknown'),
    (KeyError('k'), 'unknown'),
])
def test_typed_failures_map_without_parsing_messages(exc, reason):
    assert failure_evidence(exc) == FailureEvidence(reason)


@pytest.mark.parametrize('status,klass,increment', [
    (404, MetadataFailureClass.WORK, True),
    (401, MetadataFailureClass.GLOBAL, False),
    (403, MetadataFailureClass.GLOBAL, False),
    (429, MetadataFailureClass.GLOBAL, False),
    (500, MetadataFailureClass.GLOBAL, False),
    (503, MetadataFailureClass.GLOBAL, False),
    (400, MetadataFailureClass.UNKNOWN, False),
    (410, MetadataFailureClass.UNKNOWN, False),
])
def test_http_failures_classify_through_policy(status, klass, increment):
    decision = classify_failure(failure_evidence(HTTPStatusRejected(status)))
    assert decision.classification == klass
    assert decision.increment_attempts is increment


@pytest.mark.parametrize('exc', [
    AuthenticationMissing(), CookieSourceRejected(), ReadDeadlineExceeded(),
    TransportFailed(), ClientClosed(),
])
def test_non_http_global_failures_do_not_consume_attempts(exc):
    decision = classify_failure(failure_evidence(exc))
    assert decision.classification == MetadataFailureClass.GLOBAL
    assert decision.stop_run and not decision.increment_attempts


def test_unknown_failure_is_unknown_without_attempts():
    decision = classify_failure(failure_evidence(RuntimeError('x')))
    assert decision.classification == MetadataFailureClass.UNKNOWN
    assert not decision.increment_attempts


# ---------------------------------------------------------------- live adapter

class FakeClient(LivePixivReadClient):
    """Subclass without cookies/network; bypasses the real constructor."""

    def __init__(self, raw=None, error=None):
        self.raw, self.error = raw, error
        self.identity_calls, self.raw_calls = 0, []

    def require_identity(self):
        self.identity_calls += 1
        return '555'

    def get_artwork_metadata_raw(self, work_id, expected_author_id):
        self.raw_calls.append((work_id, expected_author_id))
        if self.error is not None:
            raise self.error
        return self.raw


def test_live_transport_requires_real_client_type():
    class Duck:
        def require_identity(self):
            return '1'
    with pytest.raises(TypeError):
        LivePixivMetadataTransport(Duck())
    with pytest.raises(TypeError):
        LivePixivMetadataTransport(None)


def test_construction_performs_no_io():
    client = FakeClient(raw=body())
    LivePixivMetadataTransport(client)
    assert client.identity_calls == 0 and client.raw_calls == []


def test_live_transport_delegates_identity_and_parses_single_get():
    client = FakeClient(raw=body(bookmarkCount=0, xRestrict=0))
    transport = LivePixivMetadataTransport(client)
    assert transport.verify_identity() == '555'
    result = transport.fetch_detail(WID, AUTHOR)
    assert client.raw_calls == [(WID, AUTHOR)]
    assert result.metadata['bookmark_count'] == 0
    assert result.metadata['x_restrict'] == 0


def test_live_transport_missing_bookmark_count():
    client = FakeClient(raw=body(bookmarkCount=None))
    result = LivePixivMetadataTransport(client).fetch_detail(WID, AUTHOR)
    assert result.missing_bookmark_count and result.metadata is None


def test_live_transport_identity_mismatch_in_body_is_invalid():
    client = FakeClient(raw=body(id='999'))
    with pytest.raises(MetadataPayloadInvalid):
        LivePixivMetadataTransport(client).fetch_detail(WID, AUTHOR)


def test_live_transport_propagates_typed_client_errors_unchanged():
    err = HTTPStatusRejected(404)
    client = FakeClient(error=err)
    with pytest.raises(HTTPStatusRejected) as info:
        LivePixivMetadataTransport(client).fetch_detail(WID, AUTHOR)
    assert info.value is err and info.value.status_code == 404
    assert len(client.raw_calls) == 1  # no retry loop in the adapter
