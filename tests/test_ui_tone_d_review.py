"""Regression coverage for the D review findings, using isolated stores and views."""
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.preferences import PreferenceSpec, PreferencesStore
from pixivvault_nicegui.ui import preferences_view
from pixivvault_nicegui.ui.action_controls import (
    danger_button, dialog_actions, dialog_card, outlined_input, outlined_select,
    primary_button, refresh_button, retry_button, secondary_button,
)
from pixivvault_nicegui.ui.external_vault_view import ExternalVaultView
from pixivvault_nicegui.ui.native_profile_view import NativeProfileView
from pixivvault_nicegui.ui.preferences_view import PreferencesView, settings_group
from pixivvault_nicegui.ui.request_status_view import RequestStatusView
from pixivvault_nicegui.ui.shared_get_recovery_view import SharedGetRecoveryView
from pixivvault_nicegui.ui.shared_log_view import SharedLogView
from pixivvault_nicegui.ui.stop_migration_view import StopMigrationView


@pytest.fixture
def client():
    with Client(ui.page('/_tone_d_review')) as instance:
        try:
            yield instance
        finally:
            instance.delete()


def labels(container):
    return [item.text for item in container.descendants() if isinstance(getattr(item, 'text', None), str)]


def test_migration_retains_source_exclusions_and_preservation_note(client):
    view = StopMigrationView(object())
    text = '\n'.join(labels(view.dialog))
    assert '1 保存元 / 2 内容を確認 / 3 移行結果' in text
    assert '旧ライブラリの画像そのものやCookieを指定する必要はありません。' in text
    assert '元のVault・鍵・Cookieファイルは変更しません。' in text
    assert not view.run_button.enabled
    assert view.run_button.props['data-testid'] == 'migration-execute'


def test_interrupted_get_explicitly_rules_out_automatic_resend(client):
    view = SharedGetRecoveryView(object())
    text = '\n'.join(labels(view.dialog))
    assert '自動再送は行いません。' in text
    assert '記録済みのHTTP 401・403・429によるSTOPは解除しません。' in text
    assert not view.confirm_button.enabled
    view.close()


def test_image_quality_and_database_export_guidance_is_visible(client, tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    view = PreferencesView(store)
    try:
        view.open()
        text = '\n'.join(labels(view.dialog))
        assert '静止画保存: 保存元の原画像を維持します。画質を変更する設定はありません。' in text
        assert 'データベースのエクスポート' in text
    finally:
        view.close()
        store.close()


@pytest.mark.parametrize('embedded', [False, True])
def test_startup_save_is_distinct_from_global_save(client, tmp_path, embedded):
    store = NativeProfileStore(tmp_path / 'profile')
    view = NativeProfileView(store)
    try:
        view.build(embedded=embedded)
        assert view.save_button.props['data-testid'] == 'native-profile-save'
        if embedded:
            assert view.save_button.text == '起動設定を保存'
            assert view.save_button.props.get('outline') is True
            assert not view.save_button.props.get('flat')
        else:
            assert view.save_button.text == '保存'
            assert 'pv-btn-primary' in view.save_button.classes
    finally:
        view.close()
        store.close()


def fail_to_read():
    raise RuntimeError('fixture read failed')


@pytest.mark.parametrize('bad_snapshot', [False, True])
@pytest.mark.parametrize('failure', ['status', 'budget'])
def test_vault_error_opens_group_even_when_preferences_are_unreadable(client, tmp_path, bad_snapshot, failure):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    source = SimpleNamespace(snapshot=fail_to_read) if bad_snapshot else store
    vault = SimpleNamespace(status=fail_to_read if failure == 'status' else lambda: {},
                            premium_pool_status=fail_to_read)
    view = PreferencesView(source, vault=vault, enabled_keys={'meta_backfill_enabled'})
    try:
        view.open()
        group = next(item for item in view.dialog.descendants()
                     if isinstance(item, ui.expansion) and '状態を確認できません。' in labels(item))
        assert group.value
        statuses = [item for item in group.descendants() if item.props.get('data-testid') == 'premium-vault-state']
        assert len(statuses) == 1
        assert statuses[0].text == '状態を確認できません。'
        assert statuses[0].props['role'] == 'alert'
        if bad_snapshot:
            assert '設定を読み込めません。' in view.status.text
            save = next(item for item in view.dialog.descendants()
                        if item.props.get('data-testid') == 'preferences-save')
            assert not save.enabled
    finally:
        view.close()
        store.close()


@pytest.mark.asyncio
async def test_tagger_read_failure_opens_once_and_allows_manual_collapse(client, tmp_path):
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    with client:
        view = PreferencesView(store, tagger=SimpleNamespace(verify=fail_to_read, status='利用可能'))
        view.open()
    try:
        group = next(item for item in view.dialog.descendants()
                     if isinstance(item, ui.expansion) and view.tagger_status in item.descendants())
        assert not group.value
        await view._verify_tagger(view._epoch)
        assert view.tagger_status.text == '読込失敗'
        assert group.value
        group.set_value(False)
        view._display_tagger_status('読込失敗')
        assert not group.value
        view._display_tagger_status('利用可能')
        view._display_tagger_status('読込失敗')
        assert group.value
    finally:
        view.close()
        store.close()


def test_preferences_render_all_current_specs_and_a_new_group(client, tmp_path, monkeypatch):
    original = preferences_view.SPECS
    extra = PreferenceSpec('synthetic_review', '合成設定', '0', ('0', '1'), '追加の設定')
    monkeypatch.setattr(preferences_view, 'SPECS', (*original, extra))
    store = PreferencesStore(tmp_path / 'prefs.sqlite3')
    snapshot = store.snapshot()
    view = PreferencesView(SimpleNamespace(
        snapshot=lambda: SimpleNamespace(values={**snapshot.values, extra.key: extra.default})))
    try:
        view.open()
        expected = {spec.key for spec in (*original, extra)
                    if spec.group != 'local_tagger_internal' and spec.key != 'grid_card_size_mode'}
        assert set(view.controls) == expected
        assert extra.group in labels(view.dialog)
    finally:
        view.close()
        store.close()


def test_shared_control_identifiers_and_visual_contract(client):
    with dialog_card('確認', subtitle='説明', size='lg', testid='fixture-card') as card:
        field = outlined_input('値')
        choices = outlined_select(['A'])
        with dialog_actions() as actions:
            primary = primary_button('実行', icon='save', testid='fixture-primary')
            danger = danger_button('確定', testid='fixture-danger')
            secondary = secondary_button('取消', testid='fixture-secondary')
            outline = secondary_button('確認', outline=True)
            refresh = refresh_button(lambda: None, label='更新', testid='fixture-refresh')
            retry = retry_button(testid='fixture-retry')
    assert card.props['data-testid'] == 'fixture-card'
    assert 'pv-dialog-lg' in card.classes and 'pv-dialog-actions' in actions.classes
    assert labels(card)[:2] == ['確認', '説明']
    assert all(control.props.get('outlined') and control.props.get('dense') for control in (field, choices))
    assert [(button.props['data-testid'], css in button.classes) for button, css in
            ((primary, 'pv-btn-primary'), (danger, 'pv-btn-danger'), (secondary, 'pv-btn-secondary'),
             (retry, 'pv-retry-action'))] == [
            ('fixture-primary', True), ('fixture-danger', True), ('fixture-secondary', True), ('fixture-retry', True)]
    assert primary.props['icon'] == 'save' and primary.props.get('unelevated')
    assert danger.props.get('unelevated') and secondary.props.get('flat')
    assert outline.props.get('outline')
    assert refresh.props['aria-label'] == '更新'


def test_collapsible_heading_keeps_requested_test_id(client):
    with settings_group('設定項目', collapsible=True, default_open=False, heading_testid='fixture-heading') as group:
        ui.label('内容')
    assert not group.value
    assert any(element.props.get('data-testid') == 'fixture-heading' for element in group.descendants())


def test_request_status_retains_subtitle_when_tone_changes(client):
    records = []
    view = RequestStatusView(SimpleNamespace(progress_snapshot=lambda: records))
    try:
        records.append(dict(phase='waiting', status=429, attempt=1, limit=3, remaining_seconds=5, wait_seconds=5))
        view.refresh()
        assert {'pv-subtitle', 'pv-warning-banner'} <= set(view.label.classes)
        records[0].update(phase='stopped', attempt=3)
        view.refresh()
        assert {'pv-subtitle', 'pv-alert-banner'} <= set(view.label.classes)
        assert 'pv-warning-banner' not in view.label.classes
        assert view.label.props['role'] == 'status'
        records.clear()
        view.refresh()
        assert not view.label.visible
    finally:
        view.close()


def test_shared_log_icon_follows_expansion(client):
    view = SharedLogView(SimpleNamespace(snapshot=lambda: ()))
    try:
        assert view.button.props['icon'] == 'expand_less'
        view.toggle()
        assert view.button.props['icon'] == 'expand_more'
        assert view.button.props['aria-expanded'] == 'false'
        view.toggle()
        assert view.button.props['icon'] == 'expand_less'
        assert view.button.props['aria-expanded'] == 'true'
    finally:
        view.close()


def test_standalone_vault_creation_has_a_primary_action(client):
    view = ExternalVaultView(SimpleNamespace(external_vault=SimpleNamespace(
        status=lambda: dict(initialized=False, open=False, crypto_available=True,
                            transport_available=False, busy=False, slots=[]),
        premium_pool_status=lambda: ())))
    try:
        assert view.create_button.enabled
        assert 'pv-btn-primary' in view.create_button.classes
    finally:
        view.close()
