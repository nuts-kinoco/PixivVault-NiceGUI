import asyncio
from types import SimpleNamespace

from z_learning_support_20261118 import open_discovery_view


async def test_existing_bookmark_is_removed_on_first_click(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            if intent.action == 'bookmark_add':
                return SimpleNamespace(status='already', remote_state=True)
            return SimpleNamespace(status='confirmed', remote_state=False)
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_existing')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        assert await view._bookmark_current(dict(work_id=item.work_id, generation=view.state.generation))
        assert [x.action for x in actions.calls] == ['bookmark_add', 'bookmark_delete']
        assert ('7', item.work_id) not in view._bookmark_confirmed
        assert view.status.text == 'pixivのブックマークを外した'
    finally:
        await cleanup()


async def test_unknown_delete_is_reconciled_without_resending(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []; self.reconciled = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            return SimpleNamespace(status='indeterminate', remote_state=None)
        async def reconcile(self, intent, **kwargs):
            self.reconciled.append(intent)
            return SimpleNamespace(status='observed', remote_state=False)
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_unknown')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        key = ('7', item.work_id)
        view._bookmark_confirmed.add(key)
        view._bookmark_new.add(key)
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        assert not await view._bookmark_current(payload)
        assert key in view._bookmark_confirmed
        assert await view._bookmark_current(payload)
        assert [x.action for x in actions.calls] == ['bookmark_delete']
        assert actions.reconciled == actions.calls
        assert key not in view._bookmark_confirmed and key not in view._bookmark_new
    finally:
        await cleanup()


async def test_duplicate_click_is_blocked_and_failed_delete_restores_mark(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.started = asyncio.Event(); self.release = asyncio.Event(); self.calls = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            self.started.set()
            await self.release.wait()
            return SimpleNamespace(status='rejected', remote_state=True)
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_busy')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        key = ('7', item.work_id)
        view._bookmark_confirmed.add(key)
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        first = asyncio.create_task(view._bookmark_current(payload))
        await asyncio.wait_for(actions.started.wait(), timeout=1)
        assert not await view._bookmark_current(payload)
        actions.release.set()
        assert not await first
        assert len(actions.calls) == 1 and actions.calls[0].action == 'bookmark_delete'
        assert key in view._bookmark_confirmed
        assert view.status.text == 'pixivのブックマーク切替に失敗しました'
    finally:
        await cleanup()


async def test_replayed_button_event_is_not_a_second_toggle(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            return SimpleNamespace(status='confirmed', remote_state=intent.action == 'bookmark_add')
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_replay')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        event = SimpleNamespace(args=dict(work_id=item.work_id, generation=view.state.generation,
                                          bookmark_token=view._bookmark_token))
        await view._on_learning_bookmark(event)
        await view._on_learning_bookmark(event)
        assert [i.action for i in actions.calls] == ['bookmark_add']
    finally:
        await cleanup()


async def test_cancelled_unknown_write_is_reconciled(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []; self.saved = None
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            self.saved = SimpleNamespace(status='indeterminate', remote_state=None)
            raise asyncio.CancelledError()
        def result(self, operation_id): return self.saved
        async def reconcile(self, intent, **kwargs):
            return SimpleNamespace(status='observed', remote_state=False)
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_cancel')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        key = ('7', item.work_id)
        view._bookmark_confirmed.add(key)
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        try:
            await view._bookmark_current(payload)
        except asyncio.CancelledError:
            pass
        assert key in view._bookmark_uncertain
        assert await view._bookmark_current(payload)
        assert len(actions.calls) == 1
    finally:
        await cleanup()


async def test_unknown_shared_action_is_recovered_after_view_recreation(tmp_path, monkeypatch):
    from pixivvault_nicegui.pixiv_write_client import WriteIntent
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []; self.intent = None
        def state(self, account, kind, target):
            return SimpleNamespace(unknown=True, operation_id='old')
        def result(self, operation_id):
            return SimpleNamespace(status='indeterminate', intent=self.intent)
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            raise AssertionError('unknown action must not be submitted again')
        async def reconcile(self, intent, **kwargs):
            self.calls.append(intent)
            return SimpleNamespace(status='observed', remote_state=False)
    view, services, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_ad_recreated')
    actions = services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        actions.intent = WriteIntent('old', '7', item.work_id, 'bookmark_delete', 'illust', item.author_id, 0)
        assert await view._bookmark_current(dict(work_id=item.work_id, generation=view.state.generation))
        assert actions.calls == [actions.intent]
    finally:
        await cleanup()
