"""Old accepted output intents and individual-page copies cannot bypass exclusion."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from pixivvault_nicegui.duplicate_inspection import ManagedDuplicateInspection
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, ManagedPageQuarantined
from pixivvault_nicegui.save_exports import SaveExports, digest, packet
from pixivvault_nicegui.legacy_zip_export import LegacyZipExportError, legacy_filename
from pixivvault_nicegui import save_exports as export_module, viewer_exports
from pixivvault_nicegui import legacy_zip_export as zip_module
from test_managed_quarantine import Client, Context, owned, plan_for, record


def exports_for(tmp_path, writer):
    settings = {'zip_all_after_download': '1', 'bookmark_download_mode': 'direct'}
    service = SimpleNamespace(
        closing=False, managed_downloads=writer,
        preferences=SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default)),
        author_media=SimpleNamespace(author_zip_setting=lambda aid: None),
        static_jobs=SimpleNamespace(snapshot=lambda: ()),
        _managed_job_records={}, bookmark_download_candidates=lambda: (),
        library_snapshot=lambda: SimpleNamespace(author_names={}),
    )
    return SaveExports(service, tmp_path / 'exports', '99')


def intent_job(exports, kind, identity):
    key = digest(identity)
    (exports.root / (key + '.json')).write_bytes(packet(identity))
    return SimpleNamespace(kind=kind, target_id=key)


def settle(exports):
    with exports.jobs._condition:
        assert exports.jobs._condition.wait_for(
            lambda: all(job.state in ('done', 'error', 'stopped')
                        for job in exports.jobs.snapshot()), 10)
    return exports.jobs.snapshot()


def test_stale_projection_is_refused_and_new_admission_pins_only_visible_pages(owned, tmp_path):
    writer, source, remote = owned
    relative, row, sha = next(r for r in writer.saved_receipts() if r[1]['work_id'] == '123')
    identity = dict(relative=relative, sha256=sha, work_id='123', author_id='24',
                    account='99', mode='direct', use_work_folder=False)
    exports = exports_for(tmp_path, writer)
    calls = tuple(remote.images)
    try:
        old_job = intent_job(exports, 'bookmark-projection', identity)
        source.execute(plan_for(source, '123:2'), operation_id='5' * 32)
        with pytest.raises(ValueError, match='page selection changed'):
            exports._run(old_job, Context())
        assert not list(exports.projection.root.rglob('*.png'))
        assert not (exports.root / (old_job.target_id + '.export.json')).exists()
        admitted = exports.enqueue('bookmark-projection', identity)
        finished = next(job for job in settle(exports) if job.job_id == admitted.job_id)
        assert finished.state == 'done'
        result = json.loads((exports.root / finished.result).read_bytes())
        assert result['source']['source_pages'] == [0, 2]
        assert result['source']['source_state'] == writer._generations.work_state_token('123')
        expected = {legacy_filename(row['title'], '123', index, 3, 'png'):
                    writer.read_saved_page(relative, index, sha) for index in (0, 2)}
        assert len(result['files']) == 2
        for output in result['files']:
            path = exports.projection.root / output['path']
            matches = [body for name, body in expected.items() if path.name.endswith('_' + name)]
            assert len(matches) == 1 and path.read_bytes() == matches[0]
        assert tuple(remote.images) == calls
    finally:
        exports.close()


def test_automatic_scan_skips_a_fully_quarantined_completed_work(owned, tmp_path):
    writer, source, remote = owned
    source.execute(plan_for(source, '123:1', '123:2', '123:3'), operation_id='6' * 32)
    exports = exports_for(tmp_path, writer)
    calls = tuple(remote.images)
    job = SimpleNamespace(job_id='synthetic123', target_id='123', state='done')
    exports.s.static_jobs = SimpleNamespace(snapshot=lambda: (job,))
    exports.s._managed_job_records = {job.job_id: record('123')}
    exports.s.bookmark_download_candidates = lambda: (record('123'),)
    try:
        exports.scan()
        assert exports.jobs.snapshot() == ()
        assert not list(exports.zip.root.glob('*.zip'))
        assert not list(exports.projection.root.rglob('*.png'))
        assert tuple(remote.images) == calls
    finally:
        exports.close()


@pytest.mark.parametrize('published', [False, True])
def test_zip_recovery_distinguishes_unpublished_old_intent_from_existing_bytes(owned, tmp_path, monkeypatch, published):
    writer, source, _ = owned
    exports = exports_for(tmp_path, writer)
    identity = {'author_id': '24', 'proofs': [[row['work_id'], sha]
                for _, row, sha in exports.author_snapshot('24')]}
    job = intent_job(exports, 'auto-zip', identity)
    original_link = zip_module.os.link
    original_publish = export_module._publish_owned
    def link(stage, target):
        if not published and str(target).endswith('.zip'):
            raise OSError('synthetic stop before ZIP link')
        return original_link(stage, target)
    def publish(path, data):
        if published and str(path).endswith('.export.json'):
            raise OSError('synthetic stop after ZIP link')
        return original_publish(path, data)
    try:
        monkeypatch.setattr(zip_module.os, 'link', link)
        monkeypatch.setattr(export_module, '_publish_owned', publish)
        with pytest.raises(OSError if published else LegacyZipExportError):
            exports._run(job, Context())
        note = json.loads((exports.root / (job.target_id + '.zip-pending.json')).read_bytes())
        stage = exports.zip.incomplete / note['stage']
        archive = exports.zip.root / note['archive']
        assert stage.exists() and archive.exists() is published
        before = stage.read_bytes()
        old_mtime = archive.stat().st_mtime_ns if published else None
        monkeypatch.setattr(zip_module.os, 'link', original_link)
        monkeypatch.setattr(export_module, '_publish_owned', original_publish)
        source.execute(plan_for(source, '123:2'), operation_id='7' * 32)
        if published:
            result = exports._run(job, Context())
            assert archive.read_bytes() == before and archive.stat().st_mtime_ns == old_mtime
            assert not stage.exists()
            manifest = json.loads((exports.root / result).read_bytes())
            assert manifest['sha256'] == hashlib.sha256(before).hexdigest()
        else:
            with pytest.raises(ValueError, match='page selection changed'):
                exports._run(job, Context())
            assert not archive.exists() and stage.read_bytes() == before
            assert not (exports.root / (job.target_id + '.export.json')).exists()
    finally:
        exports.close()


def test_old_viewer_page_cannot_republish_and_is_skipped_as_intentional_history(tmp_path):
    remote = Client()
    writer = ManagedPixivDownloads(tmp_path / 'individual-managed', remote, resolve_record=record)
    source = None
    exports = None
    try:
        saved = writer.save_viewer_page('123', 1, 3, '24', lambda: record('123'))
        writer.run(SimpleNamespace(kind='pixiv-illust', target_id='123'), Context())
        source = ManagedDuplicateInspection(writer)
        exports = exports_for(tmp_path, writer)
        manifest_name = viewer_exports.publish(exports, saved, use_work_folder=False)
        manifest = json.loads((exports.root / manifest_name).read_bytes())
        physical = exports.projection.root / manifest['output']['relative']
        before, calls = physical.read_bytes(), tuple(remote.images)
        authors, errors = viewer_exports.recent(exports)
        assert len(authors) == 1 and errors == ()
        source.execute(plan_for(source, '123:2'), operation_id='8' * 32)
        with pytest.raises(ManagedPageQuarantined):
            viewer_exports.source(writer, manifest['identity'])
        with pytest.raises(ManagedPageQuarantined):
            viewer_exports.publish(exports, saved, use_work_folder=False)
        assert viewer_exports.recent(exports) == ((), ())
        corrupt = '0' * 64 + '.viewer-export.json'
        (exports.root / corrupt).write_bytes(b'not valid JSON')
        assert viewer_exports.recent(exports) == ((), (corrupt,))
        assert physical.read_bytes() == before and tuple(remote.images) == calls
        assert (writer.root / saved.relative_receipt).is_file()
    finally:
        if exports is not None:
            exports.close()
        if source is not None:
            source.close()
        writer.close()
