from types import SimpleNamespace
from pixivvault_nicegui.discovery_learning import DiscoveryLearningService
from z_learning_support_20261118 import FakeReader


def test_seed_is_checked_against_current_readable_image_on_every_query(tmp_path):
    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data): return ('model', [(data.decode(), .9)])
    current = [b'first']
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=lambda _: current[0], saved_works=lambda: (SimpleNamespace(work_id='1', tags=()),), saved_ids=lambda: ('1',))
    try:
        service.refresh_saved_seeds().result(10)
        def rows(): return service.local_tag_view.snapshot().seed_counts
        assert 'first' in rows()
        current[0] = None
        assert rows() == {}
        service.refresh_saved_seeds().result(10)
        assert rows() == {}
        current[0] = b'second'
        assert rows() == {}
        service.refresh_saved_seeds().result(10)
        assert 'second' in rows() and 'first' not in rows()
    finally:
        service.close()


def test_local_catalog_does_not_use_cached_bytes_after_read_access_is_lost(tmp_path, monkeypatch):
    from pathlib import Path
    from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
    from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
    from pixivvault_nicegui.media import DemoImageCatalog
    from test_local_media_adapter import snapshot
    from test_ak_tagger_inference_20261119 import png
    folder = tmp_path / 'author(12)'
    folder.mkdir()
    saved = snapshot(pages=1)
    image = folder / f'{saved.works[0].artwork.title}(101)_1.png'
    image.write_bytes(png())
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    registry.publish(ReadonlyLocalMediaCatalog(tmp_path, saved), None)
    try:
        assert registry.local_bytes('101')
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == image:
                raise PermissionError('fixture denied')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', denied)
        assert registry.local_bytes('101') is None
    finally:
        registry.close()


def test_managed_catalog_does_not_use_cached_bytes_after_read_access_is_lost(monkeypatch):
    from test_managed_download_catalog import FakeWriter
    from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    from pixivvault_nicegui.media import DemoImageCatalog
    writer = FakeWriter()
    _, catalog, _ = build_managed_library(writer)
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    registry.publish(catalog, None)
    try:
        assert registry.local_bytes('1')
        monkeypatch.setattr(writer, 'read_saved_page', lambda *a: (_ for _ in ()).throw(PermissionError('fixture denied')))
        assert registry.local_bytes('1') is None
    finally:
        registry.close()


def test_seed_is_not_saved_if_image_changes_during_inference(tmp_path):
    current = [b'before']
    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data):
            current[0] = b'after'
            return ('model', [('old', .9)])
    service = DiscoveryLearningService(tmp_path / 'discovery', FakeReader(), tagger=Tagger(),
        local_image=lambda _: current[0], saved_works=lambda: (SimpleNamespace(work_id='1', tags=()),), saved_ids=lambda: ('1',))
    try:
        service.refresh_saved_seeds().result(10)
        assert not service.local_tag_view.snapshot().by_work
        assert service._submit(lambda: service._database().conn.execute("SELECT 1 FROM sqlite_master WHERE name='managed_local_seed'").fetchone()).result(10) is None
    finally:
        service.close()
