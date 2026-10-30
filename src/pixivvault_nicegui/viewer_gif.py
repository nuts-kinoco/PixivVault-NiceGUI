"""Save the exact displayed GIF; no remote reads or alternate-format conversion."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import re
from uuid import uuid4
from PIL import Image
from .managed_downloads import _safe_path, _read_bounded, _write, _fsync_dir, _json_unique, MAX_RECEIPT_BYTES
from .managed_viewer_page import _prepare, selection, SavedViewerPage, PAGE_MARKER, PAGE_IDENTITY
from .fs_paths import native_io_path
from .ugoira_gif import MAX_GIF_BYTES
from .ugoira_archive import MAX_FRAME_PIXELS, MAX_TOTAL_DECODED_PIXELS
from .media_parity_policy import MAX_UGOIRA_FRAMES


def metadata(record):
    if record.kind != 'illustration' or record.illustration_type != 2:
        raise ValueError('displayed animation required')
    value = dict(work_id=record.remote_id, author_id=record.author_id,
                 title=record.title, author_name=record.author_name)
    return validate_metadata(value)


def validate_metadata(value):
    if type(value) is not dict or set(value) != {'work_id','author_id','title','author_name'}:
        raise ValueError('animation identity rejected')
    selection(value['work_id'],0,1,value['author_id'])
    if value['author_id'] is None:
        raise ValueError('animation author required')
    for key in ('title','author_name'):
        if type(value[key]) is not str or len(value[key])>256 or any(ord(c)<32 or ord(c)==127 for c in value[key]):
            raise ValueError('animation metadata rejected')
    return value


def gif_info(data):
    if type(data) is not bytes or not 0<len(data)<=MAX_GIF_BYTES or not data.endswith(b';'):
        raise ValueError('displayed GIF rejected')
    with Image.open(BytesIO(data),formats=('GIF',)) as image:
        if not 0<image.width*image.height<=MAX_FRAME_PIXELS or image.info.get('loop')!=0:
            raise ValueError('displayed GIF geometry rejected')
        delays=[]
        for index in range(MAX_UGOIRA_FRAMES+1):
            try:image.seek(index)
            except EOFError:break
            if index==MAX_UGOIRA_FRAMES or image.width*image.height*(index+1)>MAX_TOTAL_DECODED_PIXELS:
                raise ValueError('displayed GIF frame limit')
            image.load()
            delays.append(image.info.get('duration',0))
        return dict(width=image.width,height=image.height,delays=delays)


@dataclass(frozen=True)
class DisplayedGif:
    record: object = field(repr=False)
    data: bytes = field(repr=False)
    sha256: str

    def verified(self):
        value=metadata(self.record)
        if type(self.sha256) is not str or sha256(self.data).hexdigest()!=self.sha256:
            raise ValueError('displayed GIF changed')
        return value,gif_info(self.data)


def read_gif(owner, relative, expected):
    match=re.fullmatch(r'viewer-pages/work-([1-9][0-9]{0,19})-gif/receipt.json',relative or '')
    if match is None:raise ValueError('viewer GIF path rejected')
    root=native_io_path(owner.root)/'viewer-pages'
    if json.loads(_read_bounded(root/PAGE_MARKER,4096),object_pairs_hook=_json_unique)!=PAGE_IDENTITY:
        raise ValueError('viewer root rejected')
    folder=root/('work-'+match[1]+'-gif');_safe_path(folder)
    raw=_read_bounded(folder/'receipt.json',MAX_RECEIPT_BYTES)
    if sha256(raw).hexdigest()!=expected:raise ValueError('viewer GIF receipt changed')
    packet=json.loads(raw,object_pairs_hook=_json_unique)
    if (type(packet) is not dict or set(packet)!={'version','scope','record','saved_at','file'}
            or type(packet['version']) is not int or packet['version']!=1 or packet['scope']!='pixiv-viewer-gif'):
        raise ValueError('viewer GIF receipt rejected')
    meta=validate_metadata(packet['record'])
    if meta['work_id']!=match[1] or type(packet['saved_at']) is not str or datetime.fromisoformat(packet['saved_at']).tzinfo is None:
        raise ValueError('viewer GIF identity rejected')
    entry=packet['file']
    if (type(entry) is not dict or set(entry)!={'name','bytes','sha256','width','height','delays'}
            or any(type(entry[k]) is not int for k in ('bytes','width','height'))
            or type(entry['delays']) is not list or any(type(d) is not int or d<0 for d in entry['delays'])):
        raise ValueError('viewer GIF file schema rejected')
    data=_read_bounded(folder/'animation.gif',MAX_GIF_BYTES)
    info=gif_info(data)
    if packet['file']!=dict(name='animation.gif',bytes=len(data),sha256=sha256(data).hexdigest(),**info):
        raise ValueError('viewer GIF bytes changed')
    if {p.name for p in folder.iterdir()}!={'animation.gif','receipt.json'}:
        raise ValueError('unknown viewer GIF content')
    return meta,data,packet['saved_at']


def save_gif(owner,display):
    if type(display) is not DisplayedGif:raise ValueError('displayed GIF required')
    meta,info=display.verified()
    with owner._page_writer:
        if owner.closed:raise ValueError('managed storage closed')
        pages,staging=_prepare(owner);wid=meta['work_id'];folder=pages/('work-'+wid+'-gif')
        _safe_path(folder);relative='viewer-pages/work-'+wid+'-gif/receipt.json'
        if folder.exists():
            raw=_read_bounded(folder/'receipt.json',MAX_RECEIPT_BYTES);digest=sha256(raw).hexdigest()
            old,body,_=read_gif(owner,relative,digest)
            if old!=meta or body!=display.data:raise ValueError('saved viewer GIF changed; preservation policy required')
            return SavedViewerPage(wid,0,1,relative,digest,relative.rsplit('/',1)[0]+'/animation.gif',True)
        stage=staging/('partial-'+uuid4().hex);stage.mkdir()
        _write(stage/'animation.gif',display.data)
        packet=dict(version=1,scope='pixiv-viewer-gif',record=meta,saved_at=datetime.now(timezone.utc).isoformat(),
                    file=dict(name='animation.gif',bytes=len(display.data),sha256=display.sha256,**info))
        raw=json.dumps(packet,sort_keys=True).encode('ascii')
        if len(raw)>MAX_RECEIPT_BYTES:raise ValueError('viewer GIF receipt limit')
        _write(stage/'receipt.json',raw);_fsync_dir(stage)
        stage.rename(folder);_fsync_dir(pages)
        digest=sha256(raw).hexdigest();read_gif(owner,relative,digest)
        return SavedViewerPage(wid,0,1,relative,digest,relative.rsplit('/',1)[0]+'/animation.gif',False)
