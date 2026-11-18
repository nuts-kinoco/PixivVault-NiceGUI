from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PIL import Image

from pixivvault_nicegui.composite_library import CompositeLocalCatalog, compose_library
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog, MediaMissing
from pixivvault_nicegui.models import Artwork


def png(color):
    output = BytesIO()
    Image.new('RGB', (12, 12), color).save(output, format='PNG')
    return output.getvalue()


def snap():
    work = LibraryWork(Artwork('novel:101', 'Novel', 'Author'), '12', work_type='novel')
    return LibrarySnapshot((work,))


def source(tmp_path):
    folder = tmp_path / 'Author(12)' / 'Novel(101)'
    folder.mkdir(parents=True)
    (folder / 'cover_101.png').write_bytes(png('red'))
    return folder


def rgb(blob):
    with Image.open(BytesIO(blob.data)) as image:
        return image.convert('RGB').getpixel((0, 0))


def test_novel_local_cover_is_thumb_only_and_never_a_saved_viewer_image(tmp_path):
    source(tmp_path)
    local = ReadonlyLocalMediaCatalog(tmp_path, snap())
    try:
        assert rgb(local.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        for variant in ('view', 'original'):
            with pytest.raises(MediaMissing):
                local.get('novel:101', 0, variant)
        assert not local.supports_original('novel:101')
    finally:
        local.close()


def test_covers_do_not_accept_other_author_zip_or_illustrations(tmp_path):
    other = tmp_path / 'Other(99)'
    other.mkdir()
    (other / 'cover_101.png').write_bytes(png('red'))
    folder = tmp_path / 'Author(12)' / 'images'
    folder.mkdir(parents=True)
    (folder / '201.png').write_bytes(png('blue'))
    with ZipFile(tmp_path / 'Author(12).zip', 'w') as archive:
        archive.writestr('cover_101.png', png('green'))
    local = ReadonlyLocalMediaCatalog(tmp_path, snap())
    try:
        with pytest.raises(MediaMissing):
            local.get('novel:101', 0, 'thumb')
    finally:
        local.close()


class Demo:
    works = {}
    def url(self, *args):
        raise ValueError('no demo')
    def get(self, *args):
        raise ValueError('no demo')
    def close(self):
        pass


class Remote(Demo):
    def __init__(self, thumbnail):
        self.works = {'novel:101': SimpleNamespace(thumbnail=thumbnail)}
    def url(self, *args):
        return '/remote'
    def get(self, *args):
        return 'remote-preview'


def test_registry_composite_uses_local_cover_but_keeps_remote_preview(tmp_path):
    source(tmp_path)
    old = snap()
    empty = LibrarySnapshot(())
    composition = compose_library(old, empty, (), (), managed_source_pages={}, managed_original_page_counts={})
    composite = CompositeLocalCatalog(composition, legacy_catalog=ReadonlyLocalMediaCatalog(tmp_path, old))
    registry = LibraryImageRegistry(Demo())
    registry.publish(composite, None)
    pin = registry.acquire()
    try:
        assert '?catalog=' in pin.url('novel:101', 0, 'thumb')
        assert rgb(pin.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        with pytest.raises(ValueError):
            pin.get('novel:101', 0, 'view')
    finally:
        pin.close()
        registry.close()

    composite = CompositeLocalCatalog(composition, legacy_catalog=ReadonlyLocalMediaCatalog(tmp_path, old))
    registry = LibraryImageRegistry(Demo())
    registry.publish(composite, Remote('https://example.invalid/thumb'))
    pin = registry.acquire()
    try:
        assert rgb(pin.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        # Composite selection previously retained the selected legacy leaf
        # for viewer requests; a cover must not change that behavior.
        with pytest.raises(MediaMissing):
            pin.get('novel:101', 0, 'view')
    finally:
        pin.close()
        registry.close()

    registry = LibraryImageRegistry(Demo())
    registry.publish(ReadonlyLocalMediaCatalog(tmp_path, old), Remote('https://example.invalid/thumb'))
    pin = registry.acquire()
    try:
        assert rgb(pin.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        assert pin.get('novel:101', 0, 'view') == 'remote-preview'
    finally:
        pin.close()
        registry.close()


def test_repository_detects_original_thumbnail_metadata_without_retaining_url(tmp_path):
    import json
    import sqlite3
    from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
    db = tmp_path / 'synthetic.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, tags TEXT, user_id TEXT, is_deleted INTEGER, content_type TEXT, work_meta TEXT)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        for number, meta in [('101', {'urls': {'small': 'synthetic'}}),
                             ('102', {'url': 'synthetic'}), ('103', {})]:
            conn.execute('INSERT INTO works VALUES (?, ?, 1, ?, ?, 0, ?, ?)',
                         (number, 'Novel', '[]', '12', 'novel', json.dumps(meta)))
    repo = ReadonlyLibraryRepository(db)
    try:
        works = repo.library_snapshot().works
        assert [w.has_thumbnail_url for w in works] == [True, True, False]
        assert 'synthetic' not in repr(works)
    finally:
        repo.close()


def test_local_cover_is_available_for_detail_even_with_legacy_thumbnail(tmp_path):
    from dataclasses import replace
    source(tmp_path)
    with_url = LibrarySnapshot((replace(snap().works[0], has_thumbnail_url=True),))
    catalog = ReadonlyLocalMediaCatalog(tmp_path, with_url)
    try:
        assert catalog.supports_novel_cover('novel:101')
        assert rgb(catalog.get('novel:101', 0, 'cover')) == (255, 0, 0)
    finally:
        catalog.close()


@pytest.mark.asyncio
async def test_author_list_and_detail_render_same_pinned_cover(tmp_path):
    from nicegui import Client, ui
    from pixivvault_nicegui.services import ApplicationServices
    from pixivvault_nicegui.ui.library_view import LibraryView
    source(tmp_path)
    services = ApplicationServices(120)
    services.library = snap()
    services.images.publish(ReadonlyLocalMediaCatalog(tmp_path, services.library), None)
    client = Client(ui.page('/_ac_novel_cover'))
    def walk(element):
        yield element
        for child in element.default_slot.children:
            yield from walk(child)
    try:
        with client:
            view = LibraryView(services)
            author = view.author_cards['12']
            author_sources = [e.props.get('src') for e in walk(author) if e.props.get('src')]
            view.open_author('12')
            detail = view.page_cards[('novel:101', 0)]
            detail_sources = [e.props.get('src') for e in walk(detail) if e.props.get('src')]
            assert len(author_sources) == len(detail_sources) == 1
            assert '/cover.png?catalog=' in detail_sources[0]
            assert '/media/novel:101/0/thumb.png?catalog=' in author_sources[0]
            assert rgb(view._image_provider.get('novel:101', 0, 'thumb')) == (255, 0, 0)
            view.close()
    finally:
        client.delete()
        services.close()


def test_quarantine_catalog_keeps_cover_subject_to_exclusion_checks(tmp_path):
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantineCatalog
    source(tmp_path)
    snapshot = snap()
    state = {'exclusions': {}}
    checks = []
    store = SimpleNamespace(catalog=SimpleNamespace(root=tmp_path),
                            state=lambda **kw: state,
                            require_available=lambda wid, page: checks.append((wid, page)))
    leaf = LegacyQuarantineCatalog(store, snapshot, snapshot, {'novel:101': (0,)}, state)
    composite = CompositeLocalCatalog(compose_library(snapshot, LibrarySnapshot(()), (), (),
        managed_source_pages={}, managed_original_page_counts={}), legacy_catalog=leaf)
    registry = LibraryImageRegistry(Demo())
    registry.publish(composite, None)
    pin = registry.acquire()
    try:
        assert rgb(pin.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        assert checks
        state['exclusions']['novel:101:1'] = True
        with pytest.raises(MediaMissing):
            pin.get('novel:101', 0, 'thumb')
    finally:
        pin.close()
        registry.close()

    state['exclusions'].clear()
    leaf = LegacyQuarantineCatalog(store, snapshot, snapshot, {'novel:101': (0,)}, state)
    composite = CompositeLocalCatalog(compose_library(snapshot, LibrarySnapshot(()), (), (),
        managed_source_pages={}, managed_original_page_counts={}), legacy_catalog=leaf)
    registry = LibraryImageRegistry(Demo())
    registry.publish(composite, Remote('synthetic'))
    pin = registry.acquire()
    try:
        assert rgb(pin.get('novel:101', 0, 'thumb')) == (255, 0, 0)
    finally:
        pin.close()
        registry.close()


@pytest.mark.parametrize('composite', [False, True])
@pytest.mark.parametrize('has_url', [False, True])
def test_cover_and_author_thumbnail_resolve_different_sources_when_needed(tmp_path, composite, has_url):
    from dataclasses import replace
    source(tmp_path)
    snapshot = LibrarySnapshot((replace(snap().works[0], has_thumbnail_url=has_url),))
    local = ReadonlyLocalMediaCatalog(tmp_path, snapshot)
    if composite:
        composition = compose_library(snapshot, LibrarySnapshot(()), (), (),
            managed_source_pages={}, managed_original_page_counts={})
        local = CompositeLocalCatalog(composition, legacy_catalog=local)
    registry = LibraryImageRegistry(Demo())
    registry.publish(local, Remote('synthetic'))
    pin = registry.acquire()
    try:
        summary = pin.get('novel:101', 0, 'thumb')
        assert (summary == 'remote-preview') == has_url
        if not has_url:
            assert rgb(summary) == (255, 0, 0)
        assert rgb(pin.get('novel:101', 0, 'cover')) == (255, 0, 0)
    finally:
        pin.close()
        registry.close()


def test_detail_cover_http_uses_pinned_catalog(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pixivvault_nicegui.media_routes import register_media_routes
    source(tmp_path)
    registry = LibraryImageRegistry(Demo())
    registry.publish(ReadonlyLocalMediaCatalog(tmp_path, snap()), None)
    pin = registry.acquire()
    try:
        app = FastAPI()
        register_media_routes(app, registry)
        with TestClient(app) as client:
            response = client.get(pin.url('novel:101', 0, 'cover'))
        assert response.status_code == 200
        assert Image.open(BytesIO(response.content)).convert('RGB').getpixel((0, 0)) == (255, 0, 0)
    finally:
        pin.close()
        registry.close()


def test_legacy_preview_disabled_or_stopped_never_calls_transport(tmp_path):
    import json
    import sqlite3
    from pixivvault_nicegui.services import ApplicationServices
    from test_managed_download_services import FakeSource
    images = tmp_path / 'images'
    source(images)
    db = tmp_path / 'old.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, tags TEXT, user_id TEXT, is_deleted INTEGER, content_type TEXT, work_meta TEXT)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        conn.execute('INSERT INTO works VALUES (?, ?, 1, ?, ?, 0, ?, ?)',
                     ('101', 'Novel', '[]', '12', 'novel', json.dumps({'url': 'https://i.pximg.net/synthetic/101.png'})))
    reader = FakeSource()
    svc = ApplicationServices(live_client=reader, library_image_root=images, library_readonly_db=db)
    class Prefs:
        enabled = '0'
        def get_setting(self, key, default):
            return self.enabled if key == 'legacy_novel_thumbnail_auto' else default
    prefs = Prefs()
    svc.preferences = prefs
    pin = svc.acquire_library_images()
    try:
        with pytest.raises(RuntimeError):
            pin.get('novel:101', 0, 'thumb')
        assert reader.calls == []
        prefs.enabled = '1'
        def stopped(_):
            raise RuntimeError('stopped')
        reader.get_image_bytes = stopped
        with pytest.raises(RuntimeError):
            pin.get('novel:101', 0, 'thumb')
        assert reader.calls == []
    finally:
        pin.close()
        svc.preferences = None
        svc.close()


def test_preview_uses_production_stop_gate_before_any_http(tmp_path):
    from pixivvault_nicegui.managed_network_stop_gate import ManagedNetworkStopGate
    from pixivvault_nicegui.remote_media import LegacyNovelPreviewImages
    from test_managed_network_stop_transport import _client
    gate = ManagedNetworkStopGate(tmp_path / 'gate.sqlite')
    requests = []
    def transport(*_args):
        requests.append('called')
        return png('red')
    reader = _client(gate, transport)
    reader._cookies = SimpleNamespace(clear=lambda: None)
    preview = LegacyNovelPreviewImages(reader, {'novel:101': 'https://i.pximg.net/synthetic/101.png'}, lambda: True)
    try:
        gate.trip(403)
        with pytest.raises(RuntimeError):
            preview.get('novel:101', 0, 'thumb')
        assert requests == []
    finally:
        preview.close()
        reader.close()


def test_quarantined_preview_rechecks_exclusion_before_remote_get(tmp_path):
    from dataclasses import replace
    from pixivvault_nicegui.legacy_quarantine import LegacyQuarantineCatalog
    source(tmp_path)
    snapshot = LibrarySnapshot((replace(snap().works[0], has_thumbnail_url=True),))
    state = {'exclusions': {}}
    store = SimpleNamespace(catalog=SimpleNamespace(root=tmp_path),
        state=lambda **kw: state, require_available=lambda *_: None)
    leaf = LegacyQuarantineCatalog(store, snapshot, snapshot, {'novel:101': (0,)}, state)
    composition = compose_library(snapshot, LibrarySnapshot(()), (), (),
        managed_source_pages={}, managed_original_page_counts={})
    registry = LibraryImageRegistry(Demo())
    registry.publish(CompositeLocalCatalog(composition, legacy_catalog=leaf), Remote('synthetic'))
    pin = registry.acquire()
    try:
        assert pin.get('novel:101', 0, 'thumb') == 'remote-preview'
        state['exclusions']['novel:101:1'] = True
        with pytest.raises(MediaMissing):
            pin.get('novel:101', 0, 'thumb')
        with pytest.raises(MediaMissing):
            pin.get('novel:101', 0, 'cover')
    finally:
        pin.close()
        registry.close()


def test_legacy_preview_pins_original_url_across_republication(tmp_path):
    from dataclasses import replace
    from pixivvault_nicegui.remote_media import LegacyNovelPreviewImages
    source(tmp_path)
    snapshot = LibrarySnapshot((replace(snap().works[0], has_thumbnail_url=True),))
    calls = []
    class Reader:
        def get_image_bytes(self, url):
            calls.append(url)
            return png('red')
    reader = Reader()
    original = 'https://i.pximg.net/synthetic/original.png'
    registry = LibraryImageRegistry(Demo())
    first = LegacyNovelPreviewImages(reader, {'novel:101': original}, lambda: True)
    registry.publish(ReadonlyLocalMediaCatalog(tmp_path, snapshot), Remote('other'), first)
    old = registry.acquire()
    cloned = registry.clone_legacy_preview(reader)
    registry.publish(ReadonlyLocalMediaCatalog(tmp_path, snapshot), Remote('other'), cloned)
    new = registry.acquire()
    try:
        assert rgb(old.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        assert rgb(new.get('novel:101', 0, 'thumb')) == (255, 0, 0)
        assert calls == [original, original]
        assert original not in repr(snapshot)
        assert original not in repr(registry)
    finally:
        old.close()
        new.close()
        registry.close()
    assert first._urls == {} and cloned._urls == {}


def test_rejected_preview_url_never_enters_image_snapshot(tmp_path):
    import json
    import sqlite3
    from pixivvault_nicegui.library_repository import ReadonlyLibraryRepository
    db = tmp_path / 'old.sqlite'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE works (work_id TEXT, title TEXT, page_count INTEGER, tags TEXT, user_id TEXT, is_deleted INTEGER, content_type TEXT, work_meta TEXT)')
        conn.execute('CREATE TABLE following_users (user_id TEXT, name TEXT)')
        conn.execute('INSERT INTO works VALUES (?, ?, 1, ?, ?, 0, ?, ?)',
                     ('101', 'Novel', '[]', '12', 'novel', json.dumps({'url': 'https://example.invalid/secret'})))
    repo = ReadonlyLibraryRepository(db)
    try:
        snapshot, previews = repo.library_snapshot_with_previews()
        assert snapshot.works[0].has_thumbnail_url
        assert previews == {}
    finally:
        repo.close()
