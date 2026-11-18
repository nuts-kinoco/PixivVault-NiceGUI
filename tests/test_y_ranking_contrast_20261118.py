"""ランキングの追加表示はライト画面で 4.5:1 以上。"""
import re

from pixivvault_nicegui.ui.theme import CSS
from test_q_shell_20261117 import DARK, LIGHT, contrast


def test_light_ranking_heart_and_filter_chip_contrast():
    light = {**DARK, **LIGHT}
    rule = re.search(r'body\.body--light\s+\.pv-grid\s+\.pv-card-heart\s*\{([^}]*)\}', CSS)
    assert rule, 'ライトではカード背景に十分な濃さの青を指定する'
    assert re.search(r'color\s*:\s*var\(--pv-fill\)', rule.group(1))
    assert contrast(light['--pv-fill'], light['--pv-bg-card']) >= 4.5
    assert contrast(light['--pv-text-main'], light['--pv-bg-card']) >= 4.5
    assert contrast(light['--pv-text-muted'], light['--pv-bg-card']) >= 4.5
