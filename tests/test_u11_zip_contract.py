"""Original U11 global-OR-author ZIP policy with generated managed images.

Original core.py:1769-1789 makes author auto_archive override the author's
following flag, not zip_all_after_download. No explicit zip_policy is involved.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from required_network_guard import no_network
from test_managed_downloads import create, Context, Job, record
from pixivvault_nicegui.author_media import AuthorMediaService
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.job_service import UNFINISHED
from pixivvault_nicegui.save_exports import SaveExports


@contextmanager
def generated_exports(tmp_path, global_on, author_setting):
    managed, reader = create(tmp_path)
    media = exports = None
    try:
        relative = managed.run(Job(), Context())
        completed = SimpleNamespace(target_id='123', state='done', job_id='123', result=relative)
        jobs = [completed]
        settings = {'zip_all_after_download': '1' if global_on else '0'}
        service = SimpleNamespace(
            closing=False, managed_downloads=managed,
            preferences=SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default)),
            static_jobs=SimpleNamespace(snapshot=lambda: tuple(jobs)),
            _managed_job_records={'123': record()}, bookmark_download_candidates=lambda: (),
        )
        media = AuthorMediaService(service)
        service.author_media = media
        media.enable_zip_settings()
        if author_setting is not None:
            media.configure_author_zip('456', author_setting, expected_revision=0)
        exports = SaveExports(service, tmp_path / 'exports')
        yield SimpleNamespace(exports=exports, managed=managed, reader=reader,
                              media=media, settings=settings, jobs=jobs)
    finally:
        if exports is not None: exports.close()
        if media is not None: media.close()
        managed.close()


def settled(exports):
    with exports.jobs._condition:
        assert exports.jobs._condition.wait_for(
            lambda: all(job.state not in UNFINISHED for job in exports.jobs.snapshot()), 10)
    return exports.jobs.snapshot()


def verify_archive(exports, managed, job):
    assert job.kind == 'auto-zip' and job.state == 'done'
    manifest = native_io_path(exports.root / job.result)
    receipt = json.loads(manifest.read_bytes())
    archive = native_io_path(exports.zip.root / receipt['archive'])
    assert archive.name == 'tester(456).zip'
    assert sha256(archive.read_bytes()).hexdigest() == receipt['sha256']
    rows = managed.saved_receipts()
    assert receipt['source'] == {'author_id': '456', 'proofs': [['123', rows[0][2]]]}
    with ZipFile(archive) as zf:
        assert zf.namelist() == ['synthetic title(123)_1.png', 'synthetic title(123)_2.png']
        assert zf.testzip() is None
        for index, name in enumerate(zf.namelist()):
            assert zf.read(name) == managed.read_saved_page(rows[0][0], index, rows[0][2])
    return archive, receipt


@pytest.mark.parametrize('global_on,author_setting,expected', [
    (False, None, False), (False, False, False), (False, True, True),
    (True, None, True), (True, False, True), (True, True, True),
])
def test_original_zip_policy_generates_expected_archive(tmp_path, global_on, author_setting, expected):
    with generated_exports(tmp_path, global_on, author_setting) as fixture:
        ex, managed, reader = fixture.exports, fixture.managed, fixture.reader
        saved = managed.saved_receipts()
        reads = (reader.identity, reader.urls, reader.count)
        ex.scan()
        jobs = settled(ex)
        assert len(jobs) == int(expected)
        assert not ex.scan_errors
        assert fixture.media.author_zip_setting('456') is author_setting
        if expected:
            archive, receipt = verify_archive(ex, managed, jobs[0])
            original_bytes = archive.read_bytes()
            original_job = jobs[0].job_id
            ex.scan()
            assert [job.job_id for job in settled(ex)] == [original_job]
            assert ex.jobs.operation_count == 1
            ex.close()
            reopened = SaveExports(ex.s, ex.root)
            try:
                reopened.scan()
                assert [job.job_id for job in settled(reopened)] == [original_job]
                assert reopened.jobs.operation_count == 1 and not reopened.scan_errors
                assert archive.read_bytes() == original_bytes
                assert json.loads(native_io_path(reopened.root / jobs[0].result).read_bytes()) == receipt
            finally:
                reopened.close()
        else:
            assert not list(native_io_path(ex.zip.root).glob('*.zip'))
            assert not list(native_io_path(ex.root).glob('*.export.json'))
        assert managed.saved_receipts() == saved
        assert (reader.identity, reader.urls, reader.count) == reads


@pytest.mark.parametrize('state', sorted(UNFINISHED))
def test_zip_waits_for_existing_static_download_batch(tmp_path, state):
    with generated_exports(tmp_path, True, False) as fixture:
        pending = SimpleNamespace(target_id='124', state=state, job_id='124')
        fixture.jobs.append(pending)
        fixture.exports.scan()
        assert not fixture.exports.jobs.snapshot()
        assert not list(native_io_path(fixture.exports.zip.root).glob('*.zip'))
        fixture.jobs.remove(pending)
        fixture.exports.scan()
        jobs = settled(fixture.exports)
        assert len(jobs) == 1
        verify_archive(fixture.exports, fixture.managed, jobs[0])


def test_accepted_zip_survives_later_setting_change_without_duplicate(tmp_path, monkeypatch):
    with generated_exports(tmp_path, True, False) as fixture:
        ex = fixture.exports
        entered, release = Event(), Event()
        run = ex._run
        def blocked(job, context):
            entered.set()
            assert release.wait(10)
            return run(job, context)
        monkeypatch.setattr(ex, '_run', blocked)
        try:
            ex.scan()
            assert entered.wait(3)
            first = ex.jobs.snapshot()[0]
            intent = native_io_path(ex.root / (first.target_id + '.json'))
            accepted_bytes = intent.read_bytes()
            fixture.settings['zip_all_after_download'] = '0'
            ex.scan()
            assert ex.jobs.operation_count == 1 and intent.read_bytes() == accepted_bytes
            release.set()
            jobs = settled(ex)
            assert len(jobs) == 1 and jobs[0].job_id == first.job_id
            archive, _ = verify_archive(ex, fixture.managed, jobs[0])
            existing_bytes = archive.read_bytes()
            fixture.settings['zip_all_after_download'] = '1'
            ex.scan()
            assert ex.jobs.operation_count == 1
            assert [job.job_id for job in ex.jobs.snapshot()] == [first.job_id]
            assert archive.read_bytes() == existing_bytes and intent.read_bytes() == accepted_bytes
        finally:
            release.set()
