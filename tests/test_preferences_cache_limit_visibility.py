"""S16: one active cache-limit unit, with both values retained across reopening."""
import pytest
from nicegui import Client, ui

from pixivvault_nicegui.preferences import PreferencesStore
from pixivvault_nicegui.ui.preferences_view import PreferencesView

CACHE_KEYS = {
    'discovery_cache_limit_mode',
    'discovery_cache_size_mb',
    'discovery_cache_size_count',
}


@pytest.mark.asyncio
async def test_cache_limit_unit_switch_keeps_both_drafts_and_reopens_saved_mode(tmp_path):
    path = tmp_path / 'settings.sqlite3'
    store = PreferencesStore(path)
    client = Client(ui.page('/_s16_cache_limit_units'))
    applied = []
    try:
        with client:
            view = PreferencesView(store, enabled_keys=CACHE_KEYS, on_change=applied.append)
            view.open()
            mode = view.controls['discovery_cache_limit_mode']
            mb = view.controls['discovery_cache_size_mb']
            count = view.controls['discovery_cache_size_count']
            assert mode.value == 'bytes' and mb.visible and not count.visible
            assert mb.enabled and count.enabled
            mb.set_value('2048')
            mode.set_value('count')
            assert count.visible and not mb.visible
            count.set_value('200')
            mode.set_value('bytes')
            assert mb.visible and not count.visible
            assert (mb.value, count.value) == ('2048', '200')
            assert applied == [] and store.snapshot().revision == 0
            mode.set_value('count')
            assert view.save()
            assert len(applied) == 1
            saved = store.snapshot()
            assert saved.values['discovery_cache_limit_mode'] == 'count'
            assert saved.values['discovery_cache_size_mb'] == '2048'
            assert saved.values['discovery_cache_size_count'] == '200'
            view.close_dialog()
            view.open()
            assert view.controls['discovery_cache_size_count'].visible
            assert not view.controls['discovery_cache_size_mb'].visible
            assert view.controls['discovery_cache_size_mb'].value == '2048'
            assert view.controls['discovery_cache_size_count'].value == '200'
            # A queued event from the old, deleted form must not affect the new one.
            mode.set_value('bytes')
            assert view.controls['discovery_cache_limit_mode'].value == 'count'
            assert view.controls['discovery_cache_size_count'].visible
            assert not view.controls['discovery_cache_size_mb'].visible
            view.close()
    finally:
        client.delete()
        store.close()

    store = PreferencesStore(path)
    client = Client(ui.page('/_s16_cache_limit_units_restart'))
    try:
        with client:
            view = PreferencesView(store, enabled_keys=CACHE_KEYS)
            view.open()
            assert view.controls['discovery_cache_limit_mode'].value == 'count'
            assert view.controls['discovery_cache_size_count'].visible
            assert not view.controls['discovery_cache_size_mb'].visible
            assert view.controls['discovery_cache_size_count'].value == '200'
            assert view.controls['discovery_cache_size_mb'].value == '2048'
            view.close()
    finally:
        client.delete()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,active,inactive', [
    ('bytes', 'discovery_cache_size_mb', 'discovery_cache_size_count'),
    ('count', 'discovery_cache_size_count', 'discovery_cache_size_mb'),
])
async def test_unavailable_cache_consumer_shows_only_active_unit_and_reason(
        tmp_path, mode, active, inactive):
    store = PreferencesStore(tmp_path / 'settings.sqlite3')
    store.set_setting('discovery_cache_limit_mode', mode)
    client = Client(ui.page('/_s16_unavailable_' + mode))
    try:
        with client:
            view = PreferencesView(store)
            view.open()
            assert view.controls[active].visible and not view.controls[active].enabled
            assert not view.controls[inactive].visible
            notes = {element.props.get('data-testid'): element
                     for element in client.elements.values()
                     if str(element.props.get('data-testid', '')).startswith('preference-unavailable-')}
            assert notes['preference-unavailable-' + active].visible
            assert not notes['preference-unavailable-' + inactive].visible
            assert '未接続' in notes['preference-unavailable-' + active].text
            view.close()
    finally:
        client.delete()
        store.close()

