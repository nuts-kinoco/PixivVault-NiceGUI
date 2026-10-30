"""Generated, socket-free Pixiv fixtures for the P3 loopback product checker.

No tests modules, credential files, real images, model/GPU calls, or live clients.
"""
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image
from pixivvault_nicegui.bookmark_remote_adapter import RemoteBookmark
from pixivvault_nicegui.ugoira_fixtures import FixtureUgoiraReader
from pixivvault_nicegui.pixiv_read_client import _discovery_record

DATE='2026-10-26T00:00:00+00:00'
PREFIX='https://i.pximg.net/p3-generated/'

class FixtureReader(FixtureUgoiraReader):
    def __init__(self):
        super().__init__()
        data=BytesIO()
        Image.new('RGB',(96,64),(24,80,140)).save(data,format='PNG')
        self.png=data.getvalue()
    def require_identity(self):return '91'
    def verify_identity(self):return '91'
    def set_request_admission(self,*args,**kwargs):pass
    def close(self):pass
    def record(self,wid,aid='91'):
        wid=str(wid)
        # Use the real reader's pure normalization contract. Consumers may
        # legitimately require illust_type rather than its legacy type alias.
        return _discovery_record(dict(id=wid,title='Generated work '+wid,userId=aid,userName='Fixture author',
            url=PREFIX+wid+'.png',tags=['landscape','sky'],illustType=2 if wid=='123' else 0,
            pageCount=1,aiType=1,xRestrict=0,createDate=DATE,updateDate=DATE))
    def get_ranking(self,**kwargs):
        self.calls.append(('ranking',kwargs))
        return [dict(illust_id=str(i),title='Generated work '+str(i),user_id='91',
            user_name='Fixture author',url=PREFIX+str(i)+'.png',tags=['landscape','sky'],
            rank=i,yes_rank=i+1,rating_count=100,view_count=200,page_count=1,illust_type=0,ai_type=1)
            for i in range(1,31)]
    def get_user_works(self,user_id,early_exit_checker=None,lane='background'):
        self.calls.append(('author',user_id))
        rows=[]
        for i in range(201,241):
            row=self.record(i,user_id)
            if early_exit_checker is not None and early_exit_checker(row):break
            rows.append(row)
        return rows
    def get_recommended_users(self,user_id,**kwargs):
        self.calls.append(('related',user_id))
        return {'users':[],'works':[]}
    def get_following_users(self,*args,**kwargs):return ()
    def get_bookmarked_works(self,*args,**kwargs):return ()
    def get_bookmarked_novels(self,*args,**kwargs):return ()
    def search_recommendation_illusts(self,word,*,page=1,mode='all'):
        self.calls.append(('search',word,page,mode))
        start=1000+page*20+(0 if word=='landscape' else 100)
        return dict(works=[self.record(i) for i in range(start,start+20)],total=60)
    def search_illusts(self,word,**kwargs):
        return self.search_recommendation_illusts(word,**{k:v for k,v in kwargs.items() if k in ('page','mode')})
    def get_discovery_work(self,wid):
        self.calls.append(('detail',wid))
        return self.record(wid)
    def get_work_info(self,wid):return {'ai_type':1}
    def get_artwork_download_record(self,wid):
        if str(wid)=='123':raise ValueError('animated fixture must use the ugoira owner')
        self.calls.append(('download-record',wid))
        return RemoteBookmark(str(wid),'illustration','show','Generated work '+str(wid),'91',
            'Fixture author',('landscape','sky'),PREFIX+str(wid)+'.png',DATE,1,0,0,True)
    def get_artwork_pages(self,wid):return (PREFIX+str(wid)+'.png',)
    def get_artwork_original_pages(self,wid):
        self.calls.append(('original-pages',wid))
        return (PREFIX+str(wid)+'.png',)
    def get_image_bytes(self,url,**kwargs):
        if not url.startswith(PREFIX):raise AssertionError('unrecognized fixture image')
        self.calls.append(('image',urlsplit(url).path))
        return self.png
    def get_artwork_update_revision(self,wid,aid):
        return dict(work_id=str(wid),author_id=aid,page_count=1,illustration_type=2 if str(wid)=='123' else 0,update_date=DATE)
    def get_author_artworks_complete(self,aid):
        self.calls.append(('author-complete',aid))
        return (dict(id='701',user_id=aid,type=0,page_count=1,title='Generated static',update_date=DATE),
                dict(id='123',user_id=aid,type=2,page_count=1,title='Generated animation',update_date=DATE))
    def get_author_artworks(self,aid):return self.get_author_artworks_complete(aid)
    def get_artwork_metadata_raw(self,wid,aid):
        if wid=='123':
            return dict(super().get_artwork_metadata_raw(wid,aid),updateDate=DATE)
        return dict(id=wid,userId=aid,illustType=0,title='Generated static',userName='Fixture author',
            pageCount=1,updateDate=DATE)
    def get_author_novel_ids(self,aid):
        self.calls.append(('author-novel',aid))
        return ('501',)
    def get_novel_payload(self,wid,aid):
        self.calls.append(('novel',wid))
        return dict(id=wid,userId=aid,userName='Fixture author',title='Generated novel',
            content='Generated fixture text.[newpage]Second page.',
            seriesNavData=None,textEmbeddedImages={},updateDate=DATE)

class UnavailableCrypto:
    available=False

class NoVaultTransport:
    def __getattr__(self,name):
        raise AttributeError(name)
    def close(self):pass


def create_services(app,sandbox,*,reader=None):
    from pixivvault_nicegui.services import get_services
    from pixivvault_nicegui.managed_novel_jobs import PinnedNovelSource
    from pixivvault_nicegui.ugoira_practical import PinnedUgoiraSource
    from pixivvault_nicegui.pixiv_read_client import VaultImageReader
    sandbox=Path(sandbox)
    sandbox.mkdir(parents=True,exist_ok=True)
    reader=FixtureReader() if reader is None else reader
    services=get_services(app,live_client=reader,pixiv_download_root=sandbox/'static',
        novel_fixture_root=sandbox/'novel',novel_fixture_source=PinnedNovelSource(reader,{}),
        ugoira_root=sandbox/'ugoira',ugoira_source=PinnedUgoiraSource(reader),
        preferences_root=sandbox/'preferences',discovery_root=sandbox/'discovery',
        recommendation_preferences_root=sandbox/'recommendations',
        image_cache_root=sandbox/'image-cache',discovery_following=(),
        external_vault_root=sandbox/'external',external_vault_crypto=UnavailableCrypto(),
        external_vault_transport=NoVaultTransport())
    # Product constructs a public CDN reader lazily; replace it before any route,
    # tab or image request can run. The same generated PNG source serves all lanes.
    if services._public_images is not None:services._public_images.close()
    services._public_images=reader
    services._vault_images=VaultImageReader(services.external_vault,reader)
    return services,reader
