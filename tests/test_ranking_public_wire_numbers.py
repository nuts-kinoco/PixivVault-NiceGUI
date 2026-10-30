"""Public wire schema: artwork type/page count are bounded decimal strings.

Values and IDs below are generated, not copied from a real account response.
The real diagnostic retained only field type counts, never rows or payloads.
"""
import pytest

from pixivvault_nicegui.ranking_source import parse_item, RankingQuery, RankingSource
from test_ranking_source import row, Reader


@pytest.mark.parametrize('kind', (0, 1, 2))
def test_public_ranking_wire_type_and_declared_page_count(kind):
    source = row(123)
    source.update(illust_type=str(kind), illust_page_count='3')
    parsed = parse_item(source, RankingQuery(content=('illust', 'manga', 'ugoira')[kind]))
    assert parsed.record.illustration_type == kind
    assert parsed.record.page_count == 3 and parsed.record.page_count_known
    assert parsed.record.work_id == '123'


def test_public_page_can_parse_generated_fifty_row_wire_schema():
    reader = Reader()
    reader.rows = [dict(row(1000 + n), illust_type='0', illust_page_count='2', rank=n+1)
                   for n in range(50)]
    page = RankingSource(reader).fetch(RankingQuery())
    assert len(page.items) == 50 and not page.exhausted
    assert all(item.record.page_count == 2 and item.record.page_count_known for item in page.items)


@pytest.mark.parametrize('value', ('', '01', '+1', '-1', ' 1', '1 ', '1.0', '１', True, 1.0, '3', '1000000'))
def test_invalid_public_artwork_type_remains_rejected(value):
    with pytest.raises(ValueError):
        parse_item(dict(row(), illust_type=value, illust_page_count='1'), RankingQuery())


@pytest.mark.parametrize('value', ('', '00', '02', '0', '-1', '+2', ' 2', '2.0', '２', '1001', True, 2.0, None))
def test_invalid_public_wire_page_count_remains_rejected(value):
    with pytest.raises(ValueError):
        parse_item(dict(row(), illust_type='0', illust_page_count=value), RankingQuery())


def test_duplicate_page_count_fields_must_agree_and_missing_stays_unknown():
    both = parse_item(dict(row(), illust_type='0', page_count=2, illust_page_count='2'), RankingQuery())
    assert both.record.page_count == 2 and both.record.page_count_known
    with pytest.raises(ValueError):
        parse_item(dict(row(), illust_type='0', page_count=2, illust_page_count='3'), RankingQuery())
    absent = parse_item(row(), RankingQuery())
    assert absent.record.page_count == 1 and not absent.record.page_count_known


def test_other_numeric_fields_do_not_become_permissive_strings():
    for name in ('rank', 'yes_rank', 'rating_count', 'view_count', 'ai_type'):
        with pytest.raises(ValueError):
            parse_item(dict(row(), illust_type='0', illust_page_count='1', **{name: '1'}), RankingQuery())
