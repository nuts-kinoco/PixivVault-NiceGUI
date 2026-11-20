"""FL-Y04: 除外集合の読込に失敗したとき「⚠️一部の除外条件を適用できませんでした」を出す（元 ranking_tab.py:1436-1456, 1640-1642）。

除外しない側へ倒し、読めた分だけ適用する。実 NiceGUI の部品・偽の借用保管庫のみ。通信なし。
"""
import asyncio

import pytest
from nicegui import ui

from test_y_ranking_personal_20261118 import build, open_view, settle

WARNING = '⚠️一部の除外条件を適用できませんでした'


async def search(view, tag='tag'):
    view.add_tag(tag)
    await view.load()
    assert view.page is not None


@pytest.mark.asyncio
async def test_warning_when_an_enabled_exclusion_set_could_not_be_read_and_the_rest_still_applies(monkeypatch):
    # 保存済み（downloaded_ids）が読めなかった。出さない作品（hidden_work_ids）は読めた。
    services, vault, client = await build(monkeypatch, 'warn', context={
        'hidden_work_ids': {'123'}, 'hidden_author_ids': set(), 'hated_ids': set()})
    try:
        with client:
            view = await open_view(services, vault)
            view.exclusions['exclude_downloaded'].set_value(True)
            await search(view)
            assert WARNING in view.notice.text
            assert set(view.gallery.cards) == {'124'}, '読めた分（出さない作品）は適用する'
            assert '1件を条件で除外' in view.notice.text
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_default_hidden_works_exclusion_warns_when_its_set_is_missing(monkeypatch):
    services, vault, client = await build(monkeypatch, 'warn_default', context={})
    try:
        with client:
            view = await open_view(services, vault)
            await search(view)
            assert WARNING in view.notice.text
            assert set(view.gallery.cards) == {'123', '124'}, '読めない除外は適用せず、結果を出す'
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_no_warning_for_missing_sets_of_exclusions_that_are_off_or_when_all_are_read(monkeypatch):
    services, vault, client = await build(monkeypatch, 'nowarn', context={'hidden_work_ids': set()})
    try:
        with client:
            view = await open_view(services, vault)
            await search(view)
            assert WARNING not in view.notice.text  # 他の除外は OFF（元は ON の項目だけ DB を引く）
            for key in ('exclude_downloaded', 'exclude_hidden_authors', 'exclude_hated'):
                view.exclusions[key].set_value(True)
            assert WARNING in view.notice.text  # render() が走る手元条件の変更で出る
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)
    services, vault, client = await build(monkeypatch, 'allread')
    try:
        with client:
            view = await open_view(services, vault)
            for key in ('exclude_downloaded', 'exclude_hidden_authors', 'exclude_hated'):
                view.exclusions[key].set_value(True)
            await search(view)
            assert WARNING not in view.notice.text
            view.close()
    finally:
        client.delete()
        services.close()
        await asyncio.sleep(0)


def test_one_failing_loader_does_not_discard_the_sets_that_were_read(monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.recommendation_state import RecommendationError

    services = ApplicationServices(1)
    try:
        services.saved_discovery_ids = lambda: (_ for _ in ()).throw(OSError('synthetic'))
        services.recommendations = type('R', (), {
            'refresh': lambda self: {'hidden_works': ('9',), 'hidden_authors': ('8',)}, 'source': object(),
            'close': lambda self: None})()
        services.discovery = type('D', (), {
            'recommendation_snapshot': lambda self: (_ for _ in ()).throw(RecommendationError()),
            'trend_rows': lambda self: [{'tag': 't'}], 'close': lambda self: None})()
        context = services.personal_ranking_context()
        assert context['hidden_work_ids'] == {'9'} and context['hidden_author_ids'] == {'8'}
        assert 'downloaded_ids' not in context and 'hated_ids' not in context
        assert context['trend_rows'] == [{'tag': 't'}]
    finally:
        services.close()
