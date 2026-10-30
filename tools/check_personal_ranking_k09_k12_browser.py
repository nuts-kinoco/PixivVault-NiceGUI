"""K09-K12: isolated synthetic NiceGUI browser acceptance, no Pixiv access.

Only a memory-only Vault fixture supplies candidates and preferences. Browser
requests outside this checker's own loopback origin are aborted. Both viewports
exercise the same visible controls rather than private application handlers.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from tools.run_browser_checks import (
    child_environment, free_port, port_released, source_hashes, stop_child, wait_for_health,
)

HOST = '127.0.0.1'
OWNED = ('src/pixivvault_nicegui/ui/personal_ranking_view.py',
         'src/pixivvault_nicegui/personal_ranking.py')


def raw(wid, aid, tags, *, date='2099-01-01T00:00:00+09:00'):
    return {'id': str(wid), 'title': f'架空作品 {wid}',
            'user': {'id': str(aid), 'name': f'架空作者 {aid}'},
            'image_urls': {'square_medium':
                f'https://i.pximg.net/img-master/img/2026/01/01/00/00/00/{wid}_p0_square1200.jpg'},
            'tags': [{'name': tag} for tag in ('feed', *tags)],
            'page_count': 1, 'type': 'illust', 'create_date': date}


ROWS = (
    raw(101,10,('green','sun')),
    raw(102,10,('blue','sun')),
    raw(103,20,('green','old'),date='2020-01-01T00:00:00+09:00'),
    raw(104,20,('green','sun')),
    raw(105,30,('blue','rain')),
)


class MemoryVault:
    def __init__(self):
        self.saved = {}
        self.calls = []
        self.open = True
    def status(self):
        return {'open': self.open, 'mode': 'nana7mi',
                'transport_available': self.open, 'crypto_available': True}
    def ranking_preferences(self):
        return deepcopy(self.saved)
    def save_ranking_preferences(self, tags, filters):
        from pixivvault_nicegui.personal_ranking import normalize_personal_filters
        self.saved = {'tags': list(tags), 'filters': normalize_personal_filters(filters)}
    def search(self, word, bookmark_min, *, sort, r18, exclude_ai, offset):
        from pixivvault_nicegui.external_vault import VaultError
        self.calls.append({'word': word, 'bookmark_min': bookmark_min,
                           'sort': sort, 'r18': r18, 'exclude_ai': exclude_ai,
                           'offset': offset})
        if word == 'failure-case':
            raise VaultError('fixture-search-failed')
        if word == 'no-match-000':
            return {'illusts': [], 'next_url': None}
        if word != 'feed':
            return {'illusts': [], 'next_url': None}
        rows = ROWS if bookmark_min == 0 else tuple(
            item for item in ROWS if item['id'] in ('101','104'))
        return {'illusts': deepcopy(list(rows)), 'next_url': None}
    def close(self):
        self.open = False


def serve(port):
    from nicegui import app, ui
    from pixivvault_nicegui.bootstrap import register_pages
    from pixivvault_nicegui.services import get_services
    register_pages()
    service = get_services(app)
    service.external_vault = MemoryVault()
    service.personal_ranking_context = lambda: {
        'hidden_work_ids': set(), 'hidden_author_ids': set(),
        'downloaded_ids': set(), 'hated_ids': set(),
        'trend_rows': [{'tag': 'feed', 'display': '候補 feed'}],
    }
    class SyntheticPin:
        def __init__(self):
            self.closed=False
        def url(self, work_id, page=0, variant='thumb'):
            if self.closed:raise RuntimeError('closed synthetic pin')
            # Every card gets a known generated local PNG; image request
            # identity is deliberately separate from the tested result IDs.
            return '/media/demo-0001/0/thumb.png'
        def can_open(self, work_id):
            return False
        def needs_metadata(self, work_id):
            return False
        def close(self):
            self.closed=True
    def acquire(records):
        return SyntheticPin()
    service.acquire_ranking_images = acquire
    ui.run(host=HOST, port=port, show=False, reload=False,
           binding_refresh_interval=None)


def select_option(control, label, page):
    control.click()
    page.get_by_role('option', name=label, exact=True).last.click()


def field(page, testid):
    # NiceGUI may place data-testid on the native input itself rather than its
    # Quasar wrapper. Match either shape without relying on CSS ancestry.
    return page.locator(
        f'textarea[data-testid="{testid}"],input[data-testid="{testid}"],'
        f'[data-testid="{testid}"] textarea,[data-testid="{testid}"] input',
    ).first


def run_viewport(page, width, height, report, output):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(report['url'], wait_until='domcontentloaded', timeout=20000)
    page.get_by_test_id('tab-personal').click()
    panel = page.get_by_test_id('personal-ranking-view')
    expect(panel).to_be_visible()
    # The same memory-only service intentionally survives page navigation.
    # Start both viewport passes from an identical server-side preset, after
    # observing the prior pass's saved value on its own reload below.
    bookmark = page.get_by_test_id('personal-bookmarks')
    if '指定なし' not in bookmark.inner_text():
        select_option(bookmark, '指定なし', page)
    sort = page.get_by_test_id('personal-sort')
    if '人気順' not in sort.inner_text():
        select_option(sort, '人気順', page)
    expect(page.get_by_test_id('personal-r18')).to_be_disabled()
    candidate = page.get_by_test_id('personal-candidates-toggle')
    expect(candidate).to_be_visible()
    # Original K-9: candidate line is expanded by default and its toggle remains
    # available; user can explicitly add a suggested tag.
    expect(panel.get_by_role('button', name='候補 feed')).to_be_visible()
    candidate.locator('.q-item').first.click()
    expect(panel.get_by_role('button', name='候補 feed')).to_be_hidden()
    candidate.locator('.q-item').first.click()
    expect(panel.get_by_role('button', name='候補 feed')).to_be_visible()
    selection = field(page, 'personal-tags')
    selection.fill('')
    fetch = page.get_by_test_id('personal-fetch')
    expect(fetch).to_be_disabled()
    expect(panel.locator('.pv-card')).to_have_count(0)
    assert '未取得' in panel.locator('.pv-counter').first.inner_text()
    panel.get_by_role('button', name='候補 feed').click()
    expect(selection).to_have_value('feed')
    expect(fetch).to_be_enabled()
    fetch.click()
    cards = panel.locator('.pv-card')
    try:
        expect(cards).to_have_count(5)
    except Exception:
        raise AssertionError(json.dumps({
            'stage':'first-search',
            'notice':page.get_by_test_id('personal-notice').inner_text(),
            'card_count':cards.count(),
            'card_ids':[el.get_attribute('data-work-id') for el in cards.all()],
            'counter':[x.inner_text() for x in panel.locator('.pv-counter').all()],
            'tags':selection.input_value(),
            'fetch_enabled':fetch.is_enabled(),
        },ensure_ascii=False)) from None
    assert {element.get_attribute('data-work-id') for element in cards.all()} == {
        '101','102','103','104','105'}

    # K09/K11/K12: these filters are local and immediately replace the cards;
    # unlike server options they do not require a second search.
    filters = panel.get_by_text('絞り込み（取得済みの結果へ適用）', exact=True)
    filters.click()
    include = field(page, 'personal-tags-include')
    exclude = field(page, 'personal-tags-exclude')
    include.fill('green')
    expect(cards).to_have_count(3)
    exclude.fill('old')
    expect(cards).to_have_count(2)
    date = page.get_by_test_id('personal-date-preset')
    select_option(date, '1か月', page)
    expect(date).to_contain_text('1か月')
    exclude.fill('')
    expect(cards).to_have_count(2)  # older green work excluded by date
    select_option(date,'すべて',page)
    expect(cards).to_have_count(3)
    author_max = field(page, 'personal-author-max')
    author_max.fill('1')
    author_max.press('Tab')
    expect(cards).to_have_count(2)
    include.fill('zzzz_nonexistent_999')
    expect(cards).to_have_count(0)
    expect(panel.get_by_text('条件に一致する作品は0件です。条件を変更してください。')).to_be_visible()
    assert '0件' in page.get_by_test_id('personal-notice').inner_text()
    page.get_by_test_id('personal-clear-filters').click()
    expect(include).to_have_value('')
    expect(exclude).to_have_value('')
    expect(author_max).to_have_value('0')
    expect(cards).to_have_count(5)

    # K10 changes the Pixiv/server-side query. Old results must not be falsely
    # displayed under 500+ until the user explicitly fetches.
    bookmark = page.get_by_test_id('personal-bookmarks')
    select_option(bookmark, '500+', page)
    expect(bookmark).to_contain_text('500+')
    expect(cards).to_have_count(0)
    expect(panel.locator('.pv-counter').first).to_contain_text('未取得')
    expect(page.get_by_test_id('personal-notice')).to_contain_text('取得・更新')
    expect(fetch).to_be_enabled()
    fetch.click()
    expect(cards).to_have_count(2)
    ids={element.get_attribute('data-work-id') for element in cards.all()}
    assert ids=={'101','104'}
    # The remaining K-9 server preset: changing sort cannot reuse the old
    # server query results under the new requested order.
    sort=page.get_by_test_id('personal-sort')
    select_option(sort,'新着順',page)
    expect(sort).to_contain_text('新着順')
    expect(cards).to_have_count(0)
    fetch.click()
    expect(cards).to_have_count(2)
    page.get_by_test_id('personal-save-filters').click()
    expect(page.get_by_test_id('personal-notice')).to_contain_text('保存しました')

    # K-13 adjacent persistence boundary: preferences survive tab switches
    # and a fresh NiceGUI page in this isolated in-memory app.
    page.get_by_test_id('tab-ranking').click()
    expect(panel).to_be_hidden()
    page.get_by_test_id('tab-personal').click()
    expect(panel).to_be_visible()
    expect(bookmark).to_contain_text('500+')
    expect(cards).to_have_count(2)
    page.reload(wait_until='domcontentloaded')
    page.get_by_test_id('tab-personal').click()
    panel = page.get_by_test_id('personal-ranking-view')
    bookmark=page.get_by_test_id('personal-bookmarks')
    expect(bookmark).to_contain_text('500+')
    expect(page.get_by_test_id('personal-sort')).to_contain_text('新着順')
    assert '500' in panel.inner_text()
    expect(panel.locator('.pv-card')).to_have_count(0)  # no phantom auto-search
    page.get_by_text('絞り込み（取得済みの結果へ適用）',exact=True).click()
    page.get_by_test_id('personal-clear-filters').click()
    expect(bookmark).to_contain_text('指定なし')
    fetch=page.get_by_test_id('personal-fetch')
    fetch.click()
    expect(panel.locator('.pv-card')).to_have_count(5)

    # Distinguish true empty success from a failed query, neither may retain
    # cards from a previous server-side key.
    selection=field(page,'personal-tags')
    selection.fill('no-match-000')
    expect(panel.locator('.pv-card')).to_have_count(0)
    fetch.click()
    expect(page.get_by_test_id('personal-notice')).to_contain_text('0件表示')
    expect(panel.get_by_text('条件に一致する作品は0件です。条件を変更してください。')).to_be_visible()
    selection.fill('failure-case')
    fetch.click()
    expect(page.get_by_test_id('personal-notice')).to_contain_text('検索を完了できませんでした')
    expect(panel.locator('.pv-card')).to_have_count(0)
    expect(fetch).to_be_enabled()
    selection.fill('')
    expect(fetch).to_be_disabled()
    page.screenshot(path=str(output/f'personal-k09-k12-{width}x{height}.png'),animations='disabled')
    metrics=panel.evaluate("""el => {
      const r=el.getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,
        scrollWidth:document.documentElement.scrollWidth,
        panelWidth:r.width,panelHeight:r.height};
    }""")
    assert metrics['scrollWidth']<=width+1,metrics
    report['viewports'][f'{width}x{height}']={
        'metrics': metrics,
        'assertions': ('candidate-toggle-default-open','tag-add','search-five',
             'include-exclude','date-preset','per-author-limit','zero-results',
             'clear','bookmark-requery-only','sort-requery-only','filter-save','tab-return',
             'reload-persisted','failure-distinct','disabled-empty-tag'),
    }


def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--channel',choices=('msedge','chromium'),default='msedge')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int)
    args=parser.parse_args()
    if args.serve:
        if not args.port or not 1024<=args.port<=65535:
            raise ValueError('invalid fixture port')
        serve(args.port)
        return
    if args.output is None:
        parser.error('--output required for acceptance')
    output=args.output.resolve()
    allowed=(ROOT/'artifacts').resolve()
    if output==allowed or not output.is_relative_to(allowed):
        raise ValueError('output must be under candidate artifacts')
    output.mkdir(parents=True,exist_ok=False)
    before=source_hashes()
    port=free_port()
    report={'url':f'http://{HOST}:{port}', 'channel':args.channel,
            'before':{key:before[key] for key in OWNED},
            'viewports':{},'external_requests':[],
            'console_errors':[],'page_errors':[],'process':{},
            'error':None,'success':False}
    process=None
    try:
        with (output/'app-stderr.log').open('wb') as err:
            process=subprocess.Popen([
                sys.executable,str(Path(__file__).resolve()),
                '--serve','--port',str(port)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=err,
                cwd=str(ROOT),env=child_environment())
            report['process']['pid']=process.pid
            wait_for_health(report['url'],process)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(
                    channel=None if args.channel=='chromium' else args.channel,headless=True)
                context=browser.new_context(viewport={'width':1280,'height':720})
                def guard(route):
                    u=urlsplit(route.request.url)
                    if (u.scheme,u.netloc)!=('http',f'{HOST}:{port}'):
                        report['external_requests'].append(f'{u.scheme}://{u.netloc}')
                        route.abort()
                    else:route.continue_()
                context.route('**/*',guard)
                page=context.new_page()
                page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
                page.on('console',lambda msg:report['console_errors'].append(msg.text)
                    if msg.type=='error' else None)
                try:
                    for width,height in ((1280,720),(1920,1080)):
                        run_viewport(page,width,height,report,output)
                finally:
                    context.close();browser.close()
    except BaseException as exc:
        report['error']=type(exc).__name__+': '+str(exc)
    finally:
        stop_child(process)
        if process is not None:report['process']['exit']=process.poll()
        report['process']['port_released']=port_released(port)
        after=source_hashes()
        report['after']={key:after[key] for key in OWNED}
        report['source_changed']=before!=after
        report['success']=(report['error'] is None and len(report['viewports'])==2
            and not report['external_requests'] and not report['console_errors']
            and not report['page_errors'] and not report['source_changed']
            and report['process']['port_released']
            and report['process'].get('exit') is not None)
        (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not report['success']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
