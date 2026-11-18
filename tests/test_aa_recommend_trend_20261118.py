import asyncio
import pytest
from nicegui import Client, core, ui
from test_x_ctx_recommend_20261118 import make, open_view
from pixivvault_nicegui.recommendation_state import RecommendationSnapshot


@pytest.mark.asyncio
async def test_trend_tiers_and_overflow_count(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    services = make(tmp_path)
    services.recommendations.acquisition.trend_provider = lambda: [dict(tag=f't{i}', display=f'T{i}') for i in range(43)]
    services.recommendations.source.load = lambda: RecommendationSnapshot(added_tags=(('t0','T0'),), unavailable=())
    client = Client(ui.page('/_aa_trend'))
    try:
        with client:
            view = await open_view(services)
            nodes = list(view.body.descendants())
            pills = [n for n in nodes if n.props.get('data-testid', '').startswith('recommend-trend-pill-')]
            assert len(pills) == 40
            assert [p._style['font-size'] for p in pills[:5]] == ['16px', '13.5px', '12px', '11px', '11px']
            assert pills[0].text == 'T0'
            assert pills[0].props.get('icon-right') == 'close'
            assert pills[0]._style['background'] == 'var(--pv-pixiv-blue-glow)'
            assert any(getattr(n, 'text', None) == '+3' for n in nodes)
            section = next(n for n in nodes if n.props.get('data-testid') == 'recommend-section-trend')
            view.data['collapsed'] = frozenset({'trend'})
            view.render()
            assert any(getattr(n, 'text', None) == '+3' for n in view.body.descendants())
            view.close()
    finally:
        client.delete(); services.close()
