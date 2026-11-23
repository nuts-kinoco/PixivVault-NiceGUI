from types import SimpleNamespace

from nicegui import Client, ui

from pixivvault_nicegui.queue_log import QueueLog
from pixivvault_nicegui.ui import action_controls
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from pixivvault_nicegui.ui.shared_log_view import SharedLogView


def test_important_notification_uses_explicit_keyboard_dismissal(monkeypatch):
    sent = []
    monkeypatch.setattr(ui, 'notify', lambda message, **options: sent.append((message, options)))
    monkeypatch.setattr(ui, 'notification', lambda *, options, **_: sent.append((options['message'], options)))

    action_controls.notify_action('保存できませんでした。', type='negative')
    action_controls.notify_action('結果を確認してください。', type='warning', important=True)
    action_controls.notify_action('保存しました。', type='positive')

    assert [message for message, _ in sent] == [
        '保存できませんでした。', '結果を確認してください。', '保存しました。',
    ]
    for _, options in sent[:2]:
        assert [action['label'] for action in options['actions']] == ['閉じる']
        assert options['timeout'] == 0
    assert 'timeout' not in sent[2][1]
    assert not sent[2][1].get('actions')


def test_compact_actions_opt_into_shared_hit_area():
    client = Client(ui.page('/_common_hit_area'))
    try:
        with client:
            buttons = (
                action_controls.refresh_button(lambda: None, label='更新', testid='refresh'),
                action_controls.retry_button(testid='retry'),
                action_controls.primary_button('保存'),
                action_controls.secondary_button('取消'),
                action_controls.danger_button('削除'),
            )
        assert all('pv-hit28' in button.classes for button in buttons)
    finally:
        client.delete()


def test_shared_log_controls_region_and_replaces_empty_state():
    client = Client(ui.page('/_common_empty_log'))
    log = QueueLog()
    try:
        with client:
            view = SharedLogView(log)
            view.show_tab('scheduler')
            assert view.button.props['aria-controls'] == view.panel.html_id
            assert view.panel.props['tabindex'] == 0
            assert [child.text for child in view.panel.default_slot.children] == ['ログはまだありません。']

            log.append('試行を終了しました。', level='info')
            view.refresh()
            assert len(view.panel.default_slot.children) == 1
            assert view.panel.default_slot.children[0].text.endswith('試行を終了しました。')

            view.toggle()
            assert not view.panel.visible
            assert view.button.props['aria-expanded'] == 'false'
    finally:
        client.delete()


def test_request_status_reserves_no_space_without_message():
    client = Client(ui.page('/_common_request_status'))
    records = []
    try:
        with client:
            view = RequestStatusView(SimpleNamespace(progress_snapshot=lambda: records))
            assert 'pv-hide-empty' in view.label.classes
            assert view.label.props['role'] == 'status'
            assert not view.label.visible

            records.append({'phase': 'failed', 'attempt': 2, 'limit': 2, 'status': None})
            view.refresh()
            assert view.label.visible and 'pv-alert-banner' in view.label.classes

            records.clear()
            view.refresh()
            assert not view.label.visible
    finally:
        client.delete()


def test_shared_log_skips_scroll_when_event_loop_is_closed(monkeypatch):
    import asyncio
    from nicegui import core

    loop = asyncio.new_event_loop()
    loop.close()
    monkeypatch.setattr(core, 'loop', loop)
    client = Client(ui.page('/_common_closed_loop_log'))
    try:
        with client:
            view = SharedLogView(QueueLog())
            view.show_tab('scheduler')
            assert view.root.visible and view.panel.visible
            view.close()
    finally:
        client.delete()
