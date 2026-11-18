"""P3-1: おすすめのカード右クリック 12 項目（元 recommend_tab.py:815-876、FL-L05）。

既存のインラインボタン・data-testid は残したまま、無かった 4 項目を足したことを確かめる。通信なし。
"""
import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.recommendation_state import (
    RecommendationController, RecommendationGroup, RecommendationPreferences,
    RecommendationSnapshot, RecommendationWork)
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.recommend_view import RecommendView
from test_x_ctx_ranking_20261118 import FakeActions, click, label_of

FULL = ['ブックマークする', '好み（保存する）', '取り下げる', '出さない', '出さない（理由をつける）',
        'ダウンロードキューへ積む', 'この作者をもっと見る', 'この作者を出さない', 'このタグで検索',
        '作者の作品一覧へ飛ぶ', 'pixivでフォローする', 'pixivで作品ページを開く']


def make(tmp_path, writable=True):
    sl = RecommendationWork('11', 'SL作品', '91', '作者', tags=('猫', '犬'))
    ce = RecommendationWork('12', '確実作品', '92', '別作者', tags=('空',))
    source = NS(load=lambda: RecommendationSnapshot(
        sections=(('superlike', (RecommendationGroup('g', 'SuperLike', (sl,)),)),
                  ('certain', (RecommendationGroup('c', '確実', (ce,)),))),
        unavailable=()))
    services = ApplicationServices(1)
    prefs = RecommendationPreferences(tmp_path / 'prefs') if writable else None
    services.recommendations = RecommendationController(source, prefs)
    return services


def ctx_items(view, kind, work_id):
    menu = next(n for n in view.body.descendants()
                if n.props.get('data-testid') == f'recommend-pixiv-context-{kind}-{work_id}')
    return [n for n in menu.descendants() if isinstance(n, ui.menu_item)]


def item(view, kind, work_id, label):
    return next(i for i in ctx_items(view, kind, work_id) if label_of(i) == label)


async def open_view(services, **kw):
    view = RecommendView(services, **kw)
    view.active = True
    await view.refresh()
    return view


@pytest.mark.asyncio
async def test_superlike_menu_has_all_twelve_original_items_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rec_items'))
    try:
        with client:
            view = await open_view(services, on_enqueue=lambda *a, **k: None)
            assert [label_of(i) for i in ctx_items(view, 'superlike', '11')] == FULL
            # 取り下げるは SuperLike の棚だけ。
            assert [label_of(i) for i in ctx_items(view, 'certain', '12')] == [x for x in FULL if x != '取り下げる']
            link = item(view, 'superlike', '11', 'pixivで作品ページを開く')
            assert link.props['href'] == 'https://www.pixiv.net/artworks/11'
            assert link.props['data-testid'] == 'recommend-pixiv-link-superlike-11'
            assert item(view, 'superlike', '11', '作者の作品一覧へ飛ぶ').props['data-testid'] == 'recommend-author-superlike-11'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_existing_inline_buttons_and_testids_remain(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rec_inline'))
    try:
        with client:
            view = await open_view(services, on_enqueue=lambda *a, **k: None, on_open=lambda w: None)
            texts = {b.text for b in view.body.descendants() if isinstance(b, ui.button)}
            for expected in ('保存済みを開く', 'キューに追加', 'SuperLikeを取り下げる', '出さない', '作者を出さない',
                             '作者をもっと見る', 'ブクマ', 'フォロー'):
                assert expected in texts, expected
            ids = {n.props.get('data-testid') for n in view.body.descendants()}
            assert {'recommend-open-superlike-11', 'recommend-dismiss-superlike-11',
                    'recommend-action-superlike-11-bookmark'} <= ids
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_hide_items_write_the_shared_hidden_data(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rec_hide'))
    try:
        with client:
            view = await open_view(services)
            await click(item(view, 'certain', '12', '出さない'))
            assert '12' in services.recommendations.snapshot()['hidden_works']
            assert view.undo_button.visible  # 元の「取り消し」導線
            await click(item(view, 'superlike', '11', 'この作者を出さない'))
            assert '91' in services.recommendations.snapshot()['hidden_authors']
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_hide_with_reason_item_opens_reason_dialog_without_hiding(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rec_reason'))
    try:
        with client:
            view = await open_view(services)
            await click(item(view, 'certain', '12', '出さない（理由をつける）'))
            assert view.hide_dialog.value and view._pending_hide[0] == '12'
            assert '12' not in services.recommendations.snapshot()['hidden_works']
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_boost_tag_search_queue_and_prefer(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    queued, tags = [], []

    async def enqueue(work_id, operation, *, is_current=None):
        queued.append(work_id)
        return object()
    client = Client(ui.page('/_x_ctx_rec_actions'))
    try:
        with client:
            view = await open_view(services, on_enqueue=enqueue, on_search_tag=tags.append)
            await click(item(view, 'certain', '12', 'この作者をもっと見る'))
            assert dict(services.recommendations.snapshot()['boosted']).get('92') == '別作者'
            await click(item(view, 'superlike', '11', 'このタグで検索'))
            assert tags == ['猫']  # 元と同じく先頭のタグ
            await click(item(view, 'certain', '12', 'ダウンロードキューへ積む'))
            assert queued == ['12']
            # 好み（保存する）: 保存を受理し、SuperLike の棚からは取り下げる（元の _on_save_superlike）。
            await click(item(view, 'superlike', '11', '好み（保存する）'))
            assert queued == ['12', '11']
            shown = {w.work_id for g in view.data['sections']['superlike'] for w in g.works}
            assert shown == set()
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_bookmark_and_follow_items_send_add_only_intents(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    # 偽の書き込み調停役にはクライアント共有の定期更新が要らない（ここはメニューの結線だけを見る）。
    monkeypatch.setattr('pixivvault_nicegui.ui.pixiv_action_buttons._client_hub', lambda s, c: None)
    services = make(tmp_path)
    services.pixiv_actions = FakeActions()
    client = Client(ui.page('/_x_ctx_rec_write'))
    try:
        with client:
            view = await open_view(services)
            await click(item(view, 'superlike', '11', 'ブックマークする'))
            assert view.notice.text == 'ブックマークしました。'
            await click(item(view, 'superlike', '11', 'pixivでフォローする'))
            assert [(i.action, i.target_id, i.author_id) for i in services.pixiv_actions.intents] == [
                ('bookmark_add', '11', '91'), ('follow', '91', '91')]
            assert view.notice.text == 'フォローしました。'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_dismiss_item_withdraws_only_from_superlike_shelf(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_x_ctx_rec_dismiss'))
    try:
        with client:
            view = await open_view(services)
            await click(item(view, 'superlike', '11', '取り下げる'))
            assert not [w for g in view.data['sections']['superlike'] for w in g.works]
            assert '11' not in services.recommendations.snapshot()['hidden_works']
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_items_without_their_prerequisites_are_disabled_and_keyboard_opens_menu(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path, writable=False)
    client = Client(ui.page('/_x_ctx_rec_disabled'))
    try:
        with client:
            view = await open_view(services)
            for label in ('出さない', '出さない（理由をつける）', 'この作者を出さない', 'この作者をもっと見る',
                          'ブックマークする', 'pixivでフォローする', 'ダウンロードキューへ積む', 'このタグで検索'):
                assert not item(view, 'certain', '12', label).enabled, label
            assert item(view, 'certain', '12', '作者の作品一覧へ飛ぶ').enabled
            assert item(view, 'certain', '12', 'pixivで作品ページを開く').enabled
            card = next(n for n in view.body.descendants()
                        if n.props.get('data-testid') == 'recommend-open-certain-12')
            listener = next(e for e in card._event_listeners.values()
                            if e.type == 'keydown' and e.js_handler and 'F10' in e.js_handler)
            menu = next(n for n in view.body.descendants()
                        if n.props.get('data-testid') == 'recommend-pixiv-context-certain-12')
            opened = []
            monkeypatch.setattr(menu, 'open', lambda: opened.append(1))
            listener.handler(NS(args=None))
            assert opened == [1]
            view.close()
    finally:
        client.delete()
        services.close()
