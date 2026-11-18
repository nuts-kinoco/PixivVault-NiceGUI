import asyncio
import pytest
from nicegui import Client, core, ui
from test_x_ctx_recommend_20261118 import make, open_view


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
