"""CoS-only original-layout/history/displayed-GIF fixture. No real transport."""
import argparse
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import zipfile
from nicegui import app,ui
from PIL import Image
from serve_viewer_page_fixture import FixtureReader
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.fs_paths import native_io_path,logical_path
from pixivvault_nicegui.managed_downloads import _safe_path
from pixivvault_nicegui.media_parity_policy import parse_ugoira_manifest
from pixivvault_nicegui.remote_media import RemoteBookmarkImages
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.desktop_integration import DesktopIntegration
from pixivvault_nicegui.ui.shell import DemoShell
from pixivvault_nicegui.viewer_exports import read


class Reader(FixtureReader):
    def __init__(self):
        super().__init__();self.gifs=[]
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as archive:
            for i,body in enumerate(self.bodies):archive.writestr(f'{i}.png',body)
        self.archive=out.getvalue()
        self.manifest=parse_ugoira_manifest(dict(originalSrc='https://i.pximg.net/img-zip-ugoira/offline/299_ugoira.zip',
            frames=[dict(file=f'{i}.png',delay=d) for i,d in enumerate((100,240,70))]),work_id='299')
    def get_ugoira_manifest(self,wid):
        if wid!='299':raise ValueError('fixture identity only')
        self.calls.append(('ugoira_meta',wid));return self.manifest
    def get_ugoira_archive(self,manifest):
        if manifest!=self.manifest:raise ValueError('fixture manifest only')
        self.calls.append(('ugoira_zip','299'))
        if self.fail:raise OSError('synthetic animation failure')
        return self.archive
    def get_artwork_metadata_raw(self,wid,aid):
        if (wid,aid)==('299','789'):
            return dict(id=wid,userId=aid,illustType=2,title='合成うごイラ',userName='合成動画作者',
                description='<p>100 / 240 / 70 ms</p>',tags={'tags':[{'tag':'fixture'}]},
                createDate='2026-10-28',bookmarkCount=0,viewCount=0,width=24,height=18)
        return super().get_artwork_metadata_raw(wid,aid)
    def get_image_bytes(self,url):
        if url=='https://i.pximg.net/offline-preview/299/0.png':return self.preview
        return super().get_image_bytes(url)


class FixtureLauncher:
    def __init__(self,root):self.root=logical_path(root);self.opened=[]
    def open_default(self,path):
        # Observe the actual product desktop handler's validated target. Never
        # launch another app from this synthetic fixture.
        self.opened.append(logical_path(path).relative_to(self.root).as_posix())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args();allowed=Path(__file__).resolve().parents[1]/'artifacts/p3-viewer-save-parity-20261028'
    root=args.root.absolute()
    if root==allowed or not root.is_relative_to(allowed) or not 1024<=args.port<=65535 or args.port==8089:
        parser.error('dedicated artifact subdirectory and free port required')
    root=native_io_path(root);_safe_path(root);root.mkdir(parents=True,exist_ok=True)
    marker=root/'.viewer-parity-fixture.json';identity={'purpose':'synthetic-viewer-parity','version':1}
    if marker.exists():
        _safe_path(marker,file=True)
        if json.loads(marker.read_bytes())!=identity:parser.error('fixture marker mismatch')
    else:
        if any(root.iterdir()):parser.error('nonempty unknown fixture root')
        with marker.open('x',encoding='utf-8') as stream:json.dump(identity,stream)
    reader=Reader();s=ApplicationServices(120,live_client=reader,pixiv_download_root=root/'managed',preferences_root=root/'preferences')
    s.configure_save_exports(root/'exports')
    static=replace(reader.get_artwork_download_record('123'),thumbnail='https://i.pximg.net/offline-preview/123/0.png')
    animation=RemoteBookmark('299','illustration','show','合成うごイラ','789','合成動画作者',('fixture',),
        'https://i.pximg.net/offline-preview/299/0.png','2026-10-28T00:00:00Z',1,2,0)
    s.artworks=(static.as_artwork(),animation.as_artwork())
    s.images.close();s.images=LibraryImageRegistry(DemoImageCatalog(s.artworks))
    s.images.publish(None,RemoteBookmarkImages(reader,(static,animation)))
    launcher=FixtureLauncher(root);s.desktop=DesktopIntegration(allowed_roots=(s.save_exports.root,),launcher=launcher,environment={})
    register_media_routes(app,s.images)
    completed=[]
    page_save=s.save_viewer_page;gif_save=s.save_viewer_gif
    async def observed_page(*args,**kwargs):
        result=await page_save(*args,**kwargs)
        completed.append(dict(kind='page',saved=result is not None,export_error=getattr(result,'export_error',False)))
        return result
    async def observed_gif(*args,**kwargs):
        result=await gif_save(*args,**kwargs)
        completed.append(dict(kind='gif',saved=result is not None,export_error=getattr(result,'export_error',False)))
        return result
    s.save_viewer_page=observed_page;s.save_viewer_gif=observed_gif
    @app.get('/_fixture/state')
    def state():
        manifests=[]
        for path in native_io_path(s.save_exports.root).glob('*.viewer-export.json'):
            value=read(s.save_exports,path.name)
            out=native_io_path(s.save_exports.projection.root)/value['output']['relative']
            _safe_path(out,file=True)
            manifests.append(dict(name=path.name,identity=value['identity'],output=value['output'],
                actual_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),saved_at=value['saved_at']))
        return dict(calls=list(reader.calls),opened=list(launcher.opened),exports=manifests,completed=list(completed),
            static_jobs=len(s.static_jobs.snapshot()),complete_receipts=len(s.managed_downloads.saved_receipts()),
            expected_pages=[hashlib.sha256(body).hexdigest() for body in reader.bodies])
    @ui.page('/')
    def page():
        ui.checkbox('合成取得失敗',on_change=lambda e:setattr(reader,'fail',e.value)).props('data-testid=fixture-save-fail')
        DemoShell(s)
    app.on_shutdown(s.close)
    ui.run(host='127.0.0.1',port=args.port,show=False,reload=False,title='PV viewer save parity fixture')


if __name__=='__main__':main()
