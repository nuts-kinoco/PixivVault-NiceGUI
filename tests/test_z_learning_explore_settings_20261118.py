"""FL-I12: 探索の設定（「知らないジャンルを見る割合」「知らない絵柄を見る割合」スライダーのドロワー。
元 discovery_tab.py:3176-3330 のつまみ・引き出し, 3295-3302 の見出しと吹き出し, 3360 の説明）。

元は即時スライダー: ドラッグ中は右端の「N%」だけ書き換え、指を離したときだけ保存する。保存しても画面（候補・Undo）は作り直さない。
"""
import asyncio
from types import SimpleNamespace

import pytest
from nicegui import ui

from z_learning_support_20261118 import open_discovery_view, settled


@pytest.fixture
async def dview(tmp_path, monkeypatch):
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, count=5)
    try:
        yield view, service
    finally:
        await cleanup()


def texts(view):
    return [str(getattr(e, 'text', '')) for e in view.root.descendants() if getattr(e, 'text', None)]


def by_testid(view, testid):
    found = [e for e in view.root.descendants() if getattr(e, 'props', {}).get('data-testid') == testid]
    return found[0] if found else None


def click(button):
    for listener in button._event_listeners.values():
        if listener.type == 'click':
            return listener.handler(SimpleNamespace(args={}))
    raise AssertionError('no click handler')


async def idle(view):
    async with asyncio.timeout(10):
        while view._settings_busy:
            await asyncio.sleep(.01)


async def toggle(view):
    click(by_testid(view, 'discovery-settings-open'))   # NiceGUI が async ハンドラーを背景タスクで走らせる
    await asyncio.sleep(0.05)
    await idle(view)


async def open_drawer(view):
    await toggle(view)


async def test_header_row_is_explore_settings_with_original_tooltip(dview):
    view, _ = dview
    header = by_testid(view, 'discovery-settings-open')
    assert '探索の設定' in [getattr(e, 'text', None) for e in header.descendants()] + [header.text]
    assert 'どのくらい知らないものを見るかを変えます' in [e.text for e in header.descendants() if isinstance(e, ui.tooltip)]
    assert view.settings_panel.visible is False
    assert not hasattr(view, 'settings_dialog')                       # ダイアログ＋保存ボタンではなくドロワー


async def test_opening_shows_two_original_sliders_with_percent_text(dview):
    view, _ = dview
    await open_drawer(view)
    assert view.settings_panel.visible
    assert by_testid(view, 'discovery-settings-open').props['aria-expanded'] == 'true'
    t = texts(view)
    assert '知らないジャンルを見る割合' in t and '知らない絵柄を見る割合' in t
    assert (view.settings_subject.value, view.settings_style.value) == (50, 20)   # 元 既定 50% / 20%
    assert view.settings_subject_text.text == '50%' and view.settings_style_text.text == '20%'
    assert isinstance(view.settings_subject, ui.slider) and isinstance(view.settings_style, ui.slider)
    assert view.settings_subject.props['min'] == 0 and view.settings_subject.props['max'] == 100
    assert '設定を保存' not in t and '設定を読込' not in t               # 保存ボタンは無い（離したら保存）
    assert '判定は保存され、次に出す候補の並べ替えに反映されます。' in t


async def test_dragging_only_rewrites_the_percent_text(dview):
    view, service = dview
    await open_drawer(view)
    view.settings_subject.set_value(73)
    assert view.settings_subject_text.text == '73%'
    snap = await service.settings_snapshot()
    assert float(snap.values['discovery_ratio_subject']) == 50.0       # まだ保存されない


async def test_release_saves_without_rebuilding_the_screen(dview):
    view, service = dview
    await settled(view)
    work = view.state.current_artwork.work_id
    view._on_loaded(SimpleNamespace(args=view._image_url))
    assert view.judge('like', dict(work_id=work, generation=view.state.generation, operation_id='l1'))
    await settled(view)
    state_before, current = view.state, view.state.current_artwork.work_id
    await open_drawer(view)
    view.settings_subject.set_value(30)
    view.settings_style.set_value(40)
    await view.save_discovery_settings()
    snap = await service.settings_snapshot()
    assert float(snap.values['discovery_ratio_subject']) == 30.0 and float(snap.values['discovery_ratio_style']) == 40.0
    assert view.state is state_before and view.state.current_artwork.work_id == current   # 候補・Undo は作り直さない
    assert view.state.can_undo


async def test_reopening_shows_saved_values(dview):
    view, service = dview
    await open_drawer(view)
    view.settings_subject.set_value(10)
    await view.save_discovery_settings()
    await toggle(view)                                                  # 閉じる
    assert view.settings_panel.visible is False
    view.settings_subject.set_value(99)                                  # 閉じている間に値が動いても
    await open_drawer(view)
    assert view.settings_subject.value == 10 and view.settings_subject_text.text == '10%'


async def test_second_click_closes_and_deactivation_closes(dview):
    view, _ = dview
    await open_drawer(view)
    await toggle(view)
    assert view.settings_panel.visible is False
    assert by_testid(view, 'discovery-settings-open').props['aria-expanded'] == 'false'
    await open_drawer(view)
    view.set_active(False)
    assert view.settings_panel.visible is False


async def test_search_mode_remains_reachable_and_saves(dview):
    view, service = dview
    await open_drawer(view)
    assert view.settings_mode.options == {'all': 'すべて', 'safe': '全年齢', 'r18': 'R-18'}
    view.settings_mode.set_value('safe')            # 検索対象は選んだ時点で保存する
    await asyncio.sleep(0)
    await idle(view)
    assert (await service.settings_snapshot()).values['discovery_search_mode'] == 'safe'


async def test_conflicting_write_keeps_original_message(dview):
    view, service = dview
    await open_drawer(view)
    snap = await service.settings_snapshot()
    await service._wait(service.submit_settings_update(snap.revision, dict(snap.values), 'other-view'))
    view.settings_subject.set_value(5)
    await view.save_discovery_settings()
    assert '別の画面で設定が変わりました' in view.settings_status.text
