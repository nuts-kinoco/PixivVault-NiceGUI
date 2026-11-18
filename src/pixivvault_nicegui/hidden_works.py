"""「出さない」作品の読み書き。おすすめと同じ非表示データ（RecommendationController）を共有する。

ランキングの「出さない」と一覧管理は、ここ経由で recommend と同じ専用設定の上書き表に書く。
別の保存先は作らない。通信はしない。
"""
from __future__ import annotations

from .recommendation_state import RecommendationError


class HideUnavailable(RuntimeError):
    """出さない設定の保存先が無い（未接続、または閲覧のみ）。"""


def _state(recommendations):
    try:
        return recommendations.snapshot()
    except RecommendationError:
        # まだ一度も読み込んでいない。保存済みの結果だけを読む（取得はしない）。
        return recommendations.refresh()


def read_hidden(services) -> tuple[frozenset[str], bool]:
    """(出さない作品 ID の集合, 書き込める保存先があるか)。"""
    recommendations = getattr(services, 'recommendations', None)
    if recommendations is None:
        return frozenset(), False
    state = _state(recommendations)
    return frozenset(state['hidden_works']), bool(state.get('writable'))


def set_hidden(services, work_id: str, hidden: bool) -> frozenset[str]:
    """1 件の作品を出さない／元に戻す。版が古ければ 1 回だけ読み直して再試行する。"""
    recommendations = getattr(services, 'recommendations', None)
    if recommendations is None:
        raise HideUnavailable()
    for attempt in (0, 1):
        state = _state(recommendations)
        if not state.get('writable'):
            raise HideUnavailable()
        try:
            result = recommendations.hide('work', work_id, hidden, state['revision'])
            return frozenset(result['hidden_works'])
        except RecommendationError:
            if attempt:
                raise
    raise HideUnavailable()  # pragma: no cover
