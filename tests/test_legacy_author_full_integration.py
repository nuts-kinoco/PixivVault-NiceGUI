"""A complete, offline legacy author recovery through live service/job/CAS.

Only disposable SQLite files, fake Pixiv transport and synthetic images.
"""
import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
from threading import Event

from PIL import Image

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.managed_download_requests import ManagedAuthorIntent
from pixivvault_nicegui.services import ApplicationServices


def png(rgb=(42, 71, 109)):
    raw = BytesIO()
    Image.new('RGB', (64, 32), rgb).save(raw, 'PNG')
    return raw.getvalue()


def fixture(tmp_path):
    frozen_root=tmp_path/'frozen-old-copy'
    frozen_root.mkdir()
    source = frozen_root / 'offline-db-copy.sqlite3'
    old_media = tmp_path / 'old-media'
    old_media.mkdir()
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE works (work_id TEXT PRIMARY KEY,title TEXT,user_id TEXT,'
                   'page_count INTEGER,content_type TEXT,update_date TEXT,'
                   'is_deleted INTEGER,missing_streak INTEGER)')
        db.executemany('INSERT INTO works VALUES (?,?,?,?,?,?,?,?)', (
            ('321', 'Restore work', '91', 1, 'illust', '2026-10-22', 1, 0),
            ('322', 'Missing work', '91', 1, 'illust', '2026-10-22', 0, 0),
            ('323', 'Existing work', '91', 1, 'illust', '2026-10-22', 0, 0),
            ('324', 'Remote absent', '91', 1, 'illust', '2026-10-22', 0, 2),
        ))
        db.execute('CREATE TABLE failed_queue (work_id TEXT,user_id TEXT,title TEXT,'
                   'work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER)')
        db.execute('INSERT INTO failed_queue VALUES (?,?,?,?,?,?,?)',
                   ('321', '91', 'Restore work', 'illust', 'Old offline error', '2026-10-21', 1))
    folder = old_media / 'Example(91)'
    folder.mkdir()
    (folder / 'Existing work(323).png').write_bytes(png())
    old_sha = (sha256(source.read_bytes()).hexdigest(),
               sha256((folder / 'Existing work(323).png').read_bytes()).hexdigest())
    return source, old_media, tmp_path / 'managed', old_sha


class SyntheticReader:
    date = '2026-10-22'
    failed_original = False

    def __init__(self):
        self.original_requests = 0
        self.inventory_requests = 0

    def verify_identity(self):
        return '99'

    def get_author_artworks_complete(self, author):
        assert author == '91'
        self.inventory_requests += 1
        return tuple({'id': wid, 'user_id': '91', 'title': title,
                      'type': 0, 'page_count': 1, 'update_date': self.date}
                     for wid, title in (('321', 'Restore work'), ('322', 'Missing work'),
                                        ('323', 'Existing work')))

    def get_artwork_download_record(self, wid):
        assert wid in {'321', '322', '323'}
        return RemoteBookmark(wid, 'illustration', 'show',
                              {'321': 'Restore work', '322': 'Missing work',
                               '323': 'Existing work'}[wid],
                              '91', 'Example', (), '', '2026-10-01', 1, 0, 0)

    def get_artwork_update_revision(self, wid, uid):
        assert uid == '91'
        return {'work_id': wid, 'author_id': '91',
                'illustration_type': 0, 'page_count': 1, 'update_date': self.date}

    def get_artwork_original_pages(self, wid):
        return (f'https://i.pximg.net/synthetic/{wid}.png',)

    def get_image_bytes(self, url):
        self.original_requests += 1
        if self.failed_original:
            raise OSError('synthetic network timeout')
        return png((84, 54, 21))

    def close(self):
        pass


def terminal(services, jid):
    with services.jobs._condition:
        assert services.jobs._condition.wait_for(
            lambda: any(job.job_id == jid and job.state in {'done', 'error', 'stopped'}
                        for job in services.jobs.snapshot()), timeout=12)
    return next(job for job in services.jobs.snapshot() if job.job_id == jid)


def test_old_db_zip_and_failure_history_to_managed_restore_restarts_safely(tmp_path):
    copy, old_media, managed, prior_shas = fixture(tmp_path)
    owner = 'a' * 32
    source = SyntheticReader()
    services = ApplicationServices(live_client=source, pixiv_download_root=managed,
        failed_queue_readonly_db=copy, legacy_diff_image_root=old_media)
    try:
        preview = asyncio.run(services.lookup_managed_author('91', owner=owner))
        assert {work['id']: state for work, state in preview} == {
            '321': Difference.RESTORED, '322': Difference.MISSING,
            '323': Difference.SKIPPED}
        assert preview.remote_missing_pending == 1
        assert preview[0][0]['legacy_failed'] is True
        assert preview.remote_absent[0].work_id == '324'
        assert preview.remote_absent[0].streak == 2

        observed = asyncio.run(services.confirm_legacy_author_inventory(
            '91', owner=owner, scan_id='a' * 32))
        assert observed.remote_missing_confirmed == 1
        assert observed.remote_absent[0].status == 'confirmed'
        assert asyncio.run(services.lookup_managed_author('91', owner=owner)).remote_missing_confirmed == 1
        target = source.get_artwork_download_record('321')
        services.remember_managed_author_batch(owner, (target,))
        accepted = services.enqueue_managed_download(
            ManagedAuthorIntent(target, 'id', source.date, None, 'new'),
            'phase8-recover-one', owner=owner)
        assert terminal(services, accepted.job_id).state == 'done'
        assert source.original_requests == 1
        assert services.managed_downloads.saved_update_date('321') == source.date
        refreshed = asyncio.run(services.lookup_managed_author('91', owner=owner))
        assert dict((item['id'], state) for item, state in refreshed)['321'] is Difference.SKIPPED
        assert len(tuple((managed / 'generations').glob('work-321-*'))) == 1
        assert (sha256(copy.read_bytes()).hexdigest(),
                sha256((old_media / 'Example(91)' / 'Existing work(323).png').read_bytes()).hexdigest()) == prior_shas
    finally:
        services.close()
    again = SyntheticReader()
    restored = ApplicationServices(live_client=again, pixiv_download_root=managed,
        failed_queue_readonly_db=copy, legacy_diff_image_root=old_media)
    try:
        assert next(j for j in restored.jobs.snapshot() if j.target_id == '321').state == 'done'
        refreshed = asyncio.run(restored.lookup_managed_author('91', owner=owner))
        assert dict((item['id'], state) for item, state in refreshed)['321'] is Difference.SKIPPED
        assert refreshed.remote_missing_confirmed == 1
        assert again.original_requests == 0
    finally:
        restored.close()
