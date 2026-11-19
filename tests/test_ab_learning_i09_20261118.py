from types import SimpleNamespace
from z_learning_support_20261118 import open_discovery_view, settled


async def test_bookmark_rejects_unloaded_and_stale_work(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i09_guard')
    try:
        item = view.state.current_artwork
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        assert not await view._bookmark_current(payload)
        view._on_loaded(SimpleNamespace(args=view._image_url))
        assert not await view._bookmark_current(dict(work_id='999', generation=view.state.generation))
        assert not await view._bookmark_current(dict(work_id=item.work_id, generation=view.state.generation - 1))
    finally:
        await cleanup()

async def test_bookmark_toggles_registered_without_duplicate_add(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            if intent.action == 'bookmark_add':
                return SimpleNamespace(status='confirmed', remote_state=True)
            return SimpleNamespace(status='confirmed', remote_state=False)
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i09_add')
    actions = Actions()
    services.pixiv_actions = actions
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        assert await view._bookmark_current(payload)
        assert len(actions.calls) == 1
        assert actions.calls[0].action == 'bookmark_add'
        assert await view._bookmark_current(payload)
        assert len(actions.calls) == 2
        assert actions.calls[1].action == 'bookmark_delete'
    finally:
        await cleanup()


async def test_stopped_gate_refuses_post_before_dispatch(tmp_path, action_stack):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.pixiv_write_client import WriteIntent
    gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
    gate.trip(429)
    action_stack.actions._writer._post_admission = gate.request
    account = await action_stack.actions.verify_account()
    outcome = await action_stack.actions.execute(WriteIntent('i09-stop', account, '91', 'follow', 'illust', '91', 0))
    assert outcome.status == 'rejected'
    assert not action_stack.http.posts


from test_original_author_follow import action_stack


async def test_unknown_bookmark_is_not_submitted_again(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        def __init__(self): self.calls = []
        async def execute(self, intent, **kwargs):
            self.calls.append(intent)
            return SimpleNamespace(status='indeterminate', remote_state=None)
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i09_unknown')
    actions = Actions()
    services.pixiv_actions = actions
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        assert not await view._bookmark_current(payload)
        assert not await view._bookmark_current(payload)
        assert len(actions.calls) == 1
    finally:
        await cleanup()


async def test_fatal_post_stops_gate_and_remains_uncertain(tmp_path, action_stack):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.pixiv_write_client import WriteIntent, WriteHTTPResponse
    gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
    writer = action_stack.actions._writer
    writer._post_admission = gate.request
    writer._transport = lambda *args: WriteHTTPResponse(429, b'{}')
    account = await action_stack.actions.verify_account()
    outcome = await action_stack.actions.execute(WriteIntent('i09-fatal', account, '91', 'follow', 'illust', '91', 0))
    assert outcome.status == 'indeterminate'
    assert gate.snapshot().blocked and gate.snapshot().status_code == 429


async def test_confirmed_bookmark_is_recorded_with_judgment(tmp_path, monkeypatch):
    class Actions:
        cached_verified_account_id = '7'
        async def execute(self, intent, **kwargs):
            return SimpleNamespace(status='confirmed', remote_state=True)
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i09_ledger')
    services.pixiv_actions = Actions()
    try:
        view._on_loaded(SimpleNamespace(args=view._image_url))
        item = view.state.current_artwork
        payload = dict(work_id=item.work_id, generation=view.state.generation)
        assert await view._bookmark_current(payload)
        assert view.judge('like', dict(payload, operation_id='bookmark-judge'))
        await settled(view)
        rows = (await service.review_snapshot(view._session_id))['rows']
        assert next(r for r in rows if r['work_id'] == item.work_id)['bookmarked'] == 1
    finally:
        await cleanup()
