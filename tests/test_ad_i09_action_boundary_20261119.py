import asyncio
import json
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
from pixivvault_nicegui.managed_network_stop_write import write_admission
from pixivvault_nicegui.pixiv_write_client import WriteHTTPResponse
from test_original_author_follow import action_stack
from z_learning_support_20261118 import open_discovery_view


class Remote:
    def __init__(self, stack, item, gate, *, present=True, mode='ok'):
        self.item = item
        self.present = present
        self.mode = mode
        self.posts = []
        self.started = Event()
        self.release = Event()
        self.gets = []
        self.base_get = stack.reader._transport
        stack.reader._transport = self.get
        stack.actions._writer._transport = self.post
        shared = SimpleNamespace(gate=gate, _check_owner=lambda: None)
        stack.actions._writer._post_admission = lambda: write_admission(stack.reader, None, shared)

    def get(self, request, *args):
        path = urlsplit(request.full_url).path
        self.gets.append(path)
        if path == f'/ajax/illust/{self.item.work_id}':
            record = {'id': self.item.work_id, 'userId': self.item.author_id,
                      'bookmarkData': {'id': '888', 'private': False} if self.present else None}
            return json.dumps({'error': False, 'body': record}).encode()
        return self.base_get(request, *args)

    def post(self, request, *args):
        route = urlsplit(request.full_url).path
        self.posts.append(route)
        self.started.set()
        if route.endswith('/delete'):
            assert parse_qs(request.data.decode()) == {'bookmark_id': ['888']}
            if self.mode == 'fatal':
                return WriteHTTPResponse(429, b'{}')
            if self.mode == 'reject':
                return WriteHTTPResponse(200, b'{"error":true}')
            self.present = False
            if self.mode == 'cancel':
                assert self.release.wait(3)
                raise TimeoutError()
            if self.mode == 'unknown':
                raise TimeoutError()
            return WriteHTTPResponse(200, b'{"error":false}')
        assert route.endswith('/add')
        self.present = True
        return WriteHTTPResponse(200, b'{"error":false,"body":{"last_bookmark_id":"888"}}')


def payload(view):
    props = view.bookmark_button.props
    data = {'work_id': props['data-work-id'], 'generation': int(props['data-generation'])}
    for name, value in props.items():
        if name.startswith('data-bookmark-'):
            data[name.removeprefix('data-').replace('-', '_')] = int(value) if str(value).isdigit() else value
    return data


async def click(view, data=None):
    await view._on_learning_bookmark(SimpleNamespace(args=payload(view) if data is None else data))


@pytest.mark.parametrize('mode', ['ok', 'reject', 'stopped', 'fatal', 'unknown', 'cancel'])
async def test_delete_boundary(action_stack, tmp_path, monkeypatch, mode):
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route=f'/_sol_ad_{mode}')
    services.pixiv_actions = action_stack.actions
    try:
        await action_stack.actions.verify_account()
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        key = ('7', item.work_id)
        gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
        remote = Remote(action_stack, item, gate, mode=mode)
        if mode != 'ok':
            view._bookmark_confirmed.add(key)
        if mode == 'stopped':
            gate.trip(429)
        if mode == 'cancel':
            task = asyncio.create_task(click(view))
            assert await asyncio.to_thread(remote.started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            remote.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert action_stack.actions.state('7', 'illust', item.work_id).unknown
            assert key in view._bookmark_confirmed
            await click(view)
            assert not action_stack.actions.state('7', 'illust', item.work_id).unknown
            assert key not in view._bookmark_confirmed
        else:
            await click(view)
        assert remote.posts == ([] if mode == 'stopped' else ['/ajax/illusts/bookmarks/delete'])
        assert gate.snapshot().blocked is (mode in ('stopped', 'fatal'))
        if mode in ('reject', 'stopped', 'fatal', 'unknown'):
            assert key in view._bookmark_confirmed
            assert view.bookmark_button.props['icon'] == 'favorite'
        if mode == 'unknown':
            before = len(remote.gets)
            await click(view)
            assert len(remote.gets) > before
            assert remote.posts == ['/ajax/illusts/bookmarks/delete']
        if mode in ('ok', 'cancel', 'unknown'):
            assert remote.present is False
            assert key not in view._bookmark_confirmed
            assert view.bookmark_button.props['icon'] == 'favorite_border'
    finally:
        await cleanup()


async def test_replayed_click_after_completed_delete(action_stack, tmp_path, monkeypatch):
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_sol_ad_replay')
    services.pixiv_actions = action_stack.actions
    try:
        await action_stack.actions.verify_account()
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        remote = Remote(action_stack, item, ManagedNetworkStopGate(tmp_path / 'gate.sqlite'))
        old_payload = payload(view)
        await click(view, old_payload)
        assert remote.posts == ['/ajax/illusts/bookmarks/delete']
        await click(view, old_payload)
        assert remote.posts == ['/ajax/illusts/bookmarks/delete']
        await click(view)
        assert remote.posts == ['/ajax/illusts/bookmarks/delete', '/ajax/illusts/bookmarks/add']
    finally:
        await cleanup()




async def test_new_view_can_resolve_the_shared_unknown(action_stack, tmp_path, monkeypatch):
    view, services, _, cleanup = await open_discovery_view(tmp_path / 'first_view', monkeypatch, route='/_sol_ad_old_view')
    services.pixiv_actions = action_stack.actions
    await action_stack.actions.verify_account()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        remote = Remote(action_stack, item, ManagedNetworkStopGate(tmp_path / 'gate.sqlite'), mode='unknown')
        await click(view)
        assert remote.present is False
        assert action_stack.actions.state('7', 'illust', item.work_id).unknown
    finally:
        await cleanup()

    view, services, _, cleanup = await open_discovery_view(tmp_path / 'next_view', monkeypatch, route='/_sol_ad_new_view')
    services.pixiv_actions = action_stack.actions
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        assert view.state.current_artwork.work_id == item.work_id
        await click(view)
        assert not action_stack.actions.state('7', 'illust', item.work_id).unknown
        assert remote.posts == ['/ajax/illusts/bookmarks/delete']
        assert view.bookmark_button.props['icon'] == 'favorite_border'
    finally:
        await cleanup()


async def test_exact_toast_after_rejected_delete(action_stack, tmp_path, monkeypatch):
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_sol_ad_toast')
    services.pixiv_actions = action_stack.actions
    try:
        await action_stack.actions.verify_account()
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        remote = Remote(action_stack, item, ManagedNetworkStopGate(tmp_path / 'gate.sqlite'), mode='reject')
        await click(view)
        assert remote.posts == ['/ajax/illusts/bookmarks/delete']
        assert view.toast.kind == 'light'
        assert [node.text for node in view.toast.row.default_slot.children if hasattr(node, 'text')] == [
            'pixivのブックマーク切替に失敗しました', 'もう一度♥を押すとやり直せます',
        ]
        assert view.bookmark_button.props['icon'] == 'favorite'
    finally:
        await cleanup()
