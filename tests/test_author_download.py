import pytest

from pixivvault_nicegui.author_download import (
    AuthorTarget, Difference, decide_difference, filter_authors, select_author,
)


def test_origin_first_wins_and_filter_does_not_mutate_selection():
    first = AuthorTarget('123', 'Alice', 'id')
    second = AuthorTarget('123', 'Alice Follow', 'follow')
    other = AuthorTarget('456', 'Bob', 'follow')
    selected = select_author((), first)
    assert select_author(selected, second) is selected
    assert selected[0].origin == 'id'
    assert filter_authors((first, other), 'bob') == (other,)
    assert selected == (first,)


@pytest.mark.parametrize('uid', ['', '0', '01', '-1', 'x', '9'*21])
def test_invalid_author_id(uid):
    with pytest.raises(ValueError):
        AuthorTarget(uid, 'name', 'id')


@pytest.mark.parametrize('args,expected', [
    ({'saved': False, 'remote_update_date': None, 'saved_update_date': None}, Difference.NEW),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': None}, Difference.UNKNOWN),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': '2025'}, Difference.UPDATED),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': '2026', 'deleted': True}, Difference.RESTORED),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': '2026', 'files_present': False}, Difference.MISSING),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': '2026'}, Difference.UNKNOWN),
    ({'saved': True, 'remote_update_date': '2026', 'saved_update_date': '2026', 'files_present': True}, Difference.SKIPPED),
])
def test_conservative_difference(args, expected):
    assert decide_difference(**args) is expected
