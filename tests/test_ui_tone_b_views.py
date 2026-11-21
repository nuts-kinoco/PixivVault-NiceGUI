"""Layout contracts for the five B views, exercised through real NiceGUI elements."""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.ui.following_sync_view import FollowingSyncView
from pixivvault_nicegui.ui.metadata_view import MetadataView
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
        assert view.more_button.props['flat']
        assert view.status.props['role'] == 'status'
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
