"""Original viewer save layout/history/GIF: synthetic owners and real handlers."""
import asyncio
import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from threading import Event
import zipfile
import pytest
from nicegui import Client,ui
from PIL import Image
from test_viewer_page_save import Reader,record
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.viewer_exports import read,recent,saved_folder
from pixivvault_nicegui.viewer_gif import DisplayedGif,save_gif,read_gif
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.viewer_backup_view import ViewerBackupView
from pixivvault_nicegui.desktop_integration import DesktopIntegration


def application(root,reader):
    root=native_io_path(root);root.mkdir(parents=True,exist_ok=True)
    s=ApplicationServices(120,live_client=reader,pixiv_download_root=root/'managed',preferences_root=root/'preferences')
    s.configure_save_exports(root/'exports')
    return s


async def save_complete_work(services):
    # Use the product's durable acceptance/runner binding. Calling the low-level
    # owner directly bypasses its required frozen per-job record installation.
    accepted=await services.save_explicit_work('123','parity-full-work')
    assert accepted is not None
    def wait():
        jobs=services.static_jobs
        with jobs._condition:
            assert jobs._condition.wait_for(lambda:any(
                j.job_id==accepted.job_id and j.state in ('done','error','stopped')
                for j in jobs.snapshot()),5)
            final=next(j for j in jobs.snapshot() if j.job_id==accepted.job_id)
        assert final.state=='done', (final.state,final.error_code)
        return final
    return await asyncio.to_thread(wait)


def setting(s,folder):
    snap=s.preferences.snapshot()
    s.preferences.update({'use_work_folder':'1' if folder else '0'},expected_revision=snap.revision)


def output(s,result):
    assert result.export_manifest and not result.export_error
    manifest=read(s.save_exports,result.export_manifest)
    return native_io_path(s.save_exports.projection.root)/manifest['output']['relative']


@pytest.mark.asyncio
@pytest.mark.parametrize('folder,expected',[(False,'tester(456)/synthetic(123)_2.png'),(True,'tester(456)/synthetic(123)/1.png')])
async def test_original_layout_original_bytes_and_history_restart(tmp_path,folder,expected):
    reader=Reader();s=application(tmp_path,reader);setting(s,folder)
    try:
        saved=await s.save_viewer_page('123',1,3,'456')
        path=output(s,saved)
        assert path.relative_to(native_io_path(s.save_exports.projection.root)).as_posix()==expected
        assert path.read_bytes()==reader.bodies[1]
        assert path.stat().st_nlink==1
        assert not s.managed_downloads.saved_receipts() and not s.static_jobs.snapshot()
        rows,errors=await s.recent_viewer_backups()
        assert not errors and len(rows)==1 and rows[0]['author_id']=='456'
        assert rows[0]['manifest']==saved.export_manifest
        first_time=rows[0]['saved_at'];before_calls=list(reader.images)
        s.close();s=application(tmp_path,reader)
        again=await s.save_viewer_page('123',1,3,'456')
        assert again.export_manifest==saved.export_manifest and reader.images==before_calls
        rows,errors=await s.recent_viewer_backups()
        assert not errors and rows[0]['saved_at']==first_time
        assert not s.saved_discovery_ids()  # partial history does not suppress full download
        await save_complete_work(s)
        assert s.managed_downloads.verified_saved_receipt('123')[1]['page_count']==3
        assert len(reader.images)==4
    finally:s.close()


@pytest.mark.asyncio
async def test_v1_page_upgrade_retains_proof_and_fetches_only_missing_filename(tmp_path):
    reader=Reader();s=application(tmp_path,reader)
    try:
        first=s.managed_downloads.save_viewer_page('123',1,3,'456',lambda:record())
        path=native_io_path(s.managed_downloads.root)/first.relative_receipt
        packet=json.loads(path.read_bytes());packet['version']=1;packet.pop('original_name');path.write_text(json.dumps(packet),encoding='utf-8')
        old=path.read_bytes();setting(s,True)
        saved=await s.save_viewer_page('123',1,3,'456')
        assert output(s,saved).name=='1.png'
        assert path.read_bytes()==old and len(reader.images)==1 and len(reader.originals)==2
    finally:s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['image','manifest'])
async def test_projection_failure_retry_reuses_receipt_and_never_false_history(tmp_path,monkeypatch,phase):
    import pixivvault_nicegui.viewer_exports as ve
    reader=Reader();s=application(tmp_path,reader);real=ve.publish_bytes;fired=[]
    def fail(path,data,check):
        if (str(path).endswith('.viewer-export.json'))==(phase=='manifest'):
            fired.append(str(path));raise OSError('injected export publication failure')
        return real(path,data,check)
    try:
        monkeypatch.setattr(ve,'publish_bytes',fail)
        saved=await s.save_viewer_page('123',1,3,'456')
        assert fired and saved.export_error and not saved.export_manifest
        assert (await s.recent_viewer_backups())[0]==()
        monkeypatch.setattr(ve,'publish_bytes',real)
        retry=await s.save_viewer_page('123',1,3,'456')
        assert output(s,retry).read_bytes()==reader.bodies[1] and len(reader.images)==1
        assert len((await s.recent_viewer_backups())[0])==1
    finally:s.close()


@pytest.mark.asyncio
async def test_projection_edit_conflict_no_overwrite_source_unchanged_other_author_continues(tmp_path):
    reader=Reader();s=application(tmp_path,reader)
    try:
        first=await s.save_viewer_page('123',1,3,'456');path=output(s,first)
        cas=native_io_path(s.managed_downloads.root)/first.relative_image;before=cas.read_bytes()
        path.write_bytes(b'user edit')
        retry=await s.save_viewer_page('123',1,3,'456')
        assert retry.export_error and path.read_bytes()==b'user edit' and cas.read_bytes()==before
        assert native_io_path(saved_folder(s.save_exports,first.export_manifest))==path.parent
        reader.get_artwork_download_record=lambda wid:replace(record(wid),author_id='789',author_name='second')
        other=await s.save_viewer_page('124',0,3,'789')
        assert output(s,other).read_bytes()==reader.bodies[0]
        rows,errors=await s.recent_viewer_backups()
        assert not errors and {r['author_id'] for r in rows}=={'456','789'}
    finally:s.close()


@pytest.mark.asyncio
async def test_unknown_export_links_rejected_and_deep_path_kept(tmp_path):
    root=tmp_path
    while len(str(root))<285:root/='deep-synthetic-segment'
    s=application(root,Reader())
    try:
        saved=await s.save_viewer_page('123',1,3,'456')
        path=output(s,saved);assert len(str(path))>285 and path.read_bytes()
        assert (await s.recent_viewer_backups())[1]==()
        # Unknown manifest cannot inject an arbitrary saved-folder target.
        manifest=native_io_path(s.save_exports.root)/saved.export_manifest
        raw=json.loads(manifest.read_bytes());raw['output']['relative']='../outside.png'
        manifest.write_text(json.dumps(raw),encoding='utf-8')
        rows,errors=await s.recent_viewer_backups()
        assert not rows and errors==(saved.export_manifest,)
        with pytest.raises(ValueError):saved_folder(s.save_exports,saved.export_manifest)
    finally:s.close()


class AnimationReader(Reader):
    def __init__(self):
        super().__init__();self.zip_calls=0;self.meta_calls=0
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as archive:
            for i,body in enumerate(self.bodies):archive.writestr(f'{i}.png',body)
        self.archive=out.getvalue()
        self.manifest=parse_ugoira_manifest(dict(originalSrc='https://i.pximg.net/img-zip-ugoira/offline/299_ugoira.zip',
            frames=[dict(file=f'{i}.png',delay=d) for i,d in enumerate((100,240,70))]),work_id='299')
    def get_ugoira_manifest(self,wid):
        assert wid=='299';self.meta_calls+=1;return self.manifest
    def get_ugoira_archive(self,manifest):
        assert manifest==self.manifest;self.zip_calls+=1;return self.archive


def animated_record():return replace(record('299'),illustration_type=2,page_count=1,title='animation')


@pytest.mark.asyncio
@pytest.mark.parametrize('folder',[False,True])
async def test_displayed_gif_exact_bytes_delay_order_and_no_get_when_saving(tmp_path,folder):
    reader=AnimationReader();s=application(tmp_path,reader);setting(s,folder)
    remote=RemoteBookmarkImages(reader,(animated_record(),))
    try:
        display=await asyncio.to_thread(remote.display_animation,'299')
        assert reader.zip_calls==1 and reader.meta_calls==1
        with Image.open(io.BytesIO(display.data)) as gif:
            assert gif.n_frames==3 and gif.size==(9,7)
            delays=[];colors=[]
            for i in range(3):gif.seek(i);delays.append(gif.info['duration']);colors.append(gif.convert('RGB').getpixel((0,0)))
        assert delays==[100,240,70] and colors==[(255,0,0),(0,128,0),(0,0,255)]
        saved=await s.save_viewer_gif(display)
        path=output(s,saved)
        expected='tester(456)/animation(299)/animation(299).gif' if folder else 'tester(456)/animation(299).gif'
        assert path.relative_to(native_io_path(s.save_exports.projection.root)).as_posix()==expected
        assert path.read_bytes()==display.data
        meta,data,date=read_gif(s.managed_downloads,saved.relative_receipt,saved.receipt_sha256)
        assert data==display.data and meta['work_id']=='299'
        assert (reader.zip_calls,reader.meta_calls)==(1,1)
        assert not s.static_jobs.snapshot() and not s.managed_downloads.saved_receipts()
        assert s.ugoira is None  # no MP4/frames/full-work job was fabricated
        again=await s.save_viewer_gif(display)
        assert again.reused and again.export_manifest==saved.export_manifest
        before=path.read_bytes();path.write_bytes(b'editable projection')
        assert read_gif(s.managed_downloads,saved.relative_receipt,saved.receipt_sha256)[1]==before
    finally:remote.close();s.close()


@pytest.mark.asyncio
async def test_real_viewer_gif_decode_then_individual_handler_and_close(tmp_path):
    reader=AnimationReader();s=application(tmp_path,reader);remote=RemoteBookmarkImages(reader,(animated_record(),))
    s.artworks=(animated_record().as_artwork(),)
    s.images.close();s.images=LibraryImageRegistry(DemoImageCatalog(s.artworks));pin=s.images.acquire_catalog(remote)
    client=Client(ui.page('/_viewer_gif_parity'))
    try:
        with client:
            view=GalleryView(s);view._image_provider=pin;view.open('299')
            await view._animation_task
            display=view._displayed_gif;assert display is not None
            uri=view.viewer_image.props['src'];assert base64.b64decode(uri.split(',',1)[1])==display.data
            generation=str(view._request_serial)
            await view._save_visible_work(SimpleNamespace(args=generation))
            assert not (native_io_path(s.managed_downloads.root)/'viewer-pages').exists()  # not yet decoded
            view._image_loaded(SimpleNamespace(args=view._image_url))
            await view._save_visible_work(SimpleNamespace(args=generation))
            assert view.viewer.download_button.text=='保存済'
            assert 'GIF' in view.viewer.image_status.text
            assert reader.zip_calls==1
            rows,_=await s.recent_viewer_backups();assert rows[0]['work_id']=='299'
            view.close_viewer();assert view._displayed_gif is None
            await view._save_visible_work(SimpleNamespace(args=generation))
            assert reader.zip_calls==1
            view.close()
    finally:pin.close();client.delete();s.close()


@pytest.mark.asyncio
async def test_real_history_grouping_folder_handler_and_stale_close(tmp_path):
    s=application(tmp_path,Reader());opened=[]
    s.desktop=DesktopIntegration(allowed_roots=(s.save_exports.root,),environment={},
        launcher=SimpleNamespace(open_default=lambda p:opened.append(p)))
    client=Client(ui.page('/_viewer_backup_history'))
    try:
        await s.save_viewer_page('123',0,3,'456');await s.save_viewer_page('123',1,3,'456')
        with client:
            view=ViewerBackupView(s);view.active=True
            await view.refresh()
            assert len(view.rows)==1 and set(view.openers)=={'456'}
            button=view.openers['456']
            handler=next(e.handler for e in button._event_listeners.values() if e.type=='click')
            await handler(None)
            assert len(opened)==1 and opened[0].name=='tester(456)'
            view.close();await handler(None);assert len(opened)==1
    finally:client.delete();s.close()


@pytest.mark.asyncio
async def test_delayed_gif_load_after_close_cannot_display_or_save(tmp_path):
    reader=AnimationReader();entered=Event();release=Event();original=reader.get_ugoira_archive
    def delayed(manifest):
        entered.set();assert release.wait(5);return original(manifest)
    reader.get_ugoira_archive=delayed
    s=application(tmp_path,reader);s.artworks=(animated_record().as_artwork(),)
    s.images.close();s.images=LibraryImageRegistry(DemoImageCatalog(s.artworks))
    pin=s.images.acquire_catalog(RemoteBookmarkImages(reader,(animated_record(),)))
    client=Client(ui.page('/_late_viewer_gif'));task=None
    try:
        with client:
            view=GalleryView(s);view._image_provider=pin;view.open('299');task=view._animation_task
            assert await asyncio.to_thread(entered.wait,5)
            old_generation=str(view._request_serial);view.close_viewer();release.set();await task
            assert view._displayed_gif is None and view.viewer_image.props['src']==''
            await view._save_visible_work(SimpleNamespace(args=old_generation))
            assert not (native_io_path(s.managed_downloads.root)/'viewer-pages').exists()
            view.close()
    finally:
        release.set()
        if task:await task
        pin.close();client.delete();s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['receipt','rename'])
async def test_gif_atomic_publication_failure_no_history_and_retry_no_refetch(tmp_path,monkeypatch,fault):
    import pixivvault_nicegui.viewer_gif as vg
    reader=AnimationReader();s=application(tmp_path,reader);remote=RemoteBookmarkImages(reader,(animated_record(),));fired=[]
    try:
        display=remote.display_animation('299')
        raw_write=vg._write;rename=Path.rename
        def bad_write(path,data):
            if path.name=='receipt.json':fired.append('receipt');raise OSError('synthetic receipt failure')
            return raw_write(path,data)
        def bad_rename(path,target):
            if Path(target).name=='work-299-gif':fired.append('rename');raise OSError('synthetic rename failure')
            return rename(path,target)
        if fault=='receipt':monkeypatch.setattr(vg,'_write',bad_write)
        else:monkeypatch.setattr(Path,'rename',bad_rename)
        with pytest.raises(OSError):await s.save_viewer_gif(display)
        assert fired==[fault] and (await s.recent_viewer_backups())[0]==()
        assert not (native_io_path(s.managed_downloads.root)/'viewer-pages/work-299-gif').exists()
        monkeypatch.setattr(vg,'_write',raw_write);monkeypatch.setattr(Path,'rename',rename)
        saved=await s.save_viewer_gif(display)
        assert output(s,saved).read_bytes()==display.data and reader.zip_calls==1
    finally:remote.close();s.close()


@pytest.mark.asyncio
async def test_corrupt_gif_and_unknown_extra_data_rejected_without_deletion(tmp_path):
    reader=AnimationReader();s=application(tmp_path,reader);remote=RemoteBookmarkImages(reader,(animated_record(),))
    try:
        display=remote.display_animation('299')
        with pytest.raises(ValueError):await s.save_viewer_gif(replace(display,data=b'bad'))
        assert not (native_io_path(s.managed_downloads.root)/'viewer-pages').exists()
        saved=await s.save_viewer_gif(display);folder=(native_io_path(s.managed_downloads.root)/saved.relative_receipt).parent
        unknown=folder/'unknown.json';unknown.write_bytes(b'preserve unknown evidence')
        with pytest.raises(ValueError):await s.save_viewer_gif(display)
        assert unknown.read_bytes()==b'preserve unknown evidence' and reader.zip_calls==1
    finally:remote.close();s.close()


@pytest.mark.asyncio
async def test_complete_receipt_reuse_exports_the_chosen_page_without_changing_catalog(tmp_path):
    reader=Reader();s=application(tmp_path,reader)
    try:
        await save_complete_work(s)
        before=s.managed_downloads.saved_receipts();calls=list(reader.images)
        saved=await s.save_viewer_page('123',2,3,'456')
        assert saved.reused and output(s,saved).name=='synthetic(123)_3.png'
        assert output(s,saved).read_bytes()==reader.bodies[2]
        assert s.managed_downloads.saved_receipts()==before and reader.images==calls
    finally:s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('known_name',[False,True])
async def test_unknown_author_name_uses_original_fallback_and_pins_replay(tmp_path,known_name):
    from pixivvault_nicegui.library_models import LibrarySnapshot
    reader=Reader();reader.get_artwork_download_record=lambda wid:replace(record(wid),author_name='456')
    s=application(tmp_path,reader)
    try:
        root=native_io_path(s.save_exports.projection.root)
        (root/'Z existing(456)').mkdir();(root/'A existing(456)').mkdir()
        if known_name:s.library=LibrarySnapshot((),author_names={'456':'Cached author'})
        saved=await s.save_viewer_page('123',1,3,'456')
        folder='Cached author(456)' if known_name else 'A existing(456)'
        assert output(s,saved).parent==root/folder
        assert native_io_path(saved_folder(s.save_exports,saved.export_manifest))==root/folder
        # Later directory/cache changes cannot repin a completed acceptance.
        (root/'0 later(456)').mkdir()
        s.library=LibrarySnapshot((),author_names={'456':'Different later author'})
        again=await s.save_viewer_page('123',1,3,'456')
        assert again.export_manifest==saved.export_manifest and output(s,again).parent==root/folder
        assert len(reader.images)==1
    finally:s.close()


@pytest.mark.asyncio
async def test_real_save_handler_distinguishes_source_save_and_history_failure_then_retry(tmp_path,monkeypatch):
    import pixivvault_nicegui.viewer_exports as ve
    reader=Reader();s=application(tmp_path,reader)
    s.artworks=(record().as_artwork(),);s.images.close()
    s.images=LibraryImageRegistry(DemoImageCatalog(s.artworks))
    client=Client(ui.page('/_viewer_export_failure'));fired=[];real=ve.publish_bytes
    def fail(path,data,check):
        fired.append(path.name);raise OSError('synthetic projection failure')
    try:
        with client:
            view=GalleryView(s);view.open('123',1)
            view._image_loaded(SimpleNamespace(args=view._image_url))
            monkeypatch.setattr(ve,'publish_bytes',fail)
            await view._save_visible_work(SimpleNamespace(args=str(view._request_serial)))
            assert fired and view.viewer.download_button.text=='個別DL'
            assert '画像は保存済み' in view.viewer.image_status.text
            assert '履歴の記録に失敗' in view.viewer.image_status.text
            assert view.viewer.download_button.enabled and len(reader.images)==1
            assert (await s.recent_viewer_backups())[0]==()
            monkeypatch.setattr(ve,'publish_bytes',real)
            await view._save_visible_work(SimpleNamespace(args=str(view._request_serial)))
            assert view.viewer.download_button.text=='保存済' and len(reader.images)==1
            assert len((await s.recent_viewer_backups())[0])==1
            view.close()
    finally:client.delete();s.close()
