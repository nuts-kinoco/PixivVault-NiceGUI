"""FL-Y01: 世間ランキングのカードの、好みタグを持つ作品の順位横の青ハート印（元 ranking_tab.py:519-544, 822-824, 1089-1090, 1276）。

印の判定は元の `_has_preferred_tag`（下限が NEUTRAL を超え、有効試行数が TREND_MIN_N 以上のタグ）。
好みの集合は初回の取得と「取得・更新」で引き直し、もっと見る・条件変更の自動取得では引き直さない。
実 NiceGUI の部品・偽の読み取り・偽の好み集合。通信なし。
"""
import asyncio
from threading import Event

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.ranking_preferred_tags import preferred_tag_set
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import Reader, row
from test_ranking_ui import Pin


class Prefs:
    """偽の好み集合。呼び出し回数と、呼ばれたスレッドを数える。"""

    def __init__(self, tags=()):
        self.tags = frozenset(tags)
        self.calls = 0
        self.fail = False

    def __call__(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('synthetic')
        return self.tags


def make(prefs, rows=None):
    services = ApplicationServices(1)
    reader = Reader()
    if rows is not None:
        reader.rows = rows
    services.ranking_source = RankingSource(reader)
    services.acquire_ranking_images = lambda records: Pin(records)
    services.preferred_ranking_tags = prefs
    return services, reader


def hearts(view):
    return sorted(wid for wid, card in view.gallery.cards.items()
                  if card.data['heart'])


def test_preferred_tag_set_uses_the_original_bounds_rule():
    prefs = {
        'liked': {'like_count': 10, 'display_tag': 'Liked'},
        'hated': {'hate_count': 10, 'display_tag': 'Hated'},
        'unknown': {'display_tag': 'Unknown'},
        'few': {'skip_count': 1, 'display_tag': 'Few'},
    }
    assert preferred_tag_set(prefs) == frozenset({'liked'})
    assert preferred_tag_set({}) == frozenset() and preferred_tag_set(None) == frozenset()


@pytest.mark.asyncio
async def test_heart_is_shown_beside_the_rank_only_for_works_with_a_preferred_tag():
    # row() の作品は tags が '猫' 1 件。大文字小文字・全角は元の normalize_tag で同一視する。
    prefs = Prefs({'猫'})
    services, reader = make(prefs, [row(123), row(124)])
    client = Client(ui.page('/_y_heart'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            assert hearts(view) == ['123', '124']
            card = view.gallery.cards['123']
            assert card.data['rank'] == '#1' and card.data['heart']
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_no_heart_without_preference_and_the_rank_label_dom_stays_plain():
    prefs = Prefs(set())
    services, reader = make(prefs, [row(123)])
    client = Client(ui.page('/_y_heart_none'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            assert hearts(view) == []
            card = view.gallery.cards['123']
            assert card.data['rank'] == '#1' and not card.data['heart']
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_preferences_are_rebuilt_on_first_fetch_and_on_refresh_only():
    prefs = Prefs(set())
    services, reader = make(prefs, [row(123)])
    client = Client(ui.page('/_y_heart_rebuild'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            assert prefs.calls == 0
            await view.load()                      # 初回の取得（自動取得もこの経路）
            assert prefs.calls == 1 and hearts(view) == []
            reader.rows = [row(123), row(124)]
            await view.load(append=True)           # もっと見る
            await view.load()                      # 条件変更の自動取得
            assert prefs.calls == 1
            prefs.tags = frozenset({'猫'})
            await view.refresh()                   # 更新ボタン（取得・更新）
            assert prefs.calls == 2 and hearts(view) == ['123', '124']
            await view.refresh()
            assert prefs.calls == 3
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_failure_to_read_preferences_only_drops_the_hearts():
    prefs = Prefs({'猫'})
    prefs.fail = True
    services, reader = make(prefs, [row(123)])
    client = Client(ui.page('/_y_heart_fail'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            assert view.page is not None and '取得できませんでした' not in view.notice.text
            assert hearts(view) == [] and set(view.gallery.cards) == {'123'}
            assert prefs.calls == 1
            await view.load()                      # 失敗でも「作った」扱い。取得のたびに引き直さない
            assert prefs.calls == 1
            prefs.fail = False
            await view.refresh()
            assert hearts(view) == ['123']
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_preferences_are_read_off_the_event_loop_thread():
    import threading
    seen = []
    entered = Event()

    def read():
        seen.append(threading.current_thread())
        entered.set()
        return frozenset()
    services, reader = make(read, [row(123)])
    client = Client(ui.page('/_y_heart_thread'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            assert seen and seen[0] is not threading.main_thread()
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_personal_ranking_cards_have_no_hearts():
    from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
    services, reader = make(Prefs({'猫'}))
    client = Client(ui.page('/_y_heart_personal'))
    try:
        with client:
            view = PersonalRankingView(services)
            assert view.preferred_work('123') is False
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


def test_services_reads_preferred_tags_from_discovery_then_the_readonly_source():
    services = ApplicationServices(1)
    try:
        assert services.preferred_ranking_tags() == frozenset()
        services.discovery = type('D', (), {'preferred_tags': lambda self: frozenset({'a'}), 'close': lambda self: None})()
        assert services.preferred_ranking_tags() == frozenset({'a'})
        services.discovery = None
        source = type('S', (), {'preferred_tags': lambda self: frozenset({'b'})})()
        services.recommendations = type('R', (), {'source': source, 'close': lambda self: None})()
        assert services.preferred_ranking_tags() == frozenset({'b'})
    finally:
        services.close()
