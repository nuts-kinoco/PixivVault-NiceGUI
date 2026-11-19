import pytest

from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationError, RecommendationPreferences,
    RecommendationSnapshot,
)


class Source:
    def load(self):
        return RecommendationSnapshot(certain_group_collapsed=frozenset({'古いタグ'}))


@pytest.mark.parametrize('raw,expected', [('null', set()), ('"abc"', set()), ('["猫", null, 3, ""]', {'猫'}), ('broken', set())])
def test_damaged_saved_groups_fall_back_to_open(raw, expected):
    class Prefs:
        def snapshot(self):
            return 0, {}, {'recommend_certain_group_collapsed': raw}
    controller = RecommendationController(Source(), Prefs())
    assert controller.refresh()['certain_group_collapsed'] == expected


def test_certain_group_state_survives_reopen_and_rejects_stale_write(tmp_path):
    root = tmp_path / 'prefs'
    controller = RecommendationController(Source(), RecommendationPreferences(root))
    try:
        state = controller.refresh()
        assert state['certain_group_collapsed'] == {'古いタグ'}
        state = controller.configure('recommend_certain_group_collapsed', ['古いタグ', '猫'], state['revision'])
        with pytest.raises(RecommendationError):
            controller.configure('recommend_certain_group_collapsed', [], state['revision'] - 1)
    finally:
        controller.close()
    controller = RecommendationController(Source(), RecommendationPreferences(root))
    try:
        assert controller.refresh()['certain_group_collapsed'] == {'古いタグ', '猫'}
    finally:
        controller.close()
