"""Real provider fixture integration for aggregate memory and owned thumbnail budgets."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import hashlib
import pytest
from PIL import Image
from pixivvault_nicegui.shared_image_cache import SharedImageCache,DiskThumbnailCache,ImageCacheError
from pixivvault_nicegui.media import ImageBlob,DemoImageCatalog
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.library_models import LibraryWork,LibrarySnapshot
from pixivvault_nicegui.local_media_adapter import ReadonlyLocalMediaCatalog,MediaUnavailable
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.managed_download_catalog import ManagedDownloadCatalog
from test_managed_download_catalog import FakeWriter


def png(color):
    stream=BytesIO();Image.new('RGB',(16,12),color).save(stream,'PNG');return stream.getvalue()


def blob(color):
    raw=png(color);return ImageBlob(raw,'image/png','"'+hashlib.sha256(raw).hexdigest()+'"')


def record(wid='31',thumbnail=None):
    url=thumbnail or f'https://i.pximg.net/img-original/img/2026/01/01/00/00/00/{wid}_p0.png'
    return RemoteBookmark(wid,'illustration','public','fixture','9','fixture',(),url,'2026-01-01',1,0,0)


def test_one_lru_budget_across_namespaces_and_live_resize():
    a,b,c=blob('red'),blob('blue'),blob('green')
    cache=SharedImageCache(max_bytes=len(a.data)+len(b.data))
    first=cache.namespace();second=cache.namespace()
    first.put(('1',0,'thumb'),a);second.put(('2',0,'thumb'),b)
    assert first.get(('1',0,'thumb'))==a  # first becomes MRU
    second.put(('3',0,'thumb'),c)
    assert second.get(('2',0,'thumb')) is None
    assert cache.stats()['bytes']<=cache.stats()['limit']
    first.close()
    assert second.get(('3',0,'thumb'))==c
    cache.configure(1)
    assert cache.stats()['bytes']==0
    second.put(('4',0,'thumb'),a)
    assert cache.stats()['bytes']==0
    cache.close()


def test_disk_restart_integrity_owner_and_budget(tmp_path):
    image=blob('red');root=tmp_path/'cache'
    disk=DiskThumbnailCache(root,max_bytes=max(len(image.data),len(blob('blue').data))+32)
    with pytest.raises(ImageCacheError):DiskThumbnailCache(root,max_bytes=10000)
    disk.put('source-a',image)
    assert disk.get('source-a')==image
    disk.put('source-b',blob('blue'))
    assert disk.get('source-a') is None
    assert disk.bytes<=disk.max_bytes
    disk.close()
    reopened=DiskThumbnailCache(root,max_bytes=10000)
    assert reopened.get('source-b')==blob('blue')
    path=next(root.glob('*.cache'));data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    assert reopened.get('source-b') is None
    reopened.close()
    assert all(p.suffix!='.tmp' for p in root.iterdir())


def test_unmarked_nonempty_disk_root_preserved(tmp_path):
    marker=tmp_path/'user-image.png';marker.write_bytes(png('red'));before=marker.read_bytes()
    with pytest.raises(ImageCacheError):DiskThumbnailCache(tmp_path,max_bytes=1000)
    assert marker.read_bytes()==before
    assert not (tmp_path/'.pixivvault-thumbnail-cache').exists()


def test_real_remote_and_demo_share_budget_and_remote_thumbnail_survives_restart(tmp_path):
    calls=[]
    client=SimpleNamespace(get_image_bytes=lambda url:(calls.append(url),png('red'))[1])
    disk=DiskThumbnailCache(tmp_path/'disk',max_bytes=10000)
    cache=SharedImageCache(max_bytes=10000,disk=disk)
    demo=DemoImageCatalog([Artwork('demo-1','t','a')])
    images=LibraryImageRegistry(demo,shared_cache=cache)
    remote=RemoteBookmarkImages(client,(record(),))
    images.publish(None,remote);pin=images.acquire()
    first=images.get_versioned(pin.token,'31',0,'thumb')
    assert len(calls)==1 and cache.stats()['bytes']==len(first.data)
    cache.configure(1) # empty memory but keep the thumbnail disk cache
    assert images.get_versioned(pin.token,'31',0,'thumb')==first
    assert len(calls)==1 and cache.stats()['bytes']==0
    assert not remote._cache and not demo._cache
    pin.close();images.close();cache.close()
    disk=DiskThumbnailCache(tmp_path/'disk',max_bytes=10000);cache=SharedImageCache(max_bytes=10000,disk=disk)
    remote=RemoteBookmarkImages(client,(record(),));remote.configure_cache(cache)
    assert remote.get('31',0,'thumb')==first and len(calls)==1
    remote.close();cache.close()
    # Only hash keys / integrity bytes and PNGs persisted, never source URLs.
    assert all(b'https://' not in p.read_bytes() for p in (tmp_path/'disk').iterdir() if p.is_file())


def test_remote_view_not_persisted_and_source_identity_changed_requires_fixture_read(tmp_path):
    calls=[]
    source=record()
    client=SimpleNamespace(get_image_bytes=lambda url:(calls.append(url),png('red'))[1],get_artwork_pages=lambda wid:(source.thumbnail,))
    disk=DiskThumbnailCache(tmp_path/'disk',max_bytes=10000);cache=SharedImageCache(max_bytes=10000,disk=disk)
    remote=RemoteBookmarkImages(client,(source,));remote.configure_cache(cache)
    remote.get('31',0,'view');assert disk.bytes==0
    remote.get('31',0,'thumb');remote.close()
    updated=replace(source,thumbnail=source.thumbnail.replace('/01/01/','/01/02/'))
    remote=RemoteBookmarkImages(client,(updated,));remote.configure_cache(cache)
    remote.get('31',0,'thumb')
    assert len(calls)==3
    remote.close();cache.close()


def test_local_and_managed_source_still_verified_before_disk_hit(tmp_path,monkeypatch):
    disk=DiskThumbnailCache(tmp_path/'disk',max_bytes=10000);cache=SharedImageCache(max_bytes=10000,disk=disk)
    images=tmp_path/'images';folder=images/'artist(12)';folder.mkdir(parents=True)
    page=folder/'title(101)_1.png';page.write_bytes(png('red'))
    snapshot=LibrarySnapshot((LibraryWork(Artwork('101','title','artist'),'12'),))
    local=ReadonlyLocalMediaCatalog(images,snapshot);local.configure_cache(cache)
    old=local.get('101',0,'thumb');assert disk.bytes>0
    local.close()
    page.write_bytes(png('blue'))
    local=ReadonlyLocalMediaCatalog(images,snapshot);local.configure_cache(cache)
    assert local.get('101',0,'thumb')!=old
    writer=FakeWriter();managed=ManagedDownloadCatalog(writer,writer.rows);managed.configure_cache(cache)
    managed.get('1',0,'thumb');managed.close()
    writer.pages['1'][0]=png('green')
    managed=ManagedDownloadCatalog(writer,writer.rows);managed.configure_cache(cache)
    with pytest.raises(MediaUnavailable):managed.get('1',0,'thumb')
    assert not local._cache and not managed._cache
    local.close();managed.close();cache.close()


def test_close_during_remote_fetch_cannot_publish_late(tmp_path):
    started=Event();proceed=Event()
    def read(_):started.set();assert proceed.wait(3);return png('red')
    disk=DiskThumbnailCache(tmp_path/'disk',max_bytes=10000);cache=SharedImageCache(max_bytes=10000,disk=disk)
    remote=RemoteBookmarkImages(SimpleNamespace(get_image_bytes=read),(record(),));remote.configure_cache(cache)
    with ThreadPoolExecutor() as pool:
        future=pool.submit(remote.get,'31',0,'thumb');assert started.wait(3)
        remote.close();proceed.set()
        with pytest.raises(RuntimeError):future.result(3)
    assert cache.stats()['bytes']==0 and disk.bytes==0
    cache.close()


def test_pinned_provider_close_keeps_other_versions_cache():
    cache=SharedImageCache(max_bytes=10000);images=LibraryImageRegistry(DemoImageCatalog([]),shared_cache=cache)
    def provider(color):return RemoteBookmarkImages(SimpleNamespace(get_image_bytes=lambda _:png(color)),(record(),))
    first=provider('red');images.publish(None,first);pin_a=images.acquire()
    a=images.get_versioned(pin_a.token,'31',0,'thumb')
    second=provider('blue');images.publish(None,second);pin_b=images.acquire()
    b=images.get_versioned(pin_b.token,'31',0,'thumb')
    assert a!=b and cache.stats()['bytes']==len(a.data)+len(b.data)
    pin_a.close()
    assert cache.stats()['bytes']==len(b.data)
    assert images.get_versioned(pin_b.token,'31',0,'thumb')==b
    pin_b.close();images.close()
    assert cache.stats()['bytes']==0
    cache.close()


def test_late_binding_covers_existing_pins_and_rejects_replacement():
    images=LibraryImageRegistry(DemoImageCatalog([]))
    remote=RemoteBookmarkImages(SimpleNamespace(get_image_bytes=lambda _:png('red')),(record(),))
    images.publish(None,remote);pin=images.acquire()
    images.get_versioned(pin.token,'31',0,'thumb')
    assert remote._cache
    cache=SharedImageCache(max_bytes=10000)
    images.bind_shared_cache(cache);images.bind_shared_cache(cache)
    assert not remote._cache
    images.get_versioned(pin.token,'31',0,'thumb')
    assert cache.stats()['bytes']>0
    other=SharedImageCache(max_bytes=10000)
    with pytest.raises(RuntimeError):images.bind_shared_cache(other)
    pin.close();images.close();cache.close();other.close()


def test_explicit_clear_only_removes_owned_cache_images(tmp_path):
    disk=DiskThumbnailCache(tmp_path/'cache',max_bytes=10000)
    cache=SharedImageCache(max_bytes=10000,disk=disk);space=cache.namespace()
    space.put(('1',0,'thumb'),blob('red'),disk_key='known-id')
    outside=tmp_path/'saved.png';outside.write_bytes(png('green'));before=outside.read_bytes()
    cache.clear()
    assert cache.stats()['bytes']==0 and disk.bytes==0
    assert outside.read_bytes()==before
    assert (tmp_path/'cache'/'.pixivvault-thumbnail-cache').is_file()
    space.put(('1',0,'thumb'),blob('blue'))
    assert space.get(('1',0,'thumb'))==blob('blue')
    cache.close()


def test_late_local_generation_cannot_repopulate_namespace(tmp_path,monkeypatch):
    root=tmp_path/'images';folder=root/'artist(12)';folder.mkdir(parents=True)
    (folder/'title(101)_1.png').write_bytes(png('red'))
    snapshot=LibrarySnapshot((LibraryWork(Artwork('101','title','artist'),'12'),))
    local=ReadonlyLocalMediaCatalog(root,snapshot);cache=SharedImageCache(max_bytes=10000);local.configure_cache(cache)
    started=Event();proceed=Event()
    original=Image.open
    def delayed(*args,**kwargs):started.set();assert proceed.wait(3);return original(*args,**kwargs)
    monkeypatch.setattr(Image,'open',delayed)
    with ThreadPoolExecutor() as pool:
        future=pool.submit(local.get,'101',0,'thumb');assert started.wait(3)
        local.replace_snapshot(snapshot);proceed.set()
        with pytest.raises(MediaUnavailable):future.result(3)
    assert cache.stats()['bytes']==0
    local.close();cache.close()


def test_owner_hardlink_to_external_file_rejected_before_write(tmp_path):
    import os
    import pytest
    from pixivvault_nicegui.shared_image_cache import ImageCacheError
    root=tmp_path/'cache';disk=DiskThumbnailCache(root,max_bytes=100000);disk.close()
    owner=root/'.owner.lock';owner.unlink()
    outside=tmp_path/'external';outside.write_bytes(b'')
    os.link(outside,owner)
    before=outside.stat()
    with pytest.raises(ImageCacheError):DiskThumbnailCache(root,max_bytes=100000)
    after=outside.stat()
    assert outside.read_bytes()==b'' and after.st_mtime_ns==before.st_mtime_ns


def test_replaced_cache_hardlink_does_not_touch_external_metadata(tmp_path):
    import os,hashlib
    root=tmp_path/'cache';disk=DiskThumbnailCache(root,max_bytes=100000)
    try:
        disk.put('sample',blob('red'))
        target=root/(hashlib.sha256(b'sample').hexdigest()+'.cache')
        outside=tmp_path/'external';outside.write_bytes(target.read_bytes());target.unlink()
        os.link(outside,target);before=outside.stat();data=outside.read_bytes()
        assert disk.get('sample') is None
        assert outside.read_bytes()==data and outside.stat().st_mtime_ns==before.st_mtime_ns
    finally:disk.close()
