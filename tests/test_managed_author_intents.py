"""v2 author intents coexist with byte-for-byte legacy v1 accepted requests."""
from dataclasses import replace
import hashlib
import json

import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_requests import (
    ManagedAuthorIntent, ManagedDownloadRequests, ManagedRequestError,
)
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads


def record():
    return RemoteBookmark('123', 'illustration', 'show', 'Synthetic', '456',
                          'Author', ('tag',), 'https://i.pximg.net/fake', '2026', 2, 0, 0)


def store(tmp_path):
    root = tmp_path / 'managed'
    ManagedPixivDownloads(root, None, resolve_record=lambda _: record()).close()
    return root, ManagedDownloadRequests(root)


def test_v1_and_author_v2_intents_survive_restart_without_urls(tmp_path):
    root, requests = store(tmp_path)
    old = record()
    current = ManagedAuthorIntent(old, 'follow', '2026-10-23T12:00', None, 'new')
    requests.store('v1', old)
    requests.store('v2', current)
    old_path = root / 'requests' / (hashlib.sha256(b'v1').hexdigest() + '.json')
    v1_bytes = old_path.read_bytes()
    newer = ManagedDownloadRequests(root)
    assert type(newer.load('v1')) is RemoteBookmark
    result = newer.load('v2')
    assert isinstance(result, ManagedAuthorIntent)
    assert result.work_id == '123' and result.title == 'Synthetic'
    assert result.origin == 'follow' and result.update_date == '2026-10-23T12:00'
    assert newer.equivalent(result, current)
    assert not newer.equivalent(result, old)
    assert old_path.read_bytes() == v1_bytes
    assert b'pximg' not in b''.join(p.read_bytes() for p in (root / 'requests').iterdir())


def test_update_intent_requires_pinned_generation_and_is_immutable(tmp_path):
    root, requests = store(tmp_path)
    digest = 'b' * 64
    prior = 'generations/work-123-' + 'a' * 32 + '/receipt.json'
    intent = ManagedAuthorIntent(record(), 'id', '2026-10-24', digest, 'updated', prior)
    requests.store('v2', intent)
    assert requests.load('v2') == replace(intent, record=replace(record(), thumbnail=''))
    with pytest.raises(ManagedRequestError, match='collision'):
        requests.store('v2', replace(intent, update_date='2026-10-25'))
    with pytest.raises(ManagedRequestError):
        requests.store('bad', replace(intent, expected_receipt_sha256='0'))
    with pytest.raises(ManagedRequestError):
        requests.store('bad', replace(intent, mode='new'))
    with pytest.raises(ManagedRequestError):
        requests.store('bad', replace(intent, mode='updated', expected_receipt_sha256=None))
    with pytest.raises(ManagedRequestError):
        requests.store('bad', replace(intent, expected_relative_receipt=None))


def test_malformed_v2_fails_closed_without_breaking_v1(tmp_path):
    root, requests = store(tmp_path)
    requests.store('legacy', record())
    requests.store('v2', ManagedAuthorIntent(record(), 'id', '2026-10-23', None, 'new'))
    path = root / 'requests' / (hashlib.sha256(b'v2').hexdigest() + '.json')
    valid = json.loads(path.read_text())
    for changed in ({**valid, 'source': 'bookmark'},
                    {**valid, 'version': 1},
                    {**valid, 'version': 2.0},
                    {**valid, 'version': True},
                    {**valid, 'unexpected': True},
                    {**valid, 'origin': 'unknown'},
                    {**valid, 'record': {**valid['record'], 'page_count': True}}):
        path.write_text(json.dumps(changed))
        with pytest.raises(ManagedRequestError):
            requests.load('v2')
        assert requests.load('legacy').remote_id == '123'


def test_v2_new_accepts_unknown_date_without_fabricating_one(tmp_path):
    root, requests = store(tmp_path)
    selection = ManagedAuthorIntent(record(), 'id', '', None, 'new')
    requests.store('undated', selection)
    assert requests.load('undated').update_date == ''
    with pytest.raises(ManagedRequestError):
        requests.store('invalid-updated', ManagedAuthorIntent(
            record(), 'id', '', 'a' * 64, 'updated',
            'generations/work-123-' + 'a' * 32 + '/receipt.json'))
