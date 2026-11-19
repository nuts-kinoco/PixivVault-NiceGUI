from types import SimpleNamespace
from pixivvault_nicegui.ui.ranking_view import RankingView
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView


def test_ranking_preferred_cache_falls_back_to_base_on_view_revision_change():
    class View:
        value = (1, 'model')
        def revision(self): return self.value
    local = View()
    services = SimpleNamespace(local_tag_view=local,
        preferred_ranking_tags=lambda: frozenset(('api', 'local_tag')),
        preferred_ranking_tags_base=lambda: frozenset(('api',)))
    ranking = SimpleNamespace(services=services)
    RankingView._rebuild_preferred(ranking)
    assert RankingView.preferred_tags_in(ranking, ('local_tag',))
    local.value = (2, None)
    assert not RankingView.preferred_tags_in(ranking, ('local_tag',))
    assert RankingView.preferred_tags_in(ranking, ('api',))


def test_personal_context_falls_back_to_base_after_revision_change():
    class View:
        value = (1, 'model')
        def revision(self): return self.value
    local = View()
    personal = SimpleNamespace(services=SimpleNamespace(local_tag_view=local),
        _context_revision=(1, 'model'), _context_snapshot={'trend_rows': ['local']},
        _context_base={'trend_rows': ['api']})
    assert PersonalRankingView.context(personal)['trend_rows'] == ['local']
    local.value = (2, None)
    assert PersonalRankingView.context(personal)['trend_rows'] == ['api']


def test_ranking_check_revalidates_image_without_another_query():
    from pixivvault_nicegui.local_tag_view import LocalTagView
    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data): return 'model', [('local_tag', .9)]
    image = [b'image']
    view = LocalTagView(Tagger(), lambda _: image[0],
        lambda: (SimpleNamespace(work_id='1', tags=()),), lambda: ('1',))
    try:
        view.refresh_async().result(10)
        view.snapshot()
        revision = view.revision()
        ranking = SimpleNamespace(services=SimpleNamespace(local_tag_view=view),
            _preferred=frozenset(('local_tag',)), _preferred_base=frozenset(), _preferred_revision=revision)
        assert RankingView.preferred_tags_in(ranking, ('local_tag',))
        image[0] = None
        view.refresh_now()
        assert not RankingView.preferred_tags_in(ranking, ('local_tag',))
    finally:
        view.close()
