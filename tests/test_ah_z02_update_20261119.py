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


@pytest.mark.parametrize(('old_title', 'new_title', 'legacy', 'added_title'), [
    ('portrait(124)', 'portrait(124)(123)', False, None),
    ('portrait', 'portrait', True, None),
    ('portrait', 'portrait(123)', False, 'portrait(123)(124)'),
], ids=['id-in-title', 'no-id', 'verified-other-work'])
def test_auto_zip_does_not_reassign_old_title(tmp_path, old_title, new_title, legacy, added_title):
    with generated_author_exports(tmp_path, old_title) as q:
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
            (q.exports.root / (job.target_id + '.zip-pending.json')).unlink()

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
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title=new_title)
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())

        q.exports.scan()
        assert all(j.state == 'done' for j in settled(q.exports))
        with ZipFile(path) as z:
            expected = {
                **previous, 'revised(123)_1.png': q.reader.payload, 'revised(123)_2.png': q.reader.payload}
            if added_title:
                expected.update({f'{added_title}_{page}.png': q.reader.payload for page in (1, 2)})
            assert {name: z.read(name) for name in z.namelist()} == expected
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


def test_numeric_title_page_is_removed_and_restored_in_author_zip(tmp_path):
    with generated_author_exports(tmp_path, 'portrait (2025)') as q:
        q.exports.scan()
        assert settled(q.exports)[0].state == 'done'
        path = archive(q)
        with ZipFile(path) as z:
            first = z.read('portrait (2025)(123)_1.png')
            second = z.read('portrait (2025)(123)_2.png')
        source = ManagedDuplicateInspection(q.managed)
        try:
            result = source.execute(plan_for(source, '123:2'), operation_id='a'*32)
            assert not result.failed and q.managed.visible_source_pages('123') == (0,)
            q.exports.scan()
            jobs = settled(q.exports)
            assert len(jobs) == 2 and all(job.state == 'done' for job in jobs)
            with ZipFile(path) as z:
                assert z.namelist() == ['portrait (2025)(123)_1.png']
                assert z.read(z.namelist()[0]) == first and z.testzip() is None

            q.managed.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
            q.exports.scan()
            jobs = settled(q.exports)
            assert len(jobs) == 3 and all(job.state == 'done' for job in jobs)
            with ZipFile(path) as z:
                assert {name: z.read(name) for name in z.namelist()} == {
                    'portrait (2025)(123)_1.png': first, 'portrait (2025)(123)_2.png': second}
                assert z.testzip() is None
            for job in jobs:
                assert q.exports.run(job, Context()) == job.result
        finally:
            source.close()


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


def test_repeated_copy_isolation_and_restore_republish(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        original = settled(q.exports)[0]
        assert original.state == 'done'
        path = archive(q)
        with ZipFile(path) as z:
            pages = {name: z.read(name) for name in z.namelist()}
        source = ManagedDuplicateInspection(q.managed)
        try:
            for cycle in range(1, 4):
                result = source.execute(plan_for(source, '123:1'), operation_id=f'{cycle:032x}')
                assert not result.failed
                q.exports.scan()
                jobs = settled(q.exports)
                assert len(jobs) == 2 * cycle and all(j.state == 'done' for j in jobs)
                with ZipFile(path) as z:
                    assert z.namelist() == ['synthetic title(123)_2.png']
                q.managed.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
                q.exports.scan()
                jobs = settled(q.exports)
                with ZipFile(path) as z:
                    assert {name: z.read(name) for name in z.namelist()} == pages
                assert len(jobs) == 2 * cycle + 1 and all(j.state == 'done' for j in jobs)
                q.exports.scan()
                assert len(settled(q.exports)) == len(jobs)
        finally:
            source.close()


def test_auto_zip_does_not_reuse_completion_after_member_removal(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.exports.scan()
        assert settled(q.exports)[0].state == 'done'
        path = archive(q)
        with ZipFile(path) as z:
            pages = {name: z.read(name) for name in z.namelist()}
        q.exports.zip.remove_entry(path, 'synthetic title(123)_1.png', sha256(path.read_bytes()).hexdigest())
        with ZipFile(path) as z:
            assert {name: z.read(name) for name in z.namelist()} != pages
        q.exports.scan()
        jobs = settled(q.exports)
        with ZipFile(path) as z:
            assert {name: z.read(name) for name in z.namelist()} == pages
        assert len(jobs) == 2 and all(j.state == 'done' for j in jobs)


def test_author_zip_republishes_when_all_pages_of_one_work_are_isolated(tmp_path):
    with generated_exports(tmp_path, True, None) as q:
        q.managed.resolve_record = lambda wid: replace(record(), remote_id=wid, title='second')
        q.managed.run(SimpleNamespace(kind='pixiv-illust', target_id='124'), Context())
        q.exports.scan()
        assert settled(q.exports)[0].state == 'done'
        path = archive(q)
        with ZipFile(path) as z:
            pages = {name: z.read(name) for name in z.namelist()}
        source = ManagedDuplicateInspection(q.managed)
        try:
            result = source.execute(plan_for(source, '123:1', '123:2'), operation_id='b'*32)
            assert not result.failed and q.managed.visible_source_pages('123') == ()
            q.exports.scan()
            jobs = settled(q.exports)
            with ZipFile(path) as z:
                assert z.namelist() == ['second(124)_1.png', 'second(124)_2.png']
                assert z.testzip() is None
            assert len(jobs) == 2 and all(j.state == 'done' for j in jobs)

            q.managed.quarantine.restore(result.operation_id, expected_revision=result.state_revision)
            q.exports.scan()
            jobs = settled(q.exports)
            with ZipFile(path) as z:
                assert {name: z.read(name) for name in z.namelist()} == pages
            assert len(jobs) == 3 and all(j.state == 'done' for j in jobs)
        finally:
            source.close()
