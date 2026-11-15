"""Offline reproductions of the four accepted FAIL7 defects."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4
from zipfile import ZipFile
import pytest
from nicegui import Client, ui
from pixivvault_nicegui.ui.failed_records_view import FailedRecordsView
from pixivvault_nicegui.ui.recommend_view import RecommendView
from pixivvault_nicegui.recommendation_state import RecommendationController, EmptyRecommendationSource
from test_q07_novel_retry import create
from test_required_legacy_duplicates import setup, select
from test_u11_zip_contract import generated_exports, settled
from test_managed_downloads import Job, Context, record

@pytest.mark.asyncio
async def test_q17_failed_and_unprocessed_counts(tmp_path, monkeypatch):
    service, reader, db = create(tmp_path, count=3)
    client = Client(ui.page('/fail7-q17'))
    try:
        async def fail(*a, **kw): raise ValueError('synthetic metadata error')
        monkeypatch.setattr(service, 'lookup_legacy_failed_original', fail)
        with client:
            view = FailedRecordsView(service)
            await view._refresh(view.state.begin_refresh())
            assert not await view._retry_records(view.state.records)
            assert '対象 3 件' in view.retry_notice.text
            assert '再失敗 1 件' in view.retry_notice.text
            assert '未処理 2 件' in view.retry_notice.text
            view.close()
    finally:
        service.close(); client.delete()

@pytest.mark.asyncio
async def test_r01a_unconnected_not_loaded_success():
    client = Client(ui.page('/fail7-r01a'))
    try:
        with client:
            view = RecommendView(NS(recommendations=RecommendationController(EmptyRecommendationSource())))
            view.active = True
            await view.refresh()
            assert '未接続' in view.notice.text
            assert '読み込みました' not in view.notice.text
            view.close()
    finally: client.delete()

def test_x09_missing_copy_does_not_hide_healthy_items(tmp_path):
    source, legacy, owner, *_ = setup(tmp_path)
    try:
        source.execute(select(source, {'1', '2'}), operation_id=uuid4().hex)
        state = legacy.state()
        victim = next(iter(state['exclusions'].values()))
        # The generated copy is moved aside; no real data or evidence is deleted.
        path = legacy._path(victim['copy'])
        path.rename(path.with_name(path.name + '.held'))
        snapshot = source.quarantine_snapshot()
        assert snapshot.copy_count == 1 and snapshot.copy_bytes > 0
        assert snapshot.excluded_pages == 2
        assert snapshot.missing_count == 1
    finally: source.close(); owner.close()

def test_s14_second_author_zip_includes_new_work(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state == 'done' for j in settled(q.exports))
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='second title')
        q.managed.run(NS(kind='pixiv-illust', target_id='124'), Context())
        q.exports.scan(); jobs=settled(q.exports)
        assert len(jobs) == 2 and all(j.state == 'done' for j in jobs)
        with ZipFile(q.exports.zip.root/'tester(456).zip') as z:
            assert len(z.namelist()) == 4
            assert any('(124)' in n for n in z.namelist())
            assert z.testzip() is None

@pytest.mark.asyncio
async def test_q17_worker_failure_is_counted_without_calling_pending_failed(tmp_path):
    service, reader, _ = create(tmp_path, count=2)
    client = Client(ui.page('/fail7-q17-results'))
    try:
        with client:
            view = FailedRecordsView(service)
            view._retry_batch_summary = dict(total=3, failed=0, jobs=('one','two'), interrupted=True)
            view._retry_results = {'one':NS(state='done'),'two':NS(state='error')}
            view._update_retry_summary()
            assert '対象 3 件中、再失敗 1 件' in view.retry_notice.text
            assert '未処理 1 件' in view.retry_notice.text
            assert '保存完了 1 件' in view.retry_notice.text
            view._retry_results['two']=NS(state='queued')
            view._update_retry_summary()
            assert '再失敗 0 件' in view.retry_notice.text
            assert '保存結果未確定 1 件' in view.retry_notice.text
            view.close()
    finally: service.close();client.delete()

@pytest.mark.asyncio
async def test_r01a_known_empty_is_success_not_unconnected():
    from pixivvault_nicegui.recommendation_state import RecommendationSnapshot
    class Connected:
        def load(self):return RecommendationSnapshot(unavailable=())
    client = Client(ui.page('/fail7-r01a-empty'))
    try:
        with client:
            view = RecommendView(NS(recommendations=RecommendationController(Connected())))
            view.active=True;await view.refresh()
            assert view.notice.text == '保存済み結果を読み込みました。'
            view.close()
    finally:client.delete()

@pytest.mark.asyncio
async def test_x09_missing_summary_visible_in_product_dialog(tmp_path):
    from pixivvault_nicegui.ui.duplicate_view import DuplicateView
    source, legacy, owner, *_ = setup(tmp_path)
    client = Client(ui.page('/fail7-x09'))
    try:
        source.execute(select(source, {'1','2'}), operation_id=uuid4().hex)
        for item in legacy.state()['exclusions'].values():
            path=legacy._path(item['copy']);path.rename(path.with_name(path.name+'.held'))
        with client:
            view=DuplicateView(source);view.set_active(True)
            await view.open_trash()
            assert view._trash_state == 'ready'
            assert '0件' in view.trash_copy_count.text
            assert '2ページ' in view.trash_excluded.text
            assert '実体が見つからない隔離コピー: 2件' in view.trash_status.text
            view.close()
    finally:client.delete();source.close();owner.close()

def test_x09_changed_copy_is_reported_but_default_state_rejects_it(tmp_path):
    source, legacy, owner, *_ = setup(tmp_path)
    try:
        source.execute(select(source, {'1'}), operation_id=uuid4().hex)
        item=next(iter(legacy.state()['exclusions'].values()))
        legacy._path(item['copy']).write_bytes(b'corrupt synthetic copy')
        snapshot=source.quarantine_snapshot()
        assert snapshot.corrupt_count==1 and snapshot.copy_count==0
        assert snapshot.copy_bytes==0 and snapshot.missing_count==0
        assert snapshot.excluded_pages==1
        with pytest.raises(ValueError):legacy.state()
    finally:source.close();owner.close()

def add_second(q):
    q.managed.resolve_record=lambda wid:replace(record(),remote_id=wid,title='second title')
    q.managed.run(NS(kind='pixiv-illust',target_id='124'),Context())

@pytest.mark.parametrize('interruption',['before_replace','after_link'])
def test_s14_update_recovers_retained_stage(tmp_path,monkeypatch,interruption):
    import os
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        archive=q.exports.zip.root/'tester(456).zip';before=archive.read_bytes()
        add_second(q);replace_file=os.replace
        def fail(src,dst):
            if str(dst).endswith('.zip'):raise OSError('synthetic update interruption')
            return replace_file(src,dst)
        monkeypatch.setattr(os,'replace',fail)
        q.exports.scan();jobs=settled(q.exports)
        failed=next(j for j in jobs if j.state=='error')
        assert archive.read_bytes()==before
        monkeypatch.setattr(os,'replace',replace_file)
        if interruption=='after_link':
            import json
            note=json.loads((q.exports.root/(failed.target_id+'.zip-pending.json')).read_bytes())
            stage=q.exports.zip.incomplete/note['stage']
            os.link(stage,stage.with_name(stage.name+'.publish'))
            from pixivvault_nicegui.legacy_zip_export import LegacyZipExporter
            reopened=LegacyZipExporter(q.exports.zip.root,q.managed,preserve_incomplete=True)
            reopened.close()
        result=q.exports.run(failed,Context())
        assert result.endswith('.export.json')
        with ZipFile(archive) as z:assert len(z.namelist())==4 and z.testzip() is None

def test_s14_foreign_modified_zip_not_overwritten(tmp_path):
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        archive=q.exports.zip.root/'tester(456).zip'
        with ZipFile(archive,'a') as z:z.writestr('foreign.txt',b'synthetic external change')
        before=archive.read_bytes();add_second(q)
        q.exports.scan();assert any(j.state=='error' for j in settled(q.exports))
        assert archive.read_bytes()==before
