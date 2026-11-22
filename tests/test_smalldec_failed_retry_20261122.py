from types import SimpleNamespace

import pytest
from nicegui import Client, ui
from pixivvault_nicegui.failed_records_state import FailureRecord, FailedRecordsState, PAGE_SIZE
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView


class RetryConnection:
    data_mode = 'synthetic'
    failed_records_available = True
    legacy_failed_retry_available = True
    novel_jobs = None
    _supported_failed_original = staticmethod(ApplicationServices._supported_failed_original)
    supports_legacy_failed_retry = ApplicationServices.supports_legacy_failed_retry


def record(work, *, user='7', kind='illust', title='サンプル'):
    return FailureRecord(work, user, title, kind, 'Timeout', '', 0)


def row_guidance(view):
    return [item.text for item in view.rows.descendants()
            if item.props.get('data-testid') == 'failed-row-retry-exclusion']


@pytest.mark.asyncio
async def test_retry_counts_use_filtered_records_and_rows_explain_exclusions(monkeypatch):
    excluded = (
        record('invalid', title='対象外'),
        record('2', user='０', title='対象外'),
        record('3', kind='ugoira', title='対象外'),
        record('4', kind='novel', title='対象外'),
    )
    eligible = tuple(record(str(index)) for index in range(10, 10 + PAGE_SIZE))
    records = excluded + eligible
    client = Client(ui.page('/smalldec-failed-retry'))
    try:
        with client:
            view = FailedRecordsView(RetryConnection())
            try:
                view.state = FailedRecordsState(records)
                view.render()
                assert view.retry_all.enabled
                assert view.retry_all.text == '全 12 件中 8 件を再試行（4 件は対象外）'
                assert row_guidance(view) == [
                    '再試行の対象外: 作品 ID が有効な数字ではありません',
                    '再試行の対象外: ユーザー ID が有効な数字ではありません',
                    '再試行の対象外: 対応していない作品種別です',
                    '再試行の対象外: 小説の保存先が設定されていません',
                ]
                captured = []
                monkeypatch.setattr(view, '_retry_records', lambda rows: captured.extend(rows))
                monkeypatch.setattr('pixivvault_nicegui.ui.failed_records_view.background_tasks.create',
                                    lambda _: None)
                view.request_retry()
                assert captured == list(eligible)

                view.search('対象外')
                assert not view.retry_all.enabled
                assert view.retry_all.text == '全 4 件中 0 件を再試行（4 件は対象外）'
                assert '再試行できる対応作品がありません' in view.retry_exclusions.text
                assert all(reason.split(': ', 1)[1] in view.retry_exclusions.text
                           for reason in row_guidance(view))
                view.request_retry()
                assert captured == list(eligible)

                view.search('サンプル')
                assert view.retry_all.text == '全 8 件中 8 件を再試行（0 件は対象外）'
                assert not view.retry_exclusions.visible
                assert not row_guidance(view)
            finally:
                view.close()
    finally:
        client.delete()


@pytest.mark.parametrize(('work', 'user', 'reason'), [
    (None, '7', '作品 ID が有効な数字ではありません'),
    ('2', 0, 'ユーザー ID が有効な数字ではありません'),
], ids=['work-type', 'user-type'])
def test_exclusion_explains_non_text_identity(work, user, reason):
    view = object.__new__(FailedRecordsView)
    view.services = RetryConnection()
    item = SimpleNamespace(work_type='illust', work_id=work, user_id=user)
    assert view._retry_exclusion_reason(item) == reason


def test_other_exclusion_does_not_claim_novel_path_missing():
    view = object.__new__(FailedRecordsView)
    view.services = SimpleNamespace(novel_jobs=object())
    assert view._retry_exclusion_reason(record('2', kind='novel')) == 'この作品を安全に再試行できません'


@pytest.mark.asyncio
async def test_empty_filter_shows_disabled_retry_message():
    client = Client(ui.page('/review-empty-retry'))
    try:
        with client:
            view = FailedRecordsView(RetryConnection())
            try:
                view.search('一致しない試験文字列')
                assert not view.retry_all.enabled
                assert view.retry_all.text == '再試行できる対応作品がありません'
            finally:
                view.close()
    finally:
        client.delete()
