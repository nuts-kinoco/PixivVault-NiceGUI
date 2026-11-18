"""FL-Y02: 「よく効くタグ」副行を隠す／表示するトグルと、開閉の保存（元 ranking_tab.py:130, 1844-1866, 2146-2166）。

設定キーは ranking_personal_candidates_open、既定は開き。隠すのはラベルとチップだけで、トグルは常に出す。
実 NiceGUI の部品・隔離した借用保管庫（ローカルのファイルだけ）。通信なし。
"""
import asyncio
import json

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.ui.personal_ranking_view import PersonalRankingView
from test_external_vault_ui import setup


def state(view):
    return (view.candidates_label.visible, view.candidates_box.visible,
            view.candidates_toggle.visible, view.candidates_toggle.text)


async def drain(view):
    view.close()
    task = getattr(view, '_context_task', None)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_toggle_hides_only_label_and_chips_and_the_choice_survives_reopen_and_tag_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault, transport, pins = setup(tmp_path)
    client = Client(ui.page('/_y_candidates'))
    first = second = third = None
    try:
        with client:
            first = PersonalRankingView(services)
            assert state(first) == (True, True, True, 'よく効くタグを隠す')  # 既定は開き
            first.toggle_candidates()
            assert state(first) == (False, False, True, 'よく効くタグを表示')  # トグルは残る
            assert vault.ranking_preferences()['ranking_personal_candidates_open'] is False
            # タグ編集（preferences の再保存）が開閉の保存を消さない
            first.tags.set_value('tag')
            assert vault.ranking_preferences()['tags'] == ['tag']
            assert vault.ranking_preferences()['ranking_personal_candidates_open'] is False
            await drain(first)
            second = PersonalRankingView(services)
            assert state(second) == (False, False, True, 'よく効くタグを表示')
            assert second.tags.value == 'tag'
            second.toggle_candidates()
            assert state(second) == (True, True, True, 'よく効くタグを隠す')
            assert vault.ranking_preferences()['ranking_personal_candidates_open'] is True
            await drain(second)
            third = PersonalRankingView(services)
            assert state(third) == (True, True, True, 'よく効くタグを隠す')
            await drain(third)
    finally:
        client.delete()
        vault.close()
        services.close()


@pytest.mark.asyncio
async def test_candidates_default_open_when_nothing_or_garbage_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault, transport, pins = setup(tmp_path)
    client = Client(ui.page('/_y_candidates_default'))
    try:
        with client:
            view = PersonalRankingView(services)
            assert state(view)[:3] == (True, True, True)
            await drain(view)
            path = vault.root / 'ranking-preferences.json'
            path.write_text(json.dumps({'tags': [], 'ranking_personal_candidates_open': 'no'}), encoding='utf-8')
            view = PersonalRankingView(services)
            assert state(view)[:3] == (True, True, True)
            await drain(view)
    finally:
        client.delete()
        vault.close()
        services.close()


@pytest.mark.asyncio
async def test_toggle_still_works_in_session_when_saving_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services, vault, transport, pins = setup(tmp_path)
    client = Client(ui.page('/_y_candidates_fail'))
    try:
        with client:
            view = PersonalRankingView(services)

            def broken(opened):
                raise OSError('synthetic')
            vault.save_ranking_candidates_open = broken
            view.toggle_candidates()  # 元は保存を先に試すが、失敗しても画面は切り替える
            assert state(view) == (False, False, True, 'よく効くタグを表示')
            await drain(view)
    finally:
        client.delete()
        vault.close()
        services.close()
