"""Normal profile + frozen legacy data + managed saves, entirely synthetic."""
import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from PIL import Image
import pytest

from pixivvault_nicegui.author_download import Difference
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.native_profile import NativeProfileStore
from pixivvault_nicegui.services import ApplicationServices


def image(color):
    out = BytesIO()
    Image.new('RGB', (24, 16), color).save(out, 'PNG')
    return out.getvalue()


def fixture(tmp_path, *, failures=True):
    frozen = tmp_path / 'frozen'
    frozen.mkdir()
    db = frozen / 'legacy.sqlite3'
    images = frozen / 'images'
    author = images / 'Old Author(91)'
    author.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE following_users(user_id TEXT PRIMARY KEY,name TEXT);
            INSERT INTO following_users VALUES('91','Old Author');
            CREATE TABLE works(work_id TEXT PRIMARY KEY,title TEXT,user_id TEXT,
                page_count INTEGER,tags TEXT,is_deleted INTEGER,content_type TEXT,
                update_date TEXT,missing_streak INTEGER,last_backup TEXT,is_bookmarked INTEGER);
            INSERT INTO works VALUES('321','Old 321','91',1,'[]',0,'illust',
                '2026-10-01',0,'2026-10-01',1);
            INSERT INTO works VALUES('324','Old 324','91',1,'[]',0,'illust',
                '2026-10-01',0,'2026-10-01',0);
            INSERT INTO works VALUES('322','Old failed','91',1,'[]',1,'illust',
                '2026-10-01',0,'2026-10-01',0);
        ''')
        if failures:
            conn.executescript('''
                CREATE TABLE failed_queue(work_id TEXT,user_id TEXT,title TEXT,
                    work_type TEXT,error_reason TEXT,failed_at TEXT,retry_count INTEGER);
                INSERT INTO failed_queue VALUES('322','91','Old failed','illust',
                    'Synthetic timeout','2026-10-01T00:00:00',1);
            ''')
    (author / 'Old 321(321).png').write_bytes(image('red'))
    (author / 'Old 324(324).png').write_bytes(image('green'))
    return db, images, tmp_path / 'managed'


def fingerprint(root):
    return {str(path.relative_to(root)): (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in root.rglob('*') if path.is_file()}


class Reader:
    def __init__(self, *_):
        self.calls = []
        self.closed = False

    def verify_identity(self):
        self.calls.append('identity')
        return '99'

    def get_artwork_download_record(self, wid):
        self.calls.append(('record', wid))
        return RemoteBookmark(wid, 'illustration', 'show', 'Current ' + wid,
            '92' if wid == '323' else '91', 'Current Author', (), '', '2026-10-01', 1, 0, 0)

    def get_artwork_original_pages(self, wid):
        self.calls.append(('pages', wid))
        return (f'https://i.pximg.net/synthetic/{wid}.png',)

    def get_image_bytes(self, url):
        self.calls.append('image')
        return image('blue')

    def get_author_artworks_complete(self, author):
        self.calls.append(('inventory', author))
        return tuple(dict(id=wid, user_id='91', title='Current ' + wid,
            type=0, page_count=1, update_date='2026-10-01') for wid in ('321', '322', '324'))

    get_author_artworks = get_author_artworks_complete

    def close(self):
        self.closed = True


def terminal(service, job_id):
    owner = service.static_jobs
    with owner._condition:
        assert owner._condition.wait_for(lambda: any(
            job.job_id == job_id and job.state in {'done', 'error', 'stopped'}
            for job in owner.snapshot()), timeout=12)
    return next(job for job in owner.snapshot() if job.job_id == job_id)


def test_profile_keeps_both_sources_and_all_live_media_cross_bootstrap(tmp_path, monkeypatch):
    from pixivvault_nicegui import bootstrap, pixiv_read_client, services
    db, images, _ = fixture(tmp_path)
    original = fingerprint(db.parent)
    cookie = tmp_path / 'credentials' / 'synthetic.txt'
    cookie.parent.mkdir()
    cookie.write_text('# synthetic reference only\n', encoding='utf-8')
    parent = tmp_path / 'selected'
    parent.mkdir()
    profile = tmp_path / 'profile'
    store = NativeProfileStore(profile)
    try:
        saved = store.update(cookie_file=cookie, save_parent=parent,
            library_readonly_db=db, library_image_root=images, expected_revision=0)
        values = store.native_kwargs(saved)
    finally:
        store.close()
    config = bootstrap.parse_args(['--native', '--profile-root', str(profile), '--check'])
    assert config.library_readonly_db == db and config.pixiv_cookie_file == cookie
    assert config.profile_revision == saved.revision
    assert config.pixiv_download_root and config.novel_download_root and config.ugoira_root
    assert not list(parent.iterdir())
    captured, readers = [], []
    class StopBeforeOwners(Exception):
        pass
    def capture(*args, **kwargs):
        captured.append(kwargs)
        raise StopBeforeOwners()
    def make_reader(path):
        reader = Reader()
        readers.append(reader)
        return reader
    monkeypatch.setattr(bootstrap, '_registered', False)
    monkeypatch.setattr(services, 'get_services', capture)
    monkeypatch.setattr(pixiv_read_client, 'LivePixivReadClient', make_reader)
    with pytest.raises(StopBeforeOwners):
        bootstrap.register_pages(**values)
    assert len(captured) == 1
    args = captured[0]
    assert args['library_readonly_db'] == db and args['library_image_root'] == images
    assert args['pixiv_download_root'] == values['pixiv_download_root']
    assert args['novel_fixture_root'] == values['novel_download_root']
    assert args['ugoira_root'] == values['ugoira_root']
    assert len(readers) == 3 and all(reader.closed and not reader.calls for reader in readers)
    assert not list(parent.iterdir()) and fingerprint(db.parent) == original


@pytest.mark.asyncio
async def test_old_library_new_saves_bookmarks_failed_retry_and_restart_keep_both_sources(tmp_path, monkeypatch):
    import pixivvault_nicegui.bookmark_remote_adapter as adapter
    db, images, managed = fixture(tmp_path)
    before = fingerprint(db.parent)
    connect = sqlite3.connect
    uris = []
    def observed(database, *args, **kwargs):
        if isinstance(database, str) and 'legacy.sqlite3' in database:
            uris.append(database)
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', observed)
    reader = Reader()
    service = ApplicationServices(library_readonly_db=db, library_image_root=images,
        live_client=reader, pixiv_download_root=managed)
    initial = current = history = None
    try:
        assert not reader.calls
        assert {item.work_id for item in service.library_snapshot().works} == {'321', '324'}
        assert service.legacy_failed_retry_available and service.legacy_author_bridge is not None
        initial = service.acquire_library_images()
        assert initial.get('321', 0, 'original').data == image('red')
        owner = 'a' * 32
        preview = await service.lookup_managed_author('91', owner=owner)
        assert dict((work['id'], state) for work, state in preview)['321'] is Difference.SKIPPED
        for wid in ('321', '323'):
            record = await service.lookup_managed_work_id(wid, owner=owner)
            accepted = service.enqueue_managed_download(record, 'coexist-' + wid, owner=owner)
            assert (await asyncio.to_thread(terminal, service, accepted.job_id)).state == 'done'
        await service.refresh_library_snapshot()
        assert {item.work_id for item in service.library_snapshot().works} == {'321', '323', '324'}
        current = service.acquire_library_images()
        assert current.get('321', 0, 'original').data == image('blue')
        assert current.get('324', 0, 'original').data == image('green')
        assert initial.get('321', 0, 'original').data == image('red')
        records = tuple(reader.get_artwork_download_record(wid) for wid in ('321', '323'))
        monkeypatch.setattr(adapter, 'fetch_all_bookmarks', lambda *_: SimpleNamespace(ok=True, records=records))
        await service.refresh_bookmark_cache()
        assert {item.work_id for item in service.bookmark_local_snapshot()} == {'321', '323'}
        assert {'321', '323', '324'} <= service.saved_discovery_ids()
        assert {item.work_id for item in service.library_snapshot().works} == {'321', '323', '324'}
        rows, missing, history = await service.recent_legacy_backup_authors()
        assert not missing and {row.author_id for row in rows} == {'91'}
        assert all(row.target.is_relative_to(images) for row in rows)
        assert history.get('321', 0, 'original').data == image('red')
        failure, = await service.read_failed_records()
        record = await service.lookup_legacy_failed_original(failure, owner=owner)
        accepted = service.enqueue_legacy_failed_original(failure, record, 'coexist-retry', owner=owner)
        assert (await asyncio.to_thread(terminal, service, accepted.job_id)).state == 'done'
        await service.refresh_library_snapshot()
        assert {item.work_id for item in service.library_snapshot().works} == {'321', '322', '323', '324'}
        assert (await service.read_failed_records()) == (failure,)
        assert fingerprint(db.parent) == before
        assert uris and all('immutable=1' in uri for uri in uris)
    finally:
        for pin in (initial, current, history):
            if pin is not None:
                pin.close()
        service.close()
    restart_reader = Reader()
    reopened = ApplicationServices(library_readonly_db=db, library_image_root=images,
        live_client=restart_reader, pixiv_download_root=managed)
    try:
        assert not restart_reader.calls
        assert {item.work_id for item in reopened.library_snapshot().works} == {'321', '322', '323', '324'}
        assert len(reopened.static_jobs.snapshot()) == 3
        assert fingerprint(db.parent) == before
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_missing_optional_old_schema_keeps_library_and_marks_diff_unknown(tmp_path):
    db, images, managed = fixture(tmp_path, failures=False)
    before = fingerprint(db.parent)
    service = ApplicationServices(library_readonly_db=db, library_image_root=images,
        live_client=Reader(), pixiv_download_root=managed)
    try:
        assert {item.work_id for item in service.library_snapshot().works} == {'321', '324'}
        assert not service.legacy_failed_retry_available and service.legacy_author_bridge is None
        assert service.legacy_source_warnings
        preview = await service.lookup_managed_author('91', owner='a' * 32)
        assert dict((work['id'], state) for work, state in preview)['321'] is Difference.UNKNOWN
        with pytest.raises(ValueError):
            await service.read_failed_records()
        assert fingerprint(db.parent) == before
    finally:
        service.close()


@pytest.mark.asyncio
async def test_changed_frozen_source_cannot_replace_a_published_library(tmp_path):
    db, images, managed = fixture(tmp_path)
    service = ApplicationServices(library_readonly_db=db, library_image_root=images,
        live_client=Reader(), pixiv_download_root=managed)
    pin = service.acquire_library_images()
    previous = service.library_snapshot()
    try:
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE works SET title='Changed synthetic copy' WHERE work_id='321'")
        with pytest.raises(ValueError, match='changed|変更'):
            await service.refresh_library_snapshot()
        assert service.library_snapshot() is previous
        current = service.acquire_library_images()
        try:
            assert current.token == pin.token
        finally:
            current.close()
    finally:
        pin.close()
        service.close()


@pytest.mark.parametrize('inside', ['db', 'images', 'wal'])
def test_coexistence_keeps_source_fences_before_creating_managed_storage(tmp_path, inside):
    db, images, managed = fixture(tmp_path)
    if inside == 'db':
        managed = db.parent / 'managed'
    elif inside == 'images':
        managed = images / 'managed'
    else:
        Path(str(db) + '-wal').write_bytes(b'synthetic active sidecar')
    before = fingerprint(db.parent)
    with pytest.raises(ValueError):
        ApplicationServices(library_readonly_db=db, library_image_root=images,
            live_client=Reader(), pixiv_download_root=managed)
    assert not managed.exists() and fingerprint(db.parent) == before
