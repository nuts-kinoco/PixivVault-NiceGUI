"""Count real NiceGUI card subtrees on the baseline."""
import asyncio
import json

from nicegui import Client, ui
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_ui import setup
from test_ranking_source import row


async def main():
    services, reader, _ = setup()
    reader.rows = [row(), row(124)]
    reader.rows[1]['tags'] = []
    client = Client(ui.page('/_d6_sweep'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            view._preferred_built = True
            view._preferred = frozenset({'猫'})
            await view.load()
            counts = {}
            for work_id, card in view.gallery.cards.items():
                elements = [card, *card.descendants()]
                counts['heart' if work_id == '123' else 'plain'] = {
                    'elements': len(elements),
                    'listeners': sum(len(e._event_listeners) for e in elements),
                }
            print(json.dumps(counts))
            view.close()
    finally:
        client.delete()
        services.close()


asyncio.run(main())
