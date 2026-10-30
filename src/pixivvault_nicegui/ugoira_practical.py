"""Practical ugoira selection -> durable queue -> PR35 exports -> verified library."""
from .fs_paths import native_io_path
from copy import deepcopy
from dataclasses import dataclass, field
import base64
import hashlib
import json
from pathlib import Path
import re
from threading import local
from uuid import uuid4
from .managed_ugoira import ManagedUgoiraJobs, ManagedUgoiraError, _json, MAX_INTENT_BYTES
from .managed_downloads import _read_bounded, _safe_path, _write, _fsync_dir, _json_unique
from .media_parity_policy import parse_ugoira_manifest
from .ugoira_archive import inspect_ugoira_archive, MAX_ARCHIVE_BYTES
from . import ugoira_storage as storage
from .job_service import UNFINISHED

FORMATS={'gif':'GIF','mp4':'MP4','frames':'フォルダー'}

def identifier(value):
    if type(value) is not str or re.fullmatch(r'[1-9][0-9]{0,19}',value) is None: raise ManagedUgoiraError()
    return value

def digest(data): return hashlib.sha256(data).hexdigest()

class PinnedUgoiraSource:
    def __init__(self, reader): self.reader=reader
    def describe(self,wid,aid):
        identifier(wid); identifier(aid)
        self.reader.require_identity()
        body=self.reader.get_artwork_metadata_raw(wid,aid)
        if (str(body.get('id'))!=wid or str(body.get('userId'))!=aid or type(body.get('illustType')) is not int or body['illustType']!=2):
            raise ManagedUgoiraError()
        title=body.get('title'); author=body.get('userName')
        if any(type(t) is not str or not 0<len(t)<=256 or any(ord(c)<32 for c in t) for t in (title,author)):
            raise ManagedUgoiraError()
        manifest=self.reader.get_ugoira_manifest(wid)
        packet=dict(work_id=wid,author_id=aid,title=title,author=author,
                    frames=[dict(file=f.filename,delay=f.delay_ms) for f in manifest.frames])
        revision = body.get("updateDate")
        if type(revision) is str and revision:
            packet["update_date"] = revision
        return packet,manifest
    def archive(self,manifest): return self.reader.get_ugoira_archive(manifest)
    def close(self): self.reader.close()

@dataclass(frozen=True)
class UgoiraSelection:
    spec: dict = field(repr=False)
    saved: dict | None = field(repr=False,default=None)

class PracticalUgoiraJobs(ManagedUgoiraJobs):
    def __init__(self,root,source,*,codec_python=None):
        self.source=source
        self._fetch_state=local()
        super().__init__(root,codec_python=codec_python)
    def _spec(self,key):
        if re.fullmatch(r'[a-f0-9]{64}',key or '') is None: raise ManagedUgoiraError()
        folder=self.requests/key
        raw=_read_bounded(folder/'intent.json',MAX_INTENT_BYTES)
        if digest(raw)!=key: raise ManagedUgoiraError()
        obj=json.loads(raw,object_pairs_hook=_json_unique)
        if type(obj) is not dict or obj.get('version')!=2: raise ManagedUgoiraError()
        fields={'version','work_id','author_id','title','author','frames','output','predecessor'}
        if set(obj) not in (fields, fields | {'update_date'}): raise ManagedUgoiraError()
        if 'update_date' in obj and (type(obj['update_date']) is not str or not obj['update_date']): raise ManagedUgoiraError()
        identifier(obj['work_id']); identifier(obj['author_id'])
        if obj['output'] not in FORMATS: raise ManagedUgoiraError()
        parse_ugoira_manifest(dict(originalSrc=f"https://i.pximg.net/img-zip-ugoira/offline/{obj['work_id']}_ugoira.zip",frames=obj['frames']),work_id=obj['work_id'])
        return obj
    def preview(self,wid,aid,output):
        if output not in FORMATS: raise ManagedUgoiraError()
        if output=='mp4' and self.codec_python is None: raise ManagedUgoiraError('mp4_environment_unverified')
        packet,_=self.source.describe(wid,aid)
        saved=self.current(wid,output)
        spec=dict(version=2,**packet,output=output,predecessor=None if saved is None else saved['proof'])
        return UgoiraSelection(spec,saved)
    def enqueue_selection(self,selection,operation_id):
        if type(selection) is not UgoiraSelection: raise ManagedUgoiraError()
        if type(operation_id) is not str or not 1<=len(operation_id)<=128 or any(ord(c)<32 for c in operation_id): raise ManagedUgoiraError()
        spec=deepcopy(selection.spec)
        raw=_json(spec); key=digest(raw)
        if len(raw)>MAX_INTENT_BYTES: raise ManagedUgoiraError()
        with self._accept:
            if self._closing: raise ManagedUgoiraError()
            for job in self.jobs.snapshot():
                if operation_id in self.jobs.operation_ids(job.job_id):
                    if job.target_id!=key: raise ManagedUgoiraError()
                    return job
            current=self.current(spec['work_id'],spec['output'])
            if (None if current is None else current['proof'])!=spec['predecessor']: raise ManagedUgoiraError()
            for job in self.jobs.snapshot():
                other=self._spec(job.target_id)
                if job.state in UNFINISHED and (other['work_id'],other['output'])==(spec['work_id'],spec['output']):
                    if job.target_id!=key: raise ManagedUgoiraError()
            target=self.requests/key
            if not target.exists():
                with self._artifact:
                    self._budget(1,len(raw))
                    stage=self.request_incomplete/uuid4().hex
                    stage.mkdir()
                    _write(stage/'intent.json',raw)
                    _fsync_dir(stage)
                    storage._publish_directory(stage,target)
                    _fsync_dir(self.requests)
            self._spec(key)
            return self.jobs.enqueue('ugoira-'+spec['output'],key,
                title=f"うごイラ · {FORMATS[spec['output']]} · ID {spec['work_id']}",operation_id=operation_id)
    def _load(self,key,checkpoint=lambda:None):
        spec=self._spec(key)
        pointer=native_io_path(self.requests/key/'source-pointer.json')
        if not pointer.exists():
            if not getattr(self._fetch_state,'allowed',False): raise ManagedUgoiraError()
            checkpoint()
            packet,manifest=self.source.describe(spec['work_id'],spec['author_id'])
            keys=('work_id','author_id','title','author','frames')
            if {k:packet[k] for k in keys}!={k:spec[k] for k in keys}: raise ManagedUgoiraError()
            if 'update_date' in spec and packet.get('update_date')!=spec['update_date']: raise ManagedUgoiraError()
            checkpoint()
            raw=self.source.archive(manifest)
            if "update_date" in spec:
                after,_=self.source.describe(spec["work_id"],spec["author_id"])
                if after!=packet: raise ManagedUgoiraError()
            checkpoint()
            inspection=inspect_ugoira_archive(raw,manifest)
            with self._artifact:
                self._budget(3,len(raw)+2048)
                stage=self.request_incomplete/uuid4().hex
                stage.mkdir()
                _write(stage/'source.zip',raw)
                proof=dict(folder=stage.name,sha256=digest(raw))
                _write(stage/'source-proof.json',_json(proof))
                _fsync_dir(stage)
                temporary=stage/'pointer.json'
                # A prior uncommitted pointer is retained and fails closed.
                _write(temporary,_json(proof))
                _fsync_dir(self.request_incomplete)
                os_replace(temporary,pointer)
                _fsync_dir(pointer.parent)
        proof=json.loads(_read_bounded(pointer,1024),object_pairs_hook=_json_unique)
        if set(proof)!={'folder','sha256'} or re.fullmatch(r'[a-f0-9]{32}',proof['folder']) is None: raise ManagedUgoiraError()
        folder=self.request_incomplete/proof['folder']
        if _read_bounded(folder/'source-proof.json',1024)!=_json(proof): raise ManagedUgoiraError()
        raw=_read_bounded(folder/'source.zip',MAX_ARCHIVE_BYTES)
        if digest(raw)!=proof['sha256']: raise ManagedUgoiraError()
        manifest=parse_ugoira_manifest(dict(originalSrc=f"https://i.pximg.net/img-zip-ugoira/offline/{spec['work_id']}_ugoira.zip",frames=spec['frames']),work_id=spec['work_id'])
        checkpoint()
        return raw,manifest,spec['output'],inspect_ugoira_archive(raw,manifest)
    def run(self,job,context):
        self._fetch_state.allowed=True
        try:
            spec=self._spec(job.target_id)
            current=self.current(spec['work_id'],spec['output'])
            if (None if current is None else current['proof'])!=spec['predecessor']: raise ManagedUgoiraError()
            result=super().run(job,context)
            context.checkpoint()
            current=self.current(spec['work_id'],spec['output'])
            if (None if current is None else current['proof'])!=spec['predecessor']: raise ManagedUgoiraError()
            return result
        finally:
            self._fetch_state.allowed=False
    def entry(self,job):
        if job.state!='done': return None
        spec=self._spec(job.target_id)
        allowed=getattr(self._fetch_state,'allowed',False)
        self._fetch_state.allowed=False
        try: loaded=self._load(job.target_id)
        finally: self._fetch_state.allowed=allowed
        receipt=self._verified_result(job,loaded)
        if receipt is None or receipt!=job.result: raise ManagedUgoiraError()
        proof=dict(job_id=job.job_id,receipt=receipt,sha256=digest(_read_bounded(self.root/receipt,storage.MAX_RECEIPT_BYTES)))
        return dict(spec=spec,proof=proof,job_id=job.job_id)
    def _completed_jobs(self):
        # Durable accepted operation order identifies jobs after the display
        # window has retired them; the receipt is still verified by entry().
        ordered=[];seen=set()
        for _,jid,kind,_,_ in self.jobs.accepted_operations():
            if jid not in seen and kind in ('ugoira-gif','ugoira-mp4','ugoira-frames'):
                seen.add(jid);ordered.append(jid)
        for jid in reversed(ordered):
            job=self.jobs.retained_job(jid)
            if job is not None and job.state=='done':yield job
    def current(self,wid,output):
        for job in self._completed_jobs():
            if job.state!='done': continue
            spec=self._spec(job.target_id)
            if (spec['work_id'],spec['output'])==(wid,output): return self.entry(job)
        return None
    def library(self):
        entries={}
        for job in self._completed_jobs():
            if job.state!='done': continue
            spec=self._spec(job.target_id)
            key=(spec['work_id'],spec['output'])
            if key not in entries: entries[key]=self.entry(job)
        return tuple(entries[k] for k in sorted(entries))
    def playback(self,entry):
        spec=entry['spec']
        if self.current(spec['work_id'],spec['output'])!=entry: raise ManagedUgoiraError()
        job=self.jobs.retained_job(entry['job_id'])
        if job is None or job.state!='done':raise ManagedUgoiraError()
        raw,manifest,output,inspection=self._load(job.target_id)
        from io import BytesIO
        import zipfile
        from PIL import Image
        images=[]
        def add(data,delay,mime='image/png'):
            images.append(dict(src='data:'+mime+';base64,'+base64.b64encode(data).decode(),delay=delay))
        result_folder=(self.root/entry['proof']['receipt']).parent
        if output=='gif':
            with Image.open(BytesIO(_read_bounded(result_folder/'animation.gif',MAX_ARCHIVE_BYTES))) as gif:
                for index in range(gif.n_frames):
                    gif.seek(index)
                    png=BytesIO()
                    gif.convert('RGBA').save(png,format='PNG')
                    add(png.getvalue(),gif.info.get('duration',0))
        elif output=='mp4':
            packet=json.loads(_read_bounded(result_folder/'receipt.json',MAX_INTENT_BYTES*2))
            data=_read_bounded(result_folder/'decoded_frames.zip',MAX_ARCHIVE_BYTES)
            with zipfile.ZipFile(BytesIO(data)) as archive:
                for index in range(len(inspection.frames)):
                    add(archive.read(f'{index:06d}.png'),1000/packet['report']['fps'])
        else:
            source=self._find_source(job,inspection,lambda:None)
            folder=(self.frames.root/source.relative_receipt).parent
            for frame in inspection.frames:
                data=_read_bounded(folder/frame.filename,storage.MAX_SINGLE_FRAME_BYTES)
                mime={'png':'image/png','webp':'image/webp'}.get(frame.filename.rsplit('.',1)[-1].lower(),'image/jpeg')
                add(data,frame.delay_ms,mime)
        if self.current(spec['work_id'],spec['output'])!=entry: raise ManagedUgoiraError()
        # Zero-delay GIF frames advance immediately; an all-zero animation is
        # displayed as a still, without inventing a replacement duration.
        return dict(frames=images,width=max(f.width for f in inspection.frames),height=max(f.height for f in inspection.frames),
                    duration_ms=sum(f['delay'] for f in images),source_duration_ms=inspection.duration_ms)
    def close(self,*,timeout=10):
        super().close(timeout=timeout)
        self.source.close()

def os_replace(source,target):
    import os
    _safe_path(source,file=True)
    os.replace(native_io_path(source),native_io_path(target))