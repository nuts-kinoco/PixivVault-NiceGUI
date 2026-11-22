from collections import namedtuple
from types import SimpleNamespace

import pytest
from nicegui import Client, ui

from pixivvault_nicegui.ui.duplicate_view import DuplicateView
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui.ui.job_views import QueueView
from pixivvault_nicegui.ui.managed_download_view import ManagedDownloadView
from pixivvault_nicegui.ui.metadata_view import MetadataView
from pixivvault_nicegui.ui.scheduler_view import SchedulerView
from pixivvault_nicegui.ui.theme import CSS as THEME_CSS
from pixivvault_nicegui.ui.unified_download_view import CSS as UNIFIED_CSS, UnifiedDownloadView

_FakeFailedRecord = namedtuple(
    '_FakeFailedRecord',
    ('work_id', 'user_id', 'work_type', 'failed_at', 'retry_count', 'title', 'error_reason'),
)


@pytest.mark.asyncio
async def test_download_author_keeps_full_name_on_the_clipped_label():
    name = '合成「作者名」' * 40
    view = object.__new__(UnifiedDownloadView)
    view.work_counts = {}
    view._rows = {}
    with Client(ui.page('/test-ui-fix3-author')) as client:
        try:
            view._row({'user_id': '17', 'name': name}, False)
            row = view._rows['17']['row']
            star = view._rows['17']['star']
            label = next(element for element in row.descendants() if 'pv-ud-c-name' in element.classes)
            assert label.text == label.props.get('title') == name
            assert row.props['aria-checked'] == 'false'
            assert star.props.get('aria-label') == '☆自動監視の切り替え'
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_download_detail_toggle_is_reachable_and_announces_its_state():
    view = object.__new__(UnifiedDownloadView)
    with Client(ui.page('/test-ui-fix3-band')) as client:
        try:
            view._build_band()
            assert view.hint_row.props.get('tabindex') == '0'
            assert view.hint_row.props.get('aria-expanded') == 'false'
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_download_styles_cover_disabled_contrast_hit_targets_focus_rings_and_search_label():
    assert '@layer overrides' in THEME_CSS
    assert '.pv-ud-run:disabled' in THEME_CSS
    assert '.pv-ud-ibtn' in UNIFIED_CSS and 'flex:0 0 28px' in UNIFIED_CSS
    assert '.pv-focus-ring' in THEME_CSS
    assert '.pv-ud-search .q-field__label' in THEME_CSS and 'line-height:22px' in THEME_CSS


@pytest.mark.asyncio
async def test_download_details_dialog_has_accessible_focus_and_title():
    view = object.__new__(UnifiedDownloadView)
    with Client(ui.page('/test-ui-fix3-details-dialog')) as client:
        try:
            view.details_button = ui.element('button').props('type=button data-testid=unified-details-open')
            view._details_opener = view.details_button
            view._build_details_dialog()
            assert view.dialog.props.get('aria-labelledby')
            assert view.dialog.props.get('transition-duration') == '150'
            close_btn = next(
                el for el in view.dialog.descendants()
                if el.props.get('data-testid') == 'unified-details-close'
            )
            assert close_btn.props.get('autofocus') is True
        finally:
            client.delete()


@pytest.mark.asyncio
async def test_managed_download_mode_and_star_controls_avoid_low_contrast_grey7():
    services = SimpleNamespace(
        download_candidates=lambda: (),
        managed_downloads=None,
        jobs=None,
    )
    with Client(ui.page('/test-ui-fix3-managed')) as client:
        view = ManagedDownloadView(services)
        try:
            assert 'color' not in view.bookmark_mode_button.props
            assert 'color' not in view.author_mode_button.props
            assert 'color' not in view.follow_star_filter.props
            assert 'pv-managed-mode' in view.bookmark_mode_button.classes
            assert 'pv-managed-mode' in view.author_mode_button.classes
            assert 'pv-managed-star-filter' in view.follow_star_filter.classes
        finally:
            view.close()
            client.delete()


@pytest.mark.asyncio
async def test_queue_and_failed_long_titles_expose_full_text_and_accessible_styles():
    long_title = 'とても長い作品タイトル' * 30
    job = SimpleNamespace(
        job_id='j1', kind='demo', target_id='12345', title=long_title,
        state='queued', done=0, total=1, revision=1, control_revision=1, error_code=None,
    )
    jobs = SimpleNamespace(snapshot=lambda: (job,), faulted=False)
    services = SimpleNamespace(artworks=(), jobs=jobs, queue_log=None)
    with Client(ui.page('/test-ui-fix3-queue')) as client:
        qview = QueueView(services)
        try:
            row = qview.rows['j1']
            assert row['title'].props.get('title') == long_title
            assert row['target'].props.get('title') == '12345'
        finally:
            qview.close()
            client.delete()

    record = _FakeFailedRecord(
        work_id='999', user_id='10', work_type='illust', failed_at='2026-11-22',
        retry_count=1, title=long_title, error_reason='network error',
    )
    fservices = SimpleNamespace(
        data_mode='synthetic',
        failed_records_available=True,
        failed_record_removal_available=False,
        legacy_failed_retry_available=False,
        jobs=None,
    )
    with Client(ui.page('/test-ui-fix3-failed')) as client:
        fview = FailedRecordsView(fservices)
        try:
            fview.state.records = (record,)
            fview.render()
            title_label = next(
                el for el in fview.rows.descendants()
                if 'pv-failure-title' in el.classes
            )
            assert title_label.props.get('title') == f'{long_title}（ID: 999）'
        finally:
            fview.close()
            client.delete()


@pytest.mark.asyncio
async def test_duplicate_trash_dialog_has_fast_escape_transition_and_metadata_scheduler_attributes():
    source = SimpleNamespace(undecidable_available=False)
    with Client(ui.page('/test-ui-fix3-dedup')) as client:
        dview = DuplicateView(source)
        try:
            assert dview.trash_dialog.props.get('transition-duration') == '150'
            assert 'pv-trash-count-input' in dview.trash_confirm_count.classes
        finally:
            dview.close()
            client.delete()


@pytest.mark.asyncio
async def test_management_action_labels_describe_the_operation():
    services = SimpleNamespace(managed_scheduler=None)
    with Client(ui.page('/test-ui-fix3-management')) as client:
        scheduler = SchedulerView(services)
        metadata = MetadataView(services)
        try:
            assert scheduler.run_button.text == '実行（今すぐチェック）'
            assert metadata.run_all_button.text == '取得を実行'
            assert metadata.retry_button.text == '再試行（取得に失敗した作品）'
            assert 'pv-scheduler-authors' in scheduler.authors.classes
            assert scheduler.authors.props.get('title') == 'お気に入り（☆）作者ID（カンマ区切り）'
            assert 'pv-metadata-work-id' in metadata.work_id.classes
        finally:
            scheduler.close()
            metadata.close()
            client.delete()
