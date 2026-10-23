"""Legacy-compatible, isolated ZIP exports over fully verified managed pages."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from nicegui import Client, ui
from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.bootstrap import parse_args
from pixivvault_nicegui.legacy_zip_export import (
    LegacyZipExporter, LegacyZipExportError, legacy_filename,
)
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.library_view import LibraryView


def sample_png(color):
    image = Image.new('RGB', (48, 32), color)
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def art(wid, title, *, author='作者X', author_id='23', pages=1):
    return RemoteBookmark(wid, 'illustration', 'show', title, author_id, author,
                          (), '', '2026', pages, 0, 0)


class SyntheticOriginals:
    version = 1
    remote_requests = 0
    closed = False

    def __init__(self):
        self.records = {'301': art('301', 'いちご:パフェ', pages=2),
                        '302': art('302', '題名_12'),
                        '303': art('303', 'うめ', author='作者Y', author_id='24')}

    def get_artwork_original_pages(self, work_id):
        self.remote_requests += 1
        return tuple(f'https://i.pximg.net/synthetic/{work_id}_p{index}.png'
                     for index in range(self.records[work_id].page_count))

    def get_image_bytes(self, url):
        self.remote_requests += 1
        color = ((90, 20, 50) if self.version == 1 else (20, 160, 70))
        return sample_png(color)

    def get_artwork_update_revision(self, work_id, author_id):
        self.remote_requests += 1
        record = self.records[work_id]
        return {'author_id': record.author_id, 'page_count': record.page_count,
                'illustration_type': 0, 'update_date': f'2026-10-{self.version + 20}'}

    def verify_identity(self):
        return '12'

    def close(self):
        self.closed = True


class Job:
    kind = 'pixiv-illust'
    job_id = 'a' * 32

    def __init__(self, target_id):
        self.target_id = target_id


class Context:
    def checkpoint(self):
        pass

    def progress(self, done, total):
        pass


def writer(tmp_path):
    remote = SyntheticOriginals()
    managed = ManagedPixivDownloads(tmp_path / 'managed', remote,
                                    resolve_record=lambda wid: remote.records[wid])
    for wid in remote.records:
        managed.run(Job(wid), Context())
    return remote, managed


@pytest.mark.parametrize(('title', 'wid', 'index', 'count', 'ext', 'expected'), (
    ('A/B:C', '301', 0, 1, 'png', 'A_B_C(301).png'),
    ('みかん', '301', 0, 2, 'jpg', 'みかん(301)_1.jpg'),
    ('みかん', '301', 1, 2, 'jpg', 'みかん(301)_2.jpg'),
    ('題名_12', '302', 0, 1, 'webp', '題名(302)_12.webp'),
    ('すでに(301)', '301', 0, 1, 'png', 'すでに(301).png'),
))
def test_old_core_filename_parity(title, wid, index, count, ext, expected):
    assert legacy_filename(title, wid, index, count, ext) == expected


def test_oversized_legacy_zip_member_name_rejected_without_truncation():
    with pytest.raises(LegacyZipExportError, match='too long'):
        legacy_filename('漢' * 100, '301', 0, 1, 'png')


def test_explicit_export_zip_matches_legacy_members_and_immutable_originals(tmp_path):
    remote, managed = writer(tmp_path)
    before = managed.saved_receipts()
    originals = tuple((relative, receipt['work_id'], digest,
                       tuple(managed.read_saved_page(relative, index, digest)
                             for index in range(receipt['page_count'])))
                      for relative, receipt, digest in before)
    calls = remote.remote_requests
    export = LegacyZipExporter(tmp_path / 'separate-exports', managed)
    try:
        path = export.export_author('23')
        assert path.name == '作者X(23).zip'
        assert path.is_file()
        with ZipFile(path) as archive:
            assert archive.testzip() is None
            assert {entry.filename for entry in archive.infolist()} == {
                'いちご_パフェ(301)_1.png', 'いちご_パフェ(301)_2.png',
                '題名(302)_12.png'}
            assert all(entry.compress_type == ZIP_DEFLATED for entry in archive.infolist())
            assert all(len(archive.read(info)) > 0 for info in archive.namelist())
        assert remote.remote_requests == calls, 'the ZIP exporter must not call Pixiv'
        assert before == managed.saved_receipts()
        assert originals == tuple((relative, receipt['work_id'], digest,
                       tuple(managed.read_saved_page(relative, index, digest)
                             for index in range(receipt['page_count'])))
                      for relative, receipt, digest in managed.saved_receipts())
        digest = sha256(path.read_bytes()).digest()
        with pytest.raises(LegacyZipExportError, match='already exported'):
            export.export_author('23')
        assert sha256(path.read_bytes()).digest() == digest
        reopened = LegacyZipExporter(export.root, managed)
        with pytest.raises(LegacyZipExportError, match='already exported'):
            reopened.export_author('23')
        reopened.close()
    finally:
        export.close()
        managed.close()


def test_staged_failure_leaves_no_public_zip_and_does_not_touch_source(tmp_path, monkeypatch):
    _, managed = writer(tmp_path)
    exported = LegacyZipExporter(tmp_path / 'export-new', managed)
    original = managed.read_saved_page
    seen = []

    def failed(relative, page, digest):
        seen.append((relative, page))
        if len(seen) == 2:
            raise OSError('synthetic crash after first exported page')
        return original(relative, page, digest)

    monkeypatch.setattr(managed, 'read_saved_page', failed)
    try:
        with pytest.raises(LegacyZipExportError, match='export failed'):
            exported.export_author('23')
        assert len(seen) == 2
        assert not list(exported.root.glob('*.zip'))
        assert not list(exported.incomplete.glob('zip-*'))
        monkeypatch.setattr(managed, 'read_saved_page', original)
        assert exported.export_author('23').is_file()
    finally:
        exported.close()
        managed.close()


def test_source_corruption_is_rejected_without_publish(tmp_path):
    _, managed = writer(tmp_path)
    legacy_zip = LegacyZipExporter(tmp_path / 'separate-zip', managed)
    relative, receipt, _ = next(row for row in managed.saved_receipts()
                                if row[1]['work_id'] == '301')
    path = managed.root / relative.rsplit('/', 1)[0] / receipt['files'][0]['name']
    path.write_bytes(b'corrupt')
    try:
        with pytest.raises(Exception):
            legacy_zip.export_author('23')
        assert not list(legacy_zip.root.glob('*.zip'))
    finally:
        legacy_zip.close()
        managed.close()


def test_updated_v2_active_page_is_exported_without_old_generation_loss(tmp_path):
    source = SyntheticOriginals()
    managed = ManagedPixivDownloads(tmp_path / 'managed', source,
                                    resolve_record=lambda wid: source.records[wid])
    first = ManagedAuthorIntent(source.records['301'], 'id', '2026-10-21',
                                None, 'new')
    original = managed.run(Job('301'), Context(), intent=first)
    original_receipt = next(row for row in managed.saved_receipts() if row[1]['work_id'] == '301')
    first_sha = original_receipt[2]
    first_bytes = managed.read_saved_page(original, 0, first_sha)
    source.version = 2

    class UpdatedJob(Job):
        job_id = 'b' * 32

    second = ManagedAuthorIntent(source.records['301'], 'follow', '2026-10-22',
                                 first_sha, 'updated', original)
    updated = managed.run(UpdatedJob('301'), Context(), intent=second)
    assert updated != original
    assert managed.read_saved_page(original, 0, first_sha) == first_bytes
    expected = managed.saved_receipts()[0]
    assert expected[0] == updated
    zip_writer = LegacyZipExporter(tmp_path / 'zip-separate', managed)
    try:
        archive_path = zip_writer.export_author('23')
        with ZipFile(archive_path) as archive:
            assert archive.read('いちご_パフェ(301)_1.png') == managed.read_saved_page(
                updated, 0, expected[2])
            assert archive.read('いちご_パフェ(301)_1.png') != first_bytes
        assert managed.read_saved_page(original, 0, first_sha) == first_bytes
    finally:
        zip_writer.close()
        managed.close()


def test_failed_atomic_zip_publication_leaves_no_visible_archive(tmp_path, monkeypatch):
    _, managed = writer(tmp_path)
    output = LegacyZipExporter(tmp_path / 'export-isolated', managed)
    from pixivvault_nicegui import legacy_zip_export

    def fail_link(*_args, **_kwargs):
        raise OSError('synthetic hardlink publication failure')

    try:
        monkeypatch.setattr(legacy_zip_export.os, 'link', fail_link)
        with pytest.raises(LegacyZipExportError, match='export failed'):
            output.export_author('23')
        assert not list(output.root.glob('*.zip'))
        assert not list(output.incomplete.glob('zip-*'))
        assert len(managed.saved_receipts()) == 3
    finally:
        output.close()
        managed.close()


def test_parallel_exports_do_not_race_or_overwrite(tmp_path, monkeypatch):
    _, managed = writer(tmp_path)
    exporter = LegacyZipExporter(tmp_path / 'exclusive', managed)
    first_page = Event()
    proceed = Event()
    original = managed.read_saved_page
    results = []

    def slow(relative, index, digest):
        first_page.set()
        assert proceed.wait(8)
        return original(relative, index, digest)

    monkeypatch.setattr(managed, 'read_saved_page', slow)

    def write():
        try:
            results.append(exporter.export_author('23'))
        except Exception as exc:
            results.append(type(exc).__name__)

    thread = Thread(target=write)
    try:
        thread.start()
        assert first_page.wait(5)
        with pytest.raises(LegacyZipExportError, match='already active'):
            exporter.export_author('24')
        proceed.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(results) == 1 and isinstance(results[0], Path)
        with ZipFile(results[0]) as z:
            assert z.testzip() is None
    finally:
        proceed.set()
        thread.join(10)
        exporter.close()
        managed.close()


def test_crash_after_exclusive_link_recovers_without_changing_published_zip(tmp_path):
    from os import link
    _, managed = writer(tmp_path)
    first = LegacyZipExporter(tmp_path / 'recover-zip', managed)
    try:
        published = first.export_author('23')
        before = sha256(published.read_bytes()).digest()
        stage = first.incomplete / f'zip-{uuid4().hex}.tmp'
        # A synthetic crash just after os.link and before staging unlink.
        link(published, stage)
        assert stage.is_file() and published.stat().st_nlink == 2
        first.close()
        reopened = LegacyZipExporter(first.root, managed)
        try:
            assert not stage.exists()
            assert published.is_file() and published.stat().st_nlink == 1
            assert sha256(published.read_bytes()).digest() == before
            with pytest.raises(LegacyZipExportError, match='already exported'):
                reopened.export_author('23')
        finally:
            reopened.close()
    finally:
        managed.close()


def test_unknown_incomplete_path_blocks_recovery_without_deleting_it(tmp_path):
    _, managed = writer(tmp_path)
    first = LegacyZipExporter(tmp_path / 'unknown-incomplete', managed)
    foreign = first.incomplete / 'not-ours.txt'
    foreign.write_bytes(b'preserve me')
    first.close()
    try:
        with pytest.raises(LegacyZipExportError, match='unsafe incomplete'):
            LegacyZipExporter(first.root, managed)
        assert foreign.read_bytes() == b'preserve me'
    finally:
        managed.close()


def test_shutdown_aborts_inflight_export_and_preserves_managed_images(tmp_path, monkeypatch):
    _, managed = writer(tmp_path)
    first = LegacyZipExporter(tmp_path / 'shutdown', managed)
    reached, release = Event(), Event()
    original = managed.read_saved_page
    reports = []

    def interrupted_read(relative, index, digest):
        reached.set()
        assert release.wait(8)
        return original(relative, index, digest)

    def export():
        try:
            first.export_author('23')
            reports.append('unexpected ZIP success')
        except LegacyZipExportError as exc:
            reports.append(str(exc))

    monkeypatch.setattr(managed, 'read_saved_page', interrupted_read)
    worker = Thread(target=export)
    closing = Thread(target=first.close)
    try:
        worker.start()
        assert reached.wait(5)
        closing.start()
        for _ in range(100):
            if first._closing:
                break
            from time import sleep
            sleep(.01)
        assert first._closing
        release.set()
        worker.join(10)
        closing.join(10)
        assert not worker.is_alive() and not closing.is_alive()
        assert reports == ['ZIP export interrupted by shutdown']
        assert not list(first.root.glob('*.zip'))
        assert not list(first.incomplete.glob('zip-*'))
        assert len(managed.saved_receipts()) == 3
    finally:
        release.set()
        worker.join(10)
        closing.join(10)
        managed.close()


def test_active_generation_updated_while_exporting_never_publishes_stale_zip(tmp_path, monkeypatch):
    source = SyntheticOriginals()
    managed = ManagedPixivDownloads(tmp_path / 'managed', source,
                                    resolve_record=lambda wid: source.records[wid])
    first = ManagedAuthorIntent(source.records['301'], 'id', '2026-10-21',
                                None, 'new')
    previous = managed.run(Job('301'), Context(), intent=first)
    prior = managed.saved_receipts()[0][2]
    exporter = LegacyZipExporter(tmp_path / 'export-current', managed)
    original = managed.read_saved_page
    upgraded = []

    class UpdateJob(Job):
        job_id = 'b' * 32

    def update_after_snapshot(relative, index, digest):
        if not upgraded:
            upgraded.append(True)
            source.version = 2
            newer = ManagedAuthorIntent(source.records['301'], 'id', '2026-10-22',
                                        prior, 'updated', previous)
            managed.run(UpdateJob('301'), Context(), intent=newer)
        return original(relative, index, digest)

    monkeypatch.setattr(managed, 'read_saved_page', update_after_snapshot)
    try:
        with pytest.raises(LegacyZipExportError, match='changed during ZIP export'):
            exporter.export_author('23')
        assert upgraded
        assert not list(exporter.root.glob('*.zip'))
        assert not list(exporter.incomplete.glob('zip-*'))
        assert managed.saved_update_date('301') == '2026-10-22'
        assert managed.read_saved_page(previous, 0, prior)
    finally:
        exporter.close()
        managed.close()


def test_export_does_not_write_unsupported_or_foreign_directory(tmp_path):
    _, managed = writer(tmp_path)
    foreign = tmp_path / 'already-present'
    foreign.mkdir()
    (foreign / 'existing.zip').write_bytes(b'original untouched')
    try:
        with pytest.raises(LegacyZipExportError, match='not owned'):
            LegacyZipExporter(foreign, managed)
        assert (foreign / 'existing.zip').read_bytes() == b'original untouched'
        with pytest.raises(LegacyZipExportError, match='overlaps'):
            LegacyZipExporter(managed.root / 'export', managed)
    finally:
        managed.close()


def test_author_name_changed_does_not_create_second_archive_for_same_id(tmp_path):
    _, managed = writer(tmp_path)
    exporter = LegacyZipExporter(tmp_path / 'unique-author-zip', managed)
    earlier = exporter.root / '以前の作者名(23).zip'
    earlier.write_bytes(b'preexisting archive must remain untouched')
    original = earlier.read_bytes()
    try:
        with pytest.raises(LegacyZipExportError, match='already exported'):
            exporter.export_author('23')
        assert earlier.read_bytes() == original
        assert len(list(exporter.root.glob('*.zip'))) == 1
    finally:
        exporter.close()
        managed.close()


def test_explicit_cli_gate_rejects_managed_or_cookie_overlap(tmp_path):
    cookie = tmp_path / 'creds' / 'cookies.txt'
    cookie.parent.mkdir()
    cookie.write_text('synthetic-only')
    root = tmp_path / 'managed'
    output = tmp_path / 'new-dedicated-output'
    flags = ['--pixiv-cookie-file', str(cookie), '--pixiv-download-root', str(root),
             '--legacy-zip-export-root', str(output)]
    assert parse_args(flags).legacy_zip_export_root == output
    assert not output.exists()
    with pytest.raises(SystemExit):
        parse_args(['--legacy-zip-export-root', str(output)])
    with pytest.raises(SystemExit):
        parse_args(flags[:-1] + [str(root / 'legacy')])
    with pytest.raises(SystemExit):
        parse_args(flags[:-1] + [str(cookie.parent / 'legacy')])


@pytest.mark.asyncio
async def test_managed_library_author_export_button_uses_offloop_io(tmp_path):
    source = SyntheticOriginals()
    root = tmp_path / 'managed'
    zip_root = tmp_path / 'export'
    service = ApplicationServices(live_client=source, pixiv_download_root=root,
                                  legacy_zip_export_root=zip_root)
    client = Client(ui.page('/_legacy_zip_export'))
    try:
        for wid in ('301', '302'):
            service.remember_managed_work_id('a' * 32, source.records[wid])
            job = service.enqueue_managed_download(source.records[wid], wid, owner='a' * 32)
            with service.jobs._condition:
                assert service.jobs._condition.wait_for(lambda: any(
                    j.job_id == job.job_id and j.state in ('done', 'error')
                    for j in service.jobs.snapshot()), timeout=10)
            assert next(j for j in service.jobs.snapshot() if j.job_id == job.job_id).state == 'done'
        await service.refresh_library_snapshot()
        with client:
            view = LibraryView(service)
            view.open_author('23')
            assert view.zip_export_button.enabled
            before = source.remote_requests
            assert await view.export_legacy_zip()
            assert '作成しました' in view.zip_export_status.text
            assert source.remote_requests == before
            assert (zip_root / '作者X(23).zip').is_file()
            assert not await view.export_legacy_zip(), 'repeated explicit ZIP never overwrites'
            view.close_author()
            assert not view.zip_export_button.enabled
            view.close()
    finally:
        client.delete()
        service.close()


@pytest.mark.asyncio
async def test_late_zip_export_notice_does_not_survive_tab_roundtrip(tmp_path, monkeypatch):
    source = SyntheticOriginals()
    app = ApplicationServices(live_client=source, pixiv_download_root=tmp_path / 'managed',
                              legacy_zip_export_root=tmp_path / 'export')
    client = Client(ui.page('/_legacy_zip_stale'))
    started, release = Event(), Event()

    def paused(_author):
        started.set()
        assert release.wait(10)
        return app.legacy_zip_exporter.root / 'synthetic.zip'

    try:
        monkeypatch.setattr(app.legacy_zip_exporter, 'export_author', paused)
        # Synthetic LibraryState only: the worker receives an author ID. The
        # source need not contain real images for the stale-notice gate.
        from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
        from pixivvault_nicegui.models import Artwork
        picture = Artwork(work_id='301', title='Synthetic', author='作者X', page_count=1,
                          tags=())
        app.library = LibrarySnapshot((LibraryWork(picture, '23'),))
        with client:
            view = LibraryView(app)
            view.open_author('23')
            old_epoch = view._zip_export_epoch
            task = asyncio.create_task(view.export_legacy_zip(old_epoch))
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.01)
            assert started.is_set()
            view.set_active(False)
            view.set_active(True)
            release.set()
            assert await task
            assert view._zip_export_epoch != old_epoch
            assert '作成しました' not in view.zip_export_status.text
            view.close()
    finally:
        release.set()
        client.delete()
        app.close()
