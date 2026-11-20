from io import BytesIO
from types import SimpleNamespace
import json
import time

import pytest
from nicegui import Client, ui
from PIL import Image

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.learning_prefetch import LearningImagePrefetch, PrefetchResult
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.managed_network_stop_shared_scope import ManagedUserNetworkStopScope
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected, PublicImageReadClient, TransportFailed
from pixivvault_nicegui.preferences import RequestPreferences
from pixivvault_nicegui.ranking_source import RankingQuery, RankingReadFailed, RankingSource
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.request_policy import RequestRetryPolicy
from pixivvault_nicegui.shared_image_cache import SharedImageCache
from pixivvault_nicegui.ui.request_status_view import RequestStatusView, retry_status_text
from test_ranking_source import Reader, row
from test_stage5_r1_user_shared_scope import generated_sealed_shared_owner


@pytest.mark.parametrize(('method', 'args', 'path'), [
    ('get_image_bytes', ('https://i.pximg.net/generated/missing.png',), '/generated/missing.png'),
    ('get_discovery_work', ('123',), '/ajax/illust/123'),
    ('get_recommendation_work', ('123',), '/ajax/illust/123'),
    ('get_artwork_pages', ('123',), '/ajax/illust/123/pages'),
    ('get_author_novel_ids', ('789',), '/ajax/user/789/profile/all'),
    ('get_ranking', (), '/ranking.php'),
    ('search_illusts', ('generated',), '/ajax/search/artworks/generated'),
])
def test_missing_item_remains_local_to_the_caller(tmp_path, method, args, path):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    calls = []

    def missing(request, *_):
        calls.append(request.selector.split('?', 1)[0])
        raise HTTPStatusRejected(404)

    reader = scope.create_reader(cookie, transport=missing)
    reader._verified_identity = '55'
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 3, 5.0))
    reader.set_retry_policy(policy)
    client = Client(ui.page('/_h404_status'))
    try:
        with client:
            header = RequestStatusView(policy)
            with pytest.raises(HTTPStatusRejected) as failure:
                getattr(reader, method)(*args)
            header.refresh()

            assert failure.value.status_code == 404
            assert calls == [path]
            assert scope.admit_job(None) is True
            assert not header.label.visible, header.label.text
            assert policy.progress_snapshot() == ()
    finally:
        header.close()
        client.delete()
        reader.close()


@pytest.mark.parametrize(('status', 'attempts', 'expected'), [
    (401, 1, 'HTTP 401'),
    (403, 1, 'HTTP 403'),
    (429, 3, '共有停止しました'),
    (None, 3, '通信エラー'),
])
def test_shared_header_keeps_actionable_failures(status, attempts, expected):
    now = time.monotonic()
    waits = {}
    slept = 0
    calls = []

    def sleep(seconds):
        nonlocal now, slept
        records = policy.progress_snapshot()
        waits.setdefault(records[0]['attempt'], retry_status_text(records))
        slept += seconds
        now += seconds

    def rejected(*_):
        calls.append(1)
        if status is None:
            raise TransportFailed()
        raise HTTPStatusRejected(status)

    reader = PublicImageReadClient(transport=rejected)
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 3, 5.0),
                                clock=lambda: now, sleep=sleep, jitter=lambda *_: 0)
    reader.set_retry_policy(policy)
    try:
        with pytest.raises(TransportFailed if status is None else HTTPStatusRejected):
            reader.get_image_bytes('https://i.pximg.net/generated/rejected.png')
        assert len(calls) == attempts
        assert expected in retry_status_text(policy.progress_snapshot())
        if status == 429:
            assert set(waits) == {1, 2}
            assert slept == pytest.approx(10)
            assert all('HTTP 429のため再試行待機中' in text for text in waits.values())
            with pytest.raises(HTTPStatusRejected) as blocked:
                reader.get_image_bytes('https://i.pximg.net/generated/blocked.png')
            assert blocked.value.status_code == 429
            assert len(calls) == attempts
    finally:
        reader.close()


@pytest.mark.parametrize('page', [1, 2])
def test_ranking_missing_detail_keeps_both_works(page):
    class MissingDetail(Reader):
        def get_work_info(self, work_id):
            if work_id == '123':
                self.calls.append(('detail', work_id))
                raise HTTPStatusRejected(404)
            return super().get_work_info(work_id)

    reader = MissingDetail()
    reader.rows = [row(123), row(124)]
    result = RankingSource(reader).fetch(RankingQuery(exclude_ai=True), page)

    assert [item.record.work_id for item in result.items] == ['123', '124']
    assert result.page == page and not result.exhausted
    assert reader.calls[-2:] == [('detail', '123'), ('detail', '124')]


@pytest.mark.parametrize('status', [403, 429, 503])
def test_ranking_detail_communication_failure_still_fails_the_page(status):
    class RejectedDetail(Reader):
        def get_work_info(self, work_id):
            raise HTTPStatusRejected(status)

    reader = RejectedDetail()
    with pytest.raises(RankingReadFailed):
        RankingSource(reader).fetch(RankingQuery(exclude_ai=True), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize('pages_error, thumbnail_missing, expected', [
    (404, False, PrefetchResult('done', 2, 0)),
    (404, True, PrefetchResult('done', 1, 1)),
    (429, False, PrefetchResult('failed', 0, 1)),
    (403, False, PrefetchResult('failed', 0, 1)),
    (None, False, PrefetchResult('failed', 0, 1)),
])
async def test_learning_prefetch_keeps_missing_pages_local(tmp_path, pages_error, thumbnail_missing, expected):
    root, cookie = generated_sealed_shared_owner(tmp_path)
    scope = ManagedUserNetworkStopScope(root)
    first_thumb, second_thumb = '/generated/123-thumb.png', '/generated/124-thumb.png'
    first_pages = '/ajax/illust/123/pages'
    second_pages = '/ajax/illust/124/pages'
    second_view = '/generated/124-view.png'
    second_url = 'https://i.pximg.net' + second_view
    calls = []
    data = BytesIO()
    Image.new('RGB', (12, 8), 'blue').save(data, format='PNG')

    def get(request, *_):
        path = request.selector
        calls.append(path)
        if path == first_pages:
            if pages_error is None:
                raise TransportFailed()
            raise HTTPStatusRejected(pages_error)
        if path == second_pages:
            return json.dumps({'error': False, 'body': [{'urls': {'regular': second_url}}]}).encode()
        if path == first_thumb and thumbnail_missing:
            raise HTTPStatusRejected(404)
        assert path in (first_thumb, second_view)
        return data.getvalue()

    reader = scope.create_reader(cookie, transport=get)
    policy = RequestRetryPolicy(lambda: RequestPreferences(.6, 1, 5.0))
    reader.set_retry_policy(policy)
    records = tuple(
        RemoteBookmark(work_id, 'illustration', 'show', 'fixture', '91', 'fixture', (),
                       'https://i.pximg.net' + thumbnail, '', 1, 0, 0, page_count_known=False)
        for work_id, thumbnail in [('123', first_thumb), ('124', second_thumb)]
    )
    cache = SharedImageCache(max_bytes=1024 * 1024)
    registry = LibraryImageRegistry(DemoImageCatalog(()), shared_cache=cache)
    images = RemoteBookmarkImages(reader, records)
    pin = registry.acquire_catalog(images)
    preferences = SimpleNamespace(snapshot=lambda: SimpleNamespace(values={
        'discovery_cache_limit_mode': 'count', 'discovery_cache_size_count': '50',
    }))
    ready = []
    prefetch = LearningImagePrefetch(preferences, pin, on_ready=ready.append)
    prefetch.set_queue(['123', '124'])

    try:
        assert await prefetch.run() == expected
        assert pin.needs_metadata('123')
        if pages_error == 404:
            assert calls == [first_pages, first_thumb, second_pages, second_view]
            assert ready == (['124'] if thumbnail_missing else ['123', '124'])
            assert pin.peek('124', 0, 'view') is not None
            assert (pin.peek('123', 0, 'view') is None) == thumbnail_missing
            assert not retry_status_text(policy.progress_snapshot())
            assert scope.admit_job(None) is True
        else:
            assert calls == [first_pages]
            assert ready == []
    finally:
        prefetch.close()
        pin.close()
        registry.close()
        cache.close()
        reader.close()
