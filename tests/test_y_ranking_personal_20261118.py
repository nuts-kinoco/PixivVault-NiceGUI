"""FL-J12: 自分のランキングの絞り込み（元 ranking_tab.py:165-346, 1921-2040, 2135, 2315-2356）。

有効条件チップ行（1 クリックで個別解除・末尾に「すべて解除」）、R-18 スイッチの操作可否と既定 ON、
ブクマ数下限・並び順・条件クリアでの自動再検索。実 NiceGUI の部品・偽の借用保管庫のみ。通信なし。
自動再検索は「取得・更新」と同じ load() を通る（取得停止ゲートも同じ）。
"""
import asyncio
from types import SimpleNamespace as NS

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.personal_ranking import (
    PersonalRankingSource, active_filter_descriptors, normalize_personal_filters)
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from test_personal_ranking import raw
from test_ranking_ui import Pin


class FakeVault:
    def __init__(self):
        self.calls = []
        self.open = True
        self.saved = None

    def status(self):
        return {'open': self.open, 'mode': 'nana7mi', 'transport_available': True, 'crypto_available': True}

    def available(self):
        return self.open

    def search(self, word, bookmark_min=0, **kwargs):
        self.calls.append(dict(word=word, bookmark_min=bookmark_min, **kwargs))
        return {'illusts': [raw(123, 10), raw(124, 11)], 'next_url': None}

    def close(self):
        pass

    def ranking_preferences(self):
        return self.saved or {}

    def save_ranking_preferences(self, tags, filters):
        self.saved = {'tags': list(tags), 'filters': normalize_personal_filters(filters)}


def make(context=None):
    services = ApplicationServices(1)
    vault = FakeVault()
    services.external_vault = vault
    services.acquire_ranking_images = lambda records: Pin(records)
    services.personal_ranking_context = lambda: dict(context if context is not None else {
        'hidden_work_ids': set(), 'hidden_author_ids': set(), 'downloaded_ids': set(), 'hated_ids': set()})
    return services, vault


async def build(monkeypatch, name, **kw):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault = make(**kw)
    client = Client(ui.page('/_y_personal_' + name))
    return services, vault, client


async def open_view(services, vault):
    view = PersonalRankingView(services)
    view.source = PersonalRankingSource(vault)
    view.set_active(True)
    if view._context_task is not None:
        await view._context_task
    return view


async def settle(view):
    for _ in range(3):
        await asyncio.sleep(0)
    task = view._condition_task
    if task is not None:
        await task


def chips(view):
    return [(c.props['data-chip-key'], next(n.text for n in c.descendants() if isinstance(n, ui.label)))
            for c in view.chips_row.default_slot.children if 'data-chip-key' in c.props]


async def press(element):
    handler = next(e.handler for e in element._event_listeners.values() if e.type == 'click')
    handler(NS(args=None))
    await asyncio.sleep(0)


def chip(view, key):
    return next(c for c in view.chips_row.default_slot.children if c.props.get('data-chip-key') == key)


def clear_all_button(view):
    return next(c for c in view.chips_row.default_slot.children if c.props.get('data-testid') == 'personal-chips-clear')


def test_active_filter_descriptors_match_original():
    filters = normalize_personal_filters({
        'bookmark_min': 1000, 'date_preset': '1month', 'tags_include': ['a'], 'tags_exclude': ['b'],
        'per_author_max': 2, 'exclude_downloaded': True, 'exclude_hidden_authors': True,
        'exclude_hidden_works': True, 'exclude_hated': True})
    assert active_filter_descriptors(filters) == [
        ('bookmark_min', 'ブクマ1000+'), ('date_preset', '投稿日: 1か月以内'),
        ('tags_include:a', '+a'), ('tags_exclude:b', '-b'), ('per_author_max', '1作者2件まで'),
        ('exclude_downloaded', 'DL済みを除外'), ('exclude_hidden_authors', '出さない作者を除外'),
        ('exclude_hidden_works', '出さない作品を除外'), ('exclude_hated', '学習NGを除外')]
    # 並び順にはチップが無い。既定の exclude_hidden_works は効いているので出る。
    assert active_filter_descriptors(normalize_personal_filters({'sort': 'date_desc'})) == [
        ('exclude_hidden_works', '出さない作品を除外')]
    assert active_filter_descriptors(normalize_personal_filters({'exclude_hidden_works': False})) == []


@pytest.mark.asyncio
async def test_active_chips_row_shows_current_conditions_and_hides_when_empty(monkeypatch):
    services, vault, client = await build(monkeypatch, 'chips')
    try:
        with client:
            view = await open_view(services, vault)
            assert chips(view) == [('exclude_hidden_works', '出さない作品を除外')]
            assert view.chips_row.visible
            view.author_max.set_value(2)
            view.date.set_value('1week')
            view.include.set_value('x\ny')
            view.exclude.set_value('z')
            view.exclusions['exclude_downloaded'].set_value(True)
            assert [k for k, _ in chips(view)] == [
                'date_preset', 'tags_include:x', 'tags_include:y', 'tags_exclude:z', 'per_author_max',
                'exclude_downloaded', 'exclude_hidden_works']
            assert dict(chips(view))['date_preset'] == '投稿日: 1週間以内'
            view.exclusions['exclude_hidden_works'].set_value(False)
            view.exclusions['exclude_downloaded'].set_value(False)
            view.author_max.set_value(0)
            view.date.set_value('all')
            view.include.set_value('')
            view.exclude.set_value('')
            assert chips(view) == [] and not view.chips_row.visible
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_clicking_a_chip_removes_that_one_condition_only(monkeypatch):
    services, vault, client = await build(monkeypatch, 'chip_remove')
    try:
        with client:
            view = await open_view(services, vault)
            view.author_max.set_value(3)
            view.date.set_value('1year')
            view.include.set_value('x\ny')
            view.exclude.set_value('z\nw')
            view.exclusions['exclude_hated'].set_value(True)
            await press(chip(view, 'tags_include:x'))
            assert view.include.value == 'y'
            await press(chip(view, 'tags_exclude:z'))
            assert view.exclude.value == 'w'
            await press(chip(view, 'date_preset'))
            assert view.date.value == 'all'
            await press(chip(view, 'per_author_max'))
            assert view.author_max.value == 0
            await press(chip(view, 'exclude_hated'))
            assert view.exclusions['exclude_hated'].value is False
            await press(chip(view, 'exclude_hidden_works'))
            assert view.exclusions['exclude_hidden_works'].value is False
            assert chips(view) == [('tags_include:y', '+y'), ('tags_exclude:w', '-w')]
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_clear_all_chip_returns_every_condition_to_its_default(monkeypatch):
    services, vault, client = await build(monkeypatch, 'chip_clear')
    try:
        with client:
            view = await open_view(services, vault)
            view.author_max.set_value(3)
            view.exclusions['exclude_hidden_works'].set_value(False)
            view.exclusions['exclude_downloaded'].set_value(True)
            view.include.set_value('x')
            await press(clear_all_button(view))
            await settle(view)
            assert view.current_filters() == normalize_personal_filters({})
            # 元は「全部 False」ではなく既定へ戻す（exclude_hidden_works は既定 True）。
            assert chips(view) == [('exclude_hidden_works', '出さない作品を除外')]
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_r18_switch_default_on_and_operable_when_search_is_available(monkeypatch):
    services, vault, client = await build(monkeypatch, 'r18')
    try:
        with client:
            view = await open_view(services, vault)
            assert view.r18.value is True and view.r18.enabled
            assert '原 API 未対応' not in view.r18.text
            vault.open = False
            view.availability()
            assert not view.r18.enabled and view.r18.value is True
            vault.open = True
            view.availability()
            assert view.r18.enabled
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_server_side_conditions_search_again_by_themselves(monkeypatch):
    services, vault, client = await build(monkeypatch, 'research')
    try:
        with client:
            view = await open_view(services, vault)
            view.tags.set_value('tag')
            await settle(view)
            assert [c['bookmark_min'] for c in vault.calls] == [0]
            view.bookmarks.set_value(1000)
            await settle(view)
            assert [c['bookmark_min'] for c in vault.calls] == [0, 1000]
            assert view.page is not None and view.page.key[3] == 1000
            assert '件表示' in view.notice.text and view.gallery.counter.text != '未取得'
            view.sort.set_value('date_desc')
            await settle(view)
            assert vault.calls[-1]['sort'] == 'date_desc' and len(vault.calls) == 3
            # 手元条件は再検索しない
            view.author_max.set_value(1)
            view.date.set_value('1year')
            await settle(view)
            assert len(vault.calls) == 3
            # R-18 切替も元は再検索する（結果は同じ）
            view.r18.set_value(False)
            await settle(view)
            assert len(vault.calls) == 4 and vault.calls[-1]['r18'] is False
            view.ai.set_value(True)
            await settle(view)
            assert len(vault.calls) == 5 and vault.calls[-1]['exclude_ai'] is True
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_clear_conditions_and_bookmark_chip_search_again(monkeypatch):
    services, vault, client = await build(monkeypatch, 'clear_research')
    try:
        with client:
            view = await open_view(services, vault)
            view.tags.set_value('tag')
            view.bookmarks.set_value(5000)
            await settle(view)
            assert vault.calls[-1]['bookmark_min'] == 5000
            await press(chip(view, 'bookmark_min'))
            await settle(view)
            assert vault.calls[-1]['bookmark_min'] == 0 and len(vault.calls) == 2
            view.bookmarks.set_value(10000)
            view.sort.set_value('date_asc')
            await settle(view)
            n = len(vault.calls)
            view.clear_filters()
            await settle(view)
            assert len(vault.calls) == n + 1
            assert vault.calls[-1]['bookmark_min'] == 0 and vault.calls[-1]['sort'] == 'popular_desc'
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_no_automatic_search_without_tags_or_when_search_is_unavailable(monkeypatch):
    services, vault, client = await build(monkeypatch, 'research_guard')
    try:
        with client:
            view = await open_view(services, vault)
            view.bookmarks.set_value(1000)
            await settle(view)
            assert vault.calls == []  # タグ無し
            view.tags.set_value('tag')
            vault.open = False
            view.sort.set_value('date_desc')
            view.clear_filters()
            await settle(view)
            assert vault.calls == []  # 借用検索が使えない（停止中など）
            view.close()
            view.bookmarks.set_value(500)
            assert vault.calls == []
    finally:
        client.delete()
        services.close()
