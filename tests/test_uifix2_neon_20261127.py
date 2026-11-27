from pathlib import Path
from types import SimpleNamespace as NS
from threading import Lock, Event

from pixivvault_nicegui.ui.automation_dialog import summaries
from pixivvault_nicegui.ui.theme import CSS

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'pixivvault_nicegui' / 'ui'
def source(name):
    return (ROOT / name).read_text(encoding='utf8')

def test_learning_metadata_is_projected_and_bounded():
    s = source('learning_view.py')
    assert 'self.candidate_author' in s and 'self.candidate_tags' in s
    assert "item.tags[:4]" in s and "item.author" in s
    assert '.pv-learning-meta' in CSS and 'text-overflow:ellipsis' in CSS

def test_history_warning_is_present():
    assert '削除すると元に戻せません。件数を入力すると「全件削除」が押せます。' in source('view_history_view.py')

def test_nondense_filter_switch_is_border_box():
    assert '.pv-filter-switch:not(.q-toggle--dense) .q-toggle__inner { box-sizing:border-box; }' in CSS

def test_cache_limit_visibility_applies_to_grid_field():
    s = source('preferences_view.py')
    assert 'self._cache_limit_fields[key].set_visibility(visible)' in s

def test_disabled_automation_hides_stale_next_times():
    state=NS(interval_hours=0, startup_check=False, last_due_utc='last', next_due_utc='stale')
    runner=NS(display_state=(state, None), display_phase='idle', display_failure=None)
    services=NS(managed_schedule_runner=runner, managed_scheduler=NS(), managed_downloads=object(),
                _metadata_auto_seen=False, _metadata_run_lock=Lock(), _metadata_cancel_event=Event(),
                _metadata_auto=NS(next_run_display='stale'), live_client=None)
    result=summaries(services)
    assert all(x.state == '停止（設定 OFF）' and x.next is None and '次回 —' in x.line(k) for k,x in result.items())

def test_quality_is_a_separate_badge():
    s=source('duplicate_view.py')
    assert "ui.badge(quality)" in s
    assert "bytes · {quality}" not in s

def test_selection_buttons_use_outline():
    assert 'outline dense data-testid=bookmark-download' in source('bookmark_view.py')
    assert 'outline data-testid=queue-select-mode' in source('job_views.py')

async def test_learning_metadata_refreshes_and_clears(tmp_path, monkeypatch):
    from dataclasses import replace
    from z_learning_support_20261118 import open_discovery_view
    view, _, _, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=1)
    try:
        item = view.state.current_artwork
        view.state.artworks = (replace(item, author='作者 A', tags=('一', '二', '三', '四', '五')),)
        with view.client:
            view.refresh()
        assert view.candidate_author.text == '作者 A'
        assert view.candidate_tags.text == '一・二・三・四'
        assert view.candidate_tags.props['title'] == '一・二・三・四'
        view.state.artworks = (replace(item, author='', tags=()),)
        with view.client:
            view.refresh()
        assert not view.candidate_author.visible and not view.candidate_tags.visible
    finally:
        await cleanup()


def test_cache_fields_retain_draft_values_across_modes():
    from pixivvault_nicegui.ui.preferences_view import PreferencesView, CACHE_LIMIT_FIELDS
    class Field:
        def __init__(self): self.visible = None
        def set_visibility(self, value): self.visible = value
    fields = {key: Field() for key in CACHE_LIMIT_FIELDS.values()}
    controls = {key: NS(value='draft') for key in fields}
    mode = NS(value=next(iter(CACHE_LIMIT_FIELDS)))
    controls['discovery_cache_limit_mode'] = mode
    view = NS(controls=controls, _cache_limit_fields=fields, _cache_limit_notes={})
    PreferencesView._sync_cache_limit_visibility(view)
    assert sum(f.visible for f in fields.values()) == 1
    mode.value = list(CACHE_LIMIT_FIELDS)[1]
    PreferencesView._sync_cache_limit_visibility(view)
    assert fields[CACHE_LIMIT_FIELDS[mode.value]].visible
    assert all(controls[k].value == 'draft' for k in fields)
