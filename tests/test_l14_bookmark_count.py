"""L14 connected synthetic paths, offline; Native/official acceptance is separate."""
import asyncio
from dataclasses import replace
import hashlib
import json
import sqlite3
from threading import RLock
from types import SimpleNamespace

from nicegui import Client as UIClient, ui
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import parse_bookmark
from pixivvault_nicegui.bookmark_state import BookmarkState
from pixivvault_nicegui.library_filters import LibraryFilters
from pixivvault_nicegui.library_models import LibrarySnapshot
from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
from pixivvault_nicegui.job_service import JobService
from pixivvault_nicegui.managed_download_catalog import ManagedDownloadCatalog, build_managed_library
from pixivvault_nicegui.managed_download_requests import (
    ManagedAuthorIntent, ManagedDownloadRequests, ManagedRequestError, accepted_author_fingerprint,
)
from pixivvault_nicegui.managed_downloads import ManagedDownloadError, ManagedPixivDownloads
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.pixiv_read_client import LivePixivReadClient
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.bookmark_view import BookmarkView
from pixivvault_nicegui.ui.library_view import LibraryView
from test_composite_library import compose, work
from test_f37_library_bulk_download import DetailSource
from test_managed_downloads import Client, Context, Job, record
from test_managed_download_services import terminal
from tools.check_managed_download_native import verify_storage

MISSING = object()


def detail(value=MISSING):
    reader = object.__new__(LivePixivReadClient)
    reader._closed = False
    reader._verified_identity = '99'
    reader._identity_lock = RLock()
    body = {'id': '123', 'userId': '456', 'illustType': 0, 'pageCount': 2,
            'title': '合成作品', 'userName': '合成作者', 'tags': {'tags': []},
            'urls': {}, 'createDate': '2026-11-16'}
    if value is not MISSING:
        body['bookmarkCount'] = value
    calls = []

    def response(kind, url, *, deadline):
        calls.append((kind, url))
        return body

    reader._json = response
    dto = reader.get_artwork_download_record('123')
    assert calls == [('artwork', 'https://www.pixiv.net/ajax/illust/123')]
    return dto


@pytest.mark.parametrize('value,expected', [
    (MISSING, None), (None, None), (0, 0), (1200, 1200), ('1200', 1200),
    (-1, None), (True, None), (False, None), (1.5, None), ('', None),
    ('-1', None), ('1.2', None), ('unknown', None), ([], None), ({}, None),
])
def test_detail_dto_new_receipt_all_readers_and_restart(tmp_path, value, expected):
    dto = detail(value)
    assert dto.bookmark_count == expected
    assert dto.as_artwork().bookmark_count == expected
    client = Client()
    root = tmp_path / 'managed'
    owner = ManagedPixivDownloads(root, client, resolve_record=lambda wid: dto)
    requests = ManagedDownloadRequests(root)
    requests.store('l14', dto)
    jobs = JobService(owner.run, owner.storage_path)
    try:
        job = jobs.enqueue('pixiv-illust', '123', dto.title, operation_id='l14')
        relative = terminal(jobs, job.job_id).result
        jobs.close()
        receipt_path = root / relative
        before = receipt_path.read_bytes()
        receipt = json.loads(before)
        if expected is None:
            assert 'bookmark_count' not in receipt
        else:
            assert receipt['bookmark_count'] == expected
        digest = hashlib.sha256(before).hexdigest()
        # Owner, generation index, catalog, image reader and native verifier.
        assert owner.verified_saved_receipt('123')[2] == digest
        assert owner.saved_receipts_with_dates()[0][1] == receipt
        assert owner.catalog_snapshot().receipts[0][1] == receipt
        snapshot, catalog, _ = build_managed_library(owner)
        assert snapshot.works[0].artwork.bookmark_count == expected
        assert catalog.get('123', 1, 'original').data == client.payload
        catalog.close()
        assert verify_storage(root)['receipts'] == 1
        assert owner.run(Job(), Context()) == relative  # reuse, no rewrite
        assert client.count == 2
        assert receipt_path.read_bytes() == before
    finally:
        jobs.close()
        owner.close()
    restarted = ManagedPixivDownloads(root, client, resolve_record=lambda wid: dto)
    try:
        snapshot, catalog, _ = build_managed_library(restarted)
        assert snapshot.works[0].artwork.bookmark_count == expected
        assert (root / relative).read_bytes() == before
        catalog.close()
    finally:
        restarted.close()


@pytest.mark.parametrize('bad', [-1, True, False, 1.5, '12', [], {}])
def test_optional_receipt_count_rejected_consistently(tmp_path, bad):
    owner = ManagedPixivDownloads(tmp_path / 'managed', Client(), resolve_record=lambda wid: record())
    try:
        relative = owner.run(Job(), Context())
        path = owner.root / relative
        receipt = json.loads(path.read_bytes())
        receipt['bookmark_count'] = bad
        raw = json.dumps(receipt).encode()
        path.write_bytes(raw)  # generated fixture corruption only
        digest = hashlib.sha256(raw).hexdigest()
        with pytest.raises(ManagedDownloadError):
            owner._receipt(relative)
        with pytest.raises(ManagedDownloadError):
            owner._generations._receipt_entry(relative, '123', update_date='')
        with pytest.raises(ValueError):
            ManagedDownloadCatalog(None, ((relative, receipt, digest),))
        with pytest.raises(ValueError):
            verify_storage(owner.root)
    finally:
        owner.close()


@pytest.mark.parametrize('count', [None, 0, 2200])
@pytest.mark.parametrize('author_intent', [False, True])
def test_durable_intents_keep_count_and_old_fingerprint(tmp_path, count, author_intent):
    owner = ManagedPixivDownloads(tmp_path / 'managed', Client(), resolve_record=lambda wid: record())
    requests = ManagedDownloadRequests(owner.root)
    dto = replace(record(), bookmark_count=count)
    intent = ManagedAuthorIntent(dto, 'id', '', None, 'new') if author_intent else dto
    try:
        requests.store('accepted', intent)
        restored = requests.load('accepted')
        assert restored == intent
        raw_before = requests._path('accepted').read_bytes()
        assert ('bookmark_count' in json.loads(raw_before).get('record', json.loads(raw_before))) == (count is not None)
        requests.store('accepted', restored)
        assert requests._path('accepted').read_bytes() == raw_before
        if count is None and author_intent:
            payload = json.dumps(json.loads(raw_before), sort_keys=True, ensure_ascii=True,
                                 separators=(',', ':')).encode('ascii')
            expected = hashlib.sha256(('a' * 32).encode() + b'\0' + payload).hexdigest()
            assert accepted_author_fingerprint(restored, 'a' * 32) == expected
        with pytest.raises(ManagedRequestError):
            requests.store('bad', replace(dto, bookmark_count=True))
    finally:
        owner.close()


@pytest.mark.parametrize('legacy,managed,expected', [
    (1500, None, 1500), (1500, 2200, 2200), (1500, 0, 0),
    (0, None, 0), (None, None, None), (None, 500, 500),
])
def test_composition_preserves_precedence_and_filter(legacy, managed, expected):
    prior = work('123')
    prior = replace(prior, artwork=replace(prior.artwork, bookmark_count=legacy))
    saved = replace(prior, artwork=replace(prior.artwork, title='managed', bookmark_count=managed))
    result = compose(LibrarySnapshot((prior,)), LibrarySnapshot((saved,)))
    assert result.snapshot.works[0].artwork.bookmark_count == expected
    assert result.local_bookmarks[0].bookmark_count == expected
    assert result.snapshot.works[0].artwork.title == 'managed'
    assert LibraryFilters().matches_artwork(result.local_bookmarks[0])
    assert LibraryFilters(bookmark_min=1000).matches_artwork(result.local_bookmarks[0]) == (
        expected is not None and expected >= 1000)
    assert prior.artwork.bookmark_count == legacy
    assert saved.artwork.bookmark_count == managed


def test_enrichment_membership_downloaded_namespaces_and_filter_boundaries():
    remote = tuple(Artwork(wid, wid, 'remote') for wid in ('1', '2', '3', '4', 'novel:1'))
    known = (replace(remote[0], bookmark_count=500), replace(remote[1], bookmark_count=499),
             replace(remote[2], bookmark_count=0), Artwork('extra', 'extra', 'local', bookmark_count=999))
    state = BookmarkState(local=(remote[1],), cached_remote=remote, known_artworks=known)
    assert [c.work_id for c in state.items] == [a.work_id for a in remote]
    assert [c.downloaded for c in state.items] == [False, True, False, False, False]
    assert [c.artwork.bookmark_count for c in state.items] == [500, 499, 0, None, None]
    assert len(state.matched_items) == 5
    state.set_filters(LibraryFilters(bookmark_min=500))
    assert [c.work_id for c in state.matched_items] == ['1']
    state.set_filters(LibraryFilters(bookmark_min=1))
    assert [c.work_id for c in state.matched_items] == ['1', '2']
    epoch = state.begin_fetch()
    assert state.complete_fetch(epoch, remote, ok=True)
    assert state.items[0].artwork.bookmark_count == 500
    state.replace_known((replace(remote[0], bookmark_count=0),))
    assert state.items[0].artwork.bookmark_count == 0
    assert state.items[-1].artwork.bookmark_count is None
    # A known remote measurement survives local projection with unknown count.
    state = BookmarkState(local=(remote[0],), cached_remote=(replace(remote[0], bookmark_count=8),))
    assert state.items[0].artwork.bookmark_count == 8


@pytest.mark.parametrize('count', [None, 0, 1200])
def test_parse_cache_projection_keeps_known_value(count):
    dto = parse_bookmark({'id': '123', 'title': 'cached', 'user_id': '456',
        'thumb_url': 'https://i.pximg.net/synthetic/123.png', 'bookmark_count': count},
        kind='illustration', visibility='show')
    assert dto.as_artwork().bookmark_count == count


@pytest.mark.parametrize('count', [None, 0, 1200])
def test_single_viewer_page_receipt_shared_spec_stays_readable(tmp_path, count):
    from pixivvault_nicegui.managed_viewer_page import save_page
    dto = replace(record(), bookmark_count=count)
    client = Client()
    client.get_artwork_original_pages = lambda wid: (
        'https://i.pximg.net/synthetic/123_p0.png', 'https://i.pximg.net/synthetic/123_p1.png')
    owner = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=lambda wid: dto)
    try:
        saved = save_page(owner, '123', 0, 2, '456', lambda: dto)
        path = owner.root / saved.relative_receipt
        before = path.read_bytes()
        assert json.loads(before)['record'].get('bookmark_count') == count
        again = save_page(owner, '123', 0, 2, '456', lambda: pytest.fail('no extra detail read'))
        assert again.reused and again.receipt_sha256 == saved.receipt_sha256
        assert path.read_bytes() == before and client.count == 1
    finally:
        owner.close()


def test_author_generation_receipt_count_reaches_all_readers(tmp_path):
    dto = replace(record(), bookmark_count=1200)
    client = Client()
    client.get_artwork_update_revision = lambda *args: dict(
        author_id='456', page_count=2, illustration_type=0, update_date='2026-11-16')
    owner = ManagedPixivDownloads(tmp_path / 'managed', client, resolve_record=lambda wid: dto)
    intent = ManagedAuthorIntent(dto, 'id', '2026-11-16', None, 'new')
    job = SimpleNamespace(kind='pixiv-illust', target_id='123', job_id='a' * 32)
    try:
        relative = owner.run(job, Context(), intent=intent)
        assert relative.startswith('generations/')
        before = (owner.root / relative).read_bytes()
        assert owner.verified_saved_receipt('123')[1]['bookmark_count'] == 1200
        snapshot, catalog, _ = build_managed_library(owner)
        assert snapshot.works[0].artwork.bookmark_count == 1200
        assert catalog.get('123', 0, 'original').data == client.payload
        catalog.close()
        assert owner.run(job, Context(), intent=intent) == relative
        assert (owner.root / relative).read_bytes() == before and client.count == 2
    finally:
        owner.close()


@pytest.mark.parametrize('kind', ['illust', 'novel', None])
def test_legacy_cache_join_is_kind_scoped_readonly(tmp_path, kind):
    db = tmp_path / 'synthetic.sqlite'
    conn = sqlite3.connect(db)
    type_column = ',content_type TEXT' if kind is not None else ''
    conn.executescript('CREATE TABLE following_users(user_id TEXT,name TEXT);'
        'CREATE TABLE works(work_id TEXT,title TEXT,page_count INTEGER,user_id TEXT,'
        'tags TEXT,is_deleted INTEGER,is_bookmarked INTEGER,bookmark_count INTEGER' + type_column + ');'
        'CREATE TABLE bookmark_remote_cache(work_id TEXT,is_novel INTEGER,title TEXT,user_id TEXT,'
        'user_name TEXT,tags TEXT,create_date TEXT,thumb_url TEXT,rest_type TEXT,x_restrict INTEGER);')
    values = ['123', 'local', 1, '456', '[]', 0, 0, 1200] + ([kind] if kind is not None else [])
    conn.execute('INSERT INTO works VALUES (' + ','.join('?' for _ in values) + ')', values)
    for novel in (0, 1):
        conn.execute('INSERT INTO bookmark_remote_cache VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('123', novel, 'cached', '456', 'author', '[]', '',
             'https://i.pximg.net/synthetic/123.png', 'show', 0))
    conn.commit()
    conn.close()
    before = db.read_bytes()
    with ReadonlyLibraryRepository(db, immutable=True) as repository:
        records, local = repository.bookmark_bundle()
        assert not local  # is_bookmarked=0 never turns into a local bookmark
        assert [r.bookmark_count for r in records] == (
            [1200, None] if kind == 'illust' else [None, 1200] if kind == 'novel' else [None, None])
    assert db.read_bytes() == before


def labels(element, testid):
    return [node.text for node in element.descendants()
            if node.props.get('data-testid') == testid]


@pytest.mark.asyncio
async def test_actual_cards_known_zero_unknown_and_refresh():
    remote = tuple(Artwork(str(i), str(i), 'author') for i in range(3))
    state = BookmarkState(cached_remote=remote, known_artworks=(
        replace(remote[0], bookmark_count=1200), replace(remote[1], bookmark_count=0)))
    client = UIClient(ui.page('/_l14_bookmark'))
    try:
        with client:
            view = BookmarkView(state)
            view.render()
            assert labels(view.cards['0'], 'bookmark-card-count') == ['★ 1,200']
            assert labels(view.cards['1'], 'bookmark-card-count') == ['★ 0']
            assert labels(view.cards['2'], 'bookmark-card-count') == []
            state.replace_known((replace(remote[0], bookmark_count=500),))
            view.refresh()
            assert labels(view.cards['0'], 'bookmark-card-count') == ['★ 500']
            assert labels(view.cards['1'], 'bookmark-card-count') == []
            view.close()
    finally:
        client.delete()


@pytest.mark.asyncio
async def test_library_page_cards_zero_and_unknown_and_no_author_aggregate():
    services = ApplicationServices(120)
    originals = services.library_snapshot().works[:3]
    aid = originals[0].author_id
    snapshot = LibrarySnapshot(tuple(replace(work, author_id=aid,
        artwork=replace(work.artwork, bookmark_count=count))
        for work, count in zip(originals, (1200, 0, None))))
    services.library = snapshot
    client = UIClient(ui.page('/_l14_page_counts'))
    try:
        with client:
            view = LibraryView(services)
            assert not any('最大 ★' in getattr(node, 'text', '')
                           for node in view.author_cards[aid].descendants())
            view.open_author(aid)
            assert labels(view.page_cards[(originals[0].work_id, 0)], 'library-page-bookmark-count') == ['★ 1,200']
            assert labels(view.page_cards[(originals[1].work_id, 0)], 'library-page-bookmark-count') == ['★ 0']
            assert labels(view.page_cards[(originals[2].work_id, 0)], 'library-page-bookmark-count') == []
            view.close()
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_connected_detail_job_restart_library_cards_and_bookmark_refresh(tmp_path):
    source = DetailSource()
    root = tmp_path / 'managed'
    services = ApplicationServices(live_client=source, pixiv_download_root=root)
    client = UIClient(ui.page('/_l14_library'))
    try:
        await services.refresh_bookmark_cache()
        bookmark = services.download_candidates()[0]
        source.detail = replace(bookmark, bookmark_count=1200)
        job = await services.save_explicit_work('202', 'l14-detail-save')
        await asyncio.to_thread(terminal, services.jobs, job.job_id)
        await services.refresh_library_snapshot()
        assert services.library_snapshot().works[0].artwork.bookmark_count == 1200
        relative, _, _ = services.managed_downloads.saved_receipts()[0]
        before = (root / relative).read_bytes()
        with client:
            view = LibraryView(services)
            assert view.bookmark_state.items[0].artwork.bookmark_count == 1200
            assert len(view.bookmark_state.items) == 2  # no invented membership
            epoch = view.bookmark_state.begin_fetch()
            await view._refresh_bookmarks_async(epoch)
            assert view.bookmark_state.items[0].artwork.bookmark_count == 1200
            view.open_author('23')
            assert labels(view.page_cards[('202', 0)], 'library-page-bookmark-count') == ['★ 1,200']
            await view.refresh_from_disk()
            assert view.bookmark_state.items[0].artwork.bookmark_count == 1200
            view.close()
        assert (root / relative).read_bytes() == before
    finally:
        client.delete()
        await asyncio.to_thread(services.close)
    restarted = ApplicationServices(live_client=DetailSource(), pixiv_download_root=root)
    try:
        assert restarted.library_snapshot().works[0].artwork.bookmark_count == 1200
        assert (root / relative).read_bytes() == before
    finally:
        await asyncio.to_thread(restarted.close)
