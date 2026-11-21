"""Layout contracts for the five B views, exercised through real NiceGUI elements."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.metadata_auto import MetadataStatus
from pixivvault_nicegui.metadata_runner import MetadataRunSummary
from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.following_sync_view import FollowingSyncView
from pixivvault_nicegui.ui.metadata_view import MetadataView
from pixivvault_nicegui.ui.preferences_view import PreferencesView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.unfollowed_authors_view import UnfollowedAuthorsView
from pixivvault_nicegui.ui.view_history_view import ViewHistoryView


@pytest.fixture
def client():
    client = Client(ui.page('/_test_ui_tone_b'))
    try:
        yield client
    finally:
        client.delete()


def has_heading(root, title, *classes):
    return any(getattr(element, 'text', None) == title and set(classes) <= set(element.classes)
               for element in root.descendants())


@pytest.mark.asyncio
async def test_scheduler_primary_actions_share_toolbar_and_keep_control_ids(client):
    with client:
        view = SchedulerView(SimpleNamespace(managed_scheduler=object()))
    try:
        assert has_heading(view.root, 'お気に入り作者の定期チェック', 'pv-library-heading', 'pv-serif-heading')
        toolbar = view.run_button.parent_slot.parent
        assert 'pv-toolbar' in toolbar.classes
        assert view.save_button.parent_slot.parent is toolbar
        assert view.refresh_button.props['aria-label'] == '状態更新'
        assert 'pv-filter-switch' in view.startup.classes
        assert view.startup.props['data-testid'] == 'scheduler-startup'
        assert not view.open_button.visible
    finally:
        view.close()


@pytest.mark.asyncio
async def test_metadata_ledger_separates_local_operations_from_pixiv_acquisition(client):
    with client:
        view = MetadataView(SimpleNamespace())
    try:
        local = next((e for e in view.root.descendants() if e.props.get('data-testid') == 'metadata-ledger-local'), None)
        remote = next((e for e in view.root.descendants() if e.props.get('data-testid') == 'metadata-ledger-remote'), None)
        assert local is not None and remote is not None
        assert view.inspect_button in local.descendants()
        assert view.ack_button in local.descendants()
        assert view.run_button not in local.descendants()
        assert view.run_button in remote.descendants()
        assert has_heading(remote, 'Pixivから取得（通信あり）')
    finally:
        view.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['run_all', 'run_one'])
@pytest.mark.parametrize('failure', [MetadataRunSummary(target=1, stopped='network_stopped'),
                                     MetadataRunSummary(target=1, work_error=1)])
async def test_metadata_result_failure_is_announced_and_normal_result_clears_color(client, operation, failure):
    summary = failure
    services = SimpleNamespace(managed_metadata_status=lambda: MetadataStatus(0, 1, 1, 0, 0),
                               run_managed_metadata_all=lambda: summary,
                               run_managed_metadata_one=lambda *_args, **_kwargs: summary,
                               managed_metadata_progress=lambda: None)
    with client:
        view = MetadataView(services)
        view.revision = 1
        view.work_id.set_value('1')
        try:
            await getattr(view, operation)()
            assert view.run_status.props['role'] == 'status'
            assert 'pv-tone-b-error' in view.run_status.classes
            summary = MetadataRunSummary(target=1, filled=1)
            await getattr(view, operation)()
            assert 'pv-tone-b-error' not in view.run_status.classes
        finally:
            view.close()


@pytest.mark.asyncio
async def test_preferences_owns_the_only_following_disclosure(client, monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    with client:
        following = FollowingSyncView(SimpleNamespace())
        preferences = PreferencesView(PreferencesStore(tmp_path / 'preferences.sqlite3'), following_sync=following)
        try:
            preferences.open()
            await following._initial_task
            group = following.root.parent_slot.parent
            assert isinstance(group, ui.expansion) and not group.value
            assert group.props['label'] == 'フォロー中リストの同期'
            assert not has_heading(following.root, 'フォロー中リストの同期')
            assert not any(isinstance(e, ui.expansion) for e in following.root.descendants())
            assert following.rows.parent_slot.parent is following.root
            assert following.status.props['role'] == 'status'
            assert following.status.text == '' and following.status.visible
            assert 'pv-tone-b-notice' in following.status.classes
        finally:
            preferences.close()


@pytest.mark.asyncio
async def test_history_read_error_uses_live_status_and_clears_on_success(client):
    failing = True

    async def history():
        if failing:
            raise OSError('simulated read failure')
        return []

    with client:
        parent = SimpleNamespace(services=SimpleNamespace(), root=ui.column(), active=True)
        view = ViewHistoryView(parent, SimpleNamespace(view_history=history))
        try:
            await view.refresh()
            assert view.notice.props['role'] == 'status'
            assert 'pv-tone-b-error' in view.notice.classes
            failing = False
            await view.refresh()
            assert 'pv-tone-b-error' not in view.notice.classes
        finally:
            view.close()


@pytest.mark.asyncio
async def test_scheduler_failed_status_keeps_live_region(client):
    with client:
        view = SchedulerView(SimpleNamespace(managed_scheduler=None))
    try:
        view._show_status('読み込みに失敗しました', error=True)
        assert view.status.props.get('role') == 'status'
        assert 'pv-tone-b-error' in view.status.classes
        view._show_status('完了')
        assert 'pv-tone-b-error' not in view.status.classes
    finally:
        view.close()


@pytest.mark.asyncio
async def test_metadata_groups_primary_controls_and_compacts_ledger(client):
    with client:
        view = MetadataView(SimpleNamespace())
    try:
        assert has_heading(view.root, 'メタデータ後埋め', 'pv-library-heading', 'pv-serif-heading')
        toolbar = view.run_all_button.parent_slot.parent
        assert 'pv-toolbar' in toolbar.classes
        assert view.stop_button.parent_slot.parent is toolbar
        assert view.refresh_button.props['aria-label'] == '状態更新'
        assert view.work_id.props['outlined'] and view.work_id.props['dense']
        for button in (view.inspect_button, view.ack_button, view.resume_interrupted_button,
                       view.resume_blocked_button, view.run_button):
            assert button.props['flat'] and button.props['dense']
    finally:
        view.close()


@pytest.mark.asyncio
async def test_history_empty_state_and_footer_follow_dialog_layout(client):
    with client:
        parent = SimpleNamespace(services=SimpleNamespace(), root=ui.column(), active=True)
        view = ViewHistoryView(parent, None)
    try:
        view.render()
        assert has_heading(view.dialog, '閲覧履歴（学習タブ）', 'pv-dialog-title')
        assert has_heading(view.body, '閲覧履歴はありません。', 'pv-empty')
        card = view.dialog.default_slot.children[0]
        footer = card.default_slot.children[-1]
        assert [e.props['data-testid'] for e in footer.default_slot.children] == [
            'view-history-export', 'view-history-close']
        assert 'justify-end' in footer.classes
        assert view.confirm.props['inputmode'] == 'numeric'
        assert view.clear_button.props['color'] == 'amber'
    finally:
        view.close()


@pytest.mark.asyncio
async def test_following_saved_rows_are_inside_a_closed_expansion(client, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    with client:
        view = FollowingSyncView(SimpleNamespace())
        view.build()
    try:
        await view._initial_task
        assert has_heading(view.root, 'フォロー中リストの同期', 'pv-serif-heading')
        expansion = view.rows.parent_slot.parent
        assert expansion.props['label'] == '同期済みの作者一覧を見る'
        assert not expansion.value
        assert [e for e in view.root.descendants() if isinstance(e, ui.expansion)] == [expansion]
        assert view.more_button.props['flat']
        assert [e for e in view.root.descendants() if e.props.get('role') == 'status'] == [view.status]
        assert view.status.text == '' and view.status.visible
        assert 'pv-tone-b-notice' in view.status.classes
    finally:
        view.close()


@pytest.mark.asyncio
async def test_unfollowed_filters_and_initial_status_are_compact(client):
    with client:
        view = UnfollowedAuthorsView(SimpleNamespace())
    try:
        assert has_heading(view.dialog, 'ブックマークの未フォロー作者', 'pv-dialog-title')
        toolbar = view.fetch_button.parent_slot.parent
        assert 'pv-toolbar' in toolbar.classes
        for field in (view.kind, view.visibility):
            assert field.parent_slot.parent is toolbar
            assert field.props['outlined'] and field.props['dense']
        assert 'pv-empty' in view.notice.classes
        assert view.notice.props['role'] == 'status'
        assert view.more.props['flat']
    finally:
        view.close()
