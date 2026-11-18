"""FL-J05: 世間ランキングの R-18 切替は、借用プレミアム ON のタグ検索でも操作できる（元 ranking_tab.py:1109-1110, 1319-1328）。

元の借用検索は R-18 を API にも結果の絞り込みにも使わないので、結果は変わらない。差は切替の見え方だけ。
元の世間ランキングの既定は OFF（ranking_tab.py:1319, 452）。ON が既定なのは自分のランキング（:2135）。
実 NiceGUI の部品・偽の読み取りと偽の借用保管庫のみ。通信なし。
"""
import asyncio

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from test_ranking_source import Reader
from test_ranking_ui import Pin


class PremiumVault:
    def __init__(self):
        self.calls = []

    def status(self):
        return {'open': True, 'mode': 'nana7mi', 'transport_available': True}

    def search(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {'illusts': [], 'next_url': None}


def make():
    services = ApplicationServices(1)
    vault = PremiumVault()
    services.ranking_source = RankingSource(Reader(), vault)
    services.acquire_ranking_images = lambda records: Pin(records)
    return services, vault


@pytest.mark.asyncio
async def test_r18_switch_stays_operable_in_premium_tag_search_and_keeps_its_value():
    services, vault = make()
    client = Client(ui.page('/_y_r18_premium'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            assert view.r18.value is False  # 元の世間ランキングの既定は OFF
            view.tag.set_value('tag')
            assert view.r18.enabled, '借用プレミアム ON のタグ検索でも R-18 切替は操作できる'
            assert 'R-18' not in view.notice.text, '元に無い「適用しません」の上書き通知を出さない'
            view.r18.set_value(True)
            assert view.r18.value is True, '操作した値を勝手に OFF へ戻さない'
            assert view.r18.enabled
            view.tag.set_value('tag2')
            assert view.r18.value is True and view.r18.enabled
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_r18_switch_still_disabled_for_modes_that_cannot_use_it():
    services, vault = make()
    client = Client(ui.page('/_y_r18_unsupported'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            view._syncing_controls = True
            view.mode.set_value('monthly')
            view._syncing_controls = False
            view.changed()
            assert not view.r18.enabled and view.r18.value is False
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)
