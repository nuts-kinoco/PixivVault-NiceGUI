"""Recommendation projection keeps the baseline acquisition slate and image demand."""

import asyncio
from dataclasses import replace
from threading import Event, get_ident
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
from test_x_ctx_ranking_20261118 import click
from test_x_ctx_recommend_20261118 import make, open_view


SHA = 'a' * 64


def _work(number):
    return RecommendationWork(str(number), str(number), str(number + 100),
                              thumbnail_url=f'https://i.pximg.net/fixture/{number}.png',
                              page_count=1, illust_type=0)


def _visual(root, enabled):
    from pixivvault_nicegui.recommendation_media import RecommendationVisual

    store = VisualPreferenceStore(root, profile='test-profile', account='test-account', protected_paths=())
    generation = _publish_fixture_model(store, SHA)
    worker = VisualPreferenceWorker(store, None, model_sha=SHA)
    # No GPU job is started. Exercise the normal store, scoring and event writer.
    worker._check_running = lambda **_: True
    return RecommendationVisual(worker, enabled=enabled), worker, generation


def _publish_fixture_model(store, model_sha):
    generation = store.advance_generation(model_sha=model_sha, preprocessing_version=_PREPROCESSING_VERSION,
                                          judgment_fingerprint='b' * 64)
    authors = tuple(AuthorStyle(str(wid + 100), (0., 1.) if wid == 1 else (1., 0.), 20, 1.)
                    for wid in range(1, 8))
    model = ModelSnapshot(generation, 'centroid', 2, (((1., 0.), 1.),), (((0., 1.), 1.),), 20, 20, authors)
    assert store.publish(generation, model.to_payload())
    return generation


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
        visual, worker, generation = _visual(tmp_path / ('on' if on else 'off'), lambda: on)
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
            applied = on and scenario in {'warm', 'after_hold'}
            assert event.payload['candidates'] == [
                dict(work_id=slot.work.work_id, section='certain', group='tag',
                     base_rank=display.works.index(slot.work) + 1, rank=rank,
                     image_available=slot.image_available, image_variant=slot.image_variant,
                     view_available=False, analysis_variant=None,
                     signal='D' if applied else None, generation=generation.number if applied else None)
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
    model_check_threads = set()
    is_current = worker.store.is_current

    def check_generation(generation):
        model_check_threads.add(get_ident())
        return is_current(generation)

    monkeypatch.setattr(worker.store, 'is_current', check_generation)

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
            view.render()

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
            await view.refresh()
            assert view._recommend_viewer.selection is None
            assert [node.props['data-testid'] for node in _rendered(view)] == original_ids
            assert all('background-image' not in node.style for node in _rendered(view))
            assert len(sent) == 6
            assert model_check_threads and get_ident() not in model_check_threads
            await asyncio.to_thread(worker.flush_events)
            events = worker.store.events()
            assert len(events) == 6
            assert [row['base_rank'] for row in events[2].payload['candidates'] if row['section'] == 'certain'] == [2, 3, 4, 5, 6, 1]
            assert events[3].payload == events[2].payload
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
    author_prepared = visual.prepare(captured)
    assert visual.project(captured, author_prepared)[0].slots[0].work == rival
    assert worker.store.put_embedding(generation, EmbeddingKey(
        work.work_id, 0, 'view', a_sha, SHA, _PREPROCESSING_VERSION), (1., 0.))
    assert visual.project(captured, visual.prepare(captured))[0].slots[0].work == work
    worker._model_sha = 'e' * 64
    assert visual.project(captured, author_prepared)[0].slots[0].work == work
    assert visual.prepare(captured) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('protected', ['viewer', 'save', 'hide'])
async def test_on_miss_keeps_only_the_active_operation_in_position(tmp_path, monkeypatch, protected):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: True)
    services = make(tmp_path)
    cache = SharedImageCache(max_bytes=len(png()) * 16)
    services.images.bind_shared_cache(cache)
    services.live_client = PublicImageReadClient(transport=lambda *_: png())
    services.recommendation_visual = visual
    works = tuple(_work(n) for n in range(1, 7))
    base = RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),
                  ('superlike', (RecommendationGroup('favorites', 'favorites', (_work(7),)),))), unavailable=())
    services.recommendations.source.load = lambda: base
    client = Client(ui.page('/_gpu_recommend_active_operation'))
    try:
        with client:
            view = await open_view(services)
            _load_rendered(services.images, _rendered(view))
            await view.refresh()
            assert [_suffix(node) for node in _rendered(view)] == list('234561')
            if protected == 'viewer':
                await view.open_work('2', generation=view._render_generation)
                assert view._recommend_viewer.selection.artwork.work_id == '2'
            elif protected == 'save':
                view._saving.add('2')
            else:
                view.ask_hide_reason('2')
                assert view._pending_hide[0] == '2'
            if protected == 'hide':
                services.recommendations.source.load = lambda: replace(base, sections=base.sections[:1])
            else:
                cache.configure(len(png()) * 2)
            await view.refresh(preserve_viewer=protected == 'viewer')
            assert [_suffix(node) for node in _rendered(view)] == list('213456')
            worker.store.invalidate('undo')
            await view.refresh(preserve_viewer=protected == 'viewer')
            assert [_suffix(node) for node in _rendered(view)] == list('123456')
            await asyncio.to_thread(worker.flush_events)
    finally:
        client.delete()
        services.close()
        worker.flush_events()
        cache.close()


def _suffix(node):
    return node.props['data-testid'].rsplit('-', 1)[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('dismiss', ['cancel', 'client-dismiss', 'confirm'])
async def test_closed_hide_reason_restores_exact_off_order(tmp_path, monkeypatch, dismiss):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    enabled = True
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: enabled)
    services = make(tmp_path)
    services.live_client = PublicImageReadClient(transport=lambda *_: png())
    services.recommendation_visual = visual
    works = tuple(_work(n) for n in range(1, 7))
    services.recommendations.source.load = lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=())
    client = Client(ui.page('/_gpu_recommend_hide_reason_dismiss'))
    try:
        with client:
            view = await open_view(services)
            _load_rendered(services.images, _rendered(view))
            await view.refresh()
            assert [_suffix(node) for node in _rendered(view)] == list('234561')
            view.ask_hide_reason('2')
            assert view.hide_dialog.value and view._pending_hide[0] == '2'
            enabled = False
            if dismiss == 'cancel':
                cancel = next(node for node in view.hide_dialog.descendants()
                              if isinstance(node, ui.button) and node.text == 'キャンセル')
                await click(cancel)
            elif dismiss == 'client-dismiss':
                # Both Escape and the Quasar backdrop send this value update.
                view.hide_dialog.set_value(False)
            else:
                await view.choose_hide_reason('style')
            assert not view.hide_dialog.value
            await view.refresh()
            expected = '13456' if dismiss == 'confirm' else '123456'
            assert [_suffix(node) for node in _rendered(view)] == list(expected)
            assert view._pending_hide is None
            await asyncio.to_thread(worker.flush_events)
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_work7_settings_reach_recommendation_on_then_exact_off_with_no_extra_get(tmp_path, monkeypatch):
    from pixivvault_nicegui.services import ApplicationServices

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    sent = []
    image = png()

    def transport(request, *_):
        sent.append((request.get_method(), request.full_url))
        return image

    reader = PublicImageReadClient(transport=transport)
    reader._verified_identity = '101'
    services = ApplicationServices(live_client=reader, preferences_root=tmp_path / 'preferences',
                                   discovery_root=tmp_path / 'discovery')
    services._egara_model_path = tmp_path / 'unused.onnx'
    services.sync_visual_preference()
    worker = services.visual_preference_worker
    assert worker is not None
    monkeypatch.setattr(worker, '_check_running', lambda **_: True)
    works = tuple(_work(n) for n in range(1, 7))
    services.recommendations = RecommendationController(SimpleNamespace(load=lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=())))
    client = Client(ui.page('/_gpu_recommend_work7_settings'))
    try:
        with client:
            view = await open_view(services)
            assert not services.visual_preference_enabled()
            baseline = [_suffix(node) for node in _rendered(view)]
            _load_rendered(services.images, _rendered(view))
            assert baseline == list('123456') and len(sent) == 6
            original_requests = tuple(sent)
            services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '1'))
            assert services.visual_preference_enabled()
            _publish_fixture_model(worker.store, worker._model_sha)
            await view.refresh()
            assert [_suffix(node) for node in _rendered(view)] == list('234561')
            _load_rendered(services.images, _rendered(view))
            services.apply_preferences(services.preferences.set_setting('visual_preference_enabled', '0'))
            await view.refresh()
            assert [_suffix(node) for node in _rendered(view)] == baseline
            _load_rendered(services.images, _rendered(view))
            assert tuple(sent) == original_requests
            await asyncio.to_thread(worker.flush_events)
    finally:
        client.delete()
        services.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['cold', 'warm', 'missing', 'evicted', 'after_hold', 'expanded'])
async def test_real_view_urls_drive_identical_transport_across_modes(tmp_path, monkeypatch, scenario):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    image = png()

    async def exercise(enabled):
        sent = []
        def transport(request, *_):
            sent.append((request.get_method(), request.full_url))
            if scenario == 'missing' and request.full_url.endswith('/3.png'):
                raise HTTPStatusRejected(404)
            return image

        root = tmp_path / ('on' if enabled else 'off')
        root.mkdir()
        services = make(root)
        visual, worker, _ = _visual(root / 'visual', lambda: enabled)
        reader = PublicImageReadClient(transport=transport)
        services.live_client = reader
        services.recommendation_visual = visual
        cache = SharedImageCache(max_bytes=len(image) * 18)
        services.images.bind_shared_cache(cache)
        works = tuple(_work(n) for n in range(1, 14))
        services.recommendations.source.load = lambda: RecommendationSnapshot(
            sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=())
        client = Client(ui.page('/_gpu_recommend_actual_urls'))
        try:
            with client:
                view = await open_view(services)
                assert [_suffix(node) for node in _rendered(view)] == list('123456')
                if scenario != 'cold':
                    for node in _rendered(view):
                        if scenario == 'missing' and _suffix(node) == '3':
                            with pytest.raises(RuntimeError):
                                _load_rendered(services.images, (node,))
                        else:
                            _load_rendered(services.images, (node,))
                if scenario == 'evicted':
                    cache.configure(len(image) * 2)
                await view.refresh()
                if scenario == 'after_hold':
                    cache.configure(len(image) * 2)
                if scenario == 'expanded':
                    assert len(sent) == 6
                    view.more(('certain', 'tag'))
                    assert len(_rendered(view)) == 12
                    _load_rendered(services.images, _rendered(view))
                    await view.refresh()
                actual = _rendered(view)
                for node in actual:
                    if scenario == 'missing' and _suffix(node) == '3':
                        with pytest.raises(RuntimeError):
                            _load_rendered(services.images, (node,))
                    else:
                        _load_rendered(services.images, (node,))
                await asyncio.to_thread(worker.flush_events)
                assert not any(url.endswith('/13.png') for _, url in sent)
                return [_suffix(node) for node in actual], sent
        finally:
            client.delete()
            services.close()
            worker.flush_events()
            cache.close()

    off, off_calls = await exercise(False)
    on, on_calls = await exercise(True)
    assert on_calls == off_calls
    assert sorted(on) == sorted(off)
    if scenario in {'warm', 'after_hold', 'expanded'}:
        assert on != off
    else:
        assert on == off


def test_managed_thumb_revoked_between_route_validation_and_cache_capture_is_not_exposed(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_managed_download_catalog import FakeWriter

    writer = FakeWriter()
    denied = False

    def guard(*_):
        if denied:
            raise ValueError('synthetic managed page exclusion')

    writer.assert_page_visible = guard
    _, catalog, _ = build_managed_library(writer)
    cache = SharedImageCache(max_bytes=50_000)
    registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
    registry.publish(catalog, None)
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire)
    work = _work(1)
    media = RecommendationMediaSnapshot(services, (work,))
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: False)
    try:
        media.local_pin.get(work.work_id, 0, 'thumb')
        ensure = media.ensure_source

        def revoke_after_route(wid):
            nonlocal denied
            ensure(wid)
            denied = True

        monkeypatch.setattr(media, 'ensure_source', revoke_after_route)
        slate = media.capture_display((RecommendationDisplayGroup('certain', 'tag', (work,)),))
        visual.record(slate)
        worker.flush_events()
        event, = worker.store.events()
        assert slate[0].slots[0].image is None
        assert event.payload['candidates'][0]['image_available'] is False
    finally:
        media.close()
        registry.close()
        cache.close()


def test_saved_original_embedding_is_a_with_receipt_lineage_only(tmp_path):
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_managed_download_catalog import FakeWriter

    writer = FakeWriter()
    _, catalog, _ = build_managed_library(writer)
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    registry.publish(catalog, None)
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire)
    works = (_work(1), _work(2))
    media = RecommendationMediaSnapshot(services, works)
    visual, worker, generation = _visual(tmp_path / 'visual', lambda: True)
    try:
        for work in works:
            media.local_pin.get(work.work_id, 0, 'thumb')
        sha = writer.rows[0][1]['files'][0]['sha256']
        assert worker.store.put_embedding(generation, EmbeddingKey(
            '1', 0, 'saved_original', sha, SHA, _PREPROCESSING_VERSION), (1., 0.))
        captured = media.capture_display((RecommendationDisplayGroup('certain', 'tag', works),), lineage=True)
        projected = visual.project(captured, visual.prepare(captured))
        visual.record(projected)
        worker.flush_events()
        event, = worker.store.events()
        first, _ = event.payload['candidates']
        assert first['work_id'] == '1' and first['signal'] == 'A'
        assert first['analysis_variant'] == 'saved_original'
        assert first['generation'] == generation.number
    finally:
        media.close()
        registry.close()


def test_invalid_regular_digest_does_not_disable_the_other_candidate(tmp_path):
    from pixivvault_nicegui.media import ImageBlob
    from pixivvault_nicegui.recommendation_media import RecommendationDisplaySlot, RecommendationDisplayUnit

    visual, _, _ = _visual(tmp_path / 'visual', lambda: True)
    left, right = _work(1), _work(2)
    thumb = ImageBlob(png(), 'image/png', '"test"')
    captured = (RecommendationDisplayUnit('certain', 'tag', (
        RecommendationDisplaySlot(left, left, thumb, True, 'thumb', 'invalid-etag'),
        RecommendationDisplaySlot(right, right, thumb, True, 'thumb'),
    )),)
    assert visual.project(captured, visual.prepare(captured))[0].slots[0].work == right


def test_rank_prior_has_same_spacing_after_expansion(tmp_path):
    from pixivvault_nicegui.media import ImageBlob
    from pixivvault_nicegui.recommendation_media import RecommendationDisplaySlot, RecommendationDisplayUnit

    visual, _, _ = _visual(tmp_path / 'visual', lambda: True)
    thumb = ImageBlob(png(), 'image/png', '"test"')
    works = tuple(_work(n) for n in range(2, 14))
    slots = tuple(RecommendationDisplaySlot(work, work, thumb, True, 'thumb') for work in works)
    def scores(count):
        return visual.prepare((RecommendationDisplayUnit('certain', 'tag', slots[:count]),))[1]

    two, twelve = scores(2), scores(12)
    assert [two['tag', wid] for wid in ('2', '3')] == [twelve['tag', wid] for wid in ('2', '3')]


def test_regular_lineage_survives_namespace_renewal_only_for_the_same_source(tmp_path):
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_gpuarb_remote_media import Client as ImageReader

    reader = ImageReader()
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    visual, worker, generation = _visual(tmp_path / 'visual', lambda: True)
    works = (_work(1), _work(2))
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire, live_client=reader)
    services.acquire_recommendation_images = lambda records: registry.acquire_catalog(
        RecommendationRemoteImages(RemoteBookmarkImages(services.live_client, records), registry))
    first = RecommendationMediaSnapshot(services, works)
    try:
        first.bind_view_observer(lambda wid, page, image: visual.observe_view(first, wid, page, image))
        regular = first.remote_pin.get('1', 0, 'view')
        assert worker.store.put_embedding(generation, EmbeddingKey(
            '1', 0, 'view', regular.etag[1:-1], SHA, _PREPROCESSING_VERSION), (1., 0.))
    finally:
        first.close()

    def capture(current_works, *, warm=True):
        media = RecommendationMediaSnapshot(services, current_works)
        try:
            if warm:
                for work in current_works:
                    media.remote_pin.get(work.work_id, 0, 'thumb')
            groups = (RecommendationDisplayGroup('certain', 'tag', current_works),)
            return media.capture_display(groups, view_lookup=visual.observed_view_sha)
        finally:
            media.close()

    try:
        cold = capture(works, warm=False)
        first_slot = cold[0].slots[0]
        assert first_slot.image is None and first_slot.regular_sha == regular.etag[1:-1]
        captured = capture(works)
        slate = visual.project(captured, visual.prepare(captured))
        assert slate[0].slots[0].work == works[0] and slate[0].slots[0].signal == 'A'
        replaced = replace(works[0], thumbnail_url='https://i.pximg.net/replacement.png')
        assert capture((replaced, works[1]))[0].slots[0].regular_sha is None
        services.live_client = ImageReader()
        assert capture(works)[0].slots[0].regular_sha is None
    finally:
        registry.close()


@pytest.mark.parametrize('renew_namespace', [False, True])
def test_purge_clears_observed_view_lineage_before_new_exposure(tmp_path, renew_namespace):
    from pixivvault_nicegui.recommendation_media import RecommendationDisplayGroup
    from test_gpuarb_remote_media import Client as ImageReader

    reader = ImageReader()
    registry = LibraryImageRegistry(DemoImageCatalog(()))
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: False)
    work = _work(1)
    services = SimpleNamespace(images=registry, acquire_library_images=registry.acquire)
    services.acquire_recommendation_images = lambda records: registry.acquire_catalog(
        RecommendationRemoteImages(RemoteBookmarkImages(reader, records), registry))
    media = RecommendationMediaSnapshot(services, (work,))
    groups = (RecommendationDisplayGroup('certain', 'tag', (work,)),)
    try:
        def load_view():
            media.bind_view_observer(lambda wid, page, image: visual.observe_view(media, wid, page, image))
            return media.remote_pin.get(work.work_id, 0, 'view')

        def capture():
            return media.capture_display(groups, view_lookup=visual.observed_view_sha)

        regular = load_view()
        assert capture()[0].slots[0].regular_sha == regular.etag[1:-1]
        worker.store.purge()
        if renew_namespace:
            media.close()
            media = RecommendationMediaSnapshot(services, (work,))
        before_read = list(reader.calls)
        visual.record(capture())
        worker.flush_events()
        event, = worker.store.events()
        row, = event.payload['candidates']
        assert row['view_available'] is False and row['analysis_variant'] is None
        assert visual.observed_view_sha(media, work) is None
        assert not visual._view_hashes
        assert reader.calls == before_read
        load_view()
        assert capture()[0].slots[0].regular_sha == regular.etag[1:-1]
    finally:
        media.close()
        registry.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_at', ['capture_event_epoch', 'append_event'])
async def test_view_exposure_record_failure_stays_off_ui_thread(tmp_path, monkeypatch, failure_at):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: False)
    services = make(tmp_path)
    services.recommendation_visual = visual
    started = Event()
    threads = []

    def fail(*_):
        threads.append(get_ident())
        started.set()
        raise RuntimeError('synthetic record failure')

    monkeypatch.setattr(worker.store, failure_at, fail)
    client = Client(ui.page('/_gpu_recommend_record_failure'))
    try:
        with client:
            view = await open_view(services)
            assert any(node.props.get('data-testid') == 'recommend-work-certain-12'
                       for node in view.body.descendants())
            assert await asyncio.to_thread(started.wait, 5)
            assert all(tid != get_ident() for tid in threads)
            await asyncio.to_thread(worker.flush_events, require_complete=False)
    finally:
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_queued_exposure_cannot_be_appended_after_purge(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: False)
    services = make(tmp_path)
    services.recommendation_visual = visual
    store = worker.store
    capture = store.capture_event_epoch
    started, resume = Event(), Event()

    def wait_before_read():
        if not started.is_set():
            started.set()
            assert resume.wait(6)
        return capture()

    monkeypatch.setattr(store, 'capture_event_epoch', wait_before_read)
    client = Client(ui.page('/_gpu_recommend_purge'))
    try:
        with client:
            view = await open_view(services)
            assert await asyncio.to_thread(started.wait, 6)
            await asyncio.to_thread(store.purge)
            resume.set()
            await asyncio.to_thread(worker.flush_events)
            assert store.events() == ()
            await view.refresh()
            await asyncio.to_thread(worker.flush_events)
            assert [event.kind for event in store.events()] == ['recommendation_exposure']
    finally:
        resume.set()
        client.delete()
        services.close()


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
                                      'new_local_catalog', 'purge_during_projection', 'purge_after_prepare'])
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
            await asyncio.to_thread(worker.flush_events)
            if boundary in {'delayed_account_binding', 'purge_after_prepare'}:
                if boundary == 'purge_after_prepare':
                    from pixivvault_nicegui.media import ImageBlob
                    from pixivvault_nicegui.remote_media import RemoteImageWithVariant

                    image = ImageBlob(png(), 'image/png', '"synthetic"')
                    visual.observe_view(view._media, '1', 0, RemoteImageWithVariant(image, 'regular_view'))
                prepare = visual.prepare

                def delayed(captured):
                    result = prepare(captured)
                    assert result is not None
                    if boundary == 'purge_after_prepare':
                        assert captured[0].slots[0].regular_sha is not None
                    started.set()
                    assert release.wait(6)
                    return result

                monkeypatch.setattr(visual, 'prepare', delayed)
                refresh = asyncio.create_task(view.refresh())
                assert await asyncio.to_thread(started.wait, 6)
                if boundary == 'delayed_account_binding':
                    other, other_worker, _ = _visual(tmp_path / 'other', lambda: False)
                    services.recommendation_visual = other
                else:
                    await asyncio.to_thread(worker.store.purge)
                release.set()
                await refresh
                await asyncio.to_thread(worker.flush_events)
                if boundary == 'delayed_account_binding':
                    other_worker.flush_events()
                else:
                    event, = worker.store.events()
                    assert not any(row['view_available'] for row in event.payload['candidates'])
            else:
                project = visual.project
                original_url = view._media.thumbnail_url

                def invalidate_after_project(captured, prepared, **kwargs):
                    slate = project(captured, prepared, **kwargs)
                    assert slate[0].slots[0].work.work_id == '2'
                    if boundary == 'undo_during_projection':
                        worker.store.invalidate('undo')
                    elif boundary == 'purge_during_projection':
                        worker.store.purge()
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
            await asyncio.to_thread(worker.flush_events)
            worker.flush_events()
            if boundary == 'purge_during_projection':
                assert worker.store.events() == ()
    finally:
        release.set()
        if refresh is not None:
            await refresh
        client.delete()
        services.close()


@pytest.mark.asyncio
async def test_saved_overlay_revoked_after_model_projection_does_not_publish(tmp_path, monkeypatch):
    from pixivvault_nicegui.managed_download_catalog import build_managed_library
    from test_managed_download_catalog import FakeWriter

    monkeypatch.setattr(core, 'loop', asyncio.get_running_loop())
    visual, worker, _ = _visual(tmp_path / 'visual', lambda: True)
    writer = FakeWriter()
    writer.rows = writer.rows[1:]
    denied = False
    projected = False

    def guard(*_):
        if denied:
            raise ValueError('synthetic managed page exclusion')

    writer.assert_page_visible = guard
    _, catalog, _ = build_managed_library(writer)
    services = make(tmp_path)
    services.images.publish(catalog, None)
    services.live_client = PublicImageReadClient(transport=lambda *_: png())
    services.recommendation_visual = visual
    works = (_work(1), _work(2))
    services.recommendations.source.load = lambda: RecommendationSnapshot(
        sections=(('certain', (RecommendationGroup('tag', 'label', works),)),), unavailable=())
    client = Client(ui.page('/_gpu_recommend_saved_publication'))
    try:
        with client:
            view = await open_view(services)
            _load_rendered(services.images, _rendered(view))
            project, is_current = visual.project, visual.is_current

            def project_first(*args, **kwargs):
                nonlocal projected
                slate = project(*args, **kwargs)
                assert slate[0].slots[0].work == works[1]
                projected = True
                return slate

            def revoke_after_projection(generation):
                nonlocal denied
                if projected:
                    denied = True
                return is_current(generation)

            monkeypatch.setattr(visual, 'project', project_first)
            monkeypatch.setattr(visual, 'is_current', revoke_after_projection)
            await view.refresh()
            assert [_suffix(node) for node in _rendered(view)] == ['1']
            assert all('background-image' not in node.style for node in _rendered(view))
            await asyncio.to_thread(worker.flush_events)
            saved = worker.store.events()[-1].payload['candidates'][1]
            assert saved['work_id'] == '2' and saved['image_available'] is False
            assert saved['analysis_variant'] is None
    finally:
        client.delete()
        services.close()
