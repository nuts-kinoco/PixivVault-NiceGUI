from threading import RLock

import pytest

from pixivvault_nicegui.pixiv_read_client import (
    AuthenticationMissing, InvalidPayload, LivePixivReadClient,
)


def client(row):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '99'
    reader._identity_lock = RLock()
    reader._json = lambda kind, url, *, deadline: {'works': {'321': row}}
    return reader


def fixture():
    return {'id': '321', 'userId': '24', 'illustType': 1,
            'pageCount': 3, 'updateDate': '2026-10-23T13:30:00+09:00'}


def test_author_revision_validated_and_fixed():
    assert client(fixture()).get_artwork_update_revision('321', '24') == {
        'work_id': '321', 'author_id': '24', 'illustration_type': 1,
        'page_count': 3, 'update_date': '2026-10-23T13:30:00+09:00'}


@pytest.mark.parametrize('field,value', [
    ('id', '322'), ('userId', '0'), ('illustType', 2), ('illustType', True),
    ('pageCount', 0), ('pageCount', True), ('updateDate', None),
    ('updateDate', 0), ('updateDate', 'A\nB'),
])
def test_revision_rejects_missing_wrong_or_unavailable(field, value):
    raw = fixture()
    raw[field] = value
    with pytest.raises(InvalidPayload):
        client(raw).get_artwork_update_revision('321', '24')


def test_revision_requires_prior_authentication():
    reader = client(fixture())
    reader._verified_identity = None
    with pytest.raises(AuthenticationMissing):
        reader.get_artwork_update_revision('321', '24')


def test_missing_update_date_is_explicit_unknown_marker_for_new_work():
    raw = fixture()
    raw.pop('updateDate')
    assert client(raw).get_artwork_update_revision('321', '24')['update_date'] == ''
