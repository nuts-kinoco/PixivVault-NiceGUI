"""Keep every artwork viewer construction path on the initial NiceGUI stream."""

import ast
import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.artwork_page import ArtworkPage
from pixivvault_nicegui.ui.author_browse_view import AuthorBrowseView
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.learning_view import LearningView
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.media_views import MediaLibraryView
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from pixivvault_nicegui.ui.ranking_view import RankingView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from pixivvault_nicegui.ui.view_history_view import HeaderHistoryHost, ViewHistoryView
from test_p3_viewer_author import Reader


def make_media_library(services):
    # Select the illustration branch without starting a real save service.
    services.managed_downloads = SimpleNamespace()
    return MediaLibraryView(services)


CASES = {
    'gallery': GalleryView,
    'ranking': RankingView,
    'library': LibraryView,
    'library-downloaded': lambda services: LibraryView(services, navigation_view='downloaded'),
    'library-bookmarks': lambda services: LibraryView(services, navigation_view='bookmarks'),
    'media-library': make_media_library,
    'recommend': RecommendView,
    'personal': PersonalRankingView,
    'author': lambda services: AuthorBrowseView(services, Reader()),
    'header-history': lambda services: ViewHistoryView(
        HeaderHistoryHost(SimpleNamespace(services=services, content=ui.column()), None), None),
    'learning-history': LearningView,
}


@pytest.mark.parametrize('name', CASES)
@pytest.mark.parametrize('response_built', [False, True], ids=['html', 'deferred'])
@pytest.mark.asyncio
async def test_viewer_initial_wiring_uses_only_standard_elements(name, response_built, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = ApplicationServices(3)
    client = Client(ui.page('/_d6_viewer_creation'))
    client._response_built = response_built
    try:
        with client:
            CASES[name](services)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        [page] = [element for element in client.elements.values() if isinstance(element, ArtworkPage)]
        ids = {str(element.id) for element in page.descendants(include_self=True)}
        standard = ids.intersection(map(str, client.outbox.updates))
        duplicates = Counter(str(key) for _, kind, payload in client.outbox.messages
                             if kind == 'update' for key in payload if str(key) in standard)
        root_id = repr(page.default_slot.children[0].html_id)
        actions = [payload for _, kind, payload in client.outbox.messages
                   if kind == 'run_javascript' and root_id in payload['code']]
        print(f'{name} response_built={response_built}: standard={len(standard)}, '
              f'duplicate={sum(duplicates.values())}, viewer_js={len(actions)}')
        assert standard == ids
        assert not duplicates and not actions
    finally:
        client.delete()
        if name == 'media-library':
            services.managed_downloads = None
        services.close()


def test_viewer_creation_sites_are_covered():
    # Observe the real source. A new outer view or a second inner viewer must
    # be assigned an Outbox case before extending the production call graph.
    expected = {
        'ArtworkPage': ['artwork_viewer'],
        'ArtworkViewer': ['viewer_controller'],
        '_init_viewer': ['gallery', 'library_view', 'recommendation_viewer'],
        'GalleryView': ['shell'],
        '_Gallery': ['author_browse_view'],
        '_RankingGallery': ['personal_ranking_view', 'ranking_view'],
        'RankingView': ['shell'],
        'LibraryView': ['media_views', 'shell', 'shell'],
        'MediaLibraryView': ['shell'],
        'RecommendationViewer': ['recommend_view', 'view_history_view'],
        'RecommendView': ['shell'],
        'PersonalRankingView': ['shell'],
        'AuthorBrowseView': ['recommend_view', 'viewer_controller'],
        'ViewHistoryView': ['learning_view', 'shell'],
        'LearningView': ['shell'],
    }
    source = Path(__file__).resolve().parents[1] / 'src' / 'pixivvault_nicegui'
    sites = Counter()
    for file in source.rglob('*.py'):
        tree = ast.parse(file.read_bytes(), filename=str(file))
        aliases = {item.asname: item.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                   for item in node.names if item.asname and item.name in expected}
        for call in (node.func for node in ast.walk(tree) if isinstance(node, ast.Call)):
            name = call.id if isinstance(call, ast.Name) else getattr(call, 'attr', None)
            name = aliases.get(name, name)
            if name in expected:
                sites[name, file.stem] += 1
    assert sites == Counter((name, file) for name, files in expected.items() for file in files)
