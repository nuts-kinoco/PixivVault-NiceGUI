from dataclasses import replace
from contextlib import contextmanager
from test_managed_downloads import create
from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.save_exports import SaveExports
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile
from PIL import Image
from hashlib import sha256
import json
import pytest
from test_u11_zip_contract import generated_exports, settled
from test_managed_downloads import Context, record
from test_managed_quarantine import plan_for
from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent


@contextmanager
def generated_author_exports(tmp_path, title=None):
    managed,reader=create(tmp_path)
    reader.get_artwork_update_revision=lambda wid,aid: dict(author_id=aid,page_count=2,illustration_type=0,update_date=reader.date)
    reader.date='2026-11-16'
    dto=record() if title is None else replace(record(),title=title)
    managed.resolve_record=lambda wid:dto
    initial=ManagedAuthorIntent(dto,'follow',reader.date,None,'new')
    relative=managed.run(SimpleNamespace(kind='pixiv-illust',target_id='123',job_id='d'*32),Context(),intent=initial)
    completed=SimpleNamespace(target_id='123',state='done',job_id='123',result=relative)
    service=SimpleNamespace(closing=False,managed_downloads=managed,
        preferences=SimpleNamespace(get_setting=lambda key,default=None:'1' if key=='zip_all_after_download' else default),
        static_jobs=SimpleNamespace(snapshot=lambda:(completed,)),_managed_job_records={'123':dto},bookmark_download_candidates=lambda:())
    media=AuthorMediaService(service); service.author_media=media; media.enable_zip_settings()
    exports=SaveExports(service,tmp_path/'exports')
    try: yield SimpleNamespace(managed=managed,reader=reader,exports=exports,completed=completed)
    finally: exports.close(); media.close(); managed.close()

def archive(q): return q.exports.zip.root / 'tester(456).zip'


@pytest.mark.parametrize('legacy', [False, True], ids=['id-in-title', 'no-id'])
def test_auto_zip_does_not_reassign_old_title(tmp_path, legacy):
    with generated_author_exports(tmp_path, 'portrait' if legacy else 'portrait(124)') as q:
        q.exports.scan()
        job = settled(q.exports)[0]
        assert job.state == 'done'
        path = archive(q)
        with ZipFile(path) as z:
            previous = {name: z.read(name) for name in z.namelist()}
        if legacy:
            previous = {name.replace('(123)', ''): data for name, data in previous.items()}
            with ZipFile(path, 'w') as z:
                for name, data in previous.items():
                    z.writestr(name, data)
            manifest = q.exports.root / job.result
            value = json.loads(manifest.read_bytes())
            value['sha256'] = sha256(path.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(value), encoding='utf-8')

        q.reader.date = '2026-11-19'
        stream = BytesIO()
        Image.new('RGB', (9, 7), (250, 12, 18)).save(stream, format='PNG')
        q.reader.payload = stream.getvalue()
        current = q.managed._generations.current('123')
        revised = replace(record(), title='revised')
        q.managed.resolve_record = lambda wid: revised
        intent = ManagedAuthorIntent(revised, 'follow', q.reader.date,
                                     current['sha256'], 'updated', current['relative_receipt'])
        q.completed.result = q.managed.run(
            SimpleNamespace(kind='pixiv-illust', target_id='123', job_id='e'*32), Context(), intent=intent)
        title = 'portrait' if legacy else 'portrait(124)(123)'
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title=title)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())

        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        with ZipFile(path) as z:
            assert {name: z.read(name) for name in z.namelist()} == {
                **previous, 'revised(123)_1.png': q.reader.payload, 'revised(123)_2.png': q.reader.payload}
            assert z.testzip() is None


def test_auto_zip_refreshes_managed_generation(tmp_path):
    with generated_author_exports(tmp_path) as q:
        q.exports.scan(); assert all(j.state == 'done' for j in settled(q.exports))
        path=archive(q)
        with ZipFile(path) as z: old=z.read('synthetic title(123)_1.png')
        stream=BytesIO(); Image.new('RGB',(9,7),(250,12,18)).save(stream,format='PNG')
        q.reader.payload=stream.getvalue()
        q.reader.date='2026-11-19'
        dto=record(); current=q.managed._generations.current('123')
        intent=ManagedAuthorIntent(dto,'follow','2026-11-19',current['sha256'],'updated',current['relative_receipt'])
        q.completed.result=q.managed.run(SimpleNamespace(kind='pixiv-illust',target_id='123',job_id='e'*32),Context(),intent=intent)
        q.exports.scan(); assert all(j.state == 'done' for j in settled(q.exports))
        with ZipFile(path) as z:
            assert z.read('synthetic title(123)_1.png') == q.reader.payload != old
            assert z.testzip() is None


def test_auto_zip_applies_real_partial_quarantine(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan(); assert all(j.state == 'done' for j in settled(q.exports))
        source=ManagedDuplicateInspection(q.managed)
        try:
            result=source.execute(plan_for(source,'123:1'),operation_id='a'*32)
            assert not result.failed and q.managed.visible_source_pages('123') == (1,)
            q.exports.scan(); assert all(j.state == 'done' for j in settled(q.exports))
            with ZipFile(archive(q)) as z:
                assert z.namelist() == ['synthetic title(123)_2.png']
                assert z.testzip() is None
        finally: source.close()


def test_restore_republishes_complete_author_zip(tmp_path):
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
        source=ManagedDuplicateInspection(q.managed)
        try:
            result=source.execute(plan_for(source,'123:1'),operation_id='b'*32)
            q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
            with ZipFile(archive(q)) as z: assert len(z.namelist())==1
            q.managed.quarantine.restore(result.operation_id,expected_revision=result.state_revision)
            assert q.managed.visible_source_pages('123')==(0,1)
            q.exports.scan(); assert all(j.state=='done' for j in settled(q.exports))
            with ZipFile(archive(q)) as z: assert len(z.namelist())==2
            jobs=settled(q.exports); q.exports.scan(); assert len(settled(q.exports))==len(jobs)
        finally:source.close()


def test_move_restore_republishes_complete_author_zip(tmp_path):
    with generated_exports(tmp_path,True,None) as q:
        q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
        source=ManagedDuplicateInspection(q.managed)
        try:
            result=source.execute(plan_for(source,'123:1'),operation_id='c'*32,mode='move',confirmed_move=True)
            q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
            with ZipFile(archive(q)) as z:assert len(z.namelist())==1
            source.restore_move(result.operation_id,expected_revision=result.state_revision)
            assert q.managed.visible_source_pages('123')==(0,1)
            q.exports.scan();assert all(j.state=='done' for j in settled(q.exports))
            with ZipFile(archive(q)) as z:assert len(z.namelist())==2
        finally:source.close()
