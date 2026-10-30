"""Offline public novel ranking route and nested JSON contract, no Cookie fixture."""
import json
from urllib.parse import urlsplit

import pytest

from pixivvault_nicegui import pixiv_read_client as mod


URL = 'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=ja'


def synthetic_body(rows=None):
    if rows is None:
        rows = [{'id': '123', 'rank': 1, 'user_id': '91', 'user_name': 'Synthetic',
                 'url': 'https://i.pximg.net/cover.png', 'tag_a': ['synth'],
                 'ai_type': '1', 'x_restrict': '0', 'restrict': '0',
                 'create_date': '2026-10-01', 'bookmark_count': 4,
                 'character_count': '100', 'series_id': 0, 'title': 'Sample'}]
    return {'display_a': {'rank_a': rows, 'mode': 'daily', 'page': 1}}


def offline_client(response):
    """Run real JSON parser + URL validation with a bytes stub, never a request."""
    client = object.__new__(mod.LivePixivReadClient)
    client.require_identity = lambda: 'synthetic-verified'
    client._get = lambda kind, url, deadline: (
        mod._validate_destination(url, kind) or response)
    return client


def wire(body, *, error=False):
    return json.dumps({'error': error, 'body': body}).encode('utf-8')


def test_dedicated_endpoint_one_bounded_route_and_nested_rows():
    client = offline_client(wire(synthetic_body()))
    assert client.get_novel_ranking(mode='daily', page=1) == synthetic_body()['display_a']['rank_a']
    assert client.get_novel_ranking(mode='daily', page=1, date='20261001') == synthetic_body()['display_a']['rank_a']
    assert URL == 'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=ja'
    mod._validate_destination(URL, 'novel_ranking')


def test_weekly_ai_sparse_numeric_indices_have_stable_order():
    data=synthetic_body([])
    data['display_a']['rank_a']={
        '49': {'id':'102'}, '0':{'id':'101'}, '7':{'id':'103'}}
    data['display_a']['mode']='weekly_ai'
    client=offline_client(wire(data))
    assert [r['id'] for r in client.get_novel_ranking(mode='weekly_ai')] == [
        '101','103','102']


@pytest.mark.parametrize('url', [
    'http://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=ja',
    'https://www.pixiv.net.evil.invalid/ajax/ranking/novel?mode=daily&p=1&lang=ja',
    'https://www.pixiv.net/ranking.php?mode=daily&content=novel&format=json&p=1',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=en',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=ja&extra=1',
    'https://www.pixiv.net/ajax/ranking/novel?p=1&mode=daily&lang=ja',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=0&lang=ja',
    'https://www.pixiv.net/ajax/ranking/novel?mode=unknown&p=1&lang=ja',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&lang=ja#frag',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&date=20261301&lang=ja',
    'https://www.pixiv.net/ajax/ranking/novel?mode=daily&p=1&date=20261001&lang=ja&date=20261001',
])
def test_novel_url_allowlist_strict(url):
    with pytest.raises(mod.DestinationRejected):
        mod._validate_destination(url, 'novel_ranking')


@pytest.mark.parametrize('args', [
    {'mode': 'bad'}, {'mode': 'daily_r18'}, {'page': 0},
    {'page': True}, {'date': '2026-10-01'},
    {'date': '20261301'}, {'date': '２０２６１００１'},
])
def test_bad_arguments_rejected_before_transport(args):
    client = offline_client(wire(synthetic_body()))
    with pytest.raises(mod.DestinationRejected):
        client.get_novel_ranking(**args)


@pytest.mark.parametrize('body', [
    {}, {'display_a': None}, {'display_a': {}},
    {'display_a': {'rank_a': {}}},
    {'display_a': {'rank_a': [], 'page': True}},
    {'display_a': {'rank_a': [], 'page': 2}},
    {'display_a': {'rank_a': [None], 'page': 1}},
    {'display_a': {'rank_a': [{}] * 51, 'page': 1}},
    {'display_a': {'rank_a': {'01': {}}, 'page': 1}},
    {'display_a': {'rank_a': {'50': {}}, 'page': 1}},
    {'display_a': {'rank_a': {'-1': {}}, 'page': 1}},
])
def test_malformed_envelope_fails_without_leaking_content(body):
    client = offline_client(wire(body))
    with pytest.raises(mod.InvalidPayload) as e:
        client.get_novel_ranking()
    assert 'Sample' not in str(e.value)


def test_failed_api_duplicate_keys_and_invalid_json_use_fixed_errors():
    with pytest.raises(mod.ApiRejected):
        offline_client(wire(synthetic_body(), error=True)).get_novel_ranking()
    with pytest.raises(mod.InvalidPayload):
        offline_client(b'{"error":false,"body":{},"body":{}}').get_novel_ranking()
    with pytest.raises(mod.InvalidPayload):
        offline_client(b'not json').get_novel_ranking()
