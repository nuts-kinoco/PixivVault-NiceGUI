"""Exercise the management tabs through the isolated audit application."""
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
from unittest.mock import patch

import pytest
from PIL import Image, ImageChops

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import p5_design_evidence as p5, uiux_audit as audit, uiux_fixture


@pytest.fixture(scope='module')
def application(tmp_path_factory, request):
    if audit.QUIET.is_file():
        pytest.skip('D6 quiet window')
    playwright = pytest.importorskip('playwright.sync_api')
    profile_scope = getattr(request, 'param', 'read')
    work = tmp_path_factory.mktemp(f'management-browser-{profile_scope}')
    original = work / 'original'
    original.mkdir()
    profile = work / 'port'
    p5.seed_profile(profile, original, 'long', tone_mode=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with p5.running_app('port', sys.executable, profile, port, secrets.token_urlsafe(24),
                        tone_mode=True, personal_fixture=True, server_script=Path(__file__).resolve(),
                        extra_env={'PV_UIUX_STATE': 'long'}):
        with playwright.sync_playwright() as runner, runner.chromium.launch(
            channel='msedge', headless=True,
            env=os.environ | {'LOCALAPPDATA': str(work / 'local'), 'APPDATA': str(work / 'roaming')},
            args=['--disable-gpu', '--disable-background-networking', '--no-proxy-server',
                  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'],
        ) as browser:
            yield browser, f'http://127.0.0.1:{port}'


@pytest.fixture(params=('light', 'dark'))
def browser_page(application, request):
    from playwright.sync_api import expect

    browser, origin = application
    context = browser.new_context(viewport={'width': 1024, 'height': 768},
                                  reduced_motion='reduce', service_workers='block')
    context.route('**/*', lambda route: route.continue_()
                  if route.request.url.startswith(origin + '/') else route.abort())
    context.route_web_socket('**/*', lambda route: route.connect_to_server()
                             if route.url.startswith('ws' + origin[4:] + '/') else route.close())
    try:
        page = context.new_page()
        page.set_default_timeout(6500)
        cell = audit.Cell('download', 'long', request.param, 1024, 768, 100, None)
        audit.load_page(page, origin, cell)
        yield page, expect, request.param
    finally:
        context.close()


@pytest.mark.parametrize('trigger', ('duplicate-move', 'duplicate-group-move'))
def test_quarantine_cancel_restores_keyboard_focus(browser_page, trigger):
    page, expect, _ = browser_page
    page.get_by_test_id('tab-dedup').click()
    page.get_by_test_id('duplicate-scan').click()
    expect(page.get_by_test_id('duplicate-status')).to_have_text(re.compile('枚を確認'))
    page.get_by_test_id('duplicate-mode-near').click()
    candidate = page.locator('[data-testid^=duplicate-select-]').first
    candidate.click()
    expect(candidate).to_have_attribute('aria-checked', 'true')
    opener = page.get_by_test_id(trigger).first
    expect(opener).to_be_enabled()
    opener.press('Enter')
    cancel = page.get_by_test_id('duplicate-confirm-cancel')
    expect(cancel).to_be_focused()
    if trigger == 'duplicate-group-move':
        page.keyboard.press('Escape')
    else:
        page.keyboard.press('Enter')
    expect(cancel).to_be_hidden()
    expect(opener).to_be_focused()


def test_nested_restore_cancel_returns_to_its_own_trigger(browser_page):
    page, expect, _ = browser_page
    page.get_by_test_id('tab-dedup').click()
    page.get_by_test_id('duplicate-trash').press('Enter')
    page.get_by_test_id('duplicate-trash-details').get_by_role('button').first.click()
    opener = page.get_by_test_id('duplicate-restore-choice').nth(1)
    expect(opener).to_be_visible()
    expect(opener).to_be_enabled()
    opener.press('Enter')
    cancel = page.get_by_test_id('duplicate-restore-cancel')
    expect(cancel).to_be_focused()
    page.keyboard.press('Enter')
    expect(cancel).to_be_hidden()
    expect(opener).to_be_focused()
    expect(page.get_by_test_id('duplicate-trash-close')).to_be_visible()


def test_dialog_focus_ring_tab_cycle_and_escape(browser_page, tmp_path):
    page, expect, theme = browser_page
    page.get_by_test_id('job-delete').first.press('Enter')
    confirm = page.get_by_test_id('job-delete-confirm')
    cancel = page.get_by_role('button', name='キャンセル', exact=True)
    expect(cancel).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(confirm).to_be_focused()
    page.locator('.q-dialog__inner').evaluate('''async el => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        await Promise.all(el.getAnimations({subtree: true}).map(animation => animation.finished));
    }''')
    shadow = confirm.evaluate('e => getComputedStyle(e).boxShadow')
    assert shadow != 'none', 'Quasar button has no visible focus shadow'
    bounds = confirm.bounding_box()
    assert bounds is not None
    x, y = round(bounds['x'] + bounds['width'] / 2), round(bounds['y'])
    with Image.open(BytesIO(page.screenshot(animations='disabled'))) as screenshot:
        assert audit.contrast(screenshot.getpixel((x, y - 3))[:3], screenshot.getpixel((x, y - 1))[:3]) >= 3
    if os.environ.get('PV_FIX3_CAPTURE') == '1':
        page.screenshot(path=tmp_path / f'queue-dialog-{theme}.png', animations='disabled')
    page.keyboard.press('Tab')
    expect(cancel).to_be_focused()
    page.keyboard.press('Escape')
    expect(cancel).to_be_hidden()
    expect(page.get_by_test_id('job-delete').first).to_be_focused()


@pytest.mark.parametrize('application', ['delete'], indirect=True, scope='module')
def test_queue_delete_moves_focus_after_row_is_removed(browser_page):
    page, expect, _ = browser_page
    opener = page.get_by_test_id('job-delete').first
    job = opener.get_attribute('data-job-id')
    opener.press('Enter')
    confirm = page.get_by_test_id('job-delete-confirm')
    expect(confirm).to_be_visible()
    page.keyboard.press('Tab')
    expect(confirm).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator(f'[data-testid=job-delete][data-job-id={json.dumps(job)}]')).to_have_count(0)
    expect(page.get_by_test_id('queue-select-mode')).to_be_focused()


def test_failed_record_dialog_escape_restores_focus(browser_page):
    page, expect, _ = browser_page
    page.set_viewport_size({'width': 1920, 'height': 1080})
    page.get_by_test_id('tab-failed').click()
    opener = page.get_by_test_id('failed-row-delete').first
    expect(opener).to_be_enabled()
    opener.press('Enter')
    cancel = page.get_by_test_id('failed-remove-cancel')
    expect(cancel).to_be_focused()
    page.keyboard.press('Escape')
    expect(cancel).to_be_hidden()
    expect(opener).to_be_focused()


def test_long_author_keyboard_tooltip_is_painted_inside_viewport(browser_page, tmp_path):
    page, expect, theme = browser_page
    page.get_by_test_id('tab-download').click()
    label = page.locator('.pv-ud-c-name').filter(has_text=p5.LONG[:20]).first
    row = label.locator('..')
    name = label.get_attribute('title')
    assert name and label.evaluate('e => e.scrollWidth > e.clientWidth')
    page.mouse.move(0, 0)
    row.get_by_test_id('unified-row-star').focus()
    page.keyboard.press('Shift+Tab')
    expect(row).to_be_focused()
    tooltip = page.get_by_role('tooltip').filter(has_text=name)
    expect(tooltip).to_be_visible()
    expect(tooltip).to_have_css('opacity', '1')
    box = tooltip.bounding_box()
    assert box and box['x'] >= 0 and box['y'] >= 0
    assert box['x'] + box['width'] <= 1024 and box['y'] + box['height'] <= 768
    shown = page.screenshot(animations='disabled')
    if os.environ.get('PV_FIX3_CAPTURE') == '1':
        (tmp_path / f'full-author-name-{theme}.png').write_bytes(shown)
    page.keyboard.press('Escape')
    expect(tooltip).to_be_hidden()
    expect(row).to_be_focused()
    bounds = (int(box['x']), int(box['y']), int(box['x'] + box['width']), int(box['y'] + box['height']))
    before = Image.open(BytesIO(shown)).convert('RGB').crop(bounds)
    after = Image.open(BytesIO(page.screenshot(animations='disabled'))).convert('RGB').crop(bounds)
    assert ImageChops.difference(before, after).getbbox(), 'tooltip did not paint into the screenshot'


def test_download_controls_fit_at_effective_200_percent_width(browser_page):
    page, expect, _ = browser_page
    page.set_viewport_size({'width': 640, 'height': 400})
    page.get_by_test_id('nav-toggle').click()
    page.get_by_test_id('tab-download').click()
    author = page.locator('.pv-ud-c-name').filter(has_text=p5.LONG[:20]).first
    author.wait_for(state='visible')
    left = page.get_by_test_id('unified-left')
    assert left.evaluate('el => el.scrollWidth <= el.clientWidth'), 'author tools overflow their panel'
    page.get_by_test_id('unified-run').scroll_into_view_if_needed()
    expect(page.get_by_test_id('unified-run')).to_be_in_viewport()


def test_queue_delete_hit_area_at_effective_200_percent_width(browser_page):
    page, _, _ = browser_page
    page.set_viewport_size({'width': 640, 'height': 400})
    action = page.get_by_test_id('job-delete').first
    action.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest'})")
    assert action.evaluate('''el => {
        const {x, y, width, height} = el.getBoundingClientRect();
        const cx = x + width/2, cy = y + height/2;
        return [[-13,0], [13,0], [0,-13], [0,13]].every(([dx, dy]) =>
            el.contains(document.elementFromPoint(cx + dx, cy + dy)));
    }''')


def test_detail_inputs_have_names_and_follow_search_fits(browser_page):
    page, expect, _ = browser_page
    page.get_by_test_id('tab-download').click()
    page.get_by_test_id('unified-details-open').press('Enter')
    dialog = page.get_by_test_id('unified-details-dialog')
    expect(dialog).to_be_visible()
    names = ('作品名・作者・IDで検索', '単一作品ID（数字）', '作者ID（作品IDではありません）',
             'フォロー作者名・作者IDで絞り込み')
    for name in names:
        control = dialog.get_by_role('textbox', name=name, exact=True)
        expect(control).to_have_attribute('aria-label', name)
    follow = dialog.get_by_role('textbox', name=names[-1], exact=True)
    assert follow.evaluate('''input => {
        const css = getComputedStyle(input);
        const canvas = document.createElement('canvas').getContext('2d');
        canvas.font = css.font;
        return canvas.measureText(input.placeholder).width <= input.clientWidth;
    }''')


def test_dedup_candidate_corner_hit_toggles_one_checkbox(browser_page):
    page, expect, _ = browser_page
    page.get_by_test_id('tab-dedup').click()
    page.get_by_test_id('duplicate-scan').click()
    expect(page.get_by_test_id('duplicate-status')).to_have_text(re.compile('枚を確認'))
    page.get_by_test_id('duplicate-mode-near').click()
    candidates = page.locator('[data-testid^=duplicate-select-]')
    candidate = candidates.first
    candidate.scroll_into_view_if_needed()
    candidate.evaluate("el => el.scrollIntoView({behavior: 'instant', block: 'center', inline: 'nearest'})")
    bounds = candidate.bounding_box()
    assert bounds is not None and bounds['height'] < 28
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    for dx, dy in ((13, 0), (-13, 0), (0, 13), (0, -13)):
        assert candidate.evaluate('(el, point) => el.contains(document.elementFromPoint(...point))', (x + dx, y + dy))
    page.mouse.click(x, y + 13)
    expect(candidate).to_have_attribute('aria-checked', 'true')
    expect(candidates.and_(page.locator('[aria-checked=true]'))).to_have_count(1)


def test_failed_selected_chip_contrast_without_body_dark_class(browser_page):
    page, _, theme = browser_page
    page.get_by_test_id('tab-failed').click()
    selected = page.get_by_test_id('failed-category-all')
    if theme == 'dark':
        page.evaluate("document.body.classList.remove('body--dark')")
    foreground, background = selected.evaluate(r'''e => {
        const style = getComputedStyle(e);
        return [style.color, style.backgroundColor].map(color => color.match(/\d+/g).slice(0, 3).map(Number));
    }''')
    assert audit.contrast(foreground, background) >= 4.5


if __name__ == '__main__':
    from pixivvault_nicegui.job_service import JobService
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView

    open_trash = DuplicateView.open_trash
    snapshot_rows = uiux_fixture.AuditFixture.queue_rows
    control_job = JobService.control
    deleted_jobs = set()

    def queue_rows(fixture):
        return tuple(row for row in snapshot_rows(fixture) if row['jobId'] not in deleted_jobs)

    def delete_fixture_job(service, job_id, action, *args, **kwargs):
        if action == 'delete' and any(job.job_id == job_id for job in service.snapshot()):
            deleted_jobs.add(job_id)
            return True
        return control_job(service, job_id, action, *args, **kwargs)

    async def with_restore_candidates(view):
        records = [('synthetic-one', 'moved', 1, 1), ('synthetic-two', 'moved', 2, 1)]
        snapshot = replace(view.source.quarantine_snapshot(), state='ready')
        with (patch.object(type(view.source), 'quarantine_snapshot', return_value=snapshot),
              patch.object(type(view.source), 'recoverable_moves', return_value=records, create=True)):
            await open_trash(view)

    assert len(sys.argv) == 4 and sys.argv[1] == '--serve-port'
    assert os.environ.get('PV_UIUX_STATE') == 'long' and Path('fixture.json').is_file()
    with (patch.object(DuplicateView, 'open_trash', with_restore_candidates),
          patch.object(uiux_fixture.AuditFixture, 'queue_rows', queue_rows),
          patch.object(JobService, 'control', delete_fixture_job)):
        uiux_fixture.serve_port(int(sys.argv[2]), sys.argv[3].split('=', 1)[1])
