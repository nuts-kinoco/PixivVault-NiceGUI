"""Original individual-DL layout/history in the existing owned export projection.

No old folders, ZIPs, complete-work flags or source receipts are modified.
The publication manifest is both replay evidence and the recent-backup entry.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from .bookmark_projection import publish_bytes, arcname
from .legacy_zip_export import legacy_filename, _INVALID_FILENAME, EXPORT_MARKER, EXPORT_IDENTITY
from .managed_downloads import (ManagedPageQuarantined, _read_bounded, _safe_path,
                                _json_unique, MAX_RECEIPT_BYTES)
from .managed_viewer_page import _read, original_name, selection, PAGE_MARKER, PAGE_IDENTITY
from .fs_paths import native_io_path, logical_path
from .save_exports import packet, digest


def source(owner,identity):
    if (type(identity) is not dict or set(identity)!={'work_id','page_no','page_count','receipt','sha256','use_work_folder','original_name'}
            or type(identity['use_work_folder']) is not bool or type(identity['sha256']) is not str
            or re.fullmatch(r'[a-f0-9]{64}',identity['sha256']) is None or type(identity['receipt']) is not str):
        raise ValueError('viewer export identity rejected')
    wid=identity['work_id'];page=identity['page_no'];count=identity['page_count']
    selection(wid,page,count,None)
    relative=identity['receipt'];expected=identity['sha256'];name=identity['original_name']
    if name is not None:original_name(name)
    if relative==f'viewer-pages/work-{wid}-gif/receipt.json':
        from .viewer_gif import read_gif
        if page!=0 or count!=1 or name is not None:raise ValueError('GIF page rejected')
        meta,data,date=read_gif(owner,relative,expected)
        return meta,data,date,'gif'
    owner.assert_page_visible(wid,page)
    if relative==f'viewer-pages/work-{wid}-page-{page+1}/receipt.json':
        pages=native_io_path(owner.root)/'viewer-pages'
        if json.loads(_read_bounded(pages/PAGE_MARKER,4096),object_pairs_hook=_json_unique)!=PAGE_IDENTITY:
            raise ValueError('viewer source owner rejected')
        folder=native_io_path(owner.root)/relative.rsplit('/',1)[0]
        row,actual=_read(folder,wid,page,count,None)
        if actual!=expected:raise ValueError('viewer page proof changed')
        r=row['record'];entry=row['file']
        if row.get('original_name') is not None and name is not None and row['original_name']!=name:
            raise ValueError('original filename changed')
        data=_read_bounded(folder/entry['name'],entry['bytes'])
        meta=dict(work_id=wid,author_id=r['author_id'],author_name=r['author_name'],title=r['title'])
        owner.assert_page_visible(wid,page)
        return meta,data,row['saved_at'],entry['name'].rsplit('.',1)[1]
    row,actual=owner._receipt(relative,verify_pages=False)
    if actual!=expected or row['work_id']!=wid or row['page_count']!=count:
        raise ValueError('complete page proof changed')
    data=owner.read_saved_page(relative,page,expected)
    meta={k:row[k] for k in ('work_id','author_id','author_name','title')}
    return meta,data,row['saved_at'],row['files'][page]['name'].rsplit('.',1)[1]


def author_folder(ex,meta,root):
    author=_INVALID_FILENAME.sub('_',meta['author_name'].strip())
    aid=meta['author_id']
    if author and author!=aid:return original_name(author+'('+aid+')')
    # Original fallback order: available author directory, then the first
    # existing folder in lexical order. Only this owned projection is searched.
    cached=ex.s.library_snapshot().author_names.get(aid,'')
    if cached and cached!=aid:return original_name(_INVALID_FILENAME.sub('_',cached)+'('+aid+')')
    for node in sorted(root.iterdir(),key=lambda p:p.name):
        if node.name.endswith('('+aid+')'):
            _safe_path(node,file=node.is_file())
            if node.is_dir():return original_name(node.name)
    return None


def destination(identity,meta,ext,folder_name):
    wid=meta['work_id'];title=_INVALID_FILENAME.sub('_',meta['title'].strip() or wid)
    author=_INVALID_FILENAME.sub('_',meta['author_name'].strip());aid=meta['author_id']
    if folder_name is not None:
        original_name(folder_name)
        if not folder_name.endswith('('+aid+')'):raise ValueError('viewer author folder rejected')
    if author and author!=aid and folder_name!=author+'('+aid+')':
        raise ValueError('viewer author folder binding rejected')
    folder=Path(folder_name) if folder_name is not None else Path('.')
    if identity['use_work_folder']:folder/=title[:30]+'('+wid+')'
    if ext=='gif':filename=arcname(title+'.gif',wid)
    elif identity['use_work_folder']:
        filename=original_name(identity['original_name'])
        endings={'.jpg','.jpeg'} if ext=='jpg' else {'.'+ext}
        if Path(filename).suffix.lower() not in endings:raise ValueError('original filename format rejected')
    else:filename=legacy_filename(title,wid,identity['page_no'],identity['page_count'],ext)
    for part in (*folder.parts,filename):original_name(part)
    return (folder/filename).as_posix()


def _owned(ex):
    if ex.projection.closed or ex.s.closing:raise ValueError('export closed')
    root=native_io_path(ex.projection.root)
    if json.loads(_read_bounded(root/EXPORT_MARKER,4096),object_pairs_hook=_json_unique)!=EXPORT_IDENTITY:
        raise ValueError('projection owner rejected')
    return root


def publish(ex,saved,*,use_work_folder,original_loader=None):
    # Caller serializes individual save acceptance. Exclusive publish_bytes also
    # arbitrates collisions with automatic author/bookmark output without overwrite.
    root=_owned(ex);name=None;relative=saved.relative_receipt
    is_gif=relative.endswith('-gif/receipt.json')
    if not is_gif:
        ex.s.managed_downloads.assert_page_visible(saved.work_id,saved.page_no)
    if use_work_folder and not is_gif:
        if relative.startswith('viewer-pages/'):
            raw=json.loads(_read_bounded(native_io_path(ex.s.managed_downloads.root)/relative,MAX_RECEIPT_BYTES),object_pairs_hook=_json_unique)
            name=raw.get('original_name')
        if name is None:
            if original_loader is None:raise ValueError('original page filename unavailable')
            name=original_loader()
        original_name(name)
    identity=dict(work_id=saved.work_id,page_no=saved.page_no,page_count=saved.source_page_count,
                  receipt=relative,sha256=saved.receipt_sha256,use_work_folder=use_work_folder,original_name=name)
    with ex.s.managed_downloads.work_lock(saved.work_id):
        return _publish_identity(ex,root,identity)


def _publish_identity(ex,root,identity):
    meta,data,date,ext=source(ex.s.managed_downloads,identity)
    body_sha=sha256(data).hexdigest()
    manifest=native_io_path(ex.root)/(digest(identity)+'.viewer-export.json')
    if manifest.exists():
        old=read(ex,manifest.name)
        if old['identity']!=identity:raise ValueError('viewer export changed')
        ex._owned_bytes(root/old['output']['relative'],body_sha)
        return manifest.name
    folder_name=author_folder(ex,meta,root)
    target=destination(identity,meta,ext,folder_name)
    output=dict(relative=target,sha256=body_sha,bytes=len(data))
    publish_bytes(root/target,data,lambda p:ex._owned_bytes(p,body_sha))
    value=dict(version=1,identity=identity,author_folder=folder_name,output=output,saved_at=datetime.now(timezone.utc).isoformat())
    encoded=packet(value)
    def existing(path):
        # A publication already proven by another acceptance is reusable only
        # with the same content; its original timestamp remains authoritative.
        prior=read(ex,path.name)
        if prior['identity']!=identity or prior['output']!=output:raise ValueError('viewer export collision')
    publish_bytes(manifest,encoded,existing)
    read(ex,manifest.name)
    return manifest.name


def read(ex,name):
    if type(name) is not str or re.fullmatch(r'[a-f0-9]{64}\.viewer-export\.json',name) is None:
        raise ValueError('viewer export manifest name rejected')
    _owned(ex)
    raw=_read_bounded(native_io_path(ex.root)/name,MAX_RECEIPT_BYTES)
    value=json.loads(raw,object_pairs_hook=_json_unique)
    if (type(value) is not dict or set(value)!={'version','identity','author_folder','output','saved_at'}
            or type(value['version']) is not int or value['version']!=1
            or name!=digest(value['identity'])+'.viewer-export.json'
            or type(value['saved_at']) is not str or datetime.fromisoformat(value['saved_at']).tzinfo is None):
        raise ValueError('viewer export manifest rejected')
    meta,data,date,ext=source(ex.s.managed_downloads,value['identity'])
    expected=dict(relative=destination(value['identity'],meta,ext,value['author_folder']),bytes=len(data),sha256=sha256(data).hexdigest())
    if value['output']!=expected:raise ValueError('viewer export binding rejected')
    return value


def recent(ex):
    """Original author grouping / latest timestamp / 60 authors; partial != complete."""
    import base64
    from io import BytesIO
    from PIL import Image
    root=_owned(ex);authors={};errors=[]
    for path in native_io_path(ex.root).glob('*.viewer-export.json'):
        try:
            value=read(ex,path.name);meta,data,_,_=source(ex.s.managed_downloads,value['identity'])
            aid=meta['author_id'];old=authors.get(aid)
            if old is not None and old['saved_at']>=value['saved_at']:continue
            with Image.open(BytesIO(data)) as image:
                image.thumbnail((192,192));out=BytesIO();image.convert('RGBA').save(out,format='PNG')
            authors[aid]=dict(meta,manifest=path.name,saved_at=value['saved_at'],
                thumbnail='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii'),
                relative=value['output']['relative'])
        except ManagedPageQuarantined:
            # An intentional exclusion is not corrupt history. Retain the
            # immutable publication proof but do not offer the page again.
            continue
        except (OSError,ValueError,KeyError,TypeError):
            # Corruption is explicit and local; no raw title/path in public errors.
            errors.append(path.name if re.fullmatch(r'[a-f0-9]{64}\.viewer-export\.json',path.name) else 'invalid-manifest')
    return tuple(sorted(authors.values(),key=lambda v:v['saved_at'],reverse=True)[:60]),tuple(errors)


def saved_folder(ex,manifest):
    value=read(ex,manifest);relative=PurePosixPath(value['output']['relative'])
    root=_owned(ex);target=root/relative
    # Current user-editable projection may differ from source; do not overwrite,
    # but still reject unsafe links and missing targets when opening its folder.
    _safe_path(target,file=True)
    if not target.is_file():raise ValueError('saved projection unavailable')
    folder=root/value['author_folder'] if value['author_folder'] is not None else root
    _safe_path(folder)
    return logical_path(folder)
