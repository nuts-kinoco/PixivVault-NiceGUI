"""架空データのギャラリーを実ブラウザで受入確認する。

アプリは事前起動する。例: python tools/check_gallery_browser.py
遅延時間は入力開始からUI更新観測までの値（ブラウザ/通信/描画を含む）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.browser_reporting import capture_console_error


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def counter(page: Page, shown: int, total: int) -> None:
    expect(page.locator('.pv-content .pv-counter').first).to_have_text(
        f'{shown} / {total} 件表示', timeout=15_000
    )


def cards(page: Page, count: int) -> None:
    expect(page.locator('.pv-grid .pv-card')).to_have_count(count, timeout=15_000)


def viewer(page: Page):
    return page.locator('.pv-viewer')


def page_button(page: Page, label: str):
    return viewer(page).get_by_role('button', name=label, exact=True)


def scroll_top(page: Page) -> int:
    return page.get_by_test_id('gallery-scroll').evaluate('(el) => el.scrollTop')


def check_active_nav(page: Page) -> dict:
    tab = page.get_by_test_id('tab-ranking')
    expect(tab).to_have_attribute('aria-current', 'page')
    style = tab.evaluate('''el => ({
        color: getComputedStyle(el).color,
        background: getComputedStyle(el).backgroundColor,
        labelColor: getComputedStyle(el.querySelector('.q-btn__content') || el).color
    })''')
    check(style['color'] != style['background'] and style['labelColor'] != style['background'],
          f'activeナビゲーションの文字が背景と同色: {style}')
    return style


def check_viewport_layout(page: Page) -> dict:
    geometry = page.evaluate('''() => {
        const footer = document.querySelector('.pv-gallery > .pv-counter');
        const header = document.querySelector('.pv-header');
        const box = el => {const r=el.getBoundingClientRect(); return {top:r.top,bottom:r.bottom};};
        return {width:innerWidth,height:innerHeight,docWidth:document.documentElement.scrollWidth,
            docHeight:document.documentElement.scrollHeight,footer:box(footer),header:box(header)};
    }''')
    check(geometry['docWidth'] <= geometry['width'] + 1, f'横に画面がはみ出す: {geometry}')
    check(geometry['docHeight'] <= geometry['height'] + 1, f'画面全体が縦にスクロールする: {geometry}')
    check(geometry['header']['top'] >= 0 and geometry['footer']['bottom'] <= geometry['height'] + 1,
          f'ヘッダーまたは一覧操作が表示領域外: {geometry}')
    return geometry


def image_ready(page: Page, work_id: str, page_no: int) -> dict:
    img = page.get_by_test_id('viewer-image').locator('img')
    expect(img).to_have_attribute(
        'src', re.compile(rf'/media/{work_id}/{page_no}/view\.png(?:#request-[0-9]+)?$'),
        timeout=15_000,
    )
    img.evaluate('(el) => el.decode()', timeout=15_000)
    expect(img).to_have_js_property('complete', True)
    info = img.evaluate('''el => ({
        src: el.currentSrc || el.src, naturalWidth: el.naturalWidth,
        naturalHeight: el.naturalHeight, opacity: getComputedStyle(el.closest('.pv-view-art')).opacity
    })''')
    check(info['naturalWidth'] > 0 and info['naturalHeight'] > 0,
          f'viewer画像のnaturalSizeが無効: {info}')
    check(f'/media/{work_id}/{page_no}/view.png' in info['src'], f'viewer画像URL不一致: {info}')
    expect(page.get_by_test_id('viewer-image')).to_have_css('opacity', '1', timeout=15_000)
    expect(viewer(page).get_by_text('画像読込中')).to_be_hidden(timeout=15_000)
    expect(viewer(page).get_by_text('画像を読み込めませんでした')).to_be_hidden()
    return info


def decode_cards(page: Page, count: int) -> dict:
    images = page.locator('.pv-grid .pv-card img')
    expect(images).to_have_count(count, timeout=15_000)
    failures = []
    dimensions = []
    for index in range(count):
        img = images.nth(index)
        img.scroll_into_view_if_needed(timeout=10_000)
        try:
            img.evaluate('(el) => el.decode()', timeout=10_000)
        except Exception as exc:
            failures.append({'index': index, 'error': str(exc)})
        info = img.evaluate('(el) => ({complete: el.complete, width: el.naturalWidth, height: el.naturalHeight})')
        dimensions.append(info)
        if not info['complete'] or info['width'] <= 0 or info['height'] <= 0:
            failures.append({'index': index, 'dimensions': info})
    check(not failures, f'サムネイルdecode/naturalWidth不合格: {failures[:5]}')
    return {'checked': count, 'min_natural_width': min(v['width'] for v in dimensions)}


def search_and_clear(page: Page, result: dict) -> None:
    field = page.get_by_placeholder('作品名・作者・タグで検索')
    start = time.perf_counter()
    field.fill('架空作品 0003')
    counter(page, 1, 1)
    elapsed = (time.perf_counter() - start) * 1000
    result['search_elapsed_ms_including_pipeline'] = round(elapsed, 2)
    check(elapsed >= 150, f'入力待機200msに対し観測が早すぎる: {elapsed:.1f}ms')
    cards(page, 1)
    start = time.perf_counter()
    field.fill('')
    counter(page, 50, 120)
    result['clear_elapsed_ms_including_pipeline'] = round((time.perf_counter() - start) * 1000, 2)
    cards(page, 50)


def run_viewport(page: Page, url: str, size: tuple[int, int], output: Path, result: dict) -> None:
    width, height = size
    page.set_viewport_size({'width': width, 'height': height})
    page.goto(url, wait_until='domcontentloaded', timeout=25_000)
    expect(page.get_by_test_id('tab-ranking')).to_have_attribute('aria-current', 'page')
    counter(page, 50, 120)
    cards(page, 50)
    key = f'{width}x{height}'
    record: dict = {'viewport': {'width': width, 'height': height}}
    result['viewports'][key] = record
    record['active_nav_style'] = check_active_nav(page)
    record['layout'] = check_viewport_layout(page)
    record['initial_images'] = decode_cards(page, 50)
    page.get_by_test_id('gallery-scroll').evaluate('(el) => {el.scrollTop = 0}')
    check(scroll_top(page) == 0, '初期スクリーンショットでscrollTopが0ではない')
    expect(page.get_by_role('button', name='もっと見る', exact=True)).not_to_be_in_viewport()
    page.screenshot(path=str(output / f'gallery-{key}-initial.png'), full_page=True)
    search_and_clear(page, record)

    first = page.locator('.pv-card').first.element_handle()
    check(first is not None, '先頭カードDOMを取得できない')
    more = page.get_by_role('button', name='もっと見る', exact=True)
    more.scroll_into_view_if_needed()
    before_more_scroll = scroll_top(page)
    more.click()
    counter(page, 100, 120)
    cards(page, 100)
    record['same_first_dom_node_after_more'] = bool(
        page.locator('.pv-card').first.evaluate('(el, original) => el === original', first)
    )
    check(record['same_first_dom_node_after_more'], 'もっと見るで先頭カードDOMが交換された')
    check(abs(scroll_top(page) - before_more_scroll) <= 5, '追加後にスクロール位置が飛ぶ')
    record['more_added'] = 50

    grid = page.locator('.pv-grid')
    observed_sizes = {}
    for label, expected in [('小', 12), ('中', 8), ('大', 6)]:
        page.get_by_role('button', name=label, exact=True).click()
        expect(grid).to_have_css('--pv-max-cols', str(expected))
        expect(page.get_by_role('button', name=label, exact=True)).to_have_attribute('aria-pressed', 'true')
        observed_sizes[label] = grid.evaluate('''el => ({
            maxCols: getComputedStyle(el).getPropertyValue('--pv-max-cols').trim(),
            gridColumns: getComputedStyle(el).gridTemplateColumns,
        firstCardWidth: el.querySelector('.pv-card').getBoundingClientRect().width,
        columnCount: getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length,
        availableWidth: el.getBoundingClientRect().width,
        gap: parseFloat(getComputedStyle(el).columnGap)
        })''')
        check(observed_sizes[label]['maxCols'] == str(expected), f'列数CSS変数不一致: {label}')
        info = observed_sizes[label]
        fitted = max(1, int((info['availableWidth'] + 12) // 182))
        check(info['columnCount'] == min(expected, fitted), f'列数が170px/12px契約に反する: {label} {info}')
        check(info['gap'] == 12 and info['firstCardWidth'] >= 169.5,
              f'カード最小幅/間隔の契約違反: {label} {info}')
    check(observed_sizes['小']['firstCardWidth'] <= observed_sizes['中']['firstCardWidth']
          <= observed_sizes['大']['firstCardWidth'], 'カード幅がサイズの順序に従わない')
    record['card_sizes'] = observed_sizes

    target = page.locator('.pv-card[data-work-id="demo-0003"]')
    target.click()
    expect(viewer(page)).to_be_visible()
    expect(viewer(page).locator('.pv-counter')).to_contain_text('ページ 1 / 3')
    page_images = [image_ready(page, 'demo-0003', 0)]
    expect(page_button(page, '前のページ')).to_be_disabled()
    expect(page_button(page, '次のページ')).to_be_enabled()
    page_button(page, '次のページ').click()
    expect(viewer(page).locator('.pv-counter')).to_contain_text('ページ 2 / 3')
    page_images.append(image_ready(page, 'demo-0003', 1))
    page_button(page, '次のページ').click()
    expect(viewer(page).locator('.pv-counter')).to_contain_text('ページ 3 / 3')
    page_images.append(image_ready(page, 'demo-0003', 2))
    expect(page_button(page, '次のページ')).to_be_disabled()
    page_button(page, '前のページ').click()
    expect(viewer(page).locator('.pv-counter')).to_contain_text('ページ 2 / 3')
    page_images.append(image_ready(page, 'demo-0003', 1))
    page_button(page, '詳細').click()
    description = viewer(page).locator('.pv-description')
    expect(description).to_be_visible()
    viewer(page).focus()
    page.keyboard.press('Escape')
    expect(viewer(page)).to_be_visible()
    expect(description).to_be_hidden()
    viewer(page).focus()
    page.keyboard.press('i')
    expect(description).to_be_visible()
    viewer(page).focus()
    page.keyboard.press('Tab')
    expect(description).to_be_hidden()
    viewer(page).focus()
    page.keyboard.press('Escape')
    expect(viewer(page)).to_be_hidden()

    lower = page.locator('.pv-card[data-work-id="demo-0030"]')
    lower.scroll_into_view_if_needed()
    scroll_before = scroll_top(page)
    check(scroll_before > 0, '下方カードへのスクロールが成立していない')
    failed_url = '**/media/demo-0030/0/view.png*'
    record['injected_failure'] = 'HTTP 503 for demo-0030/0/view.png'
    result['expecting_injected_image_failure'] = True
    page.route(failed_url, lambda route: route.fulfill(status=503, body='synthetic failure'))
    lower.click()
    expect(viewer(page)).to_be_visible()
    expect(viewer(page).get_by_text('画像を読み込めませんでした')).to_be_visible(timeout=15_000)
    retry = page_button(page, '画像を再読込')
    expect(retry).to_be_visible()
    page.unroute(failed_url)
    result['expecting_injected_image_failure'] = False
    retry.click()
    image_ready(page, 'demo-0030', 0)
    page_button(page, '閉じる').click()
    expect(viewer(page)).to_be_hidden()
    scroll_after_close = scroll_top(page)
    check(abs(scroll_after_close - scroll_before) <= 5, '閉じる後の内部スクロール位置が復元されない')
    lower.click()
    expect(viewer(page)).to_be_visible()
    viewer(page).focus()
    page.keyboard.press('Escape')
    expect(viewer(page)).to_be_hidden()
    scroll_after_escape = scroll_top(page)
    check(abs(scroll_after_escape - scroll_before) <= 5, 'Escape後の内部スクロール位置が復元されない')
    record['viewer'] = {'multi_page_work': 'demo-0003', 'page_navigation': True,
                        'page_images': page_images, 'detail_button_i_tab': True,
                        'two_stage_escape': True, 'close_button': True,
                        'failure_retry': True,
                        'scroll_work': 'demo-0030', 'scroll_top_before': scroll_before,
                        'scroll_top_after_escape': scroll_after_escape,
                        'scroll_top_after_close': scroll_after_close}
    page.screenshot(path=str(output / f'gallery-{key}-final.png'), full_page=True)


def check_context_isolation(context, url: str, result: dict) -> None:
    a = context.new_page()
    b = context.new_page()
    try:
        a.goto(url, wait_until='domcontentloaded')
        b.goto(url, wait_until='domcontentloaded')
        counter(a, 50, 120)
        counter(b, 50, 120)
        a.get_by_placeholder('作品名・作者・タグで検索').fill('架空作品 0003')
        counter(a, 1, 1)
        counter(b, 50, 120)
        a.get_by_role('button', name='小', exact=True).click()
        expect(a.locator('.pv-grid')).to_have_css('--pv-max-cols', '12')
        expect(b.locator('.pv-grid')).to_have_css('--pv-max-cols', '6')
        result['independent_pages_same_browser_context'] = True
    finally:
        a.close()
        b.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8089')
    parser.add_argument('--output', type=Path, default=Path('artifacts/gallery-browser'))
    parser.add_argument('--channel', choices=('msedge', 'chromium'), default='msedge')
    parser.add_argument('--headed', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result: dict = {'url': args.url, 'channel': args.channel,
                    'started_at': datetime.now(timezone.utc).isoformat(),
                    'timing_definition': 'fill開始から期待するDOM更新の観測まで。debounceのほか通信・サーバ処理・描画・Playwright待機を含む',
                    'viewports': {}, 'console_errors': [], 'page_errors': [],
                    'expected_injected_console_errors': [], 'expecting_injected_image_failure': False,
                    'passed': [],
                    'success': False, 'failure': None}
    browser = context = page = None
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel=args.channel if args.channel != 'chromium' else None,
                    headless=not args.headed,
                )
                context = browser.new_context(viewport={'width': 1280, 'height': 720})
                context.on('page', lambda p: (
                    p.on('console', lambda m: capture_console_error(m, result)),
                    p.on('pageerror', lambda e: result['page_errors'].append(str(e))),
                ))
                page = context.new_page()
                for size in ((1280, 720), (1920, 1080)):
                    run_viewport(page, args.url, size, args.output, result)
                    result['passed'].append(f'viewport {size[0]}x{size[1]}')
                check_context_isolation(context, args.url, result)
                result['passed'].append('two-page state isolation')
                check(not result['console_errors'] and not result['page_errors'], 'ブラウザコンソール/ページ例外あり')
                result['success'] = True
            finally:
                if page is not None:
                    try:
                        page.screenshot(path=str(args.output / 'gallery-last.png'), full_page=True, timeout=5_000)
                    except Exception as exc:
                        result['success'] = False
                        result['screenshot_error'] = str(exc)
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
    except Exception:
        result['success'] = False
        result['failure'] = traceback.format_exc()
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        result['passed_count'] = len(result['passed'])
        (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (args.output / 'console-errors.txt').write_text('\n'.join(result['console_errors'] + result['page_errors']), encoding='utf-8')
    print(f"passed={result['passed_count']} console_errors={len(result['console_errors'])} page_errors={len(result['page_errors'])}")
    if result['failure']:
        print(result['failure'], file=sys.stderr)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
