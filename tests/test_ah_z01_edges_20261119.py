from pixivvault_nicegui.legacy_zip_export import legacy_filename


def test_dot_only_title_matches_original_splitext():
    assert legacy_filename('.', '123', 0, 1, 'png') == '..png(123)'
