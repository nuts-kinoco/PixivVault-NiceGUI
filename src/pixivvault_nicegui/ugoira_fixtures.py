"""Small generated RGB frame fixture; never opens a network transport."""
from io import BytesIO
import zipfile
from PIL import Image
from .media_parity_policy import parse_ugoira_manifest
from .ugoira_practical import PinnedUgoiraSource

class FixtureUgoiraReader:
    def __init__(self): self.calls=[]
    def close(self): pass
    def require_identity(self): return '91'
    def get_artwork_metadata_raw(self,wid,aid):
        if wid not in ('123','124') or aid!='91': raise ValueError('fixture selection rejected')
        self.calls.append(('detail',wid))
        return dict(id=wid,userId=aid,illustType=2,title='合成うごイラ'+wid,userName='合成作者')
    def get_ugoira_manifest(self,wid):
        self.calls.append(('manifest',wid))
        return parse_ugoira_manifest(dict(originalSrc=f'https://i.pximg.net/img-zip-ugoira/offline/{wid}_ugoira.zip',
            frames=[dict(file=f'{i:06}.png',delay=d) for i,d in enumerate((40,90,170))]),work_id=wid)
    def get_ugoira_archive(self,manifest):
        self.calls.append(('archive',manifest.work_id))
        out=BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as archive:
            for i,color in enumerate(('red','green','blue')):
                data=BytesIO()
                Image.new('RGB',(96,64),color).save(data,format='PNG')
                archive.writestr(f'{i:06}.png',data.getvalue())
        return out.getvalue()

def build_ugoira_fixture(): return PinnedUgoiraSource(FixtureUgoiraReader())