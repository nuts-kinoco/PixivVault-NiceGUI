import pytest
from pixivvault_nicegui.legacy_zip_export import legacy_filename

@pytest.mark.parametrize('title,expected', [
    ('題名_2', '題名(123)_2.jpg'),
    ('CON. ', 'CON. (123).jpg'),
    ('あ' * 100, 'あ' * 100 + '(123).jpg'),
    ('a<>:/b', 'a_b(123).jpg'),
    ('x' * 300, 'x' * 300 + '(123).jpg'),
])
def test_original_filename_rules(title, expected):
    assert legacy_filename(title, '123', 0, 1, 'jpg') == expected


def test_single_page_suffix_candidate_prevents_duplicate(tmp_path):
    from pixivvault_nicegui.legacy_zip_export import zip_arcname_candidates
    assert zip_arcname_candidates('work_2.jpg', 'work_2', '123') == [
        'work_2(123).jpg', 'work(123)_2.jpg', 'work_2.jpg']
