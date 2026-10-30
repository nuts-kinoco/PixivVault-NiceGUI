"""D09/D10: original discovery summary semantics, offline and isolated."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from nicegui import Client, background_tasks, core, ui

from pixivvault_nicegui.discovery_description import layout, is_blank, safe_href, Run
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from pixivvault_nicegui.pixiv_read_client import _discovery_record
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.learning_view import LearningView


@pytest.fixture
async def view(monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    service = ApplicationServices(count=4)
    client = Client(ui.page('/_learning_summary_contract'))
    before = set(background_tasks.running_tasks)
    with client, ui.card():
        result = LearningView(service)
    result.set_active(True)
    try:
        yield result
    finally:
        result.close()
        client.delete()
        tasks = set(background_tasks.running_tasks) - before
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        service.close()


def description(view, raw):
    head = view.state.artworks[view.state.index]
    view.state.artworks = (view.state.artworks[:view.state.index]
                           + (replace(head, description=raw),)
                           + view.state.artworks[view.state.index + 1:])
    view.refresh()


def test_source_parser_preserves_text_links_and_lines_without_executable_markup():
    raw = ('原文 &lt;span&gt;<br /><b>強調</b>\n'
           '<a href="/jump.php?https%3A%2F%2Fexample.com%2Fa">URL</a>'
           '<img src="javascript:alert(1)">'
           '<script>alert(2)</script>')
    pages = layout(raw, max_units=15, lines_per_page=20)
    joined = ''.join(run.text for page in pages for line in page for run in line)
    assert '原文 <span>' in joined and '強調' in joined and 'URL' in joined
    assert '<img' not in joined and '<script' not in joined
    assert all(not run.url or run.url.startswith('https://') for page in pages
               for line in page for run in line)
    assert any(run.url == 'https://example.com/a' and run.external
               for page in pages for line in page for run in line)
    assert not is_blank('あ<br>い') and is_blank('<br><b> </b>')
    assert is_blank('') and is_blank('&nbsp;')


@pytest.mark.parametrize('target', (
    'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi',
    'https://user:pass@example.com/', 'https://example.com/\nredirect',
    '//attacker.example',
))
def test_summary_link_targets_fail_closed(target):
    assert safe_href(target) is None


def test_safe_links_are_canonical_and_noopener():
    assert safe_href('https://www.pixiv.net/users/123') == 'https://www.pixiv.net/users/123'
    assert safe_href('/jump.php?https%3A%2F%2Fexample.com%2Fnext') == 'https://example.com/next'
    assert safe_href('/jump.php?javascript%3Aalert%281%29') is None


def test_layout_page_boundary_preserves_exact_content_and_link_fragments():
    text = 'あいうえおかきくけこさしすせそ'
    pages = layout(text, max_units=5, lines_per_page=2)
    assert [[ ''.join(run.text for run in line) for line in page] for page in pages] == [
        ['あいうえお', 'かきくけこ'], ['さしすせそ']]
    assert len(layout('短い', 20, 30)) == 1
    assert layout('', 20, 30) == []
    link_pages = layout('<a href="https://example.com/p">https://example.com/p</a>', 4, 2)
    assert len(link_pages) > 1
    assert all(run.url == 'https://example.com/p' for page in link_pages for line in page for run in line)


async def test_d09_default_close_toggle_escape_no_judgment(view):
    assert not view._summary_open
    assert view.summary_button.text.startswith('概要')
    assert not view.summary_board.visible
    assert view.toggle_summary()
    assert view._summary_open and view.summary_board.visible
    previous_layout=view._summary_pages
    assert view.summary_button.props['aria-expanded'] == 'true'
    before = (view.state.index, view.state.generation, len(view.ledger.entries))
    view._on_key(SimpleNamespace(args={'action': 'summary-close'}))
    assert not view._summary_open and not view.summary_board.visible
    assert before == (view.state.index, view.state.generation, len(view.ledger.entries))
    assert view.toggle_summary() and view._summary_open
    assert view._summary_pages is previous_layout  # cached layout, no reparsing on reopen
    view.close_summary()
    assert not view._summary_open
    js = LearningView._key_js()
    assert 'escape' in js and 'event.isComposing' in js and 'role=dialog' in js


async def test_d09_empty_disabled_and_error_retry_no_additional_get(view):
    description(view, '  <br><b> </b>')
    assert not view.summary_button.enabled and not view.summary_board.visible
    assert not view.toggle_summary()
    view.set_summary_failed('fixture simulated failure')
    assert view.summary_button.enabled and view.summary_button.text == '概要 取り直す'
    task = asyncio.create_task(view.retry_summary())
    await task
    assert not view._summary_retrying and not view.summary_board.visible
    assert view.summary_button.text in ('概要 取り直す', '概要 ▸')
    assert not view.summary_button.props['aria-expanded'] == 'true'


async def test_d10_first_last_bounds_one_page_and_resize(view):
    description(view, '先頭' + '<br>' + '<br>'.join('内容%03d' % i for i in range(85)))
    view.set_summary_viewport(1280, 720)
    assert view.toggle_summary()
    assert len(view._summary_pages) > 1
    first = view._summary_page_text
    assert not view.summary_prev.enabled and view.summary_next.enabled
    assert view.step_summary(1) and view._summary_page_text != first
    assert view.summary_prev.enabled
    for _ in range(200):
        view.step_summary(1)
    assert view._summary_page == len(view._summary_pages)-1
    assert not view.summary_next.enabled and view.summary_prev.enabled
    assert not view.step_summary(1)
    view.close_summary()
    assert view._summary_page == 0
    assert view.toggle_summary() and view._summary_page == 0
    view.set_summary_viewport(1920,1080)
    assert view._summary_page == 0
    assert len(view._summary_pages) >= 1
    description(view, '一頁だけ')
    assert not view.summary_next.visible and not view.summary_prev.visible
    assert view._summary_open is False  # changing the visible work description closes the old board


async def test_d09_judge_tab_trend_and_stale_work_close_summary(view):
    assert view.toggle_summary()
    view._on_loaded(SimpleNamespace(args=view._image_url))
    item = view.state.current_artwork
    assert view.judge('like',{'work_id':item.work_id,
                             'generation':view.state.generation, 'operation_id':'summary-judge'})
    assert not view._summary_open and view._summary_page == 0
    assert view.toggle_summary()
    view.set_active(False)
    assert not view._summary_open
    view.set_active(True)
    assert not view._summary_open


async def test_discovery_projection_passes_present_description_without_extra_fetch():
    source = dict(id='124',userId='99',title='タイトル',userName='作者',
                  tags=[],url='https://i.pximg.net/img/124.jpg',
                  description='<b>作品本文</b>')
    record = _discovery_record(source)
    assert record['description'] == '<b>作品本文</b>'
    owner = dict(work_id='124',author_id='99',title='タイトル',author_name='作者',
                 tags=[],thumbnail_url='https://i.pximg.net/img/124.jpg',
                 description=record['description'],source='ranking')
    artwork = DiscoveryLearningService._artwork_for(owner)
    assert artwork.description == '<b>作品本文</b>'
    assert DiscoveryLearningService._description_for(owner) == '<b>作品本文</b>'
    assert DiscoveryLearningService._description_for({k:v for k,v in owner.items() if k!='description'}) is None
