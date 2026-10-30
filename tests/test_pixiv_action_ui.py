"""Displayed-target contracts for explicit actions; real NiceGUI/synthetic HTTP."""
import asyncio
from threading import Event
from types import SimpleNamespace

from nicegui import Client, ui
import pytest

from pixivvault_nicegui.ui.pixiv_action_buttons import PixivActionButtons, _HUBS
from test_following_sync_ui import runtime
from test_pixiv_actions import stack


def panel(stack, monkeypatch, route='/_pixiv_actions'):
    runtime(monkeypatch)
    services = SimpleNamespace(pixiv_actions=stack.actions, closing=False)
    client = Client(ui.page(route))
    with client:
        controls = PixivActionButtons(services, prefix='test-action')
        controls.bind('123', '91', author_name='Synthetic author')
    return client, controls, services


def token(controls, kind):
    button = controls.bookmark if kind == 'bookmark' else controls.follow
    return button.props['data-action-token']


@pytest.mark.asyncio
async def test_mount_poll_and_invalid_demo_targets_never_fetch(stack, monkeypatch):
    client, controls, services = panel(stack, monkeypatch)
    try:
        for _ in range(3):
            controls.hub.refresh()
        assert stack.http.reads == stack.http.posts == []
        assert controls.bookmark.enabled and controls.follow.enabled
        controls.bind('demo-0001', None)
        assert not controls.bookmark.enabled and not controls.follow.enabled
        assert not await controls.click('bookmark', 'forged')
        assert stack.http.reads == stack.http.posts == []
        controls.close()
        assert not controls.hub.timer.active and stack.actions.active_count == 0
    finally:
        client.delete()
    assert client not in _HUBS
    assert not stack.actions._closing and not stack.reader._closed


@pytest.mark.asyncio
async def test_actual_click_preserves_private_bookmark_and_displays_observation(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch)
    try:
        stack.http.bookmark = {'id': '777', 'private': True}
        assert await controls.click('bookmark', token(controls, 'bookmark'))
        assert controls.bookmark.text == 'ブクマ済み' and not controls.bookmark.enabled
        assert '既に' in controls.notice.text
        assert stack.http.posts == [] and stack.http.bookmark['private'] is True
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_old_card_page_or_account_token_cannot_retarget(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch)
    try:
        old = token(controls, 'bookmark')
        controls.bind('124', '92', author_name='Next author')
        assert not await controls.click('bookmark', old)
        assert stack.http.reads == stack.http.posts == []
        await stack.actions.verify_account()
        controls.refresh()
        old = token(controls, 'follow')
        stack.reader._verified_identity = '56'
        before = tuple(stack.http.reads)
        assert not await controls.click('follow', old)
        assert tuple(stack.http.reads) == before and stack.http.posts == []
        controls.clear()
        assert not await controls.click('follow', token(controls, 'follow'))
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_follow_and_unfollow_are_two_explicit_fixed_actions(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch)
    try:
        assert await controls.click('follow', token(controls, 'follow'))
        assert stack.http.following == {'91': 0}
        assert controls.follow.text == 'フォロー解除' and len(stack.http.posts) == 1
        assert await controls.click('follow', token(controls, 'follow'))
        assert stack.http.following == {}
        assert controls.follow.text == 'フォロー' and len(stack.http.posts) == 2
        assert not stack.following.snapshot('55').authors[0].is_following
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_unknown_button_reconciles_original_operation_without_reposting(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch)
    try:
        stack.http.post_answer = TimeoutError('synthetic unknown')
        assert not await controls.click('bookmark', token(controls, 'bookmark'))
        assert '結果を確認' in controls.bookmark.text and len(stack.http.posts) == 1
        original = stack.actions.state('55', 'illust', '123').operation_id
        stack.http.bookmark = {'id': '888', 'private': True}
        assert await controls.click('bookmark', token(controls, 'bookmark'))
        assert controls.bookmark.text == 'ブクマ済み'
        assert stack.actions.result(original).status == 'indeterminate'
        assert stack.actions.reconciliation_result(original).status == 'observed'
        assert len(stack.http.posts) == 1
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_two_clients_pending_and_departed_view_do_not_orphan_sent_follow(stack, monkeypatch):
    client_a, a, _ = panel(stack, monkeypatch, '/_action_a')
    client_b, b, _ = panel(stack, monkeypatch, '/_action_b')
    started, release = Event(), Event()
    def hold():
        started.set()
        assert release.wait(3)
    stack.http.post_hook = hold
    request = asyncio.create_task(a.click('follow', token(a, 'follow')))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        b.hub.refresh()
        assert not b.follow.enabled and '処理中' in b.follow.text
        assert not await b.click('follow', token(b, 'follow'))
        client_a.delete()
        assert not stack.actions._closing and not stack.reader._closed
    finally:
        release.set()
    try:
        await request
        b.hub.refresh()
        assert b.follow.text == 'フォロー解除' and b.follow.enabled
        assert stack.following.snapshot('55').following[0].user_id == '91'
        assert len(stack.http.posts) == 1
    finally:
        if client_a.id in Client.instances:
            client_a.delete()
        client_b.delete()


@pytest.mark.asyncio
async def test_view_departure_during_preflight_prevents_send_and_stale_status(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch)
    started, release = Event(), Event()
    def hold(route):
        if route.path == '/ajax/illust/123':
            started.set()
            assert release.wait(3)
    stack.http.get_hook = hold
    request = asyncio.create_task(controls.click('bookmark', token(controls, 'bookmark')))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        controls.clear()
        release.set()
        assert not await request
        assert stack.http.posts == []
        assert controls.notice.text == '' and not controls.bookmark.enabled
        assert not stack.reader._closed
    finally:
        release.set()
        client.delete()


@pytest.mark.asyncio
async def test_account_change_clears_old_pending_notice_after_accepted_post_drains(stack, monkeypatch):
    client, controls, _ = panel(stack, monkeypatch, '/_action_account_notice')
    await stack.actions.verify_account()
    controls.refresh()
    started, release = Event(), Event()
    def hold():
        started.set()
        assert release.wait(3)
    stack.http.post_hook = hold
    accepted_token = token(controls, 'follow')
    request = asyncio.create_task(controls.click('follow', accepted_token))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert controls._busy == 'follow'
        stack.reader._verified_identity = '56'
        controls.hub.refresh()
        release.set()
        await asyncio.wait_for(request, 3)
        assert controls._busy is None and stack.actions.active_count == 0
        assert controls.notice.text == '', 'old-account pending status must not survive its completed drain'
        assert controls.follow.enabled and controls.follow.text == 'フォロー'
        assert token(controls, 'follow') != accepted_token
        assert stack.actions.state('55', 'user', '91').observed is True
        assert stack.actions.state('56', 'user', '91').observed is None
        assert stack.following.snapshot('55').following[0].user_id == '91'
        assert stack.following.snapshot('56').following == ()
        assert len(stack.http.posts) == 1
        assert not stack.actions._closing and not stack.reader._closed
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
        client.delete()
