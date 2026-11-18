"""世間ランキングの「好みタグ」の集合（元 ranking_tab.py:519-544 の _refresh_bounds / _has_preferred_tag）。

元は生カウントを discovery_ranking.build_bounds で (下限, 上限, 有効試行数) にし、
下限が NEUTRAL を超え、かつ有効試行数が TREND_MIN_N 以上のタグを持つ作品へ青ハート印を付ける。
未知のタグは (NEUTRAL, 1.0, 0.0) なので、集合に入らないことと同じ。
"""
from . import discovery_ranking


def preferred_tag_set(preferences):
    """{正規化タグ: 生カウント} から、好みのタグの集合（正規化タグ）を返す。"""
    bounds = discovery_ranking.build_bounds(preferences)
    return frozenset(tag for tag, (lower, _upper, n) in bounds.items()
                     if lower > discovery_ranking.NEUTRAL and n >= discovery_ranking.TREND_MIN_N)
