"""Long owned paths: author journal acceptance and settings survive reopening."""
from hashlib import sha256
from pathlib import Path

import pytest

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.author_media import AuthorMediaCandidate
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.services import ApplicationServices
from test_viewer_page_save import Reader


class StaticAuthorReader(Reader):
    revision = '2026-10-23T00:00:00Z'

    def __init__(self):
        super().__init__()
        self.revisions = []
        self.identity_reads = 0

    def verify_identity(self):
        self.identity_reads += 1
        return super().verify_identity()

    def get_artwork_update_revision(self, work_id, author_id):
        assert (work_id, author_id) == ('123', '456')
        self.revisions.append((work_id, author_id))
        return dict(work_id=work_id, author_id=author_id, illustration_type=0,
                    page_count=3, update_date=self.revision)

    def observations(self):
        return (list(self.images), list(self.metadata), list(self.originals),
                list(self.revisions), self.identity_reads)


def application(root, reader):
    root = native_io_path(root)
    root.mkdir(parents=True, exist_ok=True)
    services = ApplicationServices(
        1, live_client=reader, pixiv_download_root=root / 'managed',
        preferences_root=root / 'preferences')
    try:
        services.configure_save_exports(root / 'exports')
        return services
    except BaseException:
        services.close()
        raise


def journal_rows(owner):
    with owner._db() as db:
        return tuple(db.execute(
            'SELECT operation_id,media_key,spec,job_id,fingerprint,proof '
            'FROM admissions ORDER BY operation_id'))


def receipt_files(services, saved):
    receipt = native_io_path(services.managed_downloads.root / saved[0])
    return {path.name: sha256(path.read_bytes()).hexdigest()
            for path in receipt.parent.iterdir() if path.is_file()}


def test_deep_author_journal_keeps_accepted_operation_settings_and_receipt_without_replay(tmp_path):
    root = Path(tmp_path)
    while len(str(root)) < 285:
        root /= 'deep-owned-author-journal'
    first_reader = StaticAuthorReader()
    services = application(root, first_reader)
    candidate = AuthorMediaCandidate(
        'illustration', '123', '456', first_reader.revision, 3, 'synthetic',
        Difference.NEW,
        payload=dict(id='123', user_id='456', type=0, page_count=3,
                     title='synthetic', update_date=first_reader.revision,
                     expected_relative_receipt=None, expected_receipt_sha256=None))
    operation = 'b' * 32
    try:
        owner = services.author_media
        expected_path = root / 'managed' / 'author-media.sqlite3'
        assert owner.path == expected_path and len(str(owner.path)) > 285
        target = owner.configure_author_target('456', 'illust', expected_revision=0)
        revision = owner.configure_author_zip('456', False, expected_revision=target.revision)
        setting = owner.author_target('456')
        assert setting.revision == revision and owner.author_zip_setting('456') is False

        job, created, fingerprint = owner.enqueue(candidate, operation, origin='id')
        assert created
        jobs = services.static_jobs
        with jobs._condition:
            assert jobs._condition.wait_for(
                lambda: any(row.job_id == job.job_id and row.state in ('done', 'error', 'stopped')
                            for row in jobs.snapshot()), 15), jobs.snapshot()
        completed = next(row for row in jobs.snapshot() if row.job_id == job.job_id)
        assert completed.state == 'done'
        proofs, operations, fingerprints = owner.reconcile()
        assert operations == {operation: job.job_id}
        assert proofs == {'123': fingerprint} and fingerprints == {operation: fingerprint}
        rows = journal_rows(owner)
        assert len(rows) == 1 and rows[0][:2] == (operation, '123')
        assert rows[0][3:5] == (job.job_id, fingerprint) and rows[0][5] is not None
        saved = services.managed_downloads.verified_saved_receipt('123')
        assert saved is not None and completed.result == saved[0]
        files = receipt_files(services, saved)
        assert set(files) == {'receipt.json', 'page-1.png', 'page-2.png', 'page-3.png'}
        accepted = jobs.accepted_operations()
        inode = native_io_path(owner.path).stat()
        assert inode.st_nlink == 1
        identity = inode.st_dev, inode.st_ino
    finally:
        services.close()

    second_reader = StaticAuthorReader()
    services = application(root, second_reader)
    try:
        owner = services.author_media
        assert owner.path == expected_path
        inode = native_io_path(owner.path).stat()
        assert (inode.st_dev, inode.st_ino) == identity and inode.st_nlink == 1
        assert owner.author_target('456') == setting
        assert owner.author_zip_setting('456') is False
        with pytest.raises(ValueError, match='another client'):
            owner.configure_author_target('456', 'novel', expected_revision=0)
        assert journal_rows(owner) == rows
        assert services.static_jobs.accepted_operations() == accepted
        replay, created, current_fingerprint = owner.enqueue(candidate, operation, origin='id')
        assert replay == completed and created is False and current_fingerprint == fingerprint
        assert journal_rows(owner) == rows
        assert services.managed_downloads.verified_saved_receipt('123') == saved
        assert receipt_files(services, saved) == files
        assert second_reader.observations() == ([], [], [], [], 0)
    finally:
        services.close()
