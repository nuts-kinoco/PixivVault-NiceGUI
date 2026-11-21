"""Count real NiceGUI card subtrees on the baseline."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from nicegui import Client, core, ui

from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.library_view import LibraryView
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup
from test_ranking_source import row
from test_x_ctx_recommend_20261118 import make as recommend_services, open_view


def count(card):
    elements = [card, *card.descendants()]
    return {'elements': len(elements), 'listeners': sum(len(e._event_listeners) for e in elements)}


async def main():
    core.loop = asyncio.get_running_loop()
    services, reader, _ = setup()
    reader.rows = [row(), row(124)]
    reader.rows[1]['tags'] = []
    client = Client(ui.page('/_d6_sweep'))
    library = ApplicationServices(120)
    counts = {}
    with TemporaryDirectory() as directory:
        recommend = recommend_services(Path(directory))
        try:
            with client:
                view = RankingView(services)
                view.set_active(True)
                view._preferred_built = True
                view._preferred = frozenset({'猫'})
                await view.load()
                counts.update({('heart' if wid == '123' else 'plain'): count(card)
                               for wid, card in view.gallery.cards.items()})
                view.close()
                gallery = GalleryView(library)
                counts['gallery'] = count(next(iter(gallery.cards.values())))
                bookmarks = BookmarkView(BookmarkState(local=library.artworks[:1], cached_remote=library.artworks[:2]),
                    on_open=lambda _: None, on_author=lambda _: None, on_similar=lambda _: None,
                    image_url=lambda wid: library.images.url(wid, 0, 'thumb'))
                bookmarks.render()
                counts['bookmark_downloaded'] = count(next(iter(bookmarks.cards.values())))
                library_view = LibraryView(library)
                counts['library_author'] = count(next(iter(library_view.author_cards.values())))
                library_view.open_author('demo-author-01')
                counts['library_page'] = count(next(iter(library_view.page_cards.values())))
                recommendations = await open_view(recommend, on_enqueue=lambda *a, **k: None)
                for name, testid in (('recommend_superlike', 'recommend-work-superlike-11'),
                                     ('recommend_certain', 'recommend-work-certain-12')):
                    tile = next(element for element in recommendations.body.descendants()
                                if element.props.get('data-testid') == testid)
                    counts[name] = count(tile)
                recommendations.close()
        finally:
            client.delete()
            recommend.close()
            library.close()
            services.close()
    print(json.dumps(counts, indent=2))


asyncio.run(main())
