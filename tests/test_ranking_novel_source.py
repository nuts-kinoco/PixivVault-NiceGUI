"""Synthetic schema-only public novel ranking; no credentials, media, or network."""
import pytest

from pixivvault_nicegui.ranking_source import (
    RankingQuery, RankingSource, RankingUnavailable, RankingReadFailed,
    merge_pages, sorted_items,
)


def novel_row(wid='123', **changes):
    row = {
        'id': wid, 'rank': 1, 'title': 'Generated novel', 'user_id': '91',
        'user_name': 'Synthetic author', 'url': 'https://i.pximg.net/cover.png',
        'tag_a': ['synthetic'], 'ai_type': '1', 'x_restrict': '0',
        'restrict': '0', 'create_date': '2026-10-01', 'bookmark_count': 20,
        'character_count': '100', 'series_id': 0,
    }
    row.update(changes)
    return row


class Reader:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = [novel_row()] if rows is None else rows
    def require_identity(self):
        self.calls.append('identity')
    def get_novel_ranking(self, **kw):
        self.calls.append(('novel', kw))
        return self.rows
    def get_ranking(self, **kw):
        raise AssertionError('illustration ranking must not receive novel content')
    def search_illusts(self, *args, **kw):
        raise AssertionError('novel tag must not search illustration namespace')
    def get_work_info(self, *args):
        raise AssertionError('novel AI flags must not use artwork detail')


def test_novel_has_separate_id_namespace_cover_and_known_counts():
    reader = Reader([novel_row(), novel_row('124', rank=2, bookmark_count=5,
                                          url='https://i.pximg.net/cover2.png')])
    page = RankingSource(reader).fetch(RankingQuery(content='novel', date='20261001'))
    assert reader.calls == ['identity', ('novel', dict(mode='daily', page=1, date='20261001'))]
    a, b = page.items
    assert a.record.kind == 'novel' and a.record.remote_id == '123'
    assert a.record.work_id == 'novel:123' and b.record.work_id == 'novel:124'
    assert a.record.thumbnail == 'https://i.pximg.net/cover.png'
    assert b.record.thumbnail == 'https://i.pximg.net/cover2.png'
    assert a.record.page_count == 1 and a.record.illustration_type is None
    assert a.rating_count == 20 and b.rating_count == 5
    assert a.view_count is b.view_count is None
    assert a.previous_rank is b.previous_rank is None
    assert 'ブックマーク数' in page.order_label
    assert [v.record.work_id for v in sorted_items(page.items, 'rating_count')] == [
        'novel:123', 'novel:124']
    assert not page.exhausted


def test_novel_pagination_merges_without_artwork_collision_and_no_faked_momentum():
    reader = Reader([novel_row()])
    source = RankingSource(reader)
    first = source.fetch(RankingQuery(content='novel'))
    reader.rows = [novel_row('124')]
    second = source.fetch(first.query, 2)
    merged = merge_pages(first, second)
    assert [v.record.work_id for v in merged.items] == ['novel:123', 'novel:124']
    assert [v.previous_rank for v in sorted_items(merged.items, 'momentum')] == [None, None]
    reader.rows = []
    assert source.fetch(first.query, 3).exhausted


@pytest.mark.parametrize('changed', [
    {'id': '0'}, {'id': True}, {'id': 'abc'}, {'user_id': ''},
    {'rank': '1'}, {'rank': -1}, {'bookmark_count': '30'},
    {'title': ['bad']}, {'tag_a': ['ok', 1]},
    {'url': 'https://bad.invalid/cover.png'},
    {'url': 'https://i.pximg.net:443/cover.png'},
    {'url': 'http://i.pximg.net/cover.png'},
    {'x_restrict': None}, {'restrict': None}, {'url': ''},
    {'ai_type': '0', 'url': 'https://i.pximg.net/cover.png'},
    {'ai_type': 'unknown'},
])
def test_bad_novel_row_fails_closed(changed):
    reader = Reader([novel_row(**changed)])
    with pytest.raises(RankingReadFailed):
        RankingSource(reader).fetch(RankingQuery(content='novel'))


@pytest.mark.parametrize('query', [
    {'content': 'novel', 'tag': 'synthetic'},
    {'content': 'novel', 'r18': True},
])
def test_unverified_novel_filter_is_visible_unavailable_and_no_network(query):
    reader = Reader()
    with pytest.raises(RankingUnavailable):
        RankingSource(reader).fetch(RankingQuery(**query))
    assert reader.calls == []


def test_known_mask_is_suppressed_and_ai_exclusion_uses_observed_flag_only():
    reader = Reader([
        novel_row('101', ai_type='1'),
        novel_row('102', ai_type='2', rank=2),
        novel_row('103', ai_type='0', rank=3,
                  url='https://s.pximg.net/common/images/limit_unviewable_s.png'),
    ])
    source = RankingSource(reader)
    plain = source.fetch(RankingQuery(content='novel'))
    assert [row.record.work_id for row in plain.items] == ['novel:101', 'novel:102']
    filtered = source.fetch(RankingQuery(content='novel', exclude_ai=True))
    assert [row.record.work_id for row in filtered.items] == ['novel:101']
    assert not any(call == 'detail' for call in reader.calls)


@pytest.mark.parametrize('rows', [
    [novel_row('101'), novel_row('101', rank=2)],
    [novel_row(str(i), rank=i) for i in range(1,52)],
])
def test_novel_duplicate_ids_or_oversized_page_never_publish_results(rows):
    with pytest.raises(RankingReadFailed):
        RankingSource(Reader(rows)).fetch(RankingQuery(content='novel'))


def test_novel_404_and_read_failure_keep_existing_page_contract():
    from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
    reader = Reader()
    def error(**kw):
        raise HTTPStatusRejected(404)
    reader.get_novel_ranking = error
    source = RankingSource(reader)
    with pytest.raises(RankingUnavailable):
        source.fetch(RankingQuery(content='novel'))
    assert source.fetch(RankingQuery(content='novel'), 2).exhausted
    def broken(**kw):
        raise HTTPStatusRejected(429)
    reader.get_novel_ranking = broken
    with pytest.raises(RankingReadFailed):
        source.fetch(RankingQuery(content='novel'))
