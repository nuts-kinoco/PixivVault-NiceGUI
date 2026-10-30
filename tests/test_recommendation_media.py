"""Synthetic recommendation pictures through real pin/receipt/media contracts."""
from __future__ import annotations

from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image
import pytest

from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.recommendation_media import (
    RecommendationMediaSnapshot, RecommendationMediaUnavailable)
from pixivvault_nicegui.recommendation_state import RecommendationWork
from pixivvault_nicegui.services import ApplicationServices
from test_managed_quarantine import owned, plan_for, proof_bytes


def preview(wid='11', *, unknown_type=False, unknown_count=False):
    return RecommendationWork(wid, 'Synthetic ' + wid, '24', 'Synthetic author',
        f'https://i.pximg.net/thumb/{wid}/0.png',
        None if unknown_count else 3, None if unknown_type else 1,
        ('synthetic',), '2026-10-29T00:00:00Z', 0)


class Reader:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.detail_override = None

    def get_artwork_pages(self, wid):
        self.calls.append(('pages', wid))
        return tuple(f'https://i.pximg.net/view/{wid}/{page}.png' for page in range(3))

    def get_artwork_original_pages(self, wid):
        self.calls.append(('original-pages', wid))
        return tuple(f'https://i.pximg.net/original/{wid}/{page}.png' for page in range(3))

    def get_image_bytes(self, url):
        self.calls.append(('image', url))
        route, wid, leaf = urlsplit(url).path.strip('/').split('/')
        page = int(leaf.split('.')[0])
        size = (160, 120) if route == 'original' else (64, 48)
        output = BytesIO()
        Image.new('RGB', size, (int(wid) % 255, page * 40, 90)).save(output, 'PNG')
        return output.getvalue()

    def get_discovery_work(self, wid):
        self.calls.append(('detail', wid))
        return self.detail_override or dict(id=wid, user_id='24',
            user_name='Synthetic author', title='Synthetic ' + wid,
            thumbnail_url=f'https://i.pximg.net/thumb/{wid}/0.png',
            page_count=3, illust_type=1, x_restrict=0)

    def close(self):
        self.closed = True


@pytest.fixture
def application():
    reader = Reader()
    services = ApplicationServices(live_client=reader)
    try:
        yield services, reader
    finally:
        services.close()


def test_visible_thumbnail_only_and_repeated_render_reuses_same_pin_without_metadata_get(application):
    services, reader = application
    media = RecommendationMediaSnapshot(services, (preview(),))
    try:
        assert reader.calls == []
        url = media.thumbnail_url('11')
        assert url.startswith('/media/11/0/thumb.png?catalog=')
        token = media.remote_pin.token
        blob = media.remote_pin.get('11', 0, 'thumb')
        assert blob.media_type == 'image/png'
        assert reader.calls == [('image', 'https://i.pximg.net/thumb/11/0.png')]
        assert media.matches((preview(),))
        assert media.remote_pin.token == token and media.thumbnail_url('11') == url
        media.remote_pin.get('11', 0, 'thumb')
        assert len(reader.calls) == 1
    finally:
        media.close()
    assert not reader.closed


def test_unknown_type_is_resolved_only_explicitly_and_original_fetches_selected_page(application):
    services, reader = application
    media = RecommendationMediaSnapshot(services, (preview(unknown_type=True),))
    selection = None
    try:
        assert media.needs_detail('11') and reader.calls == []
        with pytest.raises(RecommendationMediaUnavailable):
            media.select('11')
        assert reader.calls == []
        candidate = media.read_detail('11')
        assert reader.calls == [('detail', '11')]
        selection = media.select('11', candidate)
        assert selection.owns_pin and selection.artwork.page_count == 3
        assert selection.images.supports_original('11')
        blob = selection.images.get('11', 2, 'original')
        with Image.open(BytesIO(blob.data)) as image:
            assert image.size == (160, 120)
        assert reader.calls == [
            ('detail', '11'), ('original-pages', '11'),
            ('image', 'https://i.pximg.net/original/11/2.png')]
    finally:
        if selection is not None:
            selection.close()
        media.close()
    assert len(services.images._versions) == 1 and not reader.closed


def test_unknown_page_count_uses_existing_explicit_page_resolution(application):
    services, reader = application
    media = RecommendationMediaSnapshot(services, (preview(unknown_count=True),))
    selection = None
    try:
        assert not media.needs_detail('11') and reader.calls == []
        selection = media.select('11')
        assert selection.images.needs_metadata('11')
        assert reader.calls == []
        artwork = selection.images.prepare_artwork('11')
        assert artwork.page_count == 3 and artwork.work_id == '11'
        assert reader.calls == [('pages', '11')]
        # The resolved page list is retained in this same pin.
        selection.images.get('11', 2, 'view')
        assert reader.calls == [('pages', '11'), ('image', 'https://i.pximg.net/view/11/2.png')]
    finally:
        if selection is not None:
            selection.close()
        media.close()


@pytest.mark.parametrize('change', ('work', 'author'))
def test_detail_of_another_identity_is_never_adopted_or_allocated_a_viewer_pin(application, change):
    services, reader = application
    media = RecommendationMediaSnapshot(services, (preview(unknown_type=True),))
    before = set(services.images._versions)
    reader.detail_override = dict(id='12' if change == 'work' else '11',
        user_id='99' if change == 'author' else '24', user_name='Synthetic wrong identity',
        title='Synthetic', thumbnail_url='https://i.pximg.net/thumb/11/0.png',
        page_count=3, illust_type=1, x_restrict=0)
    try:
        with pytest.raises(RecommendationMediaUnavailable):
            media.read_detail('11')
        assert set(services.images._versions) == before
        assert reader.calls == [('detail', '11')]
    finally:
        media.close()


def test_saved_middle_page_mapping_and_original_bytes_are_pinned_without_remote_fallback(owned, application):
    writer, source, original_client = owned
    services, reader = application
    proof = proof_bytes(writer)
    source.execute(plan_for(source, '123:2'), operation_id='6' * 32)
    _, catalog, _ = build_managed_library(writer)
    services.images.publish(catalog, None)
    before_gets = tuple(original_client.images)
    media = RecommendationMediaSnapshot(services, (preview('123'),))
    selection = None
    try:
        selection = media.select('123')
        assert not selection.owns_pin and selection.artwork.page_count == 2
        identity = selection.images.source_page_identity('123', 1)
        assert identity.work_id == '123'
        assert identity.source_page_no == 2 and identity.source_page_count == 3
        assert identity.author_id == '24'
        expected = writer.read_saved_page(identity.relative_receipt, 2, identity.receipt_sha256)
        assert selection.images.get('123', 1, 'original').data == expected
        assert media.remote_pin is None and reader.calls == []
        assert tuple(original_client.images) == before_gets
        assert proof_bytes(writer) == proof
    finally:
        if selection is not None:
            selection.close()
        media.close()


@pytest.mark.parametrize('publish_before_mount', (True, False))
def test_managed_tombstone_blocks_remote_card_and_late_metadata_resolution(owned, application, publish_before_mount):
    writer, source, original_client = owned
    services, reader = application
    proof = proof_bytes(writer)
    media = None
    if not publish_before_mount:
        media = RecommendationMediaSnapshot(services, (preview('123', unknown_type=True),))
    source.execute(plan_for(source, '123:1', '123:2', '123:3'), operation_id='7' * 32)
    _, catalog, _ = build_managed_library(writer)
    services.images.publish(catalog, None)
    before_gets = tuple(original_client.images)
    if media is None:
        media = RecommendationMediaSnapshot(services, (preview('123', unknown_type=True),))
    try:
        for action in (media.thumbnail_url, media.select, media.read_detail):
            with pytest.raises(RecommendationMediaUnavailable):
                action('123')
        assert reader.calls == []
        assert tuple(original_client.images) == before_gets
        assert proof_bytes(writer) == proof
    finally:
        media.close()
