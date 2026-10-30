"""Original novel/ugoira compatibility names over verified saved media only.

CAS is read-only. User-editable destinations link ONLY a projection-owned copy.
No rendering/transcoding, ZIP mutation, network, or destructive update is used.
"""
from pathlib import Path,PurePosixPath
from hashlib import sha256
from uuid import uuid4
import json,os,re,logging
from .fs_paths import native_io_path
from .managed_downloads import _read_bounded,_write,_safe_path,_fsync_dir
from .legacy_zip_export import _INVALID_FILENAME
from . import ugoira_storage
from .managed_ugoira import MAX_GIF_BYTES


def arcname(name,wid):
    name=Path(name).name
    if '('+wid+')' in name:return name
    stem,ext=os.path.splitext(name)
    match=re.fullmatch(r'(.*)_(\d+)',stem)
    return (match[1]+'('+wid+')_'+match[2] if match else stem+'('+wid+')')+ext


def _largest_storage(nodes, category, warning=None):
    if not nodes:return None
    # Original core._safe_file_size uses stat size, not recursive contents.
    def size(node):
        try:return node.stat().st_size
        except OSError:return 0
    chosen=max(nodes,key=size)
    if len(nodes)>1:
        message=f'同じ作者の{category}が{len(nodes)}個あります。一番大きいものを使います: {chosen.name}'
        if warning is not None:warning(message)
        else:logging.getLogger(__name__).warning('author layout: 複数の候補から一番大きいものを使います')
    return chosen


def author_folder(root, aid, warning=None):
    """Original physical folder membership, scoped to the owned projection only."""
    matches = []
    base = native_io_path(root)
    _safe_path(base)
    for node in base.iterdir():
        if node.name.endswith('(' + aid + ')') and not node.name.startswith('☆ブックマーク('):
            # Original isdir ignores an extensionless regular file. Links and
            # reparse points are still rejected rather than classified away.
            if node.is_symlink() or getattr(node.lstat(),'st_file_attributes',0)&0x400:
                raise ValueError('unsafe author folder')
            if node.is_dir():
                _safe_path(node);matches.append(node)
    chosen=_largest_storage(matches,'フォルダ',warning)
    return chosen.name if chosen else None


def legacy_membership(root, aid, warning=None):
    """Read physical master membership only; never traverse bookmark wrappers."""
    if root is None:return None
    from .local_media_adapter import _safe_file
    base=native_io_path(root);_safe_path(base)
    if not base.is_dir():raise ValueError('legacy master unavailable')
    found=[]
    for node in base.iterdir():
        if node.name.startswith('☆ブックマーク('):continue
        if node.name.endswith('('+aid+')') or node.name.lower().endswith('('+aid+').zip'):
            if not _safe_file(node,base):raise ValueError('unsafe legacy author storage')
            if node.is_dir():found.append(('folder',node.name))
            elif node.is_file() and node.suffix.lower()=='.zip':found.append(('zip',node.name))
    folders=[base/name for kind,name in found if kind=='folder']
    zips=[base/name for kind,name in found if kind=='zip']
    chosen=_largest_storage(folders,'フォルダ',warning)
    if _largest_storage(zips,'ZIP',warning) is not None:
        raise ValueError('legacy ZIP author layout requires accepted update policy')
    return chosen.name if chosen else None


def destinations(identity,title,author,relative):
    """Heavy-owner compatibility layout; direct intentionally flattens like Flet."""
    safe_title=_INVALID_FILENAME.sub('_',title);aid=identity['author_id'];wid=identity['work_id']
    author_dir=_INVALID_FILENAME.sub('_',author)+'('+aid+')'
    master=identity.get('author_folder') or author_dir
    if type(master) is not str or Path(master).name!=master or master in ('.','..') or not master.endswith('('+aid+')'):
        raise ValueError('unsafe author folder')
    if Path(author_dir).name!=author_dir or author_dir in ('.','..') or not author_dir.endswith('('+aid+')'):
        raise ValueError('unsafe author folder')
    folder=safe_title[:30]+'('+wid+')'
    if identity.get('output_scope')=='author':
        suffix=Path(folder)/relative if identity['use_work_folder'] else Path(relative)
        return (Path(master)/suffix,)
    bookmark='☆ブックマーク('+identity['account']+')'
    if identity['mode']=='direct':
        flat=Path(bookmark)/(author_dir+'_'+arcname(relative,wid))
        suffix=Path(folder)/relative if identity['use_work_folder'] else Path(relative)
        return (flat,Path(master)/suffix) if identity.get('author_folder') else (flat,)
    if identity['mode']!='link':raise ValueError('unknown bookmark layout')
    suffix=Path(folder)/relative if identity['use_work_folder'] else Path(relative)
    mirror=Path(bookmark)/author_dir/suffix
    # Missing field means an old accepted intent: preserve its heavy layout.
    return (mirror,) if 'author_folder' in identity and identity['author_folder'] is None else (Path(master)/suffix,mirror)


def novel_snapshot(services,wid,fmt):
    saved=services.novel_jobs.current(wid,fmt)
    if saved is None:raise ValueError('saved novel unavailable')
    root=native_io_path(services.novel_jobs.store.root);folder=(root/saved.relative_receipt).parent
    raw=_read_bounded(root/saved.relative_receipt,65536)
    if sha256(raw).hexdigest()!=saved.receipt_sha256:raise ValueError('novel receipt changed')
    receipt=json.loads(raw)
    from .novel_save_presentation import NovelSavePresentation
    title,author=NovelSavePresentation(services)._saved_metadata(saved)
    files=[]
    safe=_INVALID_FILENAME.sub('_',title)+'('+wid+')'
    for row in receipt['outputs']:
        name=row['name'];path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('unsafe novel output')
        if name in ('novel.txt','novel.epub'):relative=safe+Path(name).suffix
        else:relative=name
        files.append(dict(path=folder/name,relative=relative,sha256=row['sha256'],bytes=row['bytes']))
    return dict(proof=saved.receipt_sha256,author_id=receipt['author_id'],title=title,author=author,files=files)


def ugoira_snapshot(services,wid,fmt,use_work_folder=False):
    owner=services.ugoira;entry=owner.current(wid,fmt)
    if entry is None:raise ValueError('saved ugoira unavailable')
    spec=entry['spec'];folder=(owner.root/entry['proof']['receipt']).parent
    title=spec['title'];safe=_INVALID_FILENAME.sub('_',title)+'('+wid+')';files=[]
    if fmt in ('gif','mp4'):
        name='animation.'+fmt;path=folder/name
        # current() verified the complete output and its receipt before this read.
        data=_read_bounded(path,MAX_GIF_BYTES)
        files.append(dict(path=path,relative=safe+'.'+fmt,sha256=sha256(data).hexdigest(),bytes=len(data)))
    elif fmt=='frames':
        receipt=json.loads(_read_bounded(folder/'receipt.json',ugoira_storage.MAX_RECEIPT_BYTES))
        frame_dir=_INVALID_FILENAME.sub('_',title)[:30]+'('+wid+')'
        for frame in receipt['frames']:
            name=frame['file']
            relative=name if use_work_folder else frame_dir+'/'+name
            files.append(dict(path=folder/name,relative=relative,sha256=frame['sha256'],bytes=frame['bytes']))
        data=_read_bounded(folder/'ugoira_meta.json',65536)
        relative='ugoira_meta.json' if use_work_folder else frame_dir+'/ugoira_meta.json'
        files.append(dict(path=folder/'ugoira_meta.json',relative=relative,sha256=sha256(data).hexdigest(),bytes=len(data)))
    else:raise ValueError('unsupported ugoira format')
    return dict(proof=entry['proof']['sha256'],author_id=spec['author_id'],title=title,author=spec['author'],files=files)


def snapshot(services,kind,wid,fmt,use_work_folder=False):
    return novel_snapshot(services,wid,fmt) if kind=='novel' else ugoira_snapshot(services,wid,fmt,use_work_folder)


def publish_bytes(path,data,check):
    """Existing ZIP exporter pattern: exclusive create, never replace an edited file."""
    path=native_io_path(path);_safe_path(path.parent);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():check(path);return
    stage=path.parent/('.projection-'+uuid4().hex+'.tmp')
    _write(stage,data)
    try:
        try:
            if os.name=='nt':os.rename(stage,path)  # Windows rename is exclusive, unlike POSIX.
            else:os.link(stage,path)
        except FileExistsError:check(path)
        _fsync_dir(path.parent)
    finally:
        # Only the temporary name created by this call, never CAS/user output.
        if stage.exists():stage.unlink()


def projection_data(identity,source,file,data):
    """Rewrite only adopted external references in editable direct copies."""
    if identity['mode']!='direct' or identity.get('reference_layout')!=1:return data
    names={entry['relative']:destinations(identity,source['title'],source['author'],entry['relative'])[0].name
           for entry in source['files']}
    if identity['media']=='novel' and file['relative'].endswith('.txt'):
        # Keep BOM/newlines and all unrelated text exactly as stored. Only the
        # existing generated asset marker with a known receipt output changes.
        text=data.decode('utf-8')
        for relative,name in names.items():
            if relative.startswith('images/'):
                text=text.replace('[挿絵: '+relative+']','[挿絵: '+name+']')
        return text.encode('utf-8')
    if identity['media']=='ugoira' and identity['format']=='frames' and Path(file['relative']).name=='ugoira_meta.json':
        metadata=json.loads(data)
        frame_names={Path(relative).name:name for relative,name in names.items()
                     if Path(relative).name!='ugoira_meta.json'}
        for frame in metadata['frames']:
            if frame['file'] not in frame_names:raise ValueError('unknown frame reference')
            frame['file']=frame_names[frame['file']]
        return (json.dumps(metadata,ensure_ascii=True,separators=(',',':'))+'\n').encode('utf-8')
    return data


def export_media(exports,identity,context,manifest):
    services=exports.s;kind=identity['media'];wid=identity['work_id'];fmt=identity['format']
    source=snapshot(services,kind,wid,fmt,identity['use_work_folder'])
    if (source['proof'],source['author_id'])!=(identity['sha256'],identity['author_id']):raise ValueError('media generation changed')
    base=native_io_path(exports.projection.root);records=[];seen=set()
    for file in source['files']:
        context.checkpoint();data=_read_bounded(native_io_path(file['path']),file['bytes'])
        if len(data)!=file['bytes'] or sha256(data).hexdigest()!=file['sha256']:raise ValueError('saved media bytes changed')
        # Namespace by media/ID, then bytes. TXT retained across TXT/BOTH shares
        # its owned body; equal numeric novel/illustration IDs never alias.
        original_data=data
        for relative in destinations(identity,source['title'],source['author'],file['relative']):
            if str(relative) in seen:raise ValueError('flattened projection filename collision')
            seen.add(str(relative))
            layout=identity
            if identity['mode']=='direct' and identity.get('author_folder') and relative.parts[0]==identity['author_folder']:
                layout=dict(identity,mode='link')  # master keeps nested original references
            data=projection_data(layout,source,file,original_data)
            projected_sha=sha256(data).hexdigest()
            body=base/'.bodies'/(kind+'-'+wid)/projected_sha
            exports._io_operation='media_projection_copy'
            publish_bytes(body,data,lambda p:exports._owned_bytes(p,projected_sha))
            target=base/relative
            exports._io_operation='media_projection_hardlink';_safe_path(target.parent);target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                exports._owned_bytes(target,projected_sha)
                if not os.path.samefile(body,target):raise ValueError('unowned media projection collision')
            else:os.link(body,target)
            record=dict(path=str(relative),sha256=projected_sha,bytes=len(data))
            if identity.get('reference_layout')==1:
                record.update(source_sha256=file['sha256'],source_bytes=file['bytes'])
            records.append(record)
    current=snapshot(services,kind,wid,fmt,identity['use_work_folder'])
    if current['proof']!=source['proof']:raise ValueError('media generation changed')
    data=json.dumps(dict(source=identity,files=records),sort_keys=True,separators=(',',':')).encode()
    exports._io_operation='media_projection_manifest'
    def verify(path):
        if _read_bounded(path,2_000_000)!=data:raise ValueError('projection manifest changed')
    publish_bytes(manifest,data,verify);context.checkpoint()
    return str(manifest.relative_to(exports.root))
