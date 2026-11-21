"""P3-1: ランキングのカード右クリック（元 ranking_tab.py:763-790）と「出さない一覧」の管理（FL-J09/FL-J10）。

実 NiceGUI の部品・偽の Pixiv 書き込み/保存キュー・隔離した専用設定のみ。通信なし。
"""
import asyncio
from inspect import isawaitable
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.recommendation_state import (
    EmptyRecommendationSource, RecommendationController, RecommendationGroup,
    RecommendationPreferences, RecommendationSnapshot, RecommendationWork)
from pixivvault_nicegui.ranking_source import RankingSource
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.ranking_view import RankingView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_ranking_source import Reader, row
from test_ranking_ui import Pin

MENU = ['ブックマークする', 'ダウンロードキューへ積む', '出さない', 'この作者をもっと見る', 'pixivで作品ページを開く']


def make(tmp_path, source=None):
    services = ApplicationServices(1)
    reader = Reader()
    services.ranking_source = RankingSource(reader)
    services.acquire_ranking_images = lambda records: Pin(records)
    prefs = RecommendationPreferences(tmp_path / 'prefs')
    services.recommendations = RecommendationController(source or EmptyRecommendationSource(), prefs)
    services.recommendations.refresh()
    return services, reader


def items_of(card):
    return [n for n in card.descendants() if isinstance(n, ui.menu_item)]


def label_of(item):
    return next(n.text for n in item.descendants() if isinstance(n, ui.item_section))


def by_key(card, work_id, key):
    return next(n for n in items_of(card) if n.props.get('data-testid') == f'ranking-ctx-{work_id}-{key}')


async def click(item, *, args=None):
    handler = next(e.handler for e in item._event_listeners.values()
                   if e.type == 'click' and (args is None or e.js_handler))
    before = set(background_tasks.running_tasks)
    response = handler(NS(args=args))
    if isawaitable(response):
        await response
    pending = set(background_tasks.running_tasks) - before
    if pending:
        await asyncio.gather(*pending)
    await asyncio.sleep(0)


async def settle(view):
    for _ in range(5):
        await asyncio.sleep(0)
    task = view._hm().task
    if task is not None:
        await task




@pytest.mark.asyncio
async def test_ranking_card_menu_has_the_five_original_items_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, reader = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rank_items'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await settle(view)
            await view.load()
            card = view.gallery.cards['123']
            assert [label_of(i) for i in items_of(card)] == MENU
            assert by_key(card, '123', 'pixiv').props['href'] == 'https://www.pixiv.net/artworks/123'
            assert by_key(card, '123', 'pixiv').props['target'] == '_blank'
            assert 'noopener' in by_key(card, '123', 'pixiv').props['rel']
            # 既存の入口は残る。
            assert 'data-work-id=123' in str(card._props) or card.props.get('data-work-id') == '123'
            assert view.root.props['data-testid'] == 'ranking-view'
            assert view.fetch_button.props['data-testid'] == 'ranking-fetch'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_card_menu_opens_from_keyboard_context_menu_key_and_shift_f10(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rank_keys'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            card = view.gallery.cards['123']
            listener = next(e for e in card._event_listeners.values()
                            if e.type == 'keydown' and e.js_handler and 'F10' in e.js_handler)
            assert 'ContextMenu' in listener.js_handler
            menu = next(n for n in card.descendants() if isinstance(n, ui.context_menu))
            opened = []
            monkeypatch.setattr(menu, 'open', lambda: opened.append(1))
            listener.handler(NS(args=None))
            assert opened == [1]
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_hide_removes_card_lists_it_and_restores_shared_with_recommend(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    work = RecommendationWork('123', '作品', '91', '作者')
    other = RecommendationWork('777', '別作品', '92', '別作者')
    source = type('S', (), {'load': lambda self: RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('g', 'SuperLike', (work, other)),)),),
        unavailable=('certain', 'boosted'))})()
    services, reader = make(tmp_path, source)
    reader.rows = [row(123), row(124)]
    client = Client(ui.page('/_x_ctx_rank_hide'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await settle(view)
            await view.load()
            assert set(view.gallery.cards) == {'123', '124'}
            assert not view.manage_badge.visible
            await click(by_key(view.gallery.cards['123'], '123', 'hide'))
            # 同じ非表示データ（おすすめと共有）に入る。
            assert '123' in services.recommendations.snapshot()['hidden_works']
            assert set(view.gallery.cards) == {'124'}
            assert view.manage_badge.visible and view.manage_badge.text == '1'
            assert view.manage_label.text == '出さない一覧（1件）'
            assert view.action_status.text == '次回から表示しません。'
            assert view.undo_button.visible
            # おすすめ側にも反映（共有）。
            rec = RecommendView(services)
            rec.active = True
            await rec.refresh()
            shown = {w.work_id for g in rec.data['sections']['superlike'] for w in g.works}
            assert shown == {'777'}
            rec.close()
            # 一覧に出る → 取り消す。
            view.open_hidden_overlay()
            rows = [n for n in view.hidden_rows.descendants()
                    if n.props.get('data-testid') == 'ranking-hidden-row-123']
            assert len(rows) == 1 and not view.hidden_empty.visible
            undo = next(n for n in view.hidden_rows.descendants()
                        if n.props.get('data-testid') == 'ranking-hidden-undo-123')
            assert any(isinstance(n, ui.label) and n.text == '作品' for n in rows[0].descendants())
            await click(undo)
            assert '123' not in services.recommendations.snapshot()['hidden_works']
            assert set(view.gallery.cards) == {'123', '124'}
            assert not view.manage_badge.visible and view.hidden_empty.visible
            assert view.action_status.text == '元に戻しました。'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_undo_button_after_hide_and_hidden_in_recommend_filtered_from_ranking(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, reader = make(tmp_path)
    reader.rows = [row(123), row(124)]
    client = Client(ui.page('/_x_ctx_rank_undo'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await settle(view)
            await view.load()
            await click(by_key(view.gallery.cards['124'], '124', 'hide'))
            assert set(view.gallery.cards) == {'123'}
            await click(view.undo_button)
            assert set(view.gallery.cards) == {'123', '124'} and not view.undo_button.visible
            # おすすめ側で出さない→ランキングを開き直すと取り込まれる。
            state = services.recommendations.snapshot()
            services.recommendations.hide('work', '123', True, state['revision'])
            view.set_active(False)
            view.set_active(True)
            await settle(view)
            assert set(view.gallery.cards) == {'124'}
            assert view.manage_badge.text == '1'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_hide_without_writable_store_reports_and_keeps_card(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    services.recommendations = RecommendationController(EmptyRecommendationSource())  # 閲覧のみ
    services.recommendations.refresh()
    client = Client(ui.page('/_x_ctx_rank_readonly'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            await click(by_key(view.gallery.cards['123'], '123', 'hide'))
            assert set(view.gallery.cards) == {'123'}
            assert '保存先が未指定' in view.action_status.text
            view.close()
    finally:
        client.delete()
        services.close()


class FakeActions:
    def __init__(self, observed=None):
        self.cached_verified_account_id = '7'
        self.observed = observed
        self.intents = []
        self.shutdown_unverified = False

    def close(self):
        return True

    def state(self, account, kind, target):
        return NS(pending=False, unknown=False, observed=self.observed) if self.observed is not None else None

    async def verify_account(self, **kw):
        return '7'

    async def execute(self, intent, **kw):
        self.intents.append(intent)
        return NS(status='confirmed', local_warning=False)


@pytest.mark.asyncio
async def test_bookmark_item_only_adds_through_pixiv_actions_and_never_removes(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    services.pixiv_actions = FakeActions()
    client = Client(ui.page('/_x_ctx_rank_bm'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            item = by_key(view.gallery.cards['123'], '123', 'bookmark')
            assert item.enabled
            await click(item)
            assert [(i.action, i.target_id, i.author_id, i.account_id) for i in services.pixiv_actions.intents] == [
                ('bookmark_add', '123', '91', '7')]
            assert view.action_status.text == 'ブックマークしました。'
            # 既に登録済みなら何も送らず、解除もしない。
            services.pixiv_actions = FakeActions(observed=True)
            view._hm().actions = None
            await click(by_key(view.gallery.cards['123'], '123', 'bookmark'))
            assert services.pixiv_actions.intents == []
            assert '既に' in view.action_status.text
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_bookmark_and_queue_disabled_without_their_services(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rank_disabled'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            card = view.gallery.cards['123']
            assert not by_key(card, '123', 'bookmark').enabled
            assert not by_key(card, '123', 'queue').enabled
            assert by_key(card, '123', 'hide').enabled and by_key(card, '123', 'boost').enabled
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_queue_item_uses_existing_explicit_save_with_stable_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    accepted = []

    async def save(work_id, operation=None, *, is_current=None):
        accepted.append((work_id, operation, is_current()))
        return object()
    services.managed_downloads = NS(close=lambda: None)
    services.save_explicit_work = save
    client = Client(ui.page('/_x_ctx_rank_queue'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            item = by_key(view.gallery.cards['123'], '123', 'queue')
            assert item.enabled
            await click(item)
            await click(item)
            assert [a[0] for a in accepted] == ['123', '123'] and accepted[0][1] == accepted[1][1]
            assert accepted[0][2] is True
            assert view.action_status.text == 'ダウンロードキューへ積みました。'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_boost_author_registers_in_shared_recommend_boost_list(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, _ = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rank_boost'))
    try:
        with client:
            view = RankingView(services)
            view.set_active(True)
            await view.load()
            await click(by_key(view.gallery.cards['123'], '123', 'boost'))
            assert dict(services.recommendations.snapshot()['boosted']).get('91') == '作者'
            assert view.action_status.text == '「作者」をもっと見るに追加しました。'
            view.close()
    finally:
        client.delete()
        services.close()
