"""Single-page originals and real UI handlers; synthetic bytes/roots only."""
import asyncio
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from nicegui import Client as UIClient, ui
from PIL import Image
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.managed_downloads import ManagedPixivDownloads, ManagedDownloadError
from pixivvault_nicegui.managed_viewer_page import PAGE_MARKER, PAGE_IDENTITY
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.gallery import GalleryView


def png(color):
    out=io.BytesIO();Image.new('RGB',(9,7),color).save(out,format='PNG');return out.getvalue()


def record(wid='123'):
    return RemoteBookmark(wid,'illustration','show','synthetic','456','tester',
        ('test',),'','2026-10-23T00:00:00Z',3,0,0)


class Reader:
    def __init__(self):
        self.bodies=[png('red'),png('green'),png('blue')]
        self.images=[];self.metadata=[];self.originals=[];self.fail=False
        self.entered=None;self.release=None;self.closed=False
    def verify_identity(self):return '456'
    def get_artwork_download_record(self,wid):
        self.metadata.append(wid);return record(wid)
    def get_artwork_original_pages(self,wid):
        self.originals.append(wid)
        return tuple(f'https://i.pximg.net/synthetic/{wid}/{i}.png' for i in range(3))
    def get_image_bytes(self,url):
        self.images.append(url)
        if self.entered:
            self.entered.set();assert self.release.wait(5)
        if self.fail:raise OSError('synthetic image failure')
        return self.bodies[int(url.rsplit('/',1)[1].split('.')[0])]
    def close(self):self.closed=True


class Context:
    def checkpoint(self):pass
    def progress(self,*args):pass


def owner(tmp_path,reader):
    return ManagedPixivDownloads(native_io_path(tmp_path/'managed'),reader,resolve_record=lambda wid:record(wid))


def save(storage,page=1):
    return storage.save_viewer_page('123',page,3,'456',lambda:storage.client.get_artwork_download_record('123'))


def proof(storage,result):
    path=native_io_path(storage.root/result.relative_receipt)
    raw=path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==result.receipt_sha256
    return json.loads(raw)


def test_only_selected_original_receipt_restart_and_whole_work_remains_unsaved(tmp_path):
    reader=Reader();storage=owner(tmp_path,reader)
    result=save(storage)
    assert reader.images==['https://i.pximg.net/synthetic/123/1.png']
    assert reader.metadata==['123'] and reader.originals==['123']
    receipt=proof(storage,result)
    assert receipt['page_no']==1 and receipt['record']['page_count']==3
    assert receipt['scope']=='pixiv-original-page' and receipt['file']['name']=='page-2.png'
    assert native_io_path(storage.root/result.relative_image).read_bytes()==reader.bodies[1]
    assert storage.saved_receipts()==() and storage.verified_saved_receipt('123') is None
    storage.close()
    second=owner(tmp_path,reader)
    try:
        again=save(second)
        assert again.reused and again.relative_receipt==result.relative_receipt
        assert again.receipt_sha256==result.receipt_sha256
        assert reader.images==['https://i.pximg.net/synthetic/123/1.png'] and reader.metadata==['123']
        other=save(second,0)
        assert other.relative_receipt!=result.relative_receipt
        assert proof(second,other)['page_no']==0
        # A subsequent full-work save still retrieves all three pages.
        second.run(SimpleNamespace(kind='pixiv-illust',target_id='123'),Context())
        assert len(reader.images)==5
        assert second.verified_saved_receipt('123')[1]['page_count']==3
        assert len(second.saved_receipts())==1
    finally:second.close()


def test_complete_receipt_reuse_verifies_actual_page_without_get_or_copy(tmp_path):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        storage.run(SimpleNamespace(kind='pixiv-illust',target_id='123'),Context())
        before=storage.verified_saved_receipt('123')
        counts=(len(reader.images),len(reader.originals),len(reader.metadata))
        result=save(storage,2)
        assert result.reused and result.relative_receipt==before[0]
        assert result.receipt_sha256==before[2]
        assert native_io_path(storage.root/result.relative_image).read_bytes()==reader.bodies[2]
        assert not native_io_path(storage.root/'viewer-pages').exists()
        assert counts==(len(reader.images),len(reader.originals),len(reader.metadata))
        assert storage.verified_saved_receipt('123')==before
        native_io_path(storage.root/result.relative_image).write_bytes(b'corrupt')
        with pytest.raises(ManagedDownloadError):save(storage,2)
        assert counts==(len(reader.images),len(reader.originals),len(reader.metadata))
    finally:storage.close()


@pytest.mark.parametrize('damage',['bytes','extra','page','count','author','hash','version_bool','duplicate_key'])
@pytest.mark.parametrize('receipt_version',[1,2])
def test_existing_page_corruption_is_not_overwritten_or_refetched(tmp_path,damage,receipt_version):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        result=save(storage);folder=native_io_path(storage.root/result.relative_receipt).parent
        receipt=folder/'receipt.json';value=json.loads(receipt.read_bytes())
        if receipt_version==1:
            value['version']=1;value.pop('original_name')
            receipt.write_text(json.dumps(value),encoding='utf-8')
        assert value['version']==receipt_version
        if damage=='bytes':(folder/value['file']['name']).write_bytes(b'corrupt')
        elif damage=='extra':(folder/'unknown.txt').write_bytes(b'keep')
        else:
            if damage=='page':value['page_no']=0
            elif damage=='count':value['record']['page_count']=2
            elif damage=='author':value['record']['author_id']='999'
            elif damage=='hash':value['file']['sha256']='0'*64
            elif damage=='version_bool':value['version']=True
            if damage=='duplicate_key':
                raw=receipt.read_bytes();token=f'"version": {value["version"]}'.encode('ascii')
                assert raw.count(token)==1  # the intended injection site exists
                damaged=raw.replace(token,token+b', '+token,1)
                assert damaged!=raw and damaged.count(token)==2  # fault actually fired
                receipt.write_bytes(damaged)
            else:receipt.write_text(json.dumps(value),encoding='utf-8')
        before={f.name:f.read_bytes() for f in folder.iterdir()}
        with pytest.raises(ManagedDownloadError):save(storage)
        assert before=={f.name:f.read_bytes() for f in folder.iterdir()}
        assert len(reader.images)==1 and storage.saved_receipts()==()
    finally:storage.close()


@pytest.mark.parametrize('point',['image','receipt','rename','after_rename'])
def test_interrupted_publish_retry_preserves_incomplete_and_reuses_committed_page(tmp_path,monkeypatch,point):
    import pixivvault_nicegui.managed_viewer_page as module
    reader=Reader();storage=owner(tmp_path,reader);fired=[]
    write=module._write;rename=Path.rename;sync=module._fsync_dir
    def failing_write(path,data):
        if 'partial-' in str(path) and ((point=='image' and path.name=='page-2.png') or
                                       (point=='receipt' and path.name=='receipt.json')):
            fired.append(point);raise OSError('synthetic write interruption')
        return write(path,data)
    def failing_rename(path,target):
        if point=='rename' and path.name.startswith('partial-'):
            fired.append(point);raise OSError('synthetic rename interruption')
        return rename(path,target)
    def failing_sync(path):
        if point=='after_rename' and path.name=='viewer-pages' and (path/'work-123-page-2').exists():
            fired.append(point);raise OSError('synthetic completion notification loss')
        return sync(path)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(module,'_write',failing_write);patch.setattr(Path,'rename',failing_rename)
            patch.setattr(module,'_fsync_dir',failing_sync)
            with pytest.raises(ManagedDownloadError):save(storage)
        assert fired==[point]
        stage=native_io_path(storage.root/'viewer-pages/incomplete')
        before={str(f.relative_to(stage)):f.read_bytes() for f in stage.rglob('*') if f.is_file()}
        result=save(storage);proof(storage,result)
        assert all((stage/name).read_bytes()==data for name,data in before.items())
        assert len(reader.images)==(1 if point=='after_rename' else 2)
        assert result.reused==(point=='after_rename')
        assert storage.saved_receipts()==()
    finally:storage.close()


@pytest.mark.parametrize('page,count,aid',[(-1,3,'456'),(3,3,'456'),(True,3,'456'),(0,True,'456'),(0,3,'../1')])
def test_invalid_selection_has_no_get_or_page_files(tmp_path,page,count,aid):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        with pytest.raises(ManagedDownloadError):storage.save_viewer_page('123',page,count,aid,lambda:record())
        assert not reader.images and not reader.originals
        assert not native_io_path(storage.root/'viewer-pages').exists()
    finally:storage.close()


def test_deep_path_uses_native_io_for_receipt_and_original(tmp_path):
    deep=tmp_path/('a'*70)/('b'*70)
    native_io_path(deep).mkdir(parents=True)
    reader=Reader();storage=owner(deep,reader)
    try:
        result=save(storage)
        assert len(str(storage.root/result.relative_image))>277
        assert native_io_path(storage.root/result.relative_image).read_bytes()==reader.bodies[1]
        proof(storage,result)
    finally:storage.close()


def test_foreign_namespace_and_links_are_not_adopted(tmp_path,monkeypatch):
    import pixivvault_nicegui.managed_viewer_page as module
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        pages=native_io_path(storage.root/'viewer-pages');pages.mkdir()
        unknown=pages/'unknown.txt';unknown.write_bytes(b'keep')
        with pytest.raises(ManagedDownloadError):save(storage)
        assert unknown.read_bytes()==b'keep' and not (pages/PAGE_MARKER).exists()
        assert not reader.images
    finally:storage.close()


def test_page_hardlink_is_rejected(tmp_path):
    import os
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        result=save(storage);image=native_io_path(storage.root/result.relative_image)
        alias=native_io_path(tmp_path/'alias.png');os.link(image,alias)
        with pytest.raises(ManagedDownloadError):save(storage)
        assert alias.read_bytes()==reader.bodies[1] and len(reader.images)==1
    finally:storage.close()


class Images:
    def url(self,wid,page=0,variant='view'):return f'/synthetic/{wid}/{page}/{variant}.png'
    def can_open(self,wid):return True
    def needs_metadata(self,wid):return False


def opened(services,client):
    with client:
        view=GalleryView(services);view.open('123');view.move_page(1)
        view._image_loaded(SimpleNamespace(args=view._image_url))
    return view


@pytest.mark.asyncio
async def test_real_viewer_handler_saves_page_only_failure_retry_and_late_page_change(tmp_path):
    reader=Reader();services=ApplicationServices(120,live_client=reader,pixiv_download_root=native_io_path(tmp_path/'managed'))
    services.artworks=(Artwork('123','synthetic','tester',page_count=3,author_id='456'),)
    original_images=services.images;services.images=Images()
    client=UIClient(ui.page('/_viewer_page_save'));task=None
    try:
        with client:
            view=opened(services,client)
            reader.fail=True
            handlers=[v.handler for v in view.viewer.download_button._event_listeners.values() if v.type=='click']
            assert len(handlers)==1
            handler=handlers[0]
            await handler(SimpleNamespace(args=str(view._request_serial)))
            assert '保存に失敗' in view.image_status.text and view.viewer.download_button.text=='個別DL'
            assert view.viewer.download_button.enabled and not view.viewer.retry_button.visible
            assert services.static_jobs.snapshot()==()
            reader.fail=False;reader.entered=Event();reader.release=Event()
            task=asyncio.create_task(handler(SimpleNamespace(args=str(view._request_serial))))
            assert await asyncio.to_thread(reader.entered.wait,5)
            assert view.viewer.download_button.text=='DL中'
            await handler(SimpleNamespace(args=str(view._request_serial)))
            assert len(reader.images)==2
            view.move_page(1)
            view._image_loaded(SimpleNamespace(args=view._image_url))
            reader.release.set();await task
            assert view.viewer.download_button.text=='個別DL' and '保存しました' not in view.image_status.text
            assert view.viewer.download_button.enabled
            reader.entered=None
            await handler(SimpleNamespace(args=str(view._request_serial)))
            assert view.viewer.download_button.text=='保存済' and 'このページを保存しました' in view.image_status.text
            assert reader.images==['https://i.pximg.net/synthetic/123/1.png']*2+['https://i.pximg.net/synthetic/123/2.png']
            assert services.static_jobs.snapshot()==() and services.managed_downloads.saved_receipts()==()
            view.close()
    finally:
        if reader.release:reader.release.set()
        if task:await task
        client.delete();services.images=original_images;services.close()


@pytest.mark.asyncio
async def test_service_cancel_drains_accepted_worker_and_shutdown_fences_client(tmp_path):
    reader=Reader();services=ApplicationServices(120,live_client=reader,pixiv_download_root=native_io_path(tmp_path/'managed'))
    reader.entered=Event();reader.release=Event();closed=Event();task=None;thread=None
    try:
        assert await services.save_viewer_page('123',1,3,'456',is_current=lambda:False) is None
        assert not reader.images
        task=asyncio.create_task(services.save_viewer_page('123',1,3,'456'))
        assert await asyncio.to_thread(reader.entered.wait,5)
        task.cancel()
        def shutdown():services.close();closed.set()
        thread=Thread(target=shutdown);thread.start()
        assert not reader.closed
        reader.release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert await asyncio.to_thread(closed.wait,5)
        assert reader.closed and len(reader.images)==1
        path=native_io_path(tmp_path/'managed/viewer-pages/work-123-page-2/receipt.json')
        assert json.loads(path.read_bytes())['page_no']==1
    finally:
        reader.release.set()
        if task and not task.done():
            try:await task
            except asyncio.CancelledError:pass
        if thread:await asyncio.to_thread(thread.join,5)
        services.close()


@pytest.mark.parametrize('changed', [dict(page_count=2),dict(author_id='999'),dict(illustration_type=2)])
def test_changed_remote_metadata_never_downloads_a_different_page(tmp_path,changed):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        with pytest.raises(ManagedDownloadError):
            storage.save_viewer_page('123',1,3,'456',lambda:replace(record(),**changed))
        assert reader.images==[] and reader.originals==[]
        assert not native_io_path(storage.root/'viewer-pages/work-123-page-2').exists()
    finally:storage.close()


def test_close_or_stale_image_event_never_admits_page_save(tmp_path):
    services=SimpleNamespace(artworks=(Artwork('123','synthetic','tester',page_count=3,author_id='456'),),
        images=Images(),managed_downloads=object())
    client=UIClient(ui.page('/_viewer_page_stale'))
    async def run():
        calls=[]
        async def unexpected(*args,**kwargs):calls.append(args);raise AssertionError('must not dispatch')
        services.save_viewer_page=unexpected
        with client:
            view=GalleryView(services);view.open('123');old=view._request_serial
            await view._save_visible_work(SimpleNamespace(args=str(old)))  # current image not decoded
            view.move_page(1)
            view._image_loaded(SimpleNamespace(args=view._image_url))
            await view._save_visible_work(SimpleNamespace(args=str(old)))
            view.close_viewer()
            await view._save_visible_work(SimpleNamespace(args=str(view._request_serial)))
            assert calls==[]
            view.close()
    try:asyncio.run(run())
    finally:client.delete()


@pytest.mark.parametrize('part',['folder','receipt','image'])
def test_reparse_attributes_reject_the_actual_page_read_path(tmp_path,monkeypatch,part):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        result=save(storage)
        receipt=native_io_path(storage.root/result.relative_receipt)
        image=native_io_path(storage.root/result.relative_image)
        target={'folder':receipt.parent,'receipt':receipt,'image':image}[part]
        original=Path.lstat;fired=[]
        def lstat(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if path==target:
                fired.append(str(path))
                return SimpleNamespace(st_mode=info.st_mode,st_nlink=info.st_nlink,
                    st_size=info.st_size,st_file_attributes=getattr(info,'st_file_attributes',0)|0x400)
            return info
        with monkeypatch.context() as patch:
            patch.setattr(Path,'lstat',lstat)
            with pytest.raises(ManagedDownloadError):save(storage)
        assert fired and len(reader.images)==1
        assert image.read_bytes()==reader.bodies[1]
    finally:storage.close()


def test_damaged_page_does_not_override_or_block_independent_page(tmp_path):
    reader=Reader();storage=owner(tmp_path,reader)
    try:
        first=save(storage,0);damaged=native_io_path(storage.root/first.relative_image)
        damaged.write_bytes(b'keep-damaged')
        second=save(storage,1)
        assert proof(storage,second)['page_no']==1
        assert damaged.read_bytes()==b'keep-damaged'
        with pytest.raises(ManagedDownloadError):save(storage,0)
        assert len(reader.images)==2
    finally:storage.close()
