"""Recommendation projection keeps the baseline acquisition slate and image demand."""

import asyncio
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from nicegui import Client, core, ui

from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, PublicImageReadClient
from pixivvault_nicegui.recommendation_media import RecommendationMediaSnapshot, RecommendationRemoteImages
from pixivvault_nicegui.recommendation_state import RecommendationController, RecommendationGroup, RecommendationSnapshot, RecommendationWork
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.shared_image_cache import SharedImageCache
from pixivvault_nicegui.visual_preference import AuthorStyle, EmbeddingKey, ModelSnapshot
from pixivvault_nicegui.visual_preference_store import VisualPreferenceStore
from pixivvault_nicegui.visual_preference_worker import VisualPreferenceWorker, _PREPROCESSING_VERSION
from test_gpuarb_remote_media import png
from test_x_ctx_recommend_20261118 import make, open_view


SHA = 'a' * 64


def _work(number):
    return RecommendationWork(str(number), str(number), str(number + 100),
                              thumbnail_url=f'https://i.pximg.net/fixture/{number}.png',
                              page_count=1, illust_type=0)


def _visual(root, enabled):
    from pixivvault_nicegui.recommendation_media import RecommendationVisual

    store = VisualPreferenceStore(root, profile='test-profile', account='test-account', protected_paths=())
    generation = store.advance_generation(model_sha=SHA, preprocessing_version=_PREPROCESSING_VERSION,
                                          judgment_fingerprint='b' * 64)
    authors = tuple(AuthorStyle(str(wid + 100), (0., 1.) if wid == 1 else (1., 0.), 20, 1.)
                    for wid in range(1, 8))
    model = ModelSnapshot(generation, 'centroid', 2, (((1., 0.), 1.),), (((0., 1.), 1.),), 20, 20, authors)
    assert store.publish(generation, model.to_payload())
    worker = VisualPreferenceWorker(store, None, model_sha=SHA)
    # No GPU job is started. Exercise the normal store, scoring and event writer.
    worker._check_running = lambda **_: True
    return RecommendationVisual(worker, enabled=enabled), worker, generation


@pytest.mark.parametrize('scenario', ['cold', 'warm', 'missing', 'evicted', 'after_hold'])
def test_projected_slots_keep_exact_fake_transport_trace(tmp_path, scenario):
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup

    def exercise(on):
        sent = []
        image_bytes = png()

        def transport(request, *_):
            url = request.full_url
            sent.append((request.get_method(), url))
            if scenario == 'missing' and url.endswith('/3.png'):
                raise HTTPStatusRejected(404)
            return image_bytes

        cache = SharedImageCache(max_bytes=len(image_bytes) * 12)
        registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
        reader = PublicImageReadClient(transport=transport)
        visual, worker, _ = _visual(tmp_path / ('on' if on else 'off'), lambda: on)
        works = tuple(_work(n) for n in range(1, 8))
        source = SimpleNamespace(load=lambda: RecommendationSnapshot(
            sections=(('certain', (RecommendationGroup('tag', 'label', works),)),),
            unavailable=()))
        controller = RecommendationController(source)
        group = controller.refresh()['sections']['certain'][0]
        display = RecommendationDisplayGroup('certain', group.key, group.works[:6])
        services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire,
            acquire_recommendation_images=lambda records: registry.acquire_catalog(
                RecommendationRemoteImages(RemoteBookmarkImages(reader, records), registry)))
        media = RecommendationMediaSnapshot(services, display.works)
        try:
            def demand(work):
                try:
                    return media.remote_pin.get(work.work_id, 0, 'thumb')
                except RuntimeError:
                    return None

            if scenario != 'cold':
                for work in display.works:
                    demand(work)
            if scenario == 'evicted':
                cache.configure(len(image_bytes) * 2)
            captured = media.capture_display((display,))
            candidate = visual.prepare(captured)
            slate = visual.project(captured, candidate)
            if scenario == 'after_hold':
                cache.configure(len(image_bytes) * 2)
            # Base slots drive the lazy requests even when foreground tiles move.
            for slot in slate[0].slots:
                demand(slot.base)
                if slot.base != slot.work:
                    assert slot.image is not None and slot.image.data.startswith(b'\x89PNG')
            visual.record(slate)
            worker.flush_events()
            event, = worker.store.events()
            assert event.kind == 'recommendation_exposure'
            assert event.payload['candidates'] == [
                dict(work_id=slot.work.work_id, section='certain', group='tag',
                     base_rank=display.works.index(slot.work) + 1, rank=rank,
                     image_available=slot.image_available, image_variant=slot.image_variant)
                for rank, slot in enumerate(slate[0].slots, 1)
            ]
            assert controller.snapshot()['sections']['certain'][0].works == works
            assert not any(url.endswith('/7.png') for _, url in sent)
            return tuple(slot.work.work_id for slot in slate[0].slots), sent
        finally:
            media.close()
            registry.close()
            cache.close()
            reader.close()

    off, off_calls = exercise(False)
    on, on_calls = exercise(True)
    assert off == tuple('123456')
    assert on_calls == off_calls
    assert on_calls
    if scenario in {'warm', 'after_hold'}:
        assert on[0] == '2' and set(on) == set(off)
    else:
        assert on == off


def _rendered(view):
    return [node for node in view.body.descendants()
            if str(node.props.get('data-testid', '')).startswith('recommend-thumb-certain-')]


def _load_rendered(registry, images):
    for image in images:
        target = urlsplit(image.props['src'])
        _, _, work_id, _, _ = target.path.split('/')
        catalog, = parse_qs(target.query)['catalog']
        registry.get_versioned(catalog, work_id, 0, 'thumb')


def test_composite_local_image_observation_does_not_promote_shared_recency(tmp_path):
    from pixivvault_nicegui.composite_library import CompositeLocalCatalog
    from pixivvault_nicegui.library_models import LibrarySnapshot
    from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_composite_library import compose, work

    saved = work('7', author='107')
    old = LibrarySnapshot((saved,))
    folder = tmp_path / 'Author(107)' / 'Work(7)'
    folder.mkdir(parents=True)
    (folder / '7_p0.png').write_bytes(png())
    leaf = ReadonlyLocalMediaCatalog(tmp_path, old)
    local = CompositeLocalCatalog(compose(old, LibrarySnapshot(())), legacy_catalog=leaf)
    cache = SharedImageCache(max_bytes=50_000)
    registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
    registry.publish(local, None)
    remote = PublicImageReadClient(transport=lambda *_: png())
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire,
        acquire_recommendation_images=lambda records: registry.acquire_catalog(
            RecommendationRemoteImages(RemoteBookmarkImages(remote, records), registry)))
    local_work, remote_work = _work(7), _work(1)
    media = RecommendationMediaSnapshot(services, (local_work, remote_work))
    try:
        local_blob = media.local_pin.get('7', 0, 'thumb')
        remote_blob = media.remote_pin.get('1', 0, 'thumb')
        recency = tuple(cache._entries)
        units = media.capture_display((
            RecommendationDisplayGroup('superlike', 'saved', (local_work,)),
            RecommendationDisplayGroup('certain', 'remote', (remote_work,)),
        ))
        assert [unit.slots[0].image for unit in units] == [local_blob, remote_blob]
        assert [unit.slots[0].image_variant for unit in units] == ['saved_thumb', 'thumb']
        assert tuple(cache._entries) == recency
    finally:
        media.close()
        registry.close()
        remote.close()
        cache.close()


@pytest.mark.asyncio
async def test_view_projected_order_keeps_normal_thumbnail_urls_and_protected_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    enabled = False
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: enabled)
    sent = []

    def transport(request, *_):
        sent.append((request.get_method(), request.full_url))
        return png()

    services = make(tmp_path)
    cache = SharedImageCache(max_bytes=len(png()) * 16)
    services.images.bind_shared_cache(cache)
    services.live_client = PublicImageReadClient(transport=transport)
    services.recommendation_visual = visual
    works = tuple(_work(n) for n in range(1, 8))
    services.recommendations.source.load = lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),
                  ('superlike', (RecommendationGroup('favorites', 'favorites', tuple(reversed(works[:2]))),))),
        unavailable=())
    client = Client(ui.page('/_gpu_recommend_projection'))
    try:
        with client:
            view = await open_view(services)
            original = _rendered(view)
            original_ids = [node.props['data-testid'] for node in original]
            base_urls = [node.props['src'] for node in original]
            assert original_ids == [f'recommend-thumb-certain-{wid}' for wid in '123456']
            _load_rendered(services.images, original)
            assert len(sent) == 6 and all(method == 'GET' for method, _ in sent)

            enabled = True
            assert _rendered(view) == original
            view._saving.add('1')
            await view.refresh()
            assert _rendered(view)[0].props['data-testid'] == original_ids[0]
            view._saving.remove('1')
            await view.refresh()
            projected = _rendered(view)
            assert [node.props['src'] for node in projected] == base_urls
            projected_ids = [node.props['data-testid'] for node in projected]
            assert projected_ids[0].endswith('-2') and set(projected_ids) == set(original_ids)
            assert projected[0].props['img-style'] == {'opacity': 0}
            assert projected[0].style['background-image'].startswith('url(data:image/png;base64,')
            _load_rendered(services.images, projected)
            favorites = [node.props['data-testid'] for node in view.body.descendants()
                         if str(node.props.get('data-testid', '')).startswith('recommend-work-superlike-')]
            assert favorites == ['recommend-work-superlike-2', 'recommend-work-superlike-1']

            await view.open_work('2', generation=view._render_generation)
            selection = view._recommend_viewer.selection
            assert selection.artwork.work_id == '2'
            enabled = False
            cache.configure(len(png()) * 2)
            view.render(preserve_viewer=True)
            restored = _rendered(view)
            assert view._recommend_viewer.selection is selection
            assert [node.props['data-testid'] for node in restored] == [original_ids[1], original_ids[0], *original_ids[2:]]
            assert [node.props['src'] for node in restored] == base_urls
            assert 'background-image' in restored[0].style
            view._recommend_viewer.close_viewer()
            view.render()
            assert [node.props['data-testid'] for node in _rendered(view)] == original_ids
            assert all('background-image' not in node.style for node in _rendered(view))
            assert len(sent) == 6
            worker.flush_events()
            events = worker.store.events()
            assert len(events) == 5
            assert [row['base_rank'] for row in events[2].payload['candidates'] if row['section'] == 'certain'] == [2, 3, 4, 5, 6, 1]
            assert not any(row['image_available'] for row in events[0].payload['candidates'])
    finally:
        client.delete()
        services.close()
        cache.close()


@pytest.mark.parametrize('invalidated', ['undo', 'off', 'account_change', 'model_change', 'gpu_failure'])
def test_prepared_scores_are_rechecked_at_publication(tmp_path, invalidated):
    from pixivvault_nicegui.media import ImageBlob
    from pixivvault_nicegui.recommendation_media import RecommendationDisplaySlot, RecommendationDisplayUnit

    enabled = True
    visual, worker, generation = _visual(tmp_path / 'visual', lambda: enabled)
    image = ImageBlob(png(), 'image/png', '"test"')
    works = tuple(_work(n) for n in range(1, 7))
    captured = (RecommendationDisplayUnit('certain', 'tag', tuple(
        RecommendationDisplaySlot(work, work, image, True, 'thumb') for work in works)),)
    prepared = visual.prepare(captured)
    assert visual.project(captured, prepared)[0].slots[0].work == works[1]
    previous_model = worker.store.load_model(generation)
    if invalidated == 'off':
        enabled = False
    elif invalidated == 'gpu_failure':
        worker._check_running = lambda **_: False
    else:
        worker.store.invalidate(invalidated)
        assert not worker.store.publish(generation, previous_model.to_payload())
    assert [slot.work for slot in visual.project(captured, prepared)[0].slots] == list(works)


def test_ready_image_supersedes_author_and_wrong_model_bypasses_correction(tmp_path):
    from pixivvault_nicegui.media import ImageBlob
    from pixivvault_nicegui.recommendation_media import RecommendationDisplaySlot, RecommendationDisplayUnit

    visual, worker, generation = _visual(tmp_path / 'visual', lambda: True)
    image = ImageBlob(png(), 'image/png', '"test"')
    work, rival = _work(1), _work(2)
    a_sha = 'd' * 64
    captured = (RecommendationDisplayUnit('certain', 'tag', (
        RecommendationDisplaySlot(work, work, image, True, 'thumb', a_sha),
        RecommendationDisplaySlot(rival, rival, image, True, 'thumb'),
    )),)
    assert visual.project(captured, visual.prepare(captured))[0].slots[0].work == rival
    assert worker.store.put_embedding(generation, EmbeddingKey(
        work.work_id, 0, 'view', a_sha, SHA, _PREPROCESSING_VERSION), (1., 0.))
    assert visual.project(captured, visual.prepare(captured))[0].slots[0].work == work
    prepared = visual.prepare(captured)
    worker._model_sha = 'e' * 64
    assert visual.project(captured, prepared)[0].slots[0].work == work
    assert visual.prepare(captured) is None


def test_existing_regular_response_supplies_exact_embedding_lineage():
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_gpuarb_remote_media import Client as ImageReader

    reader = ImageReader()
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    work = replace(_work(1), thumbnail_url='https://i.pximg.net/fixture/thumbnail-1.png')
    received = []
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire,
        acquire_recommendation_images=lambda records: registry.acquire_catalog(
            RecommendationRemoteImages(RemoteBookmarkImages(reader, records), registry)))
    media = RecommendationMediaSnapshot(services, (work,),
                                        view_observer=lambda *args: received.append(args))
    try:
        groups = (RecommendationDisplayGroup('certain', 'tag', (work,)),)
        assert media.capture_display(groups, lineage=True)[0].slots[0].regular_sha is None
        assert reader.calls == []
        media.remote_pin.get('1', 0, 'thumb')
        blob = media.remote_pin.get('1', 0, 'view')
        observed_work, observed_page, observed_image = received[0]
        assert (observed_work, observed_page, observed_image.variant) == ('1', 0, 'regular_view')
        assert observed_image.blob == blob
        assert media.capture_display(groups, lineage=True)[0].slots[0].regular_sha == blob.etag[1:-1]
        assert reader.calls == ['thumbnail', 'pages', 'regular']
    finally:
        media.close()
        registry.close()


def test_viewer_only_pin_cannot_submit_a_reader_with_unproven_shelf_lineage():
    from test_gpuarb_remote_media import Client as ImageReader

    reader = ImageReader()
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    work = _work(1)
    received = []
    available = False

    def acquire(records):
        if not available:
            raise RuntimeError('synthetic shelf acquisition failure')
        return registry.acquire_catalog(RecommendationRemoteImages(RemoteBookmarkImages(reader, records), registry))

    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire,
                               acquire_recommendation_images=acquire)
    media = RecommendationMediaSnapshot(services, (work,),
                                        view_observer=lambda *args: received.append(args))
    try:
        available = True
        selection = media.select(work.work_id)
        try:
            assert selection.images.get(work.work_id, 0, 'view').data
            assert received == []
        finally:
            selection.close()
    finally:
        media.close()
        registry.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['delayed_account_binding', 'undo_during_projection', 'source_revoked',
                                      'new_local_catalog'])
async def test_view_rechecks_at_publication_of_a_prepared_snapshot(tmp_path, monkeypatch, boundary):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: True)
    services = make(tmp_path)
    reader = PublicImageReadClient(transport=lambda *_: png())
    services.live_client = reader
    services.recommendation_visual = visual
    works = tuple(_work(n) for n in range(1, 7))
    services.recommendations.source.load = lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=())
    client = Client(ui.page('/_gpu_recommend_fence'))
    started, release = Event(), Event()
    refresh = None
    try:
        with client:
            view = await open_view(services)
            original = [node.props['data-testid'] for node in _rendered(view)]
            _load_rendered(services.images, _rendered(view))
            if boundary == 'delayed_account_binding':
                prepare = visual.prepare

                def delayed(captured):
                    result = prepare(captured)
                    assert result is not None
                    started.set()
                    assert release.wait(6)
                    return result

                monkeypatch.setattr(visual, 'prepare', delayed)
                refresh = asyncio.create_task(view.refresh())
                assert await asyncio.to_thread(started.wait, 6)
                other, other_worker, _ = _visual(tmp_path / 'other', lambda: False)
                services.recommendation_visual = other
                release.set()
                await refresh
                other_worker.flush_events()
            else:
                project = visual.project
                original_url = view._media.thumbnail_url

                def invalidate_after_project(captured, prepared, **kwargs):
                    slate = project(captured, prepared, **kwargs)
                    assert slate[0].slots[0].work.work_id == '2'
                    if boundary == 'undo_during_projection':
                        worker.store.invalidate('undo')
                    elif boundary == 'new_local_catalog':
                        services.images.publish(None, None)
                    else:
                        def guarded(work_id):
                            if work_id == '2':
                                raise ValueError('synthetic page revocation')
                            return original_url(work_id)
                        monkeypatch.setattr(view._media, 'thumbnail_url', guarded)
                    return slate

                monkeypatch.setattr(visual, 'project', invalidate_after_project)
                await view.refresh()
                if boundary == 'source_revoked':
                    original.remove('recommend-thumb-certain-2')
            assert [node.props['data-testid'] for node in _rendered(view)] == original
            assert all('background-image' not in node.style for node in _rendered(view))
            worker.flush_events()
    finally:
        release.set()
        if refresh is not None:
            await refresh
        client.delete()
        services.close()
