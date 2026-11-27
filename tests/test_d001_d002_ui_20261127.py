"""Regression tests for D-001 (640x400 warning+429 image visibility) and D-002 (A8 font-size floor)."""
from __future__ import annotations

from types import SimpleNamespace

from nicegui import Client, ui
from playwright.sync_api import sync_playwright

from pixivvault_nicegui.ui.failed_records_view import CSS as FAILED_CSS
from pixivvault_nicegui.ui.request_status_view import RequestStatusView, retry_status_text
from pixivvault_nicegui.ui.theme import CSS as THEME_CSS
from pixivvault_nicegui.ui.unified_download_view import CSS as DOWNLOAD_CSS


_TWO_429_RECORDS = [
    dict(phase='stopped', attempt=3, limit=3, status=429, stop_cause='own_limit', notice_id=1),
    dict(phase='stopped', attempt=2, limit=3, status=429, stop_cause='shared_limit', notice_id=2),
]


def test_d001_compact_rate_stop_label_is_keyboard_focusable_and_keeps_full_text():
    records = list(_TWO_429_RECORDS)
    policy = SimpleNamespace(progress_snapshot=lambda: list(records))
    client = Client(ui.page('/d001-unit'), request=None)
    try:
        with client:
            view = RequestStatusView(policy, compact=True)
            assert view.label.props.get('data-rate-stop') == 'true'
            assert view.label.props.get('tabindex') == '0'
            assert 'pv-focus-ring' in view.label.classes
            assert '試行 3/3' in view.label.text
            assert '試行 2/3' in view.label.text
            assert view.stop_details.visible is True
            assert view.dismiss_button.visible is True

            # Non-rate-stop state must not leave an unnecessary tab stop.
            records[:] = [dict(phase='waiting', attempt=1, limit=3, status=429,
                               remaining_seconds=5, wait_seconds=5)]
            view.refresh()
            assert view.label.props.get('data-rate-stop') == 'false'
            assert 'tabindex' not in view.label.props
            view.close()
    finally:
        client.delete()


def _header_stress_html(rate_text: str) -> str:
    return f'''<!doctype html><html><head><style>
      body{{margin:0}} .row{{display:flex;flex-wrap:wrap}} .column{{display:flex;flex-direction:column}}
      .w-full{{width:100%}} .q-btn{{display:inline-flex;align-items:center;justify-content:center;border:0;background:transparent;color:inherit;font:inherit}}
      {THEME_CSS}
    </style></head><body class="body--light">
    <div class="pv-shell">
      <div class="pv-db-banner pv-db-production" data-testid="db-status-banner">
        <div class="pv-db-compact">
          <div role="status">本番DBを使用中：S:\\…\\synthetic.sqlite</div>
          <button class="q-btn pv-hit28" data-testid="db-warning-details">詳細</button>
        </div>
      </div>
      <div class="pv-shell-chrome">
        <div class="pv-header">
          <div class="pv-header-main">
            <div class="pv-title">PixivVault</div>
            <div class="pv-auth" data-testid="auth-status">● [有効] ログイン中 (有効期限 残り約30日)</div>
            <div class="row pv-conn-row" style="flex-wrap:nowrap">
              <div class="q-toggle" style="height:28px;display:inline-flex;align-items:center">API切り替え</div>
              <button class="q-btn" data-testid="shared-get-recovery-open" style="height:28px;font-size:12px">取得停止の確認・回復</button>
            </div>
            <div class="w-full pv-header-request-status">
              <div class="pv-subtitle pv-request-status pv-alert-banner pv-focus-ring"
                   data-testid="request-retry-status" data-rate-stop="true" tabindex="0" role="alert">{rate_text}</div>
              <details class="pv-request-stop-details">
                <summary class="pv-hit28 pv-focus-ring" data-testid="request-retry-stop-details-toggle">停止の詳細</summary>
                <div class="pv-request-stop-supplement pv-focus-ring" tabindex="0" data-testid="request-retry-stop-details-text">
                  同じ通信の仕組みを使う取得は、この起動中は停止しています。再起動後も、過去に記録した通信停止は残ります。
                </div>
              </details>
              <button class="q-btn pv-btn-secondary" data-testid="request-retry-dismiss">閉じる</button>
            </div>
          </div>
          <div class="pv-header-actions">
            <button class="q-btn pv-hbtn">H</button><button class="q-btn pv-hbtn">V</button>
            <button class="q-btn pv-hbtn">F</button><button class="q-btn pv-hbtn">T</button>
            <a class="pv-hbtn">P</a><button class="q-btn pv-hbtn">S</button>
            <button class="q-btn pv-hbtn">A</button>
            <div class="pv-automation-status">
              <div data-testid="automation-unresolved">自動処理：未解消 2</div>
              <div>未確認 2／確認済み 0</div>
            </div>
          </div>
        </div>
        <div class="pv-nav-region">
          <button class="pv-nav-toggle" type="button" aria-expanded="false" data-testid="nav-toggle">
            <span>ランキング</span><span data-nav-action>全画面の一覧を開く</span>
          </button>
        </div>
      </div>
      <div class="pv-content w-full" data-testid="probe-content"></div>
    </div></body></html>'''


def test_d001_short_viewport_compresses_429_banner_while_normal_height_unchanged():
    rate_text = retry_status_text(_TWO_429_RECORDS, compact=True)
    html = _header_stress_html(rate_text)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 640, 'height': 400})
            page.set_content(html)
            narrow = page.evaluate('''() => {
                const status = document.querySelector('[data-testid=request-retry-status]');
                const details = document.querySelector('[data-testid=request-retry-stop-details-toggle]');
                const dismiss = document.querySelector('[data-testid=request-retry-dismiss]');
                const content = document.querySelector('[data-testid=probe-content]');
                const block = document.querySelector('.pv-header-request-status');
                const cs = getComputedStyle(status);
                return {
                    blockHeight: block.getBoundingClientRect().height,
                    statusHeight: status.getBoundingClientRect().height,
                    statusOverflowY: cs.overflowY,
                    statusScrollable: status.scrollHeight > status.clientHeight,
                    detailsVisible: details.getBoundingClientRect().height > 0,
                    dismissVisible: dismiss.getBoundingClientRect().height > 0,
                    contentTop: content.getBoundingClientRect().top,
                    horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
                };
            }''')
            assert narrow['detailsVisible'] is True
            assert narrow['dismissVisible'] is True
            assert narrow['horizontalOverflow'] is False
            assert narrow['statusOverflowY'] in ('auto', 'scroll')
            assert narrow['statusScrollable'] is True
            assert narrow['blockHeight'] <= 44, f"429 block too tall at 640x400: {narrow}"
            assert narrow['contentTop'] <= 172, f"chrome pushes content too low at 640x400: {narrow}"

            # Keyboard scrolling must reach the bottom of the 429 notice text.
            status_loc = page.get_by_test_id('request-retry-status')
            status_loc.focus()
            for _ in range(8):
                page.keyboard.press('PageDown')
                page.wait_for_timeout(40)
            scrolled = status_loc.evaluate('el => ({top: el.scrollTop, max: el.scrollHeight - el.clientHeight})')
            assert scrolled['top'] > 0 and scrolled['top'] >= scrolled['max'] - 2, scrolled

            # Keyboard Enter on 停止の詳細 reveals the supplement text.
            toggle = page.get_by_test_id('request-retry-stop-details-toggle')
            toggle.focus()
            page.keyboard.press('Enter')
            assert page.get_by_test_id('request-retry-stop-details-text').is_visible()

            # Normal desktop height (1280x800) must keep full multi-line 429 display unchanged.
            page.set_viewport_size({'width': 1280, 'height': 800})
            desktop = page.evaluate('''() => {
                const status = document.querySelector('[data-testid=request-retry-status]');
                const cs = getComputedStyle(status);
                return {
                    maxHeight: cs.maxHeight,
                    overflowY: cs.overflowY,
                    scrollable: status.scrollHeight > status.clientHeight + 1,
                };
            }''')
            assert desktop['maxHeight'] == 'none', desktop
            assert desktop['overflowY'] == 'visible', desktop
            assert desktop['scrollable'] is False, desktop
        finally:
            browser.close()


def test_d002_non_frozen_primary_text_meets_13px_and_frozen_exceptions_stay_unchanged():
    html = f'''<!doctype html><html><head><style>
      .row{{display:flex}} .column{{display:flex;flex-direction:column}}
      {THEME_CSS}
      {DOWNLOAD_CSS}
      {FAILED_CSS}
    </style></head><body class="body--dark">
      <div class="pv-bookmarks"><div class="pv-grid"><div class="pv-card" data-testid="bookmark-card">
        <div class="pv-card-title" id="bm-title">作品タイトル</div>
        <div class="pv-card-author" id="bm-author">作者名</div>
      </div></div></div>
      <div data-testid="personal-ranking-view"><div class="pv-grid"><div class="pv-card">
        <div class="pv-card-meta">
          <div class="pv-card-title" id="personal-title">作品タイトル</div>
          <div class="pv-card-author" id="personal-author">作者名</div>
        </div>
      </div></div></div>
      <div data-testid="recommend-view"><div data-testid="recommend-section-certain"><div class="pv-grid"><div class="pv-card">
        <div class="pv-card-meta">
          <div class="pv-card-title" id="reco-title">作品タイトル</div>
          <div class="pv-card-author" id="reco-author">作者名</div>
        </div>
      </div></div></div></div>
      <div class="pv-ud">
        <div class="pv-ud-thead"><div class="pv-ud-c-name" id="ud-head-name">作者名</div></div>
        <div class="pv-ud-list" data-testid="unified-author-list">
          <div class="pv-ud-row"><div class="pv-ud-c-name" id="ud-row-name">合成作者</div></div>
        </div>
      </div>
      <div class="pv-failure-rows" data-testid="failed-rows">
        <div class="pv-failure-row">
          <div class="pv-failure-row-top"><div class="pv-failure-title" id="fail-title">失敗タイトル</div></div>
          <div class="pv-failure-reason" id="fail-reason">失敗理由</div>
        </div>
      </div>
      <div class="pv-queue-view">
        <div class="pv-job-row"><div class="pv-job-description"><div class="pv-job-head">
          <div class="pv-job-title" id="queue-title">ジョブ名</div>
        </div></div></div>
      </div>
      <button class="q-btn pv-card-size" id="size-btn"><span><span>大</span></span></button>
      <div class="pv-library"><div class="pv-library-card-info" id="lib-info"><div>1点</div></div></div>
    </body></html>'''
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 800})
            page.set_content(html)
            sizes = page.evaluate('''() => {
                const ids = ['bm-title', 'personal-title', 'reco-title', 'ud-row-name',
                             'fail-title', 'fail-reason', 'queue-title',
                             'ud-head-name', 'bm-author', 'personal-author', 'reco-author',
                             'size-btn', 'lib-info'];
                return Object.fromEntries(ids.map(id => [
                    id, parseFloat(getComputedStyle(document.getElementById(id)).fontSize)
                ]));
            }''')
            # Non-frozen primary body/title text must meet 13px minimum.
            for key in ('bm-title', 'personal-title', 'reco-title', 'ud-row-name',
                        'fail-title', 'fail-reason', 'queue-title'):
                assert sizes[key] >= 13.0, f'{key} font-size {sizes[key]} < 13px'
            # Table header remains auxiliary >= 11px.
            assert sizes['ud-head-name'] == 11.0
            # Frozen A8 exceptions in docs/UIUX_EXCEPTIONS_20261124.md must remain unchanged.
            assert sizes['size-btn'] == 9.0
            assert sizes['lib-info'] == 9.5
            assert sizes['bm-author'] == 10.0
            assert sizes['personal-author'] == 10.0
            assert sizes['reco-author'] == 9.5
        finally:
            browser.close()
