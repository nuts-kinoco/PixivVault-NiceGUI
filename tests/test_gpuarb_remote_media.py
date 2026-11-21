import io
from types import SimpleNamespace

import pytest
from PIL import Image

from pixivvault_nicegui.media import ImageBlob
from pixivvault_nicegui.pixiv_read_client import HTTPStatusRejected
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.shared_image_cache import DiskThumbnailCache, SharedImageCache


def png():
    stream = io.BytesIO()
    Image.new('RGB', (240, 240), 'red').save(stream, 'PNG')
    return stream.getvalue()


def record(key, *, known=True, kind='illustration', illustration_type=0):
    return SimpleNamespace(work_id=key, remote_id=key, page_count=1, page_count_known=known,
                           kind=kind, illustration_type=illustration_type,
                           thumbnail='https://i.pximg.net/synthetic-thumbnail.png')


class Client:
    def __init__(self, *, missing=False):
        self.calls = []
        self.missing = missing

    def get_artwork_pages(self, _):
        self.calls.append('pages')
        if self.missing:
            raise HTTPStatusRejected(404)
        return ['https://i.pximg.net/synthetic-regular.png']

    def get_image_bytes(self, url):
        self.calls.append('thumbnail' if 'thumbnail' in url else 'regular')
        return png()


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize(('missing', 'requested', 'actual', 'calls'), [
    (False, 'view', 'regular_view', ['pages', 'regular']),
    (True, 'view', 'listing_thumb_fallback', ['pages', 'thumbnail']),
    (False, 'thumb', 'thumb_192', ['thumbnail']),
])
def test_variant_tracks_actual_bytes(shared, missing, requested, actual, calls):
    client = Client(missing=missing)
    catalog = RemoteBookmarkImages(client, (record('a', known=not missing),))
    if shared:
        catalog.configure_cache(SharedImageCache(max_bytes=4096))
    assert catalog.peek_with_variant('a', 0, requested) is None
    assert client.calls == []
    blob = catalog.get('a', 0, requested)
    assert type(blob) is ImageBlob
    assert catalog.peek_with_variant('a', 0, requested) == (blob, actual)
    assert catalog.get_with_variant('a', 0, requested) == (blob, actual)
    assert catalog.peek('a', 0, requested) == blob
    assert client.calls == calls
    catalog.close()
    with pytest.raises(RuntimeError, match='closed'):
        catalog.peek_with_variant('a', 0, requested)


def test_variant_peek_preserves_shared_lru_across_namespaces():
    client = Client()
    first = RemoteBookmarkImages(client, (record('a'), record('c')))
    second = RemoteBookmarkImages(client, (record('b'),))
    cache = SharedImageCache(max_bytes=2 * len(png()))
    first.configure_cache(cache)
    second.configure_cache(cache)
    first.get('a')
    second.get('b')
    assert first.peek_with_variant('a', 0, 'view')[1] == 'regular_view'
    first.get('c')
    assert first.peek_with_variant('a', 0, 'view') is None
    assert second.peek_with_variant('b', 0, 'view')[1] == 'regular_view'
    assert client.calls == ['pages', 'regular'] * 3


def test_variant_peek_does_not_promote_disk_thumb(tmp_path):
    disk = DiskThumbnailCache(tmp_path / 'disk', max_bytes=4096)
    cache = SharedImageCache(max_bytes=4096, disk=disk)
    client = Client()
    first = RemoteBookmarkImages(client, (record('a'),))
    second = RemoteBookmarkImages(client, (record('a'),))
    try:
        first.configure_cache(cache)
        first.get('a', variant='thumb')
        first.close()
        second.configure_cache(cache)
        assert second.peek_with_variant('a', 0, 'thumb') is None
        blob = second.get('a', variant='thumb')
        assert second.peek_with_variant('a', 0, 'thumb') == (blob, 'thumb_192')
        assert client.calls == ['thumbnail']
    finally:
        cache.close()


def test_success_result_includes_variant_even_if_cache_cannot_hold_it():
    client = Client(missing=True)
    catalog = RemoteBookmarkImages(client, (record('a', known=False),), max_bytes=1)
    blob, actual = catalog.get_with_variant('a')
    assert blob.media_type == 'image/png'
    assert actual == 'listing_thumb_fallback'
    assert catalog.peek_with_variant('a', 0, 'view') is None
    assert client.calls == ['pages', 'thumbnail']
