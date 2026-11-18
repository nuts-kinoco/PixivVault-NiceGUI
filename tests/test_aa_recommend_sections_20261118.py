import asyncio
import pytest
from nicegui import Client, core, ui
from test_x_ctx_recommend_20261118 import make, open_view
from pixivvault_nicegui.recommendation_state import RecommendationGroup, RecommendationSnapshot, RecommendationWork


@pytest.mark.asyncio
async def test_section_descriptions_and_certain_subtitle(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    client = Client(ui.page('/_aa_sections'))
    try:
        with client:
            view = await open_view(services)
            texts = {getattr(n, 'text', None) for n in view.body.descendants()}
            assert '傾向ピルをクリックして追加したタグの候補' in texts
            assert '好みの学習結果に基づく候補' in texts
            assert 'あなたが好んで見ているタグの作品' in texts
            assert '個別に登録した作者の新着' in texts
            assert '自分が強く反応した作品' in texts
            assert 'あなたが好んで見ている『確実』の作品' in texts
            view.close()
    finally:
        client.delete(); services.close()


@pytest.mark.asyncio
async def test_failed_page_shows_retry_for_its_group(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    services.recommendations.source.load = lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('cat', '猫', (RecommendationWork('12'),)),)),), unavailable=())
    services.recommendations.acquisition.errors['certain:cat'] = 'fetch_failed_retry_same_page'
    client = Client(ui.page('/_aa_retry'))
    try:
        with client:
            view = await open_view(services)
            assert any(getattr(n, 'text', None) == '取得に失敗しました。再試行' for n in view.body.descendants())
            view.close()
    finally:
        client.delete(); services.close()
