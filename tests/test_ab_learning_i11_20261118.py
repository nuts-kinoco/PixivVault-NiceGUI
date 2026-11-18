from pixivvault_nicegui.learning_trend_layout import PreferenceCapacity, displayed_rows


def test_rail_capacity_tracks_measured_height_with_hysteresis():
    capacity = PreferenceCapacity()
    assert capacity.update(17) == 1
    assert capacity.update(42.4) == 1
    assert capacity.update(42.5) == 2
    assert capacity.update(41.6) == 2
    assert capacity.update(41.4) == 1


def test_band_limits_to_six_and_uses_visible_scores():
    rows = [dict(score=score) for score in (9, 6, 3, 0, -1, -2, 100)]
    shown = displayed_rows(rows, 'band', 99)
    assert len(shown) == 6
    assert [item[1] for item in shown[:3]] == [1, 2 / 3, 1 / 3]
    assert displayed_rows(rows, 'fold', 99) == displayed_rows(rows[:5], 'fold', 99)

def test_mode_uses_image_area_and_narrow_fold():
    from pixivvault_nicegui.learning_trend_layout import layout_mode
    assert layout_mode(700, 700, 1.0, 'rail') == 'fold'
    assert layout_mode(1920, 900, 1.0, 'rail') == 'rail'
    assert layout_mode(1100, 900, 2.0, 'rail') == 'band'


async def test_learning_panel_responds_to_measured_size(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i11_measure')
    try:
        rows = [dict(display=f'tag{i}', score=10-i, like_count=i, shown_count=i+1, at_cap=False) for i in range(10)]
        with view.client:
            view._render_trend(dict(judged=20, ready=True, rows=rows))
            view._on_trend_measure(SimpleNamespace(args=dict(width=1920, height=900, viewport=67)))
            assert view._trend_mode == 'rail'
            assert view._trend_visible_rows == 3
            view._on_trend_measure(SimpleNamespace(args=dict(width=1100, height=900, viewport=67, aspect=2)))
            assert view._trend_mode == 'band'
            assert view._trend_visible_rows == 6
            view._on_trend_measure(SimpleNamespace(args=dict(width=700, height=900, viewport=67)))
            assert view._trend_mode == 'fold'
            assert not view.trend_content.visible
    finally:
        await cleanup()


async def test_trend_empty_messages_match_original(tmp_path, monkeypatch):
    from z_learning_support_20261118 import open_discovery_view
    view, services, service, cleanup = await open_discovery_view(tmp_path, monkeypatch, route='/_i11_empty')
    try:
        with view.client:
            view._render_trend(dict(judged=19, ready=False, rows=[]))
            texts = [getattr(e, 'text', '') for e in view.trend_content.descendants()]
            assert 'いまはあなたの保存から出しています' in texts
            view._render_trend(dict(judged=20, ready=True, rows=[]))
            texts = [getattr(e, 'text', '') for e in view.trend_content.descendants()]
            assert 'まだ傾向が出ていません' in texts
    finally:
        await cleanup()
