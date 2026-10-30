"""Durable original extension commands over existing media job/receipt owners.

HTTP acceptance is a persisted command, not a claim that content is saved.
No download is performed by the HTTP handler. Receipt publication remains with
existing media owners. Interrupted commands require explicit queue Retry.
"""
from __future__ import annotations
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

from .extension_bridge import ExtensionDownload
from .job_service import JobService, JobCancelled, UNFINISHED
from .request_policy import job_request_runner
from .managed_downloads import _safe_path, _read_bounded


class ExtensionApplication:
    def __init__(self, services, root):
        self.services = services
        self.root = Path(root)
        self._lock = RLock()
        self._children = {}
        _safe_path(self.root)
        marker = self.root / 'extension-owner-v1'
        if self.root.exists():
            _safe_path(marker, file=True)
            if not marker.is_file() or marker.read_bytes() != b'PixivVault extension commands v1\n':
                raise ValueError('unowned extension command root')
        else:
            self.root.mkdir()
            with marker.open('xb') as stream:stream.write(b'PixivVault extension commands v1\n')
        self.path = self.root / 'bindings.sqlite3'
        _safe_path(self.path, file=True)
        if self.path.exists():
            with sqlite3.connect(self.path) as db:
                if db.execute('PRAGMA application_id').fetchone()[0] != 1347831107:
                    raise ValueError('foreign extension bindings')
        else:
            with sqlite3.connect(self.path) as db:
                db.executescript('PRAGMA application_id=1347831107; CREATE TABLE children(parent TEXT,key TEXT,job TEXT,PRIMARY KEY(parent,key));')
        # Add only an immutable decision for newly accepted, omitted-flag work
        # intents. Existing children/intent/operation bytes are not rewritten.
        for suffix in ('','-wal','-shm','-journal'):
            _safe_path(Path(str(self.path)+suffix),file=True)
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute("CREATE TABLE IF NOT EXISTS work_media_routes("
                       "parent TEXT PRIMARY KEY,work_id TEXT NOT NULL,"
                       "media TEXT NOT NULL CHECK(media IN ('work','novel')))")
        self.jobs = JobService(job_request_runner(self._run), self.root / 'jobs.sqlite3')
        for job in self.jobs.snapshot():
            if job.kind != 'extension-command':
                self.jobs.close()
                raise ValueError('foreign extension job')
            if job.state == 'stopped' and job.error_code == 'interrupted':
                self.jobs.reconcile_interrupted(job.job_id)

    @property
    def closed(self):return self.jobs.closed
    @property
    def faulted(self):return self.jobs.faulted
    def snapshot(self):return self.jobs.snapshot()
    def operation_ids(self, job_id):return self.jobs.operation_ids(job_id)

    def enqueue(self, command):
        if type(command) is not ExtensionDownload or self.services.closing:
            raise ValueError('extension admission unavailable')
        # Canonical immutable intent is itself in the existing JobService ledger.
        from .extension_bridge import parse_download
        payload = (dict(type='work',work_id=command.target_id)
                   if command.kind == 'work' else dict(type='user',user_id=command.target_id,new_only=command.new_only))
        if command.kind == 'work' and command.is_novel is not None:
            payload['is_novel'] = command.is_novel
        parse_download(payload)
        target = json.dumps(payload,sort_keys=True,separators=(',',':'))
        with self._lock:
            return self.jobs.enqueue('extension-command', target,
                ('小説 ' if command.is_novel else '作者 ' if command.kind=='author' else '作品 ')+command.target_id,
                uuid4().hex).job_id

    def _binding(self, parent, key, child=None):
        with self._lock:
            for suffix in ('','-wal','-shm','-journal'):
                _safe_path(Path(str(self.path)+suffix),file=True)
            with sqlite3.connect(self.path) as db:
                db.execute('PRAGMA synchronous=FULL')
                if child is not None:
                    db.execute('INSERT OR REPLACE INTO children VALUES(?,?,?)',(parent,key,child))
                row=db.execute('SELECT job FROM children WHERE parent=? AND key=?',(parent,key)).fetchone()
                return row[0] if row else None

    def _work_media(self, parent, work_id, media=None):
        """Commit once before child admission; never rebase an accepted parent."""
        if media is not None and media not in ('work','novel'):
            raise ValueError('invalid extension media route')
        with self._lock:
            for suffix in ('','-wal','-shm','-journal'):
                _safe_path(Path(str(self.path)+suffix),file=True)
            with sqlite3.connect(self.path) as db:
                db.execute('PRAGMA synchronous=FULL')
                row = db.execute('SELECT work_id,media FROM work_media_routes WHERE parent=?',
                                 (parent,)).fetchone()
                if row is not None:
                    if (row[0] != work_id or row[1] not in ('work','novel')
                            or (media is not None and row[1] != media)):
                        raise RuntimeError('extension media route changed')
                    return row[1]
                if media is not None:
                    # No REPLACE/UPDATE: a retry cannot switch child namespace.
                    db.execute('INSERT INTO work_media_routes(parent,work_id,media) VALUES(?,?,?)',
                               (parent,work_id,media))
                return media

    def _auto_is_novel(self, job, command, context):
        known = self._work_media(job.job_id,command.target_id)
        if known is not None:
            return known == 'novel'
        from .pixiv_read_client import HTTPStatusRejected, LivePixivReadClient
        services = self.services
        reader = getattr(services,'live_client',None)
        if type(reader) is not LivePixivReadClient:
            raise RuntimeError('extension media detection unavailable')
        context.checkpoint()
        # Identity errors (including identity HTTP 404) are outside the artwork
        # catch. Hold the reader's identity lock so the nested require_identity
        # in get_discovery_work cannot become a different identity GET.
        with reader._identity_lock:
            account = reader.require_identity()
            if services.closing or services.live_client is not reader:
                raise RuntimeError('extension reader changed')
            try:
                reader.get_discovery_work(command.target_id)
            except HTTPStatusRejected as error:
                if error.status_code != 404:
                    raise
                media = 'novel'
            else:
                media = 'work'
            reader._ensure_open()
            if (reader._verified_identity != account or services.closing
                    or services.live_client is not reader):
                raise RuntimeError('extension reader changed')
        context.checkpoint()
        return self._work_media(job.job_id,command.target_id,media) == 'novel'

    def _await_child(self, parent, key, accept, context):
        owner=self.services.jobs
        child_id=self._binding(parent.job_id,key)
        child=next((j for j in owner.snapshot() if j.job_id==child_id),None)
        if child_id is not None and child is None:
            # A bound child can leave the display window. Its durable terminal
            # state is not an invitation to accept the same operation again.
            owners=getattr(owner,'owners',(owner,))
            found=[(part,part.retained_job(child_id)) for part in owners
                   if callable(getattr(part,'retained_job',None))]
            found=[(part,item) for part,item in found if item is not None]
            if len(found)!=1:raise RuntimeError('child history unavailable')
            child_owner,child=found[0]
            if child.state=='error' and parent.attempt>1:
                child=child_owner.restore_retained(child_id)
                if child is None:raise RuntimeError('child history unavailable')
        suffix = ''
        if child is not None and child.state == 'stopped' and parent.attempt > 1:
            suffix = ':restart:'+str(parent.attempt)
            child = None
        if child is None:
            # Deterministic operation replays acceptance after a crash before binding.
            operation=sha256((parent.job_id+':'+key+suffix).encode()).hexdigest()[:32]
            context.checkpoint()
            child=accept(operation)
            if child is None:raise RuntimeError('child was not accepted')
            child_id=child.job_id
            self._binding(parent.job_id,key,child_id)
        with self._lock:self._children[parent.job_id]=child_id
        try:
            if child.state=='done':
                self._verify_child(child)
                return
            # Explicit parent retry also retries its previously failed child.
            if child.state=='error' and parent.attempt>1:
                owner.control(child_id,'retry',expected_control_revision=child.control_revision)
            while True:
                current_parent=next((j for j in self.jobs.snapshot() if j.job_id==parent.job_id),None)
                if current_parent is not None and current_parent.state in ('paused','stopping','stopped'):
                    self._control_child(parent.job_id,child_id,'pause' if current_parent.state=='paused' else 'stop')
                context.checkpoint()
                child=next((j for j in owner.snapshot() if j.job_id==child_id),None)
                if child is None:raise RuntimeError('child history unavailable')
                if child.state=='done':
                    self._verify_child(child)
                    return
                if child.state in ('error','stopped'):raise RuntimeError('child save failed or stopped')
                time.sleep(0.05)
        except JobCancelled:
            self._control_child(parent.job_id,child_id,'stop')
            raise
        finally:
            with self._lock:self._children.pop(parent.job_id,None)

    def _verify_child(self, child):
        # A historical done snapshot alone cannot attest to current saved bytes.
        if child.kind == 'pixiv-illust':
            if self.services.managed_downloads.verified_saved_receipt(child.target_id) is None:
                raise RuntimeError('saved illustration receipt unavailable')
        elif child.kind == 'novel-export':
            parts = child.target_id.split(':')
            if len(parts) != 2 or self.services.novel_jobs.current(parts[0],parts[1]) is None:
                raise RuntimeError('saved novel receipt unavailable')
        elif child.kind.startswith('ugoira-'):
            if not any(row['proof']['receipt'] == child.result for row in self.services.ugoira.library()):
                raise RuntimeError('saved animation receipt unavailable')
        else:
            raise RuntimeError('unknown saved media job')

    def _run(self, job, context):
        from .extension_bridge import parse_download
        command=parse_download(json.loads(job.target_id))
        s=self.services
        is_novel = command.is_novel
        if command.kind == 'work' and is_novel is None:
            is_novel = self._auto_is_novel(job,command,context)
        if command.kind=='author':
            candidates=s.author_media_preview(command.target_id,new_only=command.new_only)
            eligible=[c for c in candidates if c.eligible]
            from .author_download import Difference
            if any(c.difference==Difference.UNKNOWN for c in candidates):
                raise RuntimeError('author inventory incomplete')
            for index,candidate in enumerate(eligible):
                context.checkpoint()
                self._await_child(job,candidate.key,
                    lambda operation,c=candidate:s.enqueue_author_media(c,operation,origin='id')[0],context)
                context.progress(index+1,len(eligible))
        elif is_novel:
            from .novel_save_presentation import NovelSavePresentation
            source=s.novel_fixture_source
            reader=getattr(source,'_reader',None)
            if reader is not None:
                reader.require_identity()
                source.select(command.target_id,reader.resolve_novel_author(command.target_id))
            presentation=NovelSavePresentation(s)
            fmt=s.preferences.get_setting('novel_save_format','epub').upper() if s.preferences else 'EPUB'
            selection=presentation.preview(command.target_id,fmt)
            if selection.saved is None:
                self._await_child(job,'novel:'+command.target_id,
                    lambda operation:presentation.enqueue(selection,operation),context)
        else:
            self._await_child(job,'work:'+command.target_id,
                lambda operation:asyncio.run(s.save_explicit_work(command.target_id,operation)),context)
        context.checkpoint()
        return 'extension command completed; saved results are in the library'

    def control(self, job_id, action, expected_revision=None, *, expected_control_revision=None):
        # Child control uses its CAS too. Shared children from another request
        # are not controlled unless this command's operation was attached.
        accepted=self.jobs.control(job_id,action,expected_revision=expected_revision,
            expected_control_revision=expected_control_revision)
        if accepted and action in ('pause','resume','stop'):
            with self._lock:child_id=self._children.get(job_id)
            self._control_child(job_id,child_id,action)
        return accepted

    def _control_child(self, parent_id, child_id, action):
        owner=self.services.jobs
        child=next((j for j in owner.snapshot() if j.job_id==child_id),None)
        parent=next((j for j in self.jobs.snapshot() if j.job_id==parent_id),None)
        if child is None or parent is None:return
        operations = owner.operation_ids(child_id)
        own = {sha256((parent_id+':'+key+suffix).encode()).hexdigest()[:32]
               for key in self._bound_keys(parent_id,child_id)
               for suffix in ('', *[':restart:'+str(n) for n in range(2,parent.attempt+1)])}
        if operations and operations[0] in own:
            owner.control(child_id,action,expected_control_revision=child.control_revision)

    def _bound_keys(self, parent, child):
        with self._lock, sqlite3.connect(self.path) as db:
            return tuple(row[0] for row in db.execute('SELECT key FROM children WHERE parent=? AND job=?',(parent,child)))

    def work_status(self, work_id):
        saved = work_id in self.services.saved_discovery_ids()
        if self.services.novel_jobs is not None:
            saved = saved or any(entry.work_id==work_id for entry in self.services.novel_jobs.saved_entries())
        return {'downloaded':saved}

    def author_status(self, author_id):
        # Only verified local receipts; never fetch a remote total to answer status.
        count=sum(row[1]['author_id']==author_id for row in self.services.managed_downloads.saved_receipts_with_dates())
        if self.services.novel_jobs is not None:
            count+=sum(json.loads(_read_bounded(self.services.novel_jobs.store.root / entry.relative_receipt,65536))['author_id']==author_id
                for entry in self.services.novel_jobs.saved_entries())
        if self.services.ugoira is not None:
            count+=sum(row['spec']['author_id']==author_id for row in self.services.ugoira.library())
        return {'downloaded':count,'total':count}

    def close(self, *, timeout=150.0):
        self.jobs.close(timeout=timeout)
