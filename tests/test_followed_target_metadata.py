"""U author target metadata is explicit and bound to the current reader.

No real account, network request, original database or GUI is used.
"""
import asyncio
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.services import ApplicationServices


class Reader:
    def __init__(self):
        self.calls = []
        self._verified_identity = None
        self.hook = None

    def verify_identity(self):
        self.calls.append('identity')
        self._verified_identity = '9001'
        return '9001'

    def get_following_users(self, uid, rest):
        assert uid == '9001'
        self.calls.append(rest)
        if self.hook is not None:
            self.hook()
        return ({'user_id': '41', 'name': 'Synthetic A'},
                {'user_id': '42', 'name': 'Synthetic B'}) if rest == 'show' else (
                {'user_id': '41', 'name': 'Duplicate'},
                {'user_id': '43', 'name': 'Synthetic C'})


def service(snapshot=None, favourites=None):
    app = object.__new__(ApplicationServices)
    app.closing = False
    app.managed_downloads = object()
    app.live_client = Reader()
    app.following_sync = (SimpleNamespace(snapshot=lambda uid: snapshot)
                          if snapshot is not None else None)
    app.managed_scheduler = (SimpleNamespace(favourites=lambda: tuple(favourites))
                             if favourites is not None else None)
    return app


@pytest.mark.asyncio
async def test_completed_sync_preserves_favorite_and_follow_order_without_get():
    rows = tuple(SimpleNamespace(user_id=str(41+i), name='Synthetic',
                  is_favorite=i == 1, follow_order=7+i) for i in range(3))
    app = service(SimpleNamespace(account_id='9001', last_synced_at='2026-10-30', following=rows))
    targets = await app.followed_download_authors(owner='a'*32)
    assert [(t.user_id, t.is_favorite, t.follow_order) for t in targets] == [
        ('41', False, 7), ('42', True, 8), ('43', False, 9)]
    assert app.live_client.calls == ['identity']


@pytest.mark.asyncio
@pytest.mark.parametrize('favourites', (None, (), ('42',)))
async def test_unsynced_list_uses_only_explicit_local_favorites_or_unknown(favourites):
    app = service(favourites=favourites)
    targets = await app.followed_download_authors(owner='a'*32)
    assert [t.user_id for t in targets] == ['41', '42', '43']
    assert [t.follow_order for t in targets] == [0, 1, 2]
    expected = [None]*3 if favourites is None else [False, '42' in favourites, False]
    assert [t.is_favorite for t in targets] == expected
    assert app.live_client.calls == ['identity', 'show', 'hide']


@pytest.mark.asyncio
async def test_current_reader_replacement_cannot_publish_old_following_choices():
    app = service()
    previous = app.live_client
    previous.hook = lambda: setattr(app, 'live_client', Reader())
    with pytest.raises(RuntimeError):
        await app.followed_download_authors(owner='a'*32)


@pytest.mark.asyncio
async def test_identity_switch_inside_get_cannot_publish_wrong_account_choices():
    app = service()
    app.live_client.hook = lambda: setattr(app.live_client, '_verified_identity', '9002')
    with pytest.raises(RuntimeError):
        await app.followed_download_authors(owner='a'*32)


@pytest.mark.asyncio
async def test_bad_owner_is_rejected_before_any_identity_or_following_request():
    app = service()
    with pytest.raises(ValueError):
        await app.followed_download_authors(owner='bad')
    assert app.live_client.calls == []
