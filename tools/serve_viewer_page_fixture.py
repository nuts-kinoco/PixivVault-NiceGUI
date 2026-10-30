"""CoS-only single-page GUI acceptance; synthetic originals and real services.

No HTTP transport, Cookie, user paths, GPU, or automatic saving. --root is
restricted to this candidate's artifacts/p3-viewer-page-save-20261028 folder.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
from nicegui import app, ui
from PIL import Image
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.fs_paths import native_io_path
from pixivvault_nicegui.managed_downloads import _safe_path
from pixivvault_nicegui.models import Artwork
from pixivvault_nicegui.media import DemoImageCatalog
from pixivvault_nicegui.library_media_registry import LibraryImageRegistry
from pixivvault_nicegui.media_routes import register_media_routes
from pixivvault_nicegui.services import ApplicationServices
from pixivvault_nicegui.ui.gallery import GalleryView
from pixivvault_nicegui.ui.theme import CSS


class FixtureReader:
    def __init__(self):
        self.calls=[];self.fail=False;self.bodies=[]
        for color in ['red','green','blue']:
            stream=io.BytesIO();Image.new('RGB',(24,18),color).save(stream,format='PNG')
            self.bodies.append(stream.getvalue())
        stream=io.BytesIO();Image.new('RGB',(12,9),'white').save(stream,format='PNG')
        self.preview=stream.getvalue()
    def require_identity(self):return '456'
    def get_artwork_metadata_raw(self,wid,aid):
        if (wid,aid)!=('123','456'):raise ValueError('fixture identity only')
        return dict(id=wid,userId=aid,description='<p>合成3ページ</p>',tags={'tags':[{'tag':'fixture'}]},
            createDate='2026-10-28',bookmarkCount=0,viewCount=1,width=24,height=18)
    def get_user_works(self,aid,*,lane):
        if aid!='456' or lane!='interactive':raise ValueError('fixture author only')
        return [dict(id='123',user_id='456',title='合成3ページ',user_name='合成作者',tags=['fixture'],
            thumbnail_url='https://i.pximg.net/offline-preview/123/0.png',page_count=3,illust_type=0,x_restrict=0)]
    def get_artwork_pages(self,wid):
        if wid!='123':raise ValueError('fixture work only')
        return tuple(f'https://i.pximg.net/offline-preview/123/{i}.png' for i in range(3))
    def verify_identity(self):return '456'
    def get_artwork_download_record(self,wid):
        if wid!='123':raise ValueError('fixture work only')
        self.calls.append(('metadata',wid))
        return RemoteBookmark(wid,'illustration','show','合成3ページ','456','合成作者',
            ('fixture',),'','2026-10-28T00:00:00Z',3,0,0)
    def get_artwork_original_pages(self,wid):
        if wid!='123':raise ValueError('fixture work only')
        self.calls.append(('original_pages',wid))
        return tuple(f'https://i.pximg.net/offline-original/123/{i}.png' for i in range(3))
    def get_image_bytes(self,url):
        if url in self.get_artwork_pages('123'):return self.preview
        allowed=self.get_urls()
        if url not in allowed:raise ValueError('fixture URL only')
        index=allowed.index(url);self.calls.append(('original_image',index))
        if self.fail:raise OSError('synthetic save failure')
        return self.bodies[index]
    def get_urls(self):return tuple(f'https://i.pximg.net/offline-original/123/{i}.png' for i in range(3))
    def close(self):pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    allowed=Path(__file__).resolve().parents[1]/'artifacts/p3-viewer-page-save-20261028'
    root=args.root.absolute()
    if (not 1024<=args.port<=65535 or args.port==8089 or not root.is_relative_to(allowed)
            or root==allowed):parser.error('dedicated artifact subdirectory and free port required')
    target=native_io_path(root);_safe_path(target)
    target.mkdir(parents=True,exist_ok=True)
    marker=target/'.viewer-page-fixture.json';identity={'purpose':'synthetic-viewer-page-fixture','version':1}
    if marker.exists():
        _safe_path(marker,file=True)
        if json.loads(marker.read_bytes())!=identity:parser.error('fixture marker mismatch')
    else:
        if any(target.iterdir()):parser.error('nonempty unmarked root')
        with marker.open('x',encoding='utf-8') as stream:json.dump(identity,stream)
    reader=FixtureReader()
    services=ApplicationServices(120,live_client=reader,pixiv_download_root=target/'managed')
    art=(Artwork('123','合成3ページ','合成作者',author_id='456',page_count=3),)
    services.images.close()
    services.artworks=art
    services.images=LibraryImageRegistry(DemoImageCatalog(art))
    register_media_routes(app,services.images)

    @app.get('/_fixture/state')
    def state():
        pages=target/'managed/viewer-pages'
        proofs=[]
        if pages.exists():
            for receipt in sorted(pages.glob('work-*-page-*/receipt.json')):
                _safe_path(receipt,file=True);raw=receipt.read_bytes();value=json.loads(raw)
                image=receipt.parent/value['file']['name'];_safe_path(image,file=True)
                proofs.append({'receipt':str(receipt.relative_to(target)), 'sha256':hashlib.sha256(raw).hexdigest(),
                    'page_no':value['page_no'],'source_page_count':value['record']['page_count'],
                    'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest()})
        return {'calls':list(reader.calls),'fail':reader.fail,'proofs':proofs,
            'expected_image_sha256':[hashlib.sha256(b).hexdigest() for b in reader.bodies],
            'whole_work_jobs':len(services.static_jobs.snapshot()),
            'complete_receipts':len(services.managed_downloads.saved_receipts())}

    @ui.page('/')
    def page():
        ui.add_css(CSS);dark=ui.dark_mode(True)
        with ui.column().classes('pv-shell'):
            with ui.row().classes('pv-header'):
                ui.label('合成: 表示ページだけ保存').classes('pv-title')
                ui.button('明暗を切替',on_click=dark.toggle)
                ui.checkbox('合成保存失敗',on_change=lambda e:setattr(reader,'fail',e.value)).props('data-testid=fixture-save-fail')
            with ui.element('main').classes('pv-content'):GalleryView(services)
    app.on_shutdown(services.close)
    ui.run(host='127.0.0.1',port=args.port,show=False,reload=False,title='PV single-page synthetic fixture')


if __name__=='__main__':main()
