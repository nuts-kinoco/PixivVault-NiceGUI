"""Wire synthetic data into the existing visual fixtures and real product views."""
import asyncio
from collections.abc import Callable
from contextlib import ExitStack
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

from tools import p5_design_evidence as p5, ui_tone_evidence as tone


class AuditFixture(p5.P5Fixture):
    def _response(self, request: Callable, *args, **kwargs):
        state = os.environ['PV_UIUX_STATE']
        if state == 'error':
            raise OSError('synthetic response failure')
        if state == 'loading':
            time.sleep(4)
        return request(*args, **kwargs)

    def get_ranking(self, *args, **kwargs):
        return self._response(super().get_ranking, *args, **kwargs)

    def personal_search(self, *args, **kwargs):
        return self._response(super().personal_search, *args, **kwargs)


def fixture_effects(instance):
    state = os.environ['PV_UIUX_STATE']
    instance.extension_bridge = SimpleNamespace(
        response_snapshot=lambda: (
            {} if state == 'empty' else {200: 3, 400: 1, 503: int(state == 'error')},
            {} if state == 'empty' else {(200, 'accepted'): 3, (400, 'invalid_command'): 1},
        ),
        close=lambda: None,
    )
    instance.desktop = SimpleNamespace(protocol=object(), set_uri_registration=lambda *_a, **_kw: None)
    instance.legacy_metadata_export_available = lambda: True

    def export_response():
        if state == 'error':
            raise OSError('synthetic export failure')
        return SimpleNamespace(path='合成エクスポート.zip', works=2)

    instance.export_legacy_metadata_only = export_response
    if state in {'error', 'loading'}:
        def deferred_async(original):
            async def response(*args, **kwargs):
                if state == 'error':
                    raise OSError('synthetic read failure')
                await asyncio.sleep(4)
                return await original(*args, **kwargs)
            return response

        for method in ('recent_legacy_backup_authors', 'recent_viewer_backups'):
            setattr(instance, method, deferred_async(getattr(instance, method)))
        if state == 'error' and instance.discovery is not None:
            instance.discovery.view_history = deferred_async(instance.discovery.view_history)
    if instance.external_vault:
        status = instance.external_vault.status

        def pending_vault():
            return dict(status(), oauth_review_required=True)

        instance.external_vault.status = pending_vault


def serve_port(port, nonce):
    from pixivvault_nicegui import services
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    from pixivvault_nicegui.ui.learning_reset import LearningResetView
    from pixivvault_nicegui.ui.preferences_view import PreferencesView
    from pixivvault_nicegui.ui.recommend_view import RecommendView

    get_services = services.get_services
    scan = DuplicateView.scan
    open_preferences = PreferencesView.open
    initialize_recommend = RecommendView.__init__
    initialize_reset = LearningResetView.__init__

    def with_audit_services(*args, **kwargs):
        instance = get_services(*args, **kwargs)
        if not getattr(instance, '_pv_uiux_fixture', False):
            fixture_effects(instance)
            instance._pv_uiux_fixture = True
        return instance

    async def with_scan_fixture(view):
        state = os.environ['PV_UIUX_STATE']
        if state not in {'error', 'loading'}:
            return await scan(view)
        # Defer or fail at the I/O boundary inside the product's scan handler.
        original = view.source.scan

        def deferred():
            if state == 'error':
                raise OSError('synthetic duplicate scan failure')
            time.sleep(4)
            return original()

        with patch.object(view.source, 'scan', deferred):
            return await scan(view)

    def with_failed_preferences(view):
        from nicegui import ui

        expansion = ui.expansion

        def open_expansion(*args, **kwargs):
            return expansion(*args, **dict(kwargs, value=True))

        with ExitStack() as fixture_scope:
            if os.environ.get('PV_UIUX_OPEN_SETTINGS_GROUPS') == '1':
                fixture_scope.enter_context(patch.object(ui, 'expansion', open_expansion))
            if (os.environ['PV_UIUX_STATE'] == 'error' and
                    ui.context.client.request.query_params.get('_audit_screen') == 'settings'):
                fixture_scope.enter_context(patch.object(
                    view.store, 'snapshot', side_effect=OSError('synthetic settings read failure')))
            return open_preferences(view)

    def with_recommend_fixture(view, *args, **kwargs):
        initialize_recommend(view, *args, **kwargs)
        if view.controller is None or os.environ['PV_UIUX_STATE'] not in {'error', 'loading'}:
            return
        refresh = view.controller.refresh

        def read():
            if os.environ['PV_UIUX_STATE'] == 'error':
                raise OSError('synthetic recommendation read failure')
            time.sleep(4)
            return refresh()

        view.controller.refresh = read

    def with_reset_fixture(view, parent, storage):
        class ResetCount:
            def __getattr__(self, name):
                return getattr(storage, name)

            async def judged_count(self):
                return max(1, await storage.judged_count())

        fake_count = os.environ.get('PV_UIUX_RETRY_JUDGED') == '1'
        initialize_reset(view, parent, ResetCount() if fake_count else storage)

    with (patch.object(p5, 'P5Fixture', AuditFixture),
          patch.object(services, 'get_services', with_audit_services),
          patch.object(DuplicateView, 'scan', with_scan_fixture),
          patch.object(PreferencesView, 'open', with_failed_preferences),
          patch.object(LearningResetView, '__init__', with_reset_fixture),
          patch.object(RecommendView, '__init__', with_recommend_fixture)):
        p5.serve_port(port, nonce)


def serve_original(port):
    with patch.object(p5, 'P5Fixture', AuditFixture):
        p5.serve_original(port)


def ready(page, testid):
    locator = page.get_by_test_id(testid).filter(visible=True).first
    locator.wait_for(timeout=5500)
    return locator


def click(page, testid):
    locator = ready(page, testid)
    locator.click(timeout=4000)
    return locator


def wait_label(locator, pattern, timeout=6500):
    locator.filter(has_text=pattern).wait_for(timeout=timeout)


def settings(page):
    trigger = click(page, 'preferences-open')
    card = page.locator('.pv-preferences-card').filter(visible=True).first
    card.wait_for(timeout=5000)
    collapsed = card.locator('.q-expansion-item--collapsed > .q-expansion-item__container > .q-item')
    for header in collapsed.filter(visible=True).element_handles():
        # Open the section without relying on its position relative to the
        # sticky footer. The expansion icon can own the event in Quasar.
        header.evaluate("""e => {
            if (e.getAttribute('aria-disabled') === 'true' || e.classList.contains('disabled')) return;
            (e.querySelector('[aria-expanded=false]') || e.querySelector('.q-expansion-item__toggle-icon') || e).click();
        }""")
        page.wait_for_timeout(100)
    card.evaluate('e => e.scrollTop = 0')
    return trigger


def open_port(page, cell, specs):
    """Return a product-derived state assertion and the dialog opener, if any."""
    import re

    key, state = cell.screen, cell.state
    opener = None
    if key in tone.STARTUP_CASES or key in {'stop-history-guide', 'stop-migration-dialog'}:
        tone.navigate_extra(page, key, 'port')
        return True, None
    if key in {'request-wait', 'request-limit'}:
        tone.navigate_extra(page, key, 'port')
        page.wait_for_function('window.did_handshake === true', timeout=10000)
        p5.smoke.set_theme(page, cell.theme)
        return True, None
    if key in {'settings', 'extension', 'extension-confirm', 'toast-success', 'toast-error',
               'premium-recovery', 'startup-and-storage'}:
        opener = settings(page)
        if key in {'extension', 'extension-confirm'}:
            if not page.get_by_test_id('extension-status').filter(visible=True).count():
                page.locator('.pv-preferences-card').get_by_text('ブラウザ拡張機能', exact=True).evaluate(
                    "e => e.closest('.q-item').click()", timeout=4500)
            ready(page, 'extension-status').scroll_into_view_if_needed()
            if key == 'extension-confirm':
                opener = click(page, 'preferences-uri')
                page.get_by_text('ブラウザ拡張機能の自動連携', exact=True).wait_for(timeout=5500)
        if key in {'toast-success', 'toast-error'}:
            click(page, 'preferences-export-metadata')
            page.locator('.q-notification').first.wait_for(timeout=5500)
        if key == 'startup-and-storage':
            ready(page, 'native-profile-pick-folder').scroll_into_view_if_needed()
        if key == 'premium-recovery':
            ready(page, 'premium-recovery').scroll_into_view_if_needed()
        if key == 'settings' and state == 'error':
            wait_label(ready(page, 'preferences-status'), re.compile('設定を読み込めません'))
        return True, opener
    if key in {'borrow-on', 'communication-result'}:
        opener = click(page, 'premium-borrow-toggle' if key == 'borrow-on' else 'premium-recovery-shell')
        ready(page, 'premium-password-confirm' if key == 'borrow-on' else 'premium-recovery-close')
        return True, opener
    if key in {'view-history', 'metadata-ledger', 'shared-log', 'shared-log-collapsed'}:
        if key == 'view-history':
            opener = page.get_by_test_id('view-history-open')
        tone.navigate_extra(page, key, 'port')
        if key == 'view-history' and state == 'error':
            wait_label(ready(page, 'view-history-status'), re.compile('読み込めません'))
        if key in {'view-history', 'shared-log'} and state in {'normal', 'empty', 'long'}:
            if key == 'shared-log':
                text = ready(page, 'shared-log').inner_text()
                return ('P5 合成ログ' in text) == (state != 'empty') and state != 'long', opener
            return state != 'long', opener
        return True, opener
    if key == 'view-history-delete':
        opener = page.get_by_test_id('view-history-open')
        tone.navigate_extra(page, 'view-history', 'port')
        click(page, 'view-history-danger')
        ready(page, 'view-history-confirm')
        return True, opener

    parents = {
        'ranking-date': 'ranking', 'personal-filters': 'personal', 'dedup-trash': 'dedup',
        'learning-settings': 'learning', 'learning-review': 'learning', 'learning-reset': 'learning',
        'download-details': 'download', 'viewer': 'ranking',
        'queue-delete-confirm': 'queue', 'failed-delete-confirm': 'failed',
    }
    screen = parents.get(key, key)
    # At 200%, the candidate can activate a tab while giving its content
    # panel zero height. Keep the real viewport as A11 evidence.
    if cell.zoom == 200 and page.locator('.pv-content').evaluate(
            'e => e.getBoundingClientRect().height <= 1'):
        opener = specs[screen].get('open', f'tab-{screen}') if screen != 'bookmarks' else 'tab-library'
        if ready(page, opener).is_enabled():
            click(page, opener)
        return False, None
    p5.navigate_port(page, screen, specs, tone_mode=True)
    actions = {
        'ranking-date': ('ranking-date-calendar', 'ranking-date-dialog'),
        'personal-filters': ('personal-filter-toggle', 'personal-filter-panel'),
        'dedup-trash': ('duplicate-trash', 'duplicate-trash-status'),
        'learning-settings': ('discovery-settings-open', 'discovery-settings-panel'),
        'learning-review': ('learning-review-open', 'learning-review-dialog'),
        'learning-reset': ('learning-reset-open', 'learning-reset-confirm'),
        'download-details': ('unified-details-open', 'unified-details-dialog'),
        'failed-delete-confirm': ('failed-row-delete', 'failed-remove-dialog'),
    }
    if key in actions:
        trigger, target = actions[key]
        opener = click(page, trigger)
        ready(page, target)
        return True, opener
    if key == 'queue-delete-confirm':
        click(page, 'queue-select-mode')
        click(page, 'queue-select-all')
        opener = click(page, 'queue-bulk-delete')
        ready(page, 'queue-bulk-delete-confirm')
        return True, opener
    if screen == 'recommend' and state in {'error', 'loading'}:
        target = 'recommend-notice' if state == 'error' else 'recommend-busy-status'
        wait_label(ready(page, target), re.compile('確認できません|読み込んでいます'))
        return True, None
    if screen == 'history' and state in {'error', 'loading'}:
        if state == 'error':
            wait_label(ready(page, 'backup-status'), re.compile('確認できません'))
        else:
            page.locator('[data-testid=backup-refresh]:disabled,[data-testid=backup-refresh][aria-disabled=true]').wait_for(timeout=3000)
        return True, None
    if screen in {'ranking', 'personal', 'dedup'}:
        focus = dict(ranking=('ranking-fetch', 'ranking-notice'),
                     personal=('personal-fetch', 'personal-notice'),
                     dedup=('duplicate-scan', 'duplicate-status'))
        trigger, target = focus[screen]
        if screen != 'dedup' or state != 'empty':
            click(page, trigger)
            status = ready(page, target)
            if state in {'error', 'loading'}:
                wait_label(status, re.compile('失敗|できません|利用できません' if state == 'error' else
                                              '取得中|スキャン中|確認中|スキャンしています'))
                return True, None
            if screen == 'dedup':
                wait_label(status, re.compile('枚を確認'))
                click(page, 'duplicate-mode-near')
            else:
                wait_label(status, re.compile(r'\d+\s*件|見つかりません|ありません|件表示'))
    if key == 'viewer':
        image = page.locator('[data-testid=ranking-view] .pv-card img').filter(visible=True).first
        image.click(timeout=4500)
        ready(page, 'viewer-zoom-viewport')
        return True, None
    if state == 'long':
        if screen == 'library':
            click(page, 'library-author-open')
            page.get_by_text(p5.LONG[:20], exact=False).filter(visible=True).first.wait_for(timeout=4500)
        # Match fixture text even when CSS ellipsizes it. The screenshot and A15
        # show whether the full text can actually be obtained by the user.
        return bool(page.get_by_text(p5.LONG[:20], exact=False).filter(visible=True).count()), opener
    return True, opener


def open_original(page, cell, profile):
    key = cell.screen
    if key == 'personal' and cell.state not in {'loading', 'error'}:
        if tone.navigate_extra(page, 'personal-results', 'original'):
            return True
    if key == 'view-history-delete':
        tone.navigate_extra(page, 'view-history', 'original')
        return p5.original.text_control(page, '全件削除').is_visible()
    if tone.navigate_extra(page, key, 'original'):
        return cell.state != 'error'
    target = cell.original
    p5.original.open_screen(page, target)
    if key == 'dedup' and profile != 'empty':
        p5.original.click_label(page, 'スキャンして更新')
        page.wait_for_function(
            f"() => ({p5.original.OUTLINE_JS})().some(e => e.text.startsWith('完了:'))", timeout=8000)
    if key == 'ranking':
        from playwright.sync_api import TimeoutError

        try:
            p5.original.click_label(page, '取得・更新')
        except TimeoutError:
            pass  # The Flet tab can begin the query on activation.
    if key == 'shared-log':
        page.mouse.wheel(0, 600)
    if key == 'extension-confirm':
        p5.original.click_label(page, 'ブラウザ拡張機能との自動連携')
        p5.original.text_control(page, 'ブラウザ拡張機能の自動連携を有効にしますか？').wait_for(timeout=5000)
        return True
    if key in {'startup-and-storage', 'extension', 'learning-reset'}:
        label = {'startup-and-storage': '保存フォルダーの設定',
                 'extension': 'ブラウザ拡張機能との自動連携',
                 'learning-reset': '学習状況をリセット'}[key]
        if key == 'learning-reset':
            p5.original.click_label(page, label)
            return page.locator('flt-semantics[role=alertdialog]').count() > 1
        return p5.original.text_control(page, label).is_visible() and cell.state == 'normal'
    # Without a matching fake boundary, the archived UI is retained as a visual
    # reference and the requested transient condition stays open in A16.
    return cell.state not in {'error', 'loading'} and key != 'shared-log-collapsed'
