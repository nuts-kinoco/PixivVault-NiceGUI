from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationPreferences,
    RecommendationSnapshot, RecommendationWork, RecommendationError,
)
import pytest
from pixivvault_nicegui.recommendation_acquisition import PixivRecommendationAdapter
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient


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


def test_adapter_distinguishes_empty_body_from_reader_failure():
    class Reader:
        get_recommendation_work = LivePixivReadClient.get_recommendation_work
        def require_identity(self): return '1'
        def _json(self, kind, url, *, deadline): return None
    assert PixivRecommendationAdapter(Reader()).detail('11') is None


def test_missing_cached_cards_obey_display_limit(tmp_path):
    class Many:
        def load(self):
            return RecommendationSnapshot(sections=(('superlike', (RecommendationGroup('superlike', 'S', tuple(
                RecommendationWork(str(i), thumbnail_url='https://i.pximg.net/img.jpg') for i in range(1, 41)
            )),)),))
    fetch = Fetch(); fetch.mode = 'missing'
    c = RecommendationController(Many(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh(); state = c.update(state['revision'])
        assert len(state['sections']['superlike'][0].works) == 36
    finally:
        c.close()


def test_thumbnailless_response_does_not_destroy_known_cache(tmp_path):
    fetch = Fetch()
    c = RecommendationController(Source(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh(); state = c.update(state['revision'])
        fetch.detail = lambda wid: dict(id=wid, title='no image')
        state = c.update(state['revision'])
        assert not state['sections']['superlike'][0].works
        fetch.detail = lambda wid: None
        state = c.update(state['revision'])
        assert all(w.deleted and w.title == 'cached' for w in state['sections']['superlike'][0].works)
        assert len(state['sections']['superlike'][0].works) == 2
    finally:
        c.close()


def test_hidden_work_keeps_cache_for_later_missing_response(tmp_path):
    fetch = Fetch()
    c = RecommendationController(Source(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh(); state = c.update(state['revision'])
        state = c.hide('work', '11', True, state['revision'])
        state = c.update(state['revision'])
        state = c.hide('work', '11', False, state['revision'])
        fetch.mode = 'missing'; state = c.update(state['revision'])
        item = next(w for w in state['sections']['superlike'][0].works if w.work_id == '11')
        assert item.deleted and item.title == 'cached'
    finally:
        c.close()


def test_oversized_superlike_update_does_not_commit_unreadable_cache(tmp_path):
    class Many:
        def load(self):
            return RecommendationSnapshot(sections=(('superlike', (RecommendationGroup('superlike', 'S', tuple(
                RecommendationWork(str(i)) for i in range(1, 10002)
            )),)),))
    fetch = Fetch(); fetch.mode = 'missing'
    c = RecommendationController(Many(), RecommendationPreferences(tmp_path / 'prefs'), acquisition=fetch)
    try:
        state = c.refresh()
        with pytest.raises(RecommendationError): c.update(state['revision'])
        assert c.refresh()['revision'] == state['revision']
    finally:
        c.close()
