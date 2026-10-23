import asyncio
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.services import ApplicationServices


class Reader:
    def __init__(self, works):
        self.works = works
        self.calls = []

    def verify_identity(self):
        self.calls.append('identity')

    def get_author_artworks(self, uid):
        self.calls.append(('author', uid))
        return self.works


def preview_service(works, saved=()):
    app = object.__new__(ApplicationServices)
    app.closing = False
    app.managed_downloads = SimpleNamespace(saved_receipts=lambda: tuple(
        ('outputs/fake/receipt.json', {'work_id': wid}, 'digest') for wid in saved))
    app.live_client = Reader(works)
    return app


def work(wid, updated='2026'):
    return {'id': wid, 'type': 0, 'update_date': updated}


def test_author_preview_keeps_existing_v1_unknown_and_new_actionable():
    app = preview_service((work('101'), work('102'), work('103', ''),
                           {'id': '104', 'type': 2, 'update_date': '2026'}), ('101', '103'))
    result = asyncio.run(app.lookup_managed_author('123', owner='a' * 32))
    assert [(item['id'], status) for item, status in result] == [
        ('101', Difference.UNKNOWN), ('102', Difference.NEW), ('103', Difference.UNKNOWN)]
    assert app.live_client.calls == ['identity', ('author', '123')]


def test_author_preview_rejects_bad_owner_without_get():
    app = preview_service((work('101'),))
    with pytest.raises(ValueError):
        asyncio.run(app.lookup_managed_author('123', owner='bad'))
    assert app.live_client.calls == []
