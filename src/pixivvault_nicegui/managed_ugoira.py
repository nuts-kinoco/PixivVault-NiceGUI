"""Managed offline ugoira jobs, durable accepted inputs, frames and GIF receipts.

This standalone candidate owns a JobService lifecycle/operation ledger. It is
not connected to ApplicationServices/UI. It supplies verified result receipts
and restart reconciliation, never a folder-presence shortcut. Only an explicitly new/marked private root is used;
no default library, cookie, client, command, or external codec is consulted.
"""
from __future__ import annotations

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
            or job.kind not in {'ugoira-frames', 'ugoira-gif'}
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


class ManagedUgoiraJobs:
    """One application owner; views may use jobs.snapshot/control as usual.

    Parent-root ownership is held until the worker stops. Accepted ZIPs remain
    immutable and bounded, so restart/retry requires no account or network.
    Frame generations keep their existing separate storage quotas/namespaces.
    Admission/recovery perform bounded decoding and disk I/O; the application
    must call them off its UI loop. Snapshot/control keep JobService semantics.
    """

    def __init__(self, root: Path):
        self.jobs = None
        self._owner = None
        self._closed = False
        self._closing = False
        self._accept = Lock()
        self._artifact = Lock()
        self._reservations = {}
        self._close_lock = Lock()
        try:
            self.root = Path(root)
            if not self.root.is_absolute() or self.root == self.root.parent or '..' in self.root.parts:
                raise ManagedUgoiraError()
            _safe_path(self.root)
            self.root = self.root.resolve()
            if self.root.exists():
                if _read_bounded(self.root / MARKER, 4096) != _IDENTITY:
                    raise ManagedUgoiraError()
            else:
                self.root.mkdir(mode=0o700)
                _write(self.root / MARKER, _IDENTITY)
            self._owner = storage._owner_lock(self.root)
            self._owner.__enter__()
            allowed = {MARKER, storage._OWNER, 'requests', 'request-incomplete', 'gifs',
                       'gif-incomplete', 'media', 'jobs.sqlite3', 'jobs.sqlite3.lock', 'jobs.sqlite3-journal'}
            with os.scandir(self.root) as children:
                for child in children:
                    if child.name not in allowed:
                        raise ManagedUgoiraError()
            self.requests = self.root / 'requests'
            self.request_incomplete = self.root / 'request-incomplete'
            self.gifs = self.root / 'gifs'
            self.gif_incomplete = self.root / 'gif-incomplete'
            for folder in (self.requests, self.request_incomplete, self.gifs, self.gif_incomplete):
                _safe_path(folder)
                folder.mkdir(mode=0o700, exist_ok=True)
                _safe_path(folder)
            self.frames = UgoiraFrameStore(self.root / 'media')
            _fsync_dir(self.root)
            _fsync_dir(self.root.parent)
            self.jobs = JobService(self.run, self.root / 'jobs.sqlite3')
            self._recover()
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

    def _budget(self, incoming_files, incoming_bytes):
        """Under _artifact: actual retained bytes plus unwritten reservations.

        Reservations survive pause while the worker owns a stage, without
        holding an admission/publication mutex across JobContext callbacks.
        On cancellation, actual remnants remain charged; nothing is deleted.
        """
        generations = files = size = 0
        actual = {}
        for parent in (self.requests, self.request_incomplete, self.gifs, self.gif_incomplete):
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

    def _load(self, digest, checkpoint=lambda: None):
        checkpoint()
        if type(digest) is not str or _SHA.fullmatch(digest) is None:
            raise ManagedUgoiraError()
        folder = self.requests / digest
        _safe_path(folder)
        with os.scandir(folder) as children:
            names = set()
            for child in children:
                if child.name not in {'source.zip', 'intent.json'}:
                    raise ManagedUgoiraError()
                names.add(child.name)
        if names != {'source.zip', 'intent.json'}:
            raise ManagedUgoiraError()
        packet_bytes = _read_bounded(folder / 'intent.json', MAX_INTENT_BYTES)
        if hashlib.sha256(packet_bytes).hexdigest() != digest:
            raise ManagedUgoiraError()
        packet = json.loads(packet_bytes, object_pairs_hook=_json_unique)
        if (type(packet) is not dict or packet.keys() != {'version', 'work_id', 'output', 'zip_sha256', 'frames'}
                or type(packet['version']) is not int or packet['version'] != 1
                or packet['output'] not in {'frames', 'gif'}
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
        if output == 'mp4':
            raise ManagedUgoiraError('mp4_environment_unverified')
        if output not in {'frames', 'gif'} or self._closed:
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

    def run(self, job, context):
        try:
            context.checkpoint()
            loaded = self._load(job.target_id, context.checkpoint)
            context.checkpoint()
            context.progress(0, 1)
            result = self._verified_result(job, loaded, context.checkpoint)
            if result is None:
                raw, manifest, output, inspection = loaded
                token = _candidate(job)
                source = self._find_source(job, inspection, context.checkpoint)
                if source is None:
                    source = self.frames.save(raw, manifest, generation_token=token, checkpoint=context.checkpoint)
                context.checkpoint()
                if output == 'gif':
                    rendered = render_ugoira_gif(raw, manifest, checkpoint=context.checkpoint)
                    self._save_gif(job, source, rendered, context)
                result = self._verified_result(job, loaded, context.checkpoint)
                if result is None:
                    raise ManagedUgoiraError()
            context.checkpoint()
            context.progress(1, 1)
            return result
        except JobCancelled:
            raise
        except Exception:
            raise ManagedUgoiraError() from None

    def _recover(self, checkpoint=lambda: None):
        for job in self.jobs.snapshot():
            checkpoint()
            if job.kind not in {'ugoira-frames', 'ugoira-gif'}:
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
