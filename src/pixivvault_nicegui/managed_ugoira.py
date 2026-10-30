"""Managed ugoira jobs, durable inputs, frames/GIF/MP4 and verified receipts.

Owns a JobService operation ledger and restart reconciliation. The practical
adapter connects this owner to ApplicationServices/UI. Private marked roots
and a CPU codec (an explicit interpreter or the bundled native executable)
are required; no ambient credentials or default library are consulted.
"""
from __future__ import annotations
from .fs_paths import native_io_path,logical_path

from .request_policy import job_request_runner

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import Lock
import time
from uuid import uuid4

from .job_service import JobCancelled, JobService
from .managed_downloads import _fsync_dir, _json_unique, _read_bounded, _safe_path, _write
from .media_parity_policy import parse_ugoira_manifest
from .ugoira_archive import MAX_ARCHIVE_BYTES, inspect_ugoira_archive
from . import ugoira_storage as storage
from .ugoira_storage import UgoiraFrameStore
from .ugoira_gif import MAX_GIF_BYTES, MAX_GIF_SECONDS, _verify_gif, render_ugoira_gif

MARKER = '.pixivvault-managed-ugoira.json'
_IDENTITY = b'{"application":"pixivvault-nicegui","purpose":"managed-ugoira-jobs","version":1}\n'
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_JOB = re.compile(r'[a-f0-9]{32}\Z')
MAX_INTENT_BYTES = 512 * 1024
MAX_CONTROL_GENERATIONS = 256
MAX_CONTROL_FILES = 1024
MAX_CONTROL_BYTES = 256 * 1024 * 1024


class ManagedUgoiraError(ValueError):
    def __init__(self, code='managed_ugoira_failed'):
        if code not in {'managed_ugoira_failed', 'mp4_environment_unverified'}:
            code = 'managed_ugoira_failed'
        self.code = code
        super().__init__(code)


class _DiagnosticRejected(ManagedUgoiraError):
    def __init__(self,reason):
        super().__init__();self.diagnostic_reason=reason


def _json(payload):
    return (json.dumps(payload, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii')


def _intent(raw, manifest, output):
    inspection = inspect_ugoira_archive(raw, manifest)
    packet = {'version': 1, 'work_id': inspection.work_id, 'output': output,
              'zip_sha256': hashlib.sha256(raw).hexdigest(),
              'frames': [{'file': frame.filename, 'delay': frame.delay_ms} for frame in inspection.frames]}
    payload = _json(packet)
    if len(payload) > MAX_INTENT_BYTES:
        raise ManagedUgoiraError()
    return payload, inspection


def _candidate(job):
    if (type(job.job_id) is not str or _JOB.fullmatch(job.job_id) is None
            or type(job.target_id) is not str or _SHA.fullmatch(job.target_id) is None
            or job.kind not in {'ugoira-frames', 'ugoira-gif', 'ugoira-mp4'}
            or type(job.attempt) is not int or job.attempt < 1):
        raise ManagedUgoiraError()
    return hashlib.sha256(f'{job.job_id}\0{job.target_id}\0{job.attempt}\0{job.kind}'.encode('ascii')).hexdigest()[:32]


def _gif_receipt(request, source, rendered):
    return _json({'version': 1, 'kind': 'ugoira-gif', 'request': request,
                  'source_receipt': source.relative_receipt, 'source_sha256': source.receipt_sha256,
                  'gif_sha256': rendered.sha256, 'bytes': len(rendered.data),
                  'width': rendered.width, 'height': rendered.height, 'frame_count': rendered.frame_count,
                  'source_duration_ms': rendered.source_duration_ms,
                  'encoded_delays_ms': list(rendered.encoded_delays_ms), 'timing': 'gif-centiseconds-floor'})


def _gif_completion(token, receipt):
    return _json({'version': 1, 'kind': 'ugoira-gif-complete', 'token': token,
                  'receipt_sha256': hashlib.sha256(receipt).hexdigest()})


def _mp4_completion(token, receipt):
    return _json({'version':1,'kind':'ugoira-mp4-complete','token':token,
                  'receipt_sha256':hashlib.sha256(receipt).hexdigest()})


class ManagedUgoiraJobs:
    """One application owner; views may use jobs.snapshot/control as usual.

    Parent-root ownership is held until the worker stops. Accepted ZIPs remain
    immutable and bounded, so restart/retry requires no account or network.
    Frame generations keep their existing separate storage quotas/namespaces.
    Admission/recovery perform bounded decoding and disk I/O; the application
    must call them off its UI loop. Snapshot/control keep JobService semantics.
    """

    def __init__(self, root: Path, *, codec_python=None):
        from .ugoira_mp4 import default_codec_python
        self.codec_python = default_codec_python(codec_python)
        self.jobs = None
        self._owner = None
        self._closed = False
        self._closing = False
        self._accept = Lock()
        self._artifact = Lock()
        self._reservations = {}
        self.diagnostic_errors = {}
        self._close_lock = Lock()
        try:
            self.root = Path(root)
            if not self.root.is_absolute() or self.root == self.root.parent or '..' in self.root.parts:
                raise ManagedUgoiraError()
            _safe_path(self.root)
            self.root = native_io_path(self.root.resolve())
            if self.root.exists():
                if _read_bounded(self.root / MARKER, 4096) != _IDENTITY:
                    raise ManagedUgoiraError()
            else:
                self.root.mkdir(mode=0o700)
                _write(self.root / MARKER, _IDENTITY)
            self._owner = storage._owner_lock(self.root)
            self._owner.__enter__()
            allowed = {MARKER, storage._OWNER, 'requests', 'request-incomplete', 'gifs',
                       'gif-incomplete', 'diagnostics', 'mp4s', 'mp4-incomplete', 'codec-incomplete', 'media', 'jobs.sqlite3', 'jobs.sqlite3.lock', 'jobs.sqlite3-journal'}
            with os.scandir(self.root) as children:
                for child in children:
                    if child.name not in allowed:
                        raise ManagedUgoiraError()
            self.diagnostics = self.root / 'diagnostics'
            # Preserve the established empty namespace. Existing ancillary
            # entries are neither traversed nor adopted during media startup.
            try:self.diagnostics.lstat()
            except FileNotFoundError:self.diagnostics.mkdir(mode=0o700)
            self.requests = self.root / 'requests'
            self.request_incomplete = self.root / 'request-incomplete'
            self.gifs = self.root / 'gifs'
            self.gif_incomplete = self.root / 'gif-incomplete'
            self.mp4s = self.root / 'mp4s'
            self.mp4_incomplete = self.root / 'mp4-incomplete'
            self.codec_incomplete = self.root / 'codec-incomplete'
            for folder in (self.requests, self.request_incomplete, self.gifs, self.gif_incomplete, self.mp4s, self.mp4_incomplete, self.codec_incomplete):
                _safe_path(folder)
                folder.mkdir(mode=0o700, exist_ok=True)
                _safe_path(folder)
            self.frames = UgoiraFrameStore(self.root / 'media')
            _fsync_dir(self.root)
            _fsync_dir(self.root.parent)
            self.jobs = JobService(job_request_runner(self.run), logical_path(self.root) / 'jobs.sqlite3')
            self._recover()
            self._inspect_diagnostics()
        except Exception:
            try:
                if self.jobs is not None:
                    self.jobs.close()
            finally:
                try:
                    if self._owner is not None:
                        self._owner.__exit__(None, None, None)
                finally:
                    self._owner = None
            raise ManagedUgoiraError() from None

    def _diagnostic_usage(self):
        """Opaque, bounded physical accounting; no ancillary content/ledger IO.

        Links/reparse entries are never followed or adopted. Actual retained
        bytes still count against the unchanged combined capacity boundary.
        """
        generations=files=size=0
        try:info=self.diagnostics.lstat()
        except FileNotFoundError:return (0,0,0)
        if (not stat.S_ISDIR(info.st_mode) or self.diagnostics.is_symlink()
                or getattr(info,'st_file_attributes',0)&0x400):return (0,1,max(0,info.st_size))
        pending=[self.diagnostics]
        while pending:
            with os.scandir(pending.pop()) as children:
                for child in children:
                    try:meta=os.stat(child.path,follow_symlinks=False)
                    except FileNotFoundError:continue
                    if stat.S_ISDIR(meta.st_mode) and not getattr(meta,'st_file_attributes',0)&0x400:
                        generations+=1;pending.append(Path(child.path))
                    else:files+=1;size+=max(0,meta.st_size)
                    if (generations>MAX_CONTROL_GENERATIONS or files>MAX_CONTROL_FILES
                            or size>MAX_CONTROL_BYTES):return generations,files,size
        return generations,files,size

    def _diagnostic_error(self,key,error,phase='diagnostic_validation'):
        allowed={'unsafe_namespace','unsafe_folder','unbound_job','unsafe_file','unknown_record','invalid_record'}
        code=getattr(error,'diagnostic_reason',phase)
        if code not in allowed|{'diagnostic_validation','diagnostic_capacity','diagnostic_write'}:code='diagnostic_validation'
        reason=code+':'+type(error).__name__
        if self.diagnostic_errors.get(key)!=reason:
            import logging
            logging.getLogger(__name__).warning('ugoira auxiliary diagnostic unavailable %s: %s',key,reason)
        self.diagnostic_errors[key]=reason

    def status_messages(self):
        reasons={'unsafe_namespace':'診断保存先の所有境界を確認できません',
                 'unsafe_folder':'診断フォルダーの安全性を確認できません',
                 'unbound_job':'診断と受付履歴が一致しません',
                 'unsafe_file':'診断ファイルの安全性を確認できません',
                 'unknown_record':'未知の診断名を保全しています',
                 'invalid_record':'診断の形式または受付対応を確認できません',
                 'diagnostic_validation':'診断の検査に失敗しました',
                 'diagnostic_capacity':'診断の保持容量を確保できません',
                 'diagnostic_write':'診断の書込みに失敗しました'}
        return tuple('うごイラ診断 '+key+'：'+reasons.get(reason.split(':')[0],reasons['diagnostic_validation'])+'。媒体の保存とは別の障害です'
                     for key,reason in self.diagnostic_errors.copy().items())

    def _inspect_diagnostics(self):
        # Best effort per-item inspection. Never reject/rewrite media because
        # one retained auxiliary record cannot be adopted by its own writer.
        try:
            _safe_path(self.diagnostics)
            for index,folder in enumerate(self.diagnostics.iterdir()):
                if index>=MAX_CONTROL_GENERATIONS:
                    self._diagnostic_error('namespace',ManagedUgoiraError(),'diagnostic_capacity');break
                key=folder.name if _JOB.fullmatch(folder.name) else 'namespace'
                try:
                    if key=='namespace':raise _DiagnosticRejected('unknown_record')
                    self._diagnostics_writable(key)
                except Exception as error:self._diagnostic_error(key,error)
        except Exception as error:self._diagnostic_error('namespace',error)

    def _diagnostics_writable(self,job_id=None):
        try:
            _safe_path(self.diagnostics)
            if not self.diagnostics.exists():self.diagnostics.mkdir(mode=0o700)
        except Exception:raise _DiagnosticRejected('unsafe_namespace') from None
        folders=(self.diagnostics/job_id,) if job_id is not None else self.diagnostics.iterdir()
        for folder in folders:
            if _JOB.fullmatch(folder.name) is None:raise _DiagnosticRejected('unknown_record')
            try:_safe_path(folder)
            except Exception:raise _DiagnosticRejected('unsafe_folder') from None
            if not folder.exists():continue
            try:storage._directory_identity(folder)
            except Exception:raise _DiagnosticRejected('unsafe_folder') from None
            targets={target for _,jid,kind,target,_ in self.jobs.accepted_operations()
                     if jid==folder.name and kind in {'ugoira-frames','ugoira-gif','ugoira-mp4'}}
            if len(targets)!=1:raise _DiagnosticRejected('unbound_job')
            for node in folder.iterdir():
                try:_safe_path(node,file=True)
                except Exception:raise _DiagnosticRejected('unsafe_file') from None
                if re.fullmatch(r'stage-[a-f0-9]{32}',node.name):continue
                if not node.name.startswith(folder.name+'-'):raise _DiagnosticRejected('unknown_record')
                try:self._legacy_diagnostic(node,next(iter(targets)))
                except Exception:raise _DiagnosticRejected('invalid_record') from None

    def _budget(self, incoming_files, incoming_bytes):
        """Under _artifact: actual retained bytes plus unwritten reservations.

        Reservations survive pause while the worker owns a stage, without
        holding an admission/publication mutex across JobContext callbacks.
        On cancellation, actual remnants remain charged; nothing is deleted.
        """
        generations,files,size = self._diagnostic_usage()
        actual = {}
        for parent in (self.requests, self.request_incomplete, self.gifs, self.gif_incomplete, self.mp4s, self.mp4_incomplete, self.codec_incomplete):
            _safe_path(parent)
            with os.scandir(parent) as children:
                for child in children:
                    generations += 1
                    if generations + 1 > MAX_CONTROL_GENERATIONS or re.fullmatch(r'[a-f0-9]{32,64}', child.name) is None:
                        raise ManagedUgoiraError()
                    folder = parent / child.name
                    storage._directory_identity(folder)
                    count = length = 0
                    with os.scandir(folder) as contents:
                        for item in contents:
                            count += 1
                            files += 1
                            if files + incoming_files > MAX_CONTROL_FILES:
                                raise ManagedUgoiraError()
                            info = os.stat(item.path, follow_symlinks=False)
                            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size < 0
                                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                                raise ManagedUgoiraError()
                            length += info.st_size
                            size += info.st_size
                            if size + incoming_bytes > MAX_CONTROL_BYTES:
                                raise ManagedUgoiraError()
                    actual[folder] = (count, length)
        for folder, (planned_files, planned_bytes) in self._reservations.items():
            if folder not in actual:
                raise ManagedUgoiraError()
            count, length = actual[folder]
            files += max(0, planned_files - count)
            size += max(0, planned_bytes - length)
        if (generations + 1 > MAX_CONTROL_GENERATIONS or files + incoming_files > MAX_CONTROL_FILES
                or size + incoming_bytes > MAX_CONTROL_BYTES):
            raise ManagedUgoiraError()

    def _legacy_diagnostic(self, path, digest):
        """Only the old writer's exact record bound to this accepted input.

        Never delete/migrate it and never treat it as source or completion.
        Unknown filenames, schema, links, IDs and unaccepted jobs still fail.
        """
        match=re.fullmatch(r'([a-f0-9]{32})-([1-9][0-9]*)\.io-error\.json',path.name)
        if match is None or self.jobs is None:raise ManagedUgoiraError()
        packet=json.loads(_read_bounded(path,4096),object_pairs_hook=_json_unique)
        keys={'operation','error_type','errno','winerror','job_id','attempt'}
        if type(packet) is not dict or packet.keys()!=keys:raise ManagedUgoiraError()
        if (packet['job_id']!=match[1] or type(packet['attempt']) is not int
                or packet['attempt']!=int(match[2])
                or packet['operation'] not in {'load_ugoira_source','verify_saved_ugoira',
                    'save_frame_generation','render_ugoira_gif','save_gif_generation','save_mp4_generation'}
                or type(packet['error_type']) is not str
                or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',packet['error_type']) is None
                or any(packet[key] is not None and type(packet[key]) is not int for key in ('errno','winerror'))):
            raise ManagedUgoiraError()
        accepted=any(jid==packet['job_id'] and target==digest and kind in
            {'ugoira-frames','ugoira-gif','ugoira-mp4'}
            for _,jid,kind,target,_ in self.jobs.accepted_operations())
        if not accepted:raise ManagedUgoiraError()
        known=self.jobs.retained_job(packet['job_id'])
        if known is not None and packet['attempt']>known.attempt:raise ManagedUgoiraError()
        # A v1-retired job may lack attempt metadata. The exact schema, safe
        # bounded file and immutable operation binding still identify an old
        # auxiliary record. It proves neither completion nor an attempt/state;
        # canonical ZIP/intent hashes remain independently verified by _load.

    def _record_failure(self, job, error, operation):
        # Ancillary records never enter immutable accepted-input directories.
        # They share existing retained-control quotas; no unbounded log store.
        jid=getattr(job,'job_id',None);attempt=getattr(job,'attempt',None)
        if (type(jid) is not str or _JOB.fullmatch(jid) is None
                or type(attempt) is not int or attempt<1):return
        phase='diagnostic_validation'
        try:
            with self._artifact:
                self._diagnostics_writable(job.job_id)
                folder=self.diagnostics/job.job_id
                target=folder/(job.job_id+'-'+str(job.attempt)+'.io-error.json')
                _safe_path(self.diagnostics);_safe_path(folder)
                if target.exists():
                    self.diagnostic_errors.pop(jid,None)
                    return  # preserve the first failure per attempt
                error_type=type(error).__name__
                if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',error_type) is None:error_type='Exception'
                errno=getattr(error,'errno',None);winerror=getattr(error,'winerror',None)
                payload=dict(operation=operation,error_type=error_type,
                    errno=errno if type(errno) is int else None,
                    winerror=winerror if type(winerror) is int else None,
                    job_id=job.job_id,attempt=job.attempt)
                data=json.dumps(payload,sort_keys=True).encode('ascii')
                phase='diagnostic_capacity'
                self._budget(1,len(data))
                phase='diagnostic_write'
                folder.mkdir(mode=0o700,exist_ok=True);_safe_path(folder)
                _fsync_dir(self.diagnostics)
                # An interrupted diagnostic write remains bounded staging, not
                # a published JSON record or a file in the accepted source.
                stage=folder/('stage-'+uuid4().hex)
                _write(stage,data);_fsync_dir(folder)
                if target.exists():return
                stage.rename(target);_fsync_dir(folder)
                self.diagnostic_errors.pop(jid,None)
        except Exception as diagnostic_error:
            # Preserve the original failure and expose a bounded typed reason.
            self._diagnostic_error(jid,diagnostic_error,phase)

    def _load(self, digest, checkpoint=lambda: None):
        checkpoint()
        if type(digest) is not str or _SHA.fullmatch(digest) is None:
            raise ManagedUgoiraError()
        folder = self.requests / digest
        _safe_path(folder)
        with os.scandir(folder) as children:
            names = set()
            for child in children:
                if child.name in {'source.zip', 'intent.json'}:
                    names.add(child.name)
                else:
                    self._legacy_diagnostic(folder/child.name,digest)
        if names != {'source.zip', 'intent.json'}:
            raise ManagedUgoiraError()
        packet_bytes = _read_bounded(folder / 'intent.json', MAX_INTENT_BYTES)
        if hashlib.sha256(packet_bytes).hexdigest() != digest:
            raise ManagedUgoiraError()
        packet = json.loads(packet_bytes, object_pairs_hook=_json_unique)
        if (type(packet) is not dict or packet.keys() != {'version', 'work_id', 'output', 'zip_sha256', 'frames'}
                or type(packet['version']) is not int or packet['version'] != 1
                or packet['output'] not in {'frames', 'gif', 'mp4'}
                or type(packet['work_id']) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', packet['work_id']) is None
                or type(packet['zip_sha256']) is not str or _SHA.fullmatch(packet['zip_sha256']) is None):
            raise ManagedUgoiraError()
        manifest = parse_ugoira_manifest({
            'originalSrc': f"https://i.pximg.net/img-zip-ugoira/offline/{packet['work_id']}_ugoira.zip",
            'frames': packet['frames'],
        }, work_id=packet['work_id'])
        raw = _read_bounded(folder / 'source.zip', MAX_ARCHIVE_BYTES)
        if hashlib.sha256(raw).hexdigest() != packet['zip_sha256']:
            raise ManagedUgoiraError()
        checkpoint()
        canonical, inspection = _intent(raw, manifest, packet['output'])
        checkpoint()
        if canonical != packet_bytes:
            raise ManagedUgoiraError()
        return raw, manifest, packet['output'], inspection

    def enqueue(self, operation_id, raw_zip, manifest, *, output='frames'):
        if (type(operation_id) is not str or not 1 <= len(operation_id) <= 128
                or any(ord(char) < 32 or ord(char) == 127 for char in operation_id)
                or type(output) is not str):
            raise ManagedUgoiraError()
        if output == 'mp4' and self.codec_python is None:
            raise ManagedUgoiraError('mp4_environment_unverified')
        if output not in {'frames', 'gif', 'mp4'} or self._closed:
            raise ManagedUgoiraError()
        try:
            with self._accept:
                if self._closed or self._closing:
                    raise ManagedUgoiraError()
                # Admission validation is serialized as well as publication;
                # multiple callers cannot start unbounded parallel decodes.
                packet, _ = _intent(raw_zip, manifest, output)
                if self._closing:
                    raise ManagedUgoiraError()
                digest = hashlib.sha256(packet).hexdigest()
                target = self.requests / digest
                if target.exists() or target.is_symlink():
                    self._load(digest)
                else:
                    with self._artifact:
                        self._budget(2, len(raw_zip) + len(packet))
                        stage = self.request_incomplete / uuid4().hex
                        stage.mkdir(mode=0o700)
                        _write(stage / 'source.zip', raw_zip)
                        _write(stage / 'intent.json', packet)
                        _fsync_dir(stage)
                        storage._publish_directory(stage, target)
                        _fsync_dir(self.requests)
                        _fsync_dir(self.request_incomplete)
                    self._load(digest)
                # Filesystem publication and SQLite acceptance are separate
                # commits. An enqueue failure retains a bounded validated cache,
                # NOT an accepted job or completed output. Same-digest retry
                # reuses it; no implicit cleanup/adoption is performed.
                return self.jobs.enqueue('ugoira-' + output, digest, 'ugoira', operation_id=operation_id)
        except Exception:
            raise ManagedUgoiraError() from None

    def _source(self, token, inspection, checkpoint=lambda: None):
        checkpoint()
        _safe_path(self.frames.root)
        if not self.frames.root.exists():
            return None
        _, receipt = storage._metadata(inspection)
        result = self.frames.reconcile(f'frames/{token}/receipt.json',
                                       expected_sha256=hashlib.sha256(receipt).hexdigest(),
                                       checkpoint=checkpoint)
        return result.saved if result.state == 'complete' else None

    def _find_source(self, job, inspection, checkpoint):
        """Reuse only exact, sealed content; a directory is never a receipt.

        The root has one application owner. Inventory is bounded by the frame
        store quota. Known incomplete/corrupt generations remain untouched.
        I/O and OS-lock errors fail closed rather than triggering regeneration.
        """
        checkpoint()
        _safe_path(self.frames.root)
        if not self.frames.root.exists():
            return None
        metadata, receipt = storage._metadata(inspection)
        digest = hashlib.sha256(receipt).hexdigest()
        tokens = []
        _safe_path(self.frames.frames)
        with os.scandir(self.frames.frames) as children:
            for child in children:
                checkpoint()
                if len(tokens) >= storage.MAX_RETAINED_GENERATIONS or _JOB.fullmatch(child.name) is None:
                    raise ManagedUgoiraError()
                storage._directory_identity(self.frames.frames / child.name)
                tokens.append(child.name)
        preferred = _candidate(job)
        for token in sorted(tokens, key=lambda value: (value != preferred, value)):
            checkpoint()
            folder = self.frames.frames / token
            try:
                if (_read_bounded(folder / 'receipt.json', storage.MAX_RECEIPT_BYTES) != receipt
                        or _read_bounded(folder / storage.COMPLETION, storage.MAX_COMPLETION_BYTES)
                        != storage._completion_bytes(token, digest)):
                    continue
                # Known content corruption is not reusable. Leave it preserved;
                # the authoritative reconcile (including lock and decode) runs
                # OUTSIDE this classification catch, so busy is never "missing".
                storage._verify_contents(folder, inspection, metadata, receipt,
                                         storage._completion_bytes(token, digest), checkpoint)
            except (FileNotFoundError, storage.UgoiraStorageError):
                continue
            return self._source(token, inspection, checkpoint)
        return None

    def _verified_result(self, job, loaded, checkpoint=lambda: None):
        raw, manifest, output, inspection = loaded
        if job.kind != 'ugoira-' + output:
            raise ManagedUgoiraError()
        token = _candidate(job)
        checkpoint()
        if output == 'frames':
            if job.state == 'done':
                match = re.fullmatch(r'media/frames/([a-f0-9]{32})/receipt\.json', job.result or '')
                if match is None:
                    raise ManagedUgoiraError()
                source = self._source(match.group(1), inspection, checkpoint)
            else:
                source = self._find_source(job, inspection, checkpoint)
            return None if source is None else 'media/' + source.relative_receipt
        if output == 'mp4':
            return self._verified_mp4(job, inspection, checkpoint)
        folder = self.gifs / token
        _safe_path(folder)
        if not folder.exists():
            return None
        checkpoint()
        receipt_on_disk = _read_bounded(folder / 'receipt.json', MAX_INTENT_BYTES)
        packet = json.loads(receipt_on_disk, object_pairs_hook=_json_unique)
        reference = packet.get('source_receipt') if type(packet) is dict else None
        match = re.fullmatch(r'frames/([a-f0-9]{32})/receipt\.json', reference) if type(reference) is str else None
        if match is None:
            raise ManagedUgoiraError()
        source = self._source(match.group(1), inspection, checkpoint)
        if source is None:
            raise ManagedUgoiraError()
        completion = _read_bounded(folder / storage.COMPLETION, 1024)
        if completion != _gif_completion(token, receipt_on_disk):
            raise ManagedUgoiraError()
        data = _read_bounded(folder / 'animation.gif', MAX_GIF_BYTES)
        rendered = _verify_gif(data, inspection, checkpoint, time.monotonic() + MAX_GIF_SECONDS)
        receipt = _gif_receipt(job.target_id, source, rendered)
        expected_names = {'animation.gif', 'receipt.json', storage.COMPLETION}
        with os.scandir(folder) as children:
            names = set()
            for child in children:
                checkpoint()
                if child.name not in expected_names:
                    raise ManagedUgoiraError()
                names.add(child.name)
        if names != expected_names or receipt_on_disk != receipt:
            raise ManagedUgoiraError()
        if completion != _gif_completion(token, receipt):
            raise ManagedUgoiraError()
        return f'gifs/{token}/receipt.json'

    def _save_gif(self, job, source, rendered, context):
        token = _candidate(job)
        receipt = _gif_receipt(job.target_id, source, rendered)
        completion = _gif_completion(token, receipt)
        stage, target = self.gif_incomplete / uuid4().hex, self.gifs / token
        reserved = stage
        context.checkpoint()
        # No checkpoint / JobService call while either mutex is held.
        with self._artifact:
            if self._closing:
                raise JobCancelled('managed owner is closing')
            self._budget(3, len(rendered.data) + len(receipt) + len(completion))
            stage.mkdir(mode=0o700)
            self._reservations[stage] = (3, len(rendered.data) + len(receipt) + len(completion))
        try:
            context.checkpoint()
            _write(stage / 'animation.gif', rendered.data)
            _write(stage / 'receipt.json', receipt)
            if (_read_bounded(stage / 'animation.gif', MAX_GIF_BYTES) != rendered.data
                    or _read_bounded(stage / 'receipt.json', MAX_INTENT_BYTES) != receipt):
                raise ManagedUgoiraError()
            _fsync_dir(stage)
            context.checkpoint()
            with self._artifact:
                storage._publish_directory(stage, target)
                self._reservations[target] = self._reservations.pop(stage)
                reserved = target
                _fsync_dir(self.gifs)
                _fsync_dir(self.gif_incomplete)
            context.checkpoint()
            _write(target / storage.COMPLETION, completion)
            _fsync_dir(target)
            _fsync_dir(self.gifs)
        finally:
            with self._artifact:
                # Physical remnants continue counting after cancellation/fault.
                self._reservations.pop(reserved, None)

    def _save_mp4(self, job, raw, inspection, source, context):
        from .ugoira_mp4 import render_mp4
        codec = self.codec_incomplete / uuid4().hex
        # Existing finite control budget includes retained codec intermediates.
        planned = len(raw) + MAX_GIF_BYTES * 2 + MAX_INTENT_BYTES * 2
        with self._artifact:
            self._budget(6, planned)
            codec.mkdir()
            self._reservations[codec] = (6, planned)
        try:
            data, report, decoded = render_mp4(raw, inspection, codec, self.codec_python, context.checkpoint)
        finally:
            with self._artifact:
                self._reservations.pop(codec, None)
        token = _candidate(job)
        packet = {'version':2,'kind':'ugoira-mp4','request':job.target_id,
                  'source_receipt':source.relative_receipt,'source_sha256':source.receipt_sha256,
                  'mp4_sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'report':report,
                  'decoded_sha256':hashlib.sha256(decoded).hexdigest()}
        receipt = _json(packet)
        completion = _mp4_completion(token,receipt)
        stage, target = self.mp4_incomplete / uuid4().hex, self.mp4s / token
        with self._artifact:
            self._budget(4,len(data)+len(decoded)+len(receipt)+len(completion))
            stage.mkdir()
            self._reservations[stage]=(4,len(data)+len(decoded)+len(receipt)+len(completion))
        reserved=stage
        try:
            context.checkpoint()
            _write(stage/'animation.mp4',data)
            _write(stage/'decoded_frames.zip',decoded)
            _write(stage/'receipt.json',receipt)
            _fsync_dir(stage)
            context.checkpoint()
            with self._artifact:
                storage._publish_directory(stage,target)
                self._reservations[target]=self._reservations.pop(stage)
                reserved=target
                _fsync_dir(self.mp4s)
            context.checkpoint()
            _write(target/storage.COMPLETION,completion)
            _fsync_dir(target)
            _fsync_dir(self.mp4s)
        finally:
            with self._artifact:
                self._reservations.pop(reserved,None)

    def _verified_mp4(self, job, inspection, checkpoint):
        token=_candidate(job)
        folder=self.mp4s/token
        _safe_path(folder)
        if not folder.exists(): return None
        receipt=_read_bounded(folder/'receipt.json',MAX_INTENT_BYTES*2)
        packet=json.loads(receipt,object_pairs_hook=_json_unique)
        if type(packet) is not dict or set(packet)!={'version','kind','request','source_receipt','source_sha256','mp4_sha256','bytes','report','decoded_sha256'}:
            raise ManagedUgoiraError()
        match=re.fullmatch(r'frames/([a-f0-9]{32})/receipt\.json',packet['source_receipt'])
        if match is None: raise ManagedUgoiraError()
        source=self._source(match[1],inspection,checkpoint)
        if source is None or source.receipt_sha256!=packet['source_sha256']: raise ManagedUgoiraError()
        data=_read_bounded(folder/'animation.mp4',MAX_GIF_BYTES)
        from .ugoira_mp4 import mp4_plan, verify_decoded
        decoded=_read_bounded(folder/'decoded_frames.zip',MAX_GIF_BYTES)
        if hashlib.sha256(decoded).hexdigest()!=packet['decoded_sha256']: raise ManagedUgoiraError()
        verify_decoded(decoded,inspection)
        plan=mp4_plan(inspection)
        report=packet['report']
        if (packet['version']!=2 or packet['kind']!='ugoira-mp4' or packet['request']!=job.target_id
                or packet['bytes']!=len(data) or packet['mp4_sha256']!=hashlib.sha256(data).hexdigest()
                or report['width']!=plan['width'] or report['height']!=plan['height']
                or report['frame_count']!=len(inspection.frames) or report['source_duration_ms']!=inspection.duration_ms
                or abs(report['fps']-plan['fps'])>max(.001,plan['fps']*.001)
                or report['codec']!='mp4v' or report['timing']!='legacy-average-delay-cfr-clamped-1-60'
                or _read_bounded(folder/storage.COMPLETION,1024)!=_mp4_completion(token,receipt)
                or {p.name for p in folder.iterdir()}!={'animation.mp4','decoded_frames.zip','receipt.json',storage.COMPLETION}):
            raise ManagedUgoiraError()
        checkpoint()
        return f'mp4s/{token}/receipt.json'

    def run(self, job, context):
        operation='load_ugoira_source'
        try:
            context.checkpoint()
            loaded = self._load(job.target_id, context.checkpoint)
            context.checkpoint()
            context.progress(0, 1)
            operation='verify_saved_ugoira'
            result = self._verified_result(job, loaded, context.checkpoint)
            if result is None:
                raw, manifest, output, inspection = loaded
                token = _candidate(job)
                source = self._find_source(job, inspection, context.checkpoint)
                if source is None:
                    operation='save_frame_generation'
                    source = self.frames.save(raw, manifest, generation_token=token, checkpoint=context.checkpoint)
                context.checkpoint()
                if output == 'gif':
                    operation='render_ugoira_gif'
                    rendered = render_ugoira_gif(raw, manifest, checkpoint=context.checkpoint)
                    operation='save_gif_generation'
                    self._save_gif(job, source, rendered, context)
                if output == 'mp4':
                    operation='save_mp4_generation'
                    self._save_mp4(job, raw, inspection, source, context)
                result = self._verified_result(job, loaded, context.checkpoint)
                if result is None:
                    raise ManagedUgoiraError()
            context.checkpoint()
            context.progress(1, 1)
            return result
        except JobCancelled:
            raise
        except Exception as error:
            # Keep public failure sanitized while retaining operation/WinError.
            self._record_failure(job,error,operation)
            raise ManagedUgoiraError() from None

    def _recover(self, checkpoint=lambda: None):
        for job in self.jobs.snapshot():
            checkpoint()
            if job.kind not in {'ugoira-frames', 'ugoira-gif', 'ugoira-mp4'}:
                raise ManagedUgoiraError()
            if job.state == 'done' or (job.state == 'stopped' and job.error_code == 'interrupted'):
                try:
                    result = self._verified_result(job, self._load(job.target_id, checkpoint), checkpoint)
                    valid = result is not None and (job.state != 'done' or result == job.result)
                except JobCancelled:
                    raise
                except storage.UgoiraStorageIOError:
                    # Do not demote a completed job on transient storage I/O.
                    # Constructor recovery fails closed, so unverified output is
                    # not exposed through a successfully opened managed owner.
                    raise
                except OSError:
                    # _load() also reads local intent/archive files; preserve the
                    # same fail-closed behavior for transient request I/O.
                    raise
                except Exception:
                    result, valid = None, False
                if job.state == 'done':
                    self.jobs.reconcile_completed(job.job_id, expected_result=job.result, valid=valid)
                else:
                    self.jobs.reconcile_interrupted(job.job_id, receipt=result if valid else None,
                                                    page_count=1 if valid else None)

    def close(self, *, timeout=10.0):
        with self._close_lock:
            if self._closed:
                return
            self._closing = True
            # Wake paused JobContexts and join the worker before ownership
            # release; then wait for in-flight admission I/O. Neither worker
            # checkpoint nor JobService call holds the artifact mutex.
            self.jobs.close(timeout=timeout)
            with self._accept:
                self._closed = True
                if self._owner is not None:
                    self._owner.__exit__(None, None, None)
                    self._owner = None


__all__ = ['ManagedUgoiraJobs', 'ManagedUgoiraError']
