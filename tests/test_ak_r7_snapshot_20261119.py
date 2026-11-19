from types import SimpleNamespace
from pixivvault_nicegui.local_tag_view import LocalTagView


def test_snapshot_is_cached_until_explicit_refresh():
    class Tagger:
        fp = 'model'
        def current_fingerprint(self): return self.fp
        def try_current_fingerprint(self): return self.fp
        def predict_with_fingerprint(self, data): return self.fp, [('cat', .9)]
    image = [b'image']
    reads = []
    def read(wid):
        reads.append(wid)
        return image[0]
    view = LocalTagView(Tagger(), read, lambda: (SimpleNamespace(work_id='1', tags=()),), lambda: {'1'})
    try:
        view.refresh_now()
        reads.clear()
        image[0] = None
        assert view.current_tags('1') == ('cat',)
        assert view.snapshot().seed_counts['cat'][1] == 1
        assert not reads
        view.refresh_now()
        assert view.current_tags('1') == ()
    finally:
        view.close()


from types import SimpleNamespace
from pixivvault_nicegui.local_tag_view import LocalTagView

def test_ranking_cards_do_not_stat_model_files(tmp_path, monkeypatch):
    from pathlib import Path
    from pixivvault_nicegui.local_tagger import TaggerManager, LocalTagger
    from pixivvault_nicegui.preferences import PreferencesStore
    from pixivvault_nicegui.ui.ranking_view import RankingView
    from test_ak_tagger_20261119 import make_model
    from test_ak_tagger_inference_20261119 import png
    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'preferences.sqlite3')
    store.set_setting('local_tagger_path', str(model))
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]
    tagger = TaggerManager(store)
    tagger.tagger = LocalTagger(model, session_factory=lambda _: Session(), tensor_factory=lambda x: x)
    tagger._provider = 'auto'
    view = LocalTagView(tagger, lambda _: png(), lambda: (SimpleNamespace(work_id='1', tags=()),), lambda: {'1'})
    try:
        view.refresh_now()
        revision = view.revision()
        ranking = SimpleNamespace(services=SimpleNamespace(local_tag_view=view), _preferred=frozenset({'cat'}),
                                  _preferred_base=frozenset(), _preferred_revision=revision)
        stat_calls = []
        original = Path.stat
        def counted(path, *args, **kwargs):
            stat_calls.append(path)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'stat', counted)
        assert all(RankingView.preferred_tags_in(ranking, ('cat',)) for _ in range(5))
        print('stat calls for five cards:', len(stat_calls))
        assert not stat_calls
    finally:
        view.close()
        tagger.close()
        store.close()


def test_catalog_token_revalidates_without_reading_unchanged_images(tmp_path, monkeypatch):
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
    reads, predictions = [], []

    class Tagger:
        def current_fingerprint(self): return 'model'
        def try_current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data):
            predictions.append(data)
            return 'model', [('cat', .9)]

    def read(wid):
        reads.append(wid)
        return registry.local_bytes(wid)

    view = LocalTagView(Tagger(), read, lambda: saved.works, lambda: {'101'},
                        image_token=lambda wid: registry.local_image_token(wid))
    try:
        view.refresh_now()
        assert view.current_tags('101') == ('cat',)
        assert reads == ['101'] and len(predictions) == 1
        view.refresh_now()
        assert reads == ['101'] and len(predictions) == 1
        image.write_bytes(png() + b'changed-size')
        view.refresh_now()
        assert reads == ['101', '101'] and len(predictions) == 2
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == image:
                raise PermissionError('fixture denied')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', denied)
        view.refresh_now()
        assert view.current_tags('101') == ()
        assert reads == ['101', '101']
    finally:
        view.close()
        registry.close()



def test_managed_page_token_checks_bytes_only_when_the_image_changes(tmp_path, monkeypatch):
    from pixivvault_nicegui import managed_downloads
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    from test_managed_downloads import Context, Job, create

    writer, _ = create(tmp_path)
    writer.run(Job(), Context())
    _, catalog, _ = build_managed_library(writer)
    original = managed_downloads._read_bounded
    image_reads = []

    def counted(path, limit):
        if path.suffix == '.png':
            image_reads.append(path)
        return original(path, limit)

    monkeypatch.setattr(managed_downloads, '_read_bounded', counted)
    try:
        before = catalog.image_token('123')
        assert before is not None and catalog.image_token('123') == before
        assert not image_reads
        assert catalog.get_fresh('123').data
        image, = image_reads
        original_bytes = image.read_bytes()
        image.write_bytes(bytes([0]) + original_bytes[1:])
        assert catalog.image_token('123') != before
        assert image_reads == [image]
        image.unlink()
        import pytest
        from pixivvault_nicegui.local_media_adapter import MediaUnavailable
        with pytest.raises((ValueError, MediaUnavailable)):
            catalog.image_token('123')
        assert image_reads == [image]
    finally:
        catalog.close()
        writer.close()


def test_periodic_scan_expires_published_tags_during_another_image_read(monkeypatch):
    import time
    from threading import Event
    from pixivvault_nicegui import local_tag_view

    monkeypatch.setattr(local_tag_view, 'REFRESH_INTERVAL_SECONDS', .03)
    works = [SimpleNamespace(work_id='1', tags=())]
    images = {'1': b'old'}
    entered, release = Event(), Event()

    class Tagger:
        def current_fingerprint(self): return 'model'
        def predict_with_fingerprint(self, data): return 'model', [('cat', .9)]

    def read(wid):
        if wid == '2':
            entered.set()
            assert release.wait(5)
        return images.get(wid)

    view = LocalTagView(Tagger(), read, lambda: works, lambda: images,
                        image_token=images.get)
    try:
        view.refresh_now()
        assert view.current_tags('1') == ('cat',)
        images['2'] = b'new'
        works.append(SimpleNamespace(work_id='2', tags=()))
        scan = view.refresh_async()
        assert entered.wait(2)
        del images['1']
        deadline = time.monotonic() + 2
        while view.current_tags('1') and time.monotonic() < deadline:
            time.sleep(.005)
        assert not view.current_tags('1')
        assert not scan.done() and not release.is_set()
    finally:
        release.set()
        view.close()


def test_model_invalidation_is_published_before_hashing_changed_model(tmp_path, monkeypatch):
    from threading import Event, Thread
    from pixivvault_nicegui import local_tagger
    from pixivvault_nicegui.preferences import PreferencesStore
    from test_ak_tagger_20261119 import make_model
    from test_ak_tagger_inference_20261119 import png

    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'preferences.sqlite')
    store.set_setting('local_tagger_path', str(model))

    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]

    manager = local_tagger.TaggerManager(store)
    manager.tagger = local_tagger.LocalTagger(model, session_factory=lambda _: Session(),
                                             tensor_factory=lambda value: value)
    manager._provider = 'auto'
    view = LocalTagView(manager, lambda _: png(),
                        lambda: (SimpleNamespace(work_id='1', tags=()),), lambda: ('1',))
    hashing, release = Event(), Event()
    results = []
    worker = Thread(target=lambda: results.append(manager.current_fingerprint()))
    original_hash = local_tagger._sha256

    def hash_after_signal(path):
        hashing.set()
        assert release.wait(5)
        return original_hash(path)

    try:
        view.refresh_now()
        before = view.snapshot()
        assert before.by_work['1'] == ('cat',)
        (model / 'model.onnx').write_bytes(b'changed')
        monkeypatch.setattr(local_tagger, '_sha256', hash_after_signal)
        worker.start()
        assert hashing.wait(3)
        assert view.snapshot().epoch > before.epoch
        assert not view.current_tags('1')
        assert view.merge_preferences({}, before) == {}
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(5)
        view.close()
        manager.close()
        store.close()
    assert results == [None]


def test_mapped_model_drive_expiry(tmp_path, monkeypatch):
    from pixivvault_nicegui import local_tagger
    from pixivvault_nicegui.preferences import PreferencesStore
    from test_ak_tagger_20261119 import make_model
    from test_ak_tagger_inference_20261119 import png

    model = tmp_path / 'model'
    model.mkdir()
    make_model(model)
    store = PreferencesStore(tmp_path / 'preferences.sqlite')
    store.set_setting('local_tagger_path', str(model))

    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input')]
        def run(self, *_): return [[(.9,)]]

    manager = local_tagger.TaggerManager(store)
    manager.tagger = local_tagger.LocalTagger(model, session_factory=lambda _: Session(),
                                             tensor_factory=lambda value: value)
    manager._provider = 'auto'
    view = LocalTagView(manager, lambda _: png(),
                        lambda: (SimpleNamespace(work_id='1', tags=()),), lambda: ('1',))
    try:
        view.refresh_now()
        before = view.snapshot()
        assert before.by_work['1'] == ('cat',)
        drive_type = local_tagger._drive_type
        monkeypatch.setattr(local_tagger, '_drive_type',
                            lambda drive: 4 if drive.casefold() == model.anchor.casefold() else drive_type(drive))
        assert manager.try_current_fingerprint() is None
        assert view.epoch() > before.epoch
        assert not view.current_tags('1')
    finally:
        view.close()
        manager.close()
        store.close()
