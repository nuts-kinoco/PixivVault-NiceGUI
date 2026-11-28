from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.learning_view import LearningView


@pytest.mark.parametrize('mode', ['rail', 'band', 'fold'])
def test_trend_names_have_full_tooltips_and_wider_muted_column(mode):
    client = Client(ui.page(f'/_trend_names_{mode}'))
    name = 'ウマ娘プリティーダービーと長いタグ名'
    try:
        with client:
            view = SimpleNamespace(trend_content=ui.column(), trend_label=ui.label(),
                                   _trend_mode=mode, _trend_rail_rows=5)
            row = dict(display=name, score=10, like_count=4, shown_count=5, at_cap=True)
            LearningView._render_trend(view, dict(judged=20, ready=True, rows=[row]))
            label = next(e for e in view.trend_content.descendants() if isinstance(e, ui.label) and e.text == name)
            assert any(isinstance(e, ui.tooltip) and e.text == name and e.props.get('target') == f'#{label.html_id}'
                       for e in view.trend_content.descendants())
            assert 'pv-trend-name' in label.classes
    finally:
        client.delete()


def test_seed_name_has_full_tooltip():
    client = Client(ui.page('/_trend_seed_name'))
    name = '長い保存タグ名の全文を表示します'
    try:
        with client:
            view = SimpleNamespace(trend_content=ui.column(), trend_label=ui.label())
            LearningView._render_trend(view, dict(judged=0, ready=False, rows=[],
                                      local_seed_rows=[dict(display=name, count=3)]))
            label = next(e for e in view.trend_content.descendants() if isinstance(e, ui.label) and e.text == name)
            assert any(isinstance(e, ui.tooltip) and e.text == name and e.props.get('target') == f'#{label.html_id}'
                       for e in view.trend_content.descendants())
    finally:
        client.delete()


def test_summary_link_has_url_tooltip():
    client = Client(ui.page('/_summary_link_tooltip'))
    url = 'https://example.com/very/long/path/that/might/be/clipped/by/summary/board'
    run = SimpleNamespace(text=url, url=url, external=True)
    try:
        with client:
            view = SimpleNamespace(
                _summary_open=True,
                _summary_pages=[[[run]]],
                _summary_page=0,
                _summary_cols=1,
                _summary_rows=4,
                _summary_width=276,
                _summary_height=180,
                summary_board=ui.card(),
                summary_body=ui.element('div'),
                summary_pager=ui.row(),
                summary_prev=ui.button(),
                summary_next=ui.button(),
                summary_page_label=ui.label(),
                root=ui.element('div'),
            )
            LearningView._render_summary(view)
            link = next(e for e in view.summary_body.descendants() if isinstance(e, ui.link))
            assert any(isinstance(e, ui.tooltip) and e.text == url and e.props.get('target') == f'#{link.html_id}'
                       for e in view.summary_body.descendants())
    finally:
        client.delete()
