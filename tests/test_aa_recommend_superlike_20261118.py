from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationPreferences,
    RecommendationSnapshot, RecommendationWork,
)


class Source:
    def load(self):
        return RecommendationSnapshot(sections=(('superlike', (RecommendationGroup('superlike', 'S', (
            RecommendationWork('11'), RecommendationWork('12'),
        )),)),))


class Fetch:
    mode = 'success'

    def detail(self, wid):
        if self.mode == 'error':
            raise RuntimeError('temporary')
        if self.mode == 'missing':
            return None
        return dict(id=wid, title='cached', thumbnail_url='https://i.pximg.net/img.jpg')


def test_missing_superlike_keeps_cached_card_but_error_is_not_deleted(tmp_path):
    fetch = Fetch()
    c = RecommendationController(Source(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh()
        state = c.update(state['revision'])
        fetch.mode = 'error'
        state = c.update(state['revision'])
        assert all(w.title == 'cached' and not w.deleted for w in state['sections']['superlike'][0].works)
        fetch.mode = 'missing'
        state = c.update(state['revision'])
        assert all(w.title == 'cached' and w.deleted for w in state['sections']['superlike'][0].works)
    finally:
        c.close()


def test_missing_uncached_superlike_is_not_displayed(tmp_path):
    fetch = Fetch()
    fetch.mode = 'missing'
    c = RecommendationController(Source(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh()
        state = c.update(state['revision'])
        assert not state['sections']['superlike'][0].works
    finally:
        c.close()
