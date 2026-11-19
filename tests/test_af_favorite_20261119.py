import pytest
from nicegui import Client, ui
from pixivvault_nicegui.author_download import AuthorTarget
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView
from test_author_download_view import Services
from types import SimpleNamespace
from threading import RLock
from pixivvault_nicegui.services import ApplicationServices


@pytest.mark.asyncio
async def test_star_toggles_independently_of_download_selection_and_filter():
    services = Services()
    calls = []
    async def update(uid, *, owner, expected, favorite):
        calls.append((uid, expected, favorite))
    services.set_followed_author_favorite = update
    client = Client(ui.page('/_af_favorite'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.follow_authors = (AuthorTarget('91', 'artist', 'follow', False),)
            view._render_follow_list()
            assert any(x._props.get('data-testid') == 'managed-download-follow-star' for x in view.follow_list.descendants())
            await view._toggle_author_favorite('91', view.epoch, view.follow_render_generation)
            assert calls == [('91', False, True)]
            assert view.follow_authors[0].is_favorite is True
            assert not view.author_targets
            view.follow_favorites_only = True
            await view._toggle_author_favorite('91', view.epoch, view.follow_render_generation)
            assert not view._visible_follow_authors()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_star_failure_keeps_displayed_state():
    services = Services()
    async def fail(*args, **kwargs):
        raise RuntimeError('synthetic failure')
    services.set_followed_author_favorite = fail
    client = Client(ui.page('/_af_favorite_fail'))
    try:
        with client:
            view = ManagedDownloadView(services)
            view.follow_authors = (AuthorTarget('91', 'artist', 'follow', False),)
            view._render_follow_list()
            await view._toggle_author_favorite('91', view.epoch, view.follow_render_generation)
            assert view.follow_authors[0].is_favorite is False
            assert '保存' in view.author_status_label.text
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_star_service_rejects_changed_account_and_stale_value():
    owner = 'a' * 32
    class Store:
        def __init__(self): self.writes = []
        def snapshot(self, account):
            return SimpleNamespace(account_id=account, revision=4,
                                   authors=(SimpleNamespace(user_id='91', is_favorite=False),))
        def set_favorite(self, *args, **kwargs): self.writes.append((args, kwargs))
    store = Store()
    reader = SimpleNamespace(_verified_identity='1')
    service = SimpleNamespace(_direct_owner=ApplicationServices._direct_owner,
        _managed_target_lock=RLock(), _follow_account_by_owner={owner: '1'},
        closing=False, live_client=reader, following_sync=store)
    await ApplicationServices.set_followed_author_favorite(service, '91', owner=owner, expected=False, favorite=True)
    assert store.writes == [(('1', '91', True), {'expected_revision': 4})]
    reader._verified_identity = '2'
    with pytest.raises(RuntimeError):
        await ApplicationServices.set_followed_author_favorite(service, '91', owner=owner, expected=False, favorite=True)
    assert len(store.writes) == 1
