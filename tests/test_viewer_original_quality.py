"""V03 original-quality switch: explicit one-page read, no source modification.

All data is generated; this suite never reads Pixiv accounts or original files.
"""
import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from nicegui import Client, ui
from PIL import Image
import pytest

from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.image_original import verified_original_blob
from pixivvault_nicegui.library_models import LibrarySnapshot, LibraryWork
from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.managed_download_catalog import build_managed_library
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.ui.gallery import GalleryView
from test_viewer_page_save import Reader, Context, owner


def image_bytes(kind='PNG', *, size=(2600, 1600)):
    buffer=BytesIO()
    Image.new('RGB',size,(12,91,176)).save(buffer, format=kind)
    return buffer.getvalue()


@pytest.mark.parametrize('format,mime',(('PNG','image/png'),('JPEG','image/jpeg'),('WEBP','image/webp')))
def test_exact_original_has_source_bytes_and_unscaled_dimensions(format,mime):
    source=image_bytes(format)
    blob=verified_original_blob(source,expected_size=(2600,1600))
    assert blob.data==source and blob.media_type==mime
    with Image.open(BytesIO(blob.data)) as img:
        assert img.size==(2600,1600)
    with pytest.raises(ValueError,match='dimensions'):
        verified_original_blob(source,expected_size=(640,394))


@pytest.mark.parametrize('case', ('empty','invalid-header','oversized'))
def test_original_validator_rejects_invalid_or_oversized_bytes(case):
    invalid={'empty':b'', 'invalid-header':b'not an image'}.get(case)
    if invalid is None:
        invalid=b'X'*(24*1024*1024+1)
    with pytest.raises(ValueError):verified_original_blob(invalid)


def test_frozen_legacy_folder_original_is_exact_and_regular_is_scaled(tmp_path):
    folder=tmp_path/'作者(41)'
    folder.mkdir()
    path=folder/'原画(123)_1.png'
    source=image_bytes()
    path.write_bytes(source)
    snapshot=LibrarySnapshot((LibraryWork(
        Artwork('123','原画','作者',page_count=1), '41'),))
    catalog=ReadonlyLocalMediaCatalog(tmp_path,snapshot)
    try:
        assert catalog.supports_original('123')
        assert catalog.url('123',0,'original')=='/media/123/0/original.png'
        original=catalog.get('123',0,'original')
        assert original.data==source
        regular=catalog.get('123',0,'view')
        with Image.open(BytesIO(regular.data)) as image:
            assert image.width<=640 and image.height<=640
        assert path.read_bytes()==source
    finally:
        catalog.close()


def test_existing_managed_receipt_original_reuses_verified_bytes_without_get(tmp_path):
    reader=Reader()
    store=owner(tmp_path,reader)
    try:
        store.run(SimpleNamespace(kind='pixiv-illust',target_id='123'),Context())
        original_gets=len(reader.images)
        snapshot,catalog,artworks=build_managed_library(store)
        try:
            assert catalog.supports_original('123')
            assert catalog.url('123',2,'original')=='/media/123/2/original.png'
            assert catalog.get('123',2,'original').data==reader.bodies[2]
            assert len(reader.images)==original_gets
            assert catalog.get('123',2,'view').media_type=='image/png'
        finally:
            catalog.close()
    finally:
        store.close()


def test_readonly_remote_original_fetches_only_selected_page_after_explicit_get():
    calls=[]
    source=image_bytes('JPEG')
    class Reader:
        def get_artwork_original_pages(self,wid):
            calls.append(('pages',wid))
            return ('https://i.pximg.net/img-original/a/321_p0.jpg',
                    'https://i.pximg.net/img-original/a/321_p1.jpg')
        def get_image_bytes(self,url):
            calls.append(('image',url))
            return source
    record=RemoteBookmark('321','illustration','show','Generated','91','Artist',(),
                          'https://i.pximg.net/tiny.jpg','',2,0,0)
    catalog=RemoteBookmarkImages(Reader(),(record,))
    try:
        assert catalog.supports_original('321')
        assert catalog.url('321',1,'view')=='/media/321/1/view.png'
        assert calls==[]
        blob=catalog.get('321',1,'original')
        assert blob.data==source and blob.media_type=='image/jpeg'
        assert calls==[('pages','321'),('image','https://i.pximg.net/img-original/a/321_p1.jpg')]
        assert catalog.get('321',1,'original').data==source
        assert len(calls)==2  # Per-client memory cache, no second original GET.
    finally:
        catalog.close()


def test_pinned_image_snapshot_supports_original_without_changing_new_versions():
    art=Artwork('101','First','Author')
    demo=DemoImageCatalog((art,))
    registry=LibraryImageRegistry(demo)
    pin=registry.acquire()
    try:
        assert pin.supports_original('101')
        src=pin.url('101',0,'original')
        assert src.endswith('/101/0/original.png')
        blob=registry.get_versioned(pin.token,'101',0,'original')
        with Image.open(BytesIO(blob.data)) as frame:
            assert max(frame.size)==1280
    finally:
        pin.close()
        registry.close()


def test_viewer_switches_exact_page_then_resets_at_navigation_and_rejects_old_decode():
    arts=(Artwork('101','First','Author',author_id='91',page_count=2),
          Artwork('102','Second','Author',author_id='91',page_count=1))
    images=DemoImageCatalog(arts)
    services=SimpleNamespace(artworks=arts,images=images,live_client=None,
                             managed_downloads=None)
    client=Client(ui.page('/_viewer_original_quality'))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        first=gallery._image_url
        assert '/view.png#' in first
        assert gallery.viewer.quality_button.text=='原寸を表示'
        assert not gallery.viewer.quality_button.enabled
        assert not gallery.toggle_quality()  # decode has not happened
        gallery._image_loaded(SimpleNamespace(args=first))
        assert gallery.viewer.quality_button.enabled
        assert gallery.toggle_quality()
        original=gallery._image_url
        assert '/original.png#' in original and first!=original
        assert gallery._quality=='original'
        assert not gallery.viewer.quality_button.enabled
        gallery._image_loaded(SimpleNamespace(args=first))
        assert not gallery.viewer.quality_button.enabled
        gallery._image_loaded(SimpleNamespace(args=original))
        assert gallery.viewer.quality_button.text=='通常画質に戻す'
        assert gallery.toggle_quality()
        assert '/view.png#' in gallery._image_url
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        gallery.move_page(1)
        assert gallery._quality=='view' and '/view.png#' in gallery._image_url
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        gallery.open('102')
        assert gallery._quality=='view' and '/view.png#' in gallery._image_url
        gallery.close_viewer()
        assert not gallery.viewer.quality_button.enabled
    finally:
        client.delete()
        images.close()


def test_failed_original_can_return_to_regular_without_false_full_resolution():
    art=Artwork('101','First','Author',author_id='91')
    images=DemoImageCatalog((art,))
    services=SimpleNamespace(artworks=(art,),images=images,live_client=None,managed_downloads=None)
    client=Client(ui.page('/_viewer_original_error'))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        failed=gallery._image_url
        gallery._image_failed(SimpleNamespace(args=failed))
        assert '原寸画像を読み込めませんでした' in gallery.viewer.image_status.text
        assert gallery.toggle_quality()
        assert gallery._quality=='view' and '/view.png#' in gallery._image_url
        gallery._image_loaded(SimpleNamespace(args=failed))
        assert gallery.viewer.quality_button.text=='原寸を表示'
    finally:
        client.delete()
        images.close()


def test_navigation_clears_previous_frame_but_same_page_quality_keeps_it():
    arts=(Artwork('101','First','Author',page_count=2),
          Artwork('102','Second','Author',page_count=1))
    images=DemoImageCatalog(arts)
    services=SimpleNamespace(artworks=arts,images=images,live_client=None,managed_downloads=None)
    client=Client(ui.page('/_original_retain_navigation'))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        # Same-page replacement can safely keep its preceding decoded frame.
        assert gallery.viewer_image._props['retain_previous'] is True
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        gallery.move_page(1)
        # A failed second-page decode must never expose page zero as page one.
        assert gallery.viewer_image._props['retain_previous'] is False
        gallery._image_failed(SimpleNamespace(args=gallery._image_url))
        gallery.open('102')
        assert gallery.viewer_image._props['retain_previous'] is False
    finally:
        client.delete()
        images.close()


def test_original_failure_truthful_mode_and_regular_recovery():
    art=Artwork('101','First','Author')
    images=DemoImageCatalog((art,))
    services=SimpleNamespace(artworks=(art,),images=images,live_client=None,managed_downloads=None)
    client=Client(ui.page('/_original_failed_state'))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        failed=gallery._image_url
        gallery._image_failed(SimpleNamespace(args=failed))
        assert gallery._quality=='view'
        assert gallery.viewer.root._props['data-viewer-quality']=='view'
        assert '原寸' in gallery.viewer.image_status.text
        assert gallery.viewer.retry_button.visible
        gallery.request_image()
        assert '/view.png#' in gallery._image_url
    finally:
        client.delete()
        images.close()


def test_legacy_bmp_original_preserves_pixels_without_claiming_encoded_bytes(tmp_path):
    folder=tmp_path/'作者(41)'
    folder.mkdir()
    sample=Image.new('RGB',(23,19))
    sample.putdata([((x*11)%256,(x*17)%256,(x*23)%256) for x in range(23*19)])
    path=folder/'原画(123)_1.bmp'
    sample.save(path,format='BMP')
    source=path.read_bytes()
    snapshot=LibrarySnapshot((LibraryWork(Artwork('123','原画','作者'),'41'),))
    catalog=ReadonlyLocalMediaCatalog(tmp_path,snapshot)
    try:
        assert catalog.get('123',0,'view').media_type=='image/png'
        original=catalog.get('123',0,'original')
        assert original.media_type=='image/png'
        assert original.data != source
        with Image.open(BytesIO(original.data)) as decoded:
            assert decoded.size==(23,19)
            assert decoded.convert('RGB').tobytes()==sample.tobytes()
        assert path.read_bytes()==source
    finally:
        catalog.close()


def test_legacy_zip_bmp_original_pixels_and_archive_remain_unchanged(tmp_path):
    image=Image.new('RGB',(31,17))
    image.putdata([((k*7)%256,(k*13)%256,(k*23)%256) for k in range(31*17)])
    output=BytesIO()
    image.save(output,format='BMP')
    archive_path=tmp_path/'作者(41).zip'
    with ZipFile(archive_path,'w') as archive:
        archive.writestr('原画(123)_1.bmp',output.getvalue())
    archive_before=archive_path.read_bytes()
    catalog=ReadonlyLocalMediaCatalog(tmp_path,LibrarySnapshot((
        LibraryWork(Artwork('123','原画','作者'),'41'),)))
    try:
        blob=catalog.get('123',0,'original')
        assert blob.media_type=='image/png'
        with Image.open(BytesIO(blob.data)) as decoded:
            assert decoded.size==image.size
            assert decoded.convert('RGB').tobytes()==image.tobytes()
        assert archive_path.read_bytes()==archive_before
    finally:
        catalog.close()


def test_old_browser_quality_click_rejected_after_navigation():
    arts=(Artwork('101','First','Author',page_count=2),Artwork('102','Second','Author'))
    images=DemoImageCatalog(arts)
    services=SimpleNamespace(artworks=arts,images=images,live_client=None,managed_downloads=None)
    client=Client(ui.page('/_original_stale_quality_click'))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        old=dict(serial=str(gallery._request_serial),work='101',page='0')
        gallery.request_image()
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        current_url=gallery._image_url
        assert gallery._quality_clicked(SimpleNamespace(args=old)) is False
        assert gallery._image_url==current_url
        gallery.move_page(1)
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        new_url=gallery._image_url
        assert gallery._quality_clicked(SimpleNamespace(args=old)) is False
        assert gallery._image_url==new_url and gallery._quality=='view'
        current=dict(serial=str(gallery._request_serial),work='101',page='1')
        assert gallery._quality_clicked(SimpleNamespace(args=current)) is True
        assert '/101/1/original.png#' in gallery._image_url
    finally:
        client.delete()
        images.close()


def test_animation_pending_and_failed_never_retains_other_work_pixels():
    class Images(DemoImageCatalog):
        def is_animation(self,work_id):
            return work_id=='102'

        def display_animation(self,work_id):
            raise RuntimeError('synthetic animation unavailable')

    async def scenario():
        arts=(Artwork('101','Still','Author'),Artwork('102','Animation','Author'))
        images=Images(arts)
        service=SimpleNamespace(artworks=arts,images=images,live_client=None,
                                managed_downloads=None)
        client=Client(ui.page('/_original_animation_retention'))
        try:
            with client:
                gallery=GalleryView(service)
            gallery.open('101')
            gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
            gallery.open('102')
            assert gallery.viewer_image._props['src']==''
            assert gallery.viewer_image._props['retain_previous'] is False
            await asyncio.sleep(0.05)
            assert gallery._viewer_state.selected_artwork.work_id=='102'
        finally:
            client.delete()
            images.close()

    asyncio.run(scenario())


@pytest.mark.parametrize('failing_step', ('is_animation', 'url'))
def test_viewer_preflight_exception_invalidates_prior_frame_and_load(failing_step):
    """Provider can close after a successful page but before the next URL exists."""
    class FailingImages(DemoImageCatalog):
        def is_animation(self, work_id):
            if work_id == '102' and failing_step == 'is_animation':
                raise RuntimeError('synthetic closed catalog')
            return False

        def url(self, work_id, page_no=0, variant='view'):
            if work_id == '102' and variant == 'view' and failing_step == 'url':
                raise ValueError('synthetic missing image')
            return super().url(work_id, page_no, variant)

    artworks=(Artwork('101','First','Author'), Artwork('102','Next','Author'))
    images=FailingImages(artworks)
    services=SimpleNamespace(artworks=artworks,images=images,live_client=None,
                             managed_downloads=None)
    client=Client(ui.page('/_viewer_preflight_'+failing_step))
    try:
        with client:
            gallery=GalleryView(services)
        gallery.open('101')
        previous_url=gallery._image_url
        gallery._image_loaded(SimpleNamespace(args=previous_url))
        assert gallery.viewer.root._props['data-zoom-ready']=='true'
        old_save_serial=gallery.viewer.root._props['data-save-generation']
        gallery.open('102')
        assert gallery._viewer_state.selected_artwork.work_id=='102'
        assert gallery._viewer_state.image_status=='error'
        assert gallery.viewer_image._props['src']==''
        assert gallery.viewer_image._props['retain_previous'] is False
        assert gallery._image_url=='' and gallery._image_token is None
        assert gallery.viewer.root._props['data-zoom-ready']=='false'
        assert not gallery.viewer.quality_button.enabled
        assert not gallery.viewer.download_button.enabled
        assert gallery.viewer.root._props['data-save-generation'] != old_save_serial
        assert gallery.viewer.image_status.text and gallery.viewer.retry_button.visible
        gallery._image_loaded(SimpleNamespace(args=previous_url))
        gallery._image_failed(SimpleNamespace(args=previous_url))
        assert gallery._viewer_state.image_status=='error'
        assert gallery._decoded_generation is None
        assert gallery.viewer.root._props['data-zoom-ready']=='false'
        gallery.open('101')
        assert '/101/0/view.png#' in gallery._image_url
        gallery._image_loaded(SimpleNamespace(args=gallery._image_url))
        assert gallery.toggle_quality()
        assert gallery.viewer_image._props['retain_previous'] is True
    finally:
        client.delete()
        images.close()
