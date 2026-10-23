"""アプリ単位の資源。画面の再接続では作り直したり閉じたりしない。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
import re
from threading import Lock, RLock

from .fixtures import generate_demo_artworks
from .media import DemoImageCatalog
from .library_fixtures import build_library_fixture
from .library_models import LibrarySnapshot
from .library_media_registry import LibraryImageRegistry


class ApplicationServices:
    def __init__(self, count: int = 120, *, learning_sandbox: Path | None = None,
                 library_readonly_db: Path | None = None,
                 library_image_root: Path | None = None,
                 live_client=None, download_sandbox: Path | None = None,
                 pixiv_download_root: Path | None = None,
                 failed_queue_readonly_db: Path | None = None,
                 legacy_zip_export_root: Path | None = None) -> None:
        if (library_readonly_db is None) != (library_image_root is None):
            raise ValueError("read-only library requires both explicit DB and image root")
        if download_sandbox is not None and (library_readonly_db is not None or live_client is not None):
            raise ValueError("synthetic downloads cannot be combined with a read-only or live library")
        if (download_sandbox is not None and learning_sandbox is not None
                and Path(download_sandbox).resolve() == Path(learning_sandbox).resolve()):
            raise ValueError("learning and download sandboxes must be separate")
        if pixiv_download_root is not None and (
                live_client is None or download_sandbox is not None or
                library_readonly_db is not None or learning_sandbox is not None):
            raise ValueError("managed Pixiv downloads require an isolated live-only configuration")
        if legacy_zip_export_root is not None:
            if pixiv_download_root is None or live_client is None:
                raise ValueError('legacy ZIP export requires managed Pixiv mode')
            export = Path(legacy_zip_export_root).resolve()
            source = Path(pixiv_download_root).resolve()
            if export == source or export in source.parents or source in export.parents:
                raise ValueError('legacy ZIP export must not overlap managed originals')
        if failed_queue_readonly_db is not None:
            if pixiv_download_root is None or live_client is None:
                raise ValueError('legacy retry requires isolated managed Pixiv mode')
            candidate = Path(failed_queue_readonly_db)
            if (not candidate.is_absolute() or not candidate.is_file() or candidate.is_symlink()
                    or Path(pixiv_download_root).resolve() in candidate.resolve().parents
                    or Path(pixiv_download_root).resolve() == candidate.resolve().parent
                    or any(Path(str(candidate) + suffix).exists()
                           for suffix in ('-wal', '-shm', '-journal'))):
                raise ValueError('legacy retry requires a separate immutable SQLite copy')
            from .failed_records_repository import immutable_copy_signature
            failure_signature = immutable_copy_signature(candidate)
        else:
            failure_signature = None
        self.download_sandbox = download_sandbox
        self.pixiv_download_root = pixiv_download_root
        self.legacy_zip_export_root = legacy_zip_export_root
        self.legacy_zip_exporter = None
        self.failed_queue_readonly_db = failed_queue_readonly_db
        self._failed_copy_signature = failure_signature
        self.managed_downloads = None
        self._managed_requests = None
        self._managed_job_records = {}
        self._active_managed_record = None
        self._managed_target_lock = RLock()
        self._managed_enqueue_lock = Lock()
        self._direct_download_candidates: dict[str, OrderedDict] = {}
        self._author_owned_pins: dict[str, frozenset[str]] = {}
        self._managed_receipts = ()
        self.jobs = None
        self.download_writer = None
        self.artworks = generate_demo_artworks(count)
        self.library = build_library_fixture(self.artworks)
        self.images = LibraryImageRegistry(DemoImageCatalog(self.artworks))
        self.library_repository = None
        self.live_client = live_client
        self._remote_records = ()
        self._cached_remote_bookmarks = None if live_client is not None else self.artworks[:110]
        self._cached_local_bookmarks = () if live_client is not None else self.artworks[:40]
        self.bookmark_cache_failed = False
        self.library_readonly_db = library_readonly_db
        self.library_image_root = library_image_root
        self._library_refresh_lock = asyncio.Lock()
        if library_readonly_db is not None:
            from .library_repository import ReadonlyLibraryRepository
            from .local_media_adapter import ReadonlyLocalMediaCatalog
            try:
                repository = ReadonlyLibraryRepository(library_readonly_db)
                snapshot = repository.library_snapshot()
                self._validate_library_ids(snapshot)
                try:
                    records, cached_local = repository.bookmark_bundle()
                    cached_remote = (None if records is None else
                                     tuple(record.as_artwork() for record in records))
                except Exception:
                    # Bookmarks are optional: malformed cache data must not
                    # prevent a healthy downloaded library from opening.
                    records, cached_remote, cached_local = (), None, ()
                    self.bookmark_cache_failed = True
                catalog = ReadonlyLocalMediaCatalog(library_image_root, snapshot)
            except BaseException:
                if "repository" in locals():
                    repository.close()
                self.images.close()
                raise
            self.images.publish(catalog, self._remote_images(records or ()))
            self.library = snapshot
            self.library_repository = repository
            self._cached_remote_bookmarks = cached_remote
            self._cached_local_bookmarks = cached_local
            self._remote_records = records or ()
        elif live_client is not None:
            # In live mode the library contains no invented downloaded works.
            self.library = LibrarySnapshot(())
        self.learning_sandbox = learning_sandbox
        self.learning = None
        if learning_sandbox is not None:
            from .learning_service import LearningService
            try:
                self.learning = LearningService(learning_sandbox, self.artworks)
            except Exception:
                self.images.close()
                if self.library_repository is not None:
                    self.library_repository.close()
                raise
        if download_sandbox is not None:
            from .download_sandbox import DownloadSandbox
            from .job_service import JobService
            try:
                self.download_writer = DownloadSandbox(Path(download_sandbox), self.artworks,
                                                       catalog=self.images)
                self.jobs = JobService(self.download_writer.run, self.download_writer.storage_path)
            except BaseException:
                if self.download_writer is not None:
                    self.download_writer.close()
                if self.learning is not None:
                    self.learning.close()
                self.images.close()
                if self.library_repository is not None:
                    self.library_repository.close()
                raise
        self.closing = False
        self.closed = False
        self._close_lock = Lock()
        if pixiv_download_root is not None:
            from .managed_downloads import ManagedPixivDownloads
            from .job_service import JobService
            from .managed_download_catalog import build_managed_library
            from .managed_download_requests import ManagedDownloadRequests
            try:
                self.managed_downloads = ManagedPixivDownloads(
                    Path(pixiv_download_root), live_client, resolve_record=self._resolve_managed_record)
                if legacy_zip_export_root is not None:
                    from .legacy_zip_export import LegacyZipExporter
                    self.legacy_zip_exporter = LegacyZipExporter(
                        Path(legacy_zip_export_root), self.managed_downloads)
                self.jobs = JobService(self._run_managed_job, self.managed_downloads.storage_path)
                self._managed_requests = ManagedDownloadRequests(self.managed_downloads.root)
                for job in self.jobs.snapshot():
                    if job.kind != "pixiv-illust":
                        raise ValueError("foreign job in managed download history")
                    identifiers = self.jobs.operation_ids(job.job_id)
                    if not identifiers:
                        raise ValueError("managed job has no durable accepted request")
                    recovered = [self._managed_requests.load(key) for key in identifiers]
                    first = recovered[0]
                    if (first.work_id != job.target_id or first.title != job.title or
                            any(not self._managed_requests.equivalent(first, item) for item in recovered)):
                        raise ValueError("managed job request identity mismatch")
                    self._managed_job_records[job.job_id] = first
                self._reconcile_interrupted_author_jobs()
                snapshot, catalog, local = build_managed_library(self.managed_downloads)
                try:
                    self.images.publish(catalog, None)
                except BaseException:
                    catalog.close()
                    raise
                self.library = snapshot
                self._managed_receipts = catalog.receipts
                self._cached_local_bookmarks = local
            except BaseException:
                self.close()
                raise

    @property
    def library_refresh_available(self) -> bool:
        return self.library_repository is not None or self.managed_downloads is not None

    @property
    def legacy_zip_export_available(self) -> bool:
        return not self.closing and self.legacy_zip_exporter is not None

    async def export_managed_author_zip(self, author_id: str) -> str:
        """Export only a verified local snapshot; never call a Pixiv API."""
        if not self.legacy_zip_export_available:
            raise RuntimeError('legacy ZIP export is not configured')
        archive = await asyncio.to_thread(self.legacy_zip_exporter.export_author, author_id)
        return archive.name

    def bookmark_download_candidates(self):
        """Only account bookmark results: a manual lookup never alters a client list."""
        if self.closing or self.managed_downloads is None:
            return ()
        return tuple(record for record in self._remote_records
            if record.kind == "illustration" and record.illustration_type in (0, 1)
            and record.page_count_known)

    @staticmethod
    def _direct_owner(owner):
        if type(owner) is not str or re.fullmatch(r"[a-f0-9]{32}", owner) is None:
            raise ValueError("invalid download view owner")
        return owner

    def download_candidates(self, *, owner=None):
        """Bookmarked records shared; manually looked-up IDs only for their view."""
        if self.closing or self.managed_downloads is None:
            return ()
        with self._managed_target_lock:
            candidates = {record.work_id: record for record in self.bookmark_download_candidates()}
            if owner is not None:
                token = self._direct_owner(owner)
                author_pins = self._author_owned_pins.get(token, frozenset())
                for wid, record in self._direct_download_candidates.get(token, {}).items():
                    if wid in author_pins:
                        candidates[wid] = record  # Immutable selected author candidate.
                    else:
                        candidates.setdefault(wid, record)
            return tuple(candidates.values())

    def remember_managed_work_id(self, owner, record):
        """Register the looked-up DTO only after its owning UI accepts it."""
        from .bookmark_remote_adapter import RemoteBookmark
        token = self._direct_owner(owner)
        if (not isinstance(record, RemoteBookmark) or record.kind != "illustration"
                or type(record.illustration_type) is not int or record.illustration_type not in (0, 1)
                or record.page_count_known is not True):
            raise ValueError("artwork cannot be saved")
        with self._managed_target_lock:
            if self.closing or self.managed_downloads is None:
                raise RuntimeError("managed downloads unavailable")
            records = self._direct_download_candidates.get(token)
            if records is None:
                if len(self._direct_download_candidates) >= 256:
                    raise RuntimeError("too many managed download views")
                records = OrderedDict()
                self._direct_download_candidates[token] = records
            pinned = self._author_owned_pins.get(token, frozenset())
            if record.work_id in pinned:
                if records[record.work_id] != record:
                    raise ValueError('author selection already pinned')
                return
            records[record.work_id] = record
            records.move_to_end(record.work_id)
            while len(records) > 128:
                removable = next((wid for wid in records if wid not in pinned), None)
                if removable is None:
                    raise ValueError('download candidate capacity reached')
                del records[removable]

    def remember_managed_author_batch(self, owner, records):
        """Atomically authorize a bounded preview batch for this client.

        Unlike one-off manual lookup, author batches must never evict earlier
        selections. Existing bookmark DTOs retain their original metadata.
        """
        from .bookmark_remote_adapter import RemoteBookmark
        token = self._direct_owner(owner)
        if type(records) not in (tuple, list) or len(records) > 128:
            raise ValueError('author batch too large')
        with self._managed_target_lock:
            if self.closing or self.managed_downloads is None:
                raise RuntimeError('managed downloads unavailable')
            original = self._direct_download_candidates.get(token, OrderedDict())
            proposed = OrderedDict(original)
            pinned = set(self._author_owned_pins.get(token, frozenset()))
            selected = []
            for record in records:
                if (not isinstance(record, RemoteBookmark) or record.kind != 'illustration'
                        or type(record.illustration_type) is not int
                        or record.illustration_type not in (0, 1)
                        or record.page_count_known is not True):
                    raise ValueError('author selection rejected')
                # Author-derived saves must retain the freshly resolved
                # artwork metadata. A shared bookmark snapshot may be older;
                # preserving it would write stale title/tags into the new
                # generation even when original bytes are current.
                proposed[record.work_id] = record
                pinned.add(record.work_id)
                selected.append(record)
            if len(proposed) > 128:
                raise ValueError('author selections exceed client capacity')
            if selected and token not in self._direct_download_candidates:
                if len(self._direct_download_candidates) >= 256:
                    raise RuntimeError('too many managed download views')
            if proposed:
                self._direct_download_candidates[token] = proposed
                self._author_owned_pins[token] = frozenset(pinned)
            return tuple(selected)

    def release_managed_owner(self, owner):
        token = self._direct_owner(owner)
        with self._managed_target_lock:
            self._direct_download_candidates.pop(token, None)
            self._author_owned_pins.pop(token, None)

    async def lookup_managed_work_id(self, work_id: str, *, owner: str, publish=True):
        """Verify an explicit artwork ID off the UI thread, then cache a DTO.

        A lookup does not enqueue a job, fetch images or change a bookmark.
        A bounded cache only authorizes the later explicit Save button; the
        accepted job pins its own immutable metadata in managed intent storage.
        """
        from .pixiv_read_client import _identifier, InvalidPayload
        token = self._direct_owner(owner)
        if type(work_id) is not str:
            raise ValueError("invalid work ID")
        try:
            wid = _identifier(work_id)
        except InvalidPayload:
            raise ValueError("invalid work ID") from None
        if self.closing or self.managed_downloads is None or self.live_client is None:
            raise RuntimeError("managed downloads unavailable")

        def fetch():
            require = getattr(self.live_client, "require_identity", None)
            if callable(require):
                require()
            else:
                self.live_client.verify_identity()
            return self.live_client.get_artwork_download_record(wid)

        record = await asyncio.to_thread(fetch)
        if self.closing or self.managed_downloads is None:
            raise RuntimeError("managed downloads unavailable")
        from .bookmark_remote_adapter import RemoteBookmark
        if (not isinstance(record, RemoteBookmark) or record.work_id != wid
                or record.kind != "illustration" or record.illustration_type not in (0, 1)
                or record.page_count_known is not True):
            raise ValueError("artwork cannot be saved")
        if publish:
            self.remember_managed_work_id(token, record)
        return record

    async def snapshot_author_saved_works(self):
        """Verify all active receipts once per author batch, off the UI loop."""
        if self.closing or self.managed_downloads is None:
            raise RuntimeError('managed downloads unavailable')

        def verified():
            with_dates = getattr(self.managed_downloads, 'saved_receipts_with_dates', None)
            if callable(with_dates):
                return {receipt['work_id']: (digest, date, relative)
                        for relative, receipt, digest, date in with_dates()}
            items = self.managed_downloads.saved_receipts()
            current_date = getattr(self.managed_downloads, 'saved_update_date', None)
            return {receipt['work_id']: (digest, current_date(receipt['work_id'])
                    if callable(current_date) else None, relative)
                    for relative, receipt, digest in items}

        result = await asyncio.to_thread(verified)
        if self.closing:
            raise RuntimeError('managed downloads unavailable')
        return result

    async def lookup_managed_author(self, user_id: str, *, owner: str,
                                    publish=False, saved_snapshot=None):
        """Preview bounded author works without enqueuing or mutating legacy data.

        Existing v1 receipts have no update_date; they remain UNKNOWN rather
        than being silently treated as current or forcibly overwritten.
        """
        from .author_download import Difference, decide_difference
        from .pixiv_read_client import _identifier
        self._direct_owner(owner)
        uid = _identifier(user_id)
        if self.closing or self.managed_downloads is None or self.live_client is None:
            raise RuntimeError('managed downloads unavailable')

        def fetch():
            require = getattr(self.live_client, 'require_identity', None)
            if callable(require):
                require()
            else:
                self.live_client.verify_identity()
            works = self.live_client.get_author_artworks(uid)
            saved = saved_snapshot
            if saved is None:
                with_dates = getattr(self.managed_downloads, 'saved_receipts_with_dates', None)
                if callable(with_dates):
                    saved = {receipt['work_id']: (digest, date, relative)
                             for relative, receipt, digest, date in with_dates()}
                else:
                    items = self.managed_downloads.saved_receipts()
                    current_date = getattr(self.managed_downloads, 'saved_update_date', None)
                    saved = {receipt['work_id']: (digest, current_date(receipt['work_id'])
                             if callable(current_date) else None, relative)
                             for relative, receipt, digest in items}
            output = []
            for work in works:
                if work['type'] not in (0, 1):
                    continue
                prior = saved.get(work['id'])
                difference = decide_difference(
                    saved=prior is not None, remote_update_date=work['update_date'],
                    saved_update_date=prior[1] if prior is not None else None,
                    files_present=True if prior is not None else None)
                output.append(({**work, 'expected_receipt_sha256': prior[0] if prior else None,
                                'expected_relative_receipt': prior[2] if prior else None},
                               difference))
            return tuple(output)

        items = await asyncio.to_thread(fetch)
        if self.closing:
            raise RuntimeError('managed downloads unavailable')
        return items

    async def followed_download_authors(self, *, owner: str):
        """Return fresh public/private follow choices without changing old DB."""
        from .author_download import AuthorTarget
        self._direct_owner(owner)
        if self.closing or self.managed_downloads is None or self.live_client is None:
            raise RuntimeError('managed downloads unavailable')

        def fetch():
            uid = self.live_client.verify_identity()
            seen = {}
            for visibility in ('show', 'hide'):
                for entry in self.live_client.get_following_users(uid, visibility):
                    if entry['user_id'] not in seen:
                        seen[entry['user_id']] = AuthorTarget(
                            entry['user_id'], entry['name'], 'follow')
            return tuple(seen.values())

        result = await asyncio.to_thread(fetch)
        if self.closing:
            raise RuntimeError('managed downloads unavailable')
        return result

    def _resolve_managed_record(self, work_id):
        # The serial runner installs exactly one accepted job's frozen record.
        # Another job or candidate refresh cannot substitute its metadata.
        with self._managed_target_lock:
            pinned = self._active_managed_record
        return pinned if pinned is not None and pinned.work_id == work_id else None

    def _run_managed_job(self, job, context):
        # enqueue starts its worker before returning. Wait until the accepting
        # thread has the durable job ID, then bind the record to that ID only.
        with self._managed_enqueue_lock:
            accepted = self._managed_job_records.get(job.job_id)
            if accepted is None:
                raise RuntimeError("refresh and explicitly select the artwork again")
            from .managed_download_requests import ManagedAuthorIntent
            record = accepted.record if isinstance(accepted, ManagedAuthorIntent) else accepted
            with self._managed_target_lock:
                self._active_managed_record = record
        try:
            if isinstance(accepted, ManagedAuthorIntent):
                return self.managed_downloads.run(job, context, intent=accepted)
            return self.managed_downloads.run(job, context)
        finally:
            with self._managed_target_lock:
                self._active_managed_record = None

    def _reconcile_interrupted_author_jobs(self):
        """Recover v2 jobs after active-pointer commit but before SQLite done.

        The only positive evidence is the fully verified active receipt AND a
        job-specific accepted fingerprint. A valid but not yet published v2
        save remains retryable. Legacy v1 job recovery is left unchanged.
        """
        from .managed_download_requests import (ManagedAuthorIntent,
                                                accepted_author_fingerprint)
        interrupted = [(job, self._managed_job_records.get(job.job_id))
                       for job in self.jobs.snapshot()
                       if job.state == 'stopped' and job.error_code == 'interrupted']
        if not any(isinstance(intent, ManagedAuthorIntent) for _, intent in interrupted):
            return
        verified = {receipt['work_id']: (relative, receipt, digest, date)
                    for relative, receipt, digest, date
                    in self.managed_downloads.saved_receipts_with_dates()}
        for job, intent in interrupted:
            if not isinstance(intent, ManagedAuthorIntent):
                continue
            match = verified.get(intent.work_id)
            active = self.managed_downloads._generations.current(intent.work_id)
            fingerprint = accepted_author_fingerprint(intent, job.job_id)
            if (match is not None and active is not None
                    and active.get('accepted_fingerprint') == fingerprint
                    and match[0] == active['relative_receipt']
                    and match[2] == active['sha256']
                    and match[1]['author_id'] == intent.record.author_id
                    and match[1]['illustration_type'] == intent.record.illustration_type
                    and match[1]['page_count'] == intent.record.page_count
                    and active['update_date'] == intent.update_date):
                self.jobs.reconcile_interrupted(
                    job.job_id, receipt=match[0], page_count=match[1]['page_count'])
            else:
                self.jobs.reconcile_interrupted(job.job_id)

    def enqueue_managed_download(self, record, operation_id, *, owner=None):
        """Accept one frozen selection off-loop, then let the app own its job.

        Do not hold the short candidate lock over SQLite I/O. A later refresh
        cannot replace metadata already pinned for an unfinished job.
        """
        from .bookmark_remote_adapter import RemoteBookmark
        from .managed_download_requests import ManagedAuthorIntent
        from .job_service import UNFINISHED
        selection = record.record if isinstance(record, ManagedAuthorIntent) else record
        if not isinstance(selection, RemoteBookmark) or not isinstance(operation_id, str):
            raise ValueError("invalid managed selection")
        with self._managed_enqueue_lock:
            if self.closing or self.managed_downloads is None or self.jobs is None:
                raise RuntimeError("managed downloads unavailable")
            with self._managed_target_lock:
                if selection not in self.download_candidates(owner=owner):
                    raise ValueError("download selection changed; refresh the candidates")
                existing = next((job for job in self.jobs.snapshot()
                                 if job.kind == "pixiv-illust" and job.target_id == selection.work_id
                                 and job.state in UNFINISHED), None)
                previous = self._managed_job_records.get(existing.job_id) if existing is not None else None
                if (existing is not None and previous is not None and
                        not self._managed_requests.equivalent(previous, record)):
                    raise ValueError("an earlier selection is still being processed")
            # A durable intent alone is not an accepted job. Only the committed
            # operations ledger above authorizes recovery of this exact input.
            self._managed_requests.store(operation_id, record)
            accepted = self.jobs.enqueue("pixiv-illust", selection.work_id,
                                         title=selection.title, operation_id=operation_id)
            self._managed_job_records.setdefault(accepted.job_id, record)
            live_ids = {job.job_id for job in self.jobs.snapshot()}
            self._managed_job_records = {key: value for key, value in self._managed_job_records.items()
                                         if key in live_ids}
            return accepted

    def library_snapshot(self) -> LibrarySnapshot:
        """Return the last successful immutable snapshot; never read SQLite on the UI loop."""
        if self.closing:
            raise RuntimeError("application services are closed")
        return self.library

    def _validate_library_ids(self, snapshot):
        demo_ids = {art.work_id for art in self.artworks}
        if any(work.work_id in demo_ids for work in snapshot.works):
            raise ValueError("library IDs collide with synthetic demo IDs")

    def _remote_images(self, records):
        if self.live_client is None or not records:
            return None
        from .remote_media import RemoteBookmarkImages
        return RemoteBookmarkImages(self.live_client, tuple(records))

    def _publish_images(self, snapshot, records) -> None:
        from .local_media_adapter import ReadonlyLocalMediaCatalog
        if self.managed_downloads is not None:
            # The immutable publication cache was prepared off-loop. Reusing
            # its receipt data must not rescan files in this UI-thread method.
            from .managed_download_catalog import ManagedDownloadCatalog
            local = ManagedDownloadCatalog(self.managed_downloads, self._managed_receipts)
        else:
            local = (ReadonlyLocalMediaCatalog(self.library_image_root, snapshot)
                     if self.library_image_root is not None else None)
        remote = self._remote_images(records)
        try:
            self.images.publish(local, remote)
        except BaseException:
            if local is not None:
                local.close()
            if remote is not None:
                remote.close()
            raise

    def acquire_library_images(self):
        return self.images.acquire()

    @property
    def failed_records_available(self) -> bool:
        return (self.data_mode == "synthetic" or self.library_readonly_db is not None
                or self.failed_queue_readonly_db is not None)

    @property
    def legacy_failed_retry_available(self) -> bool:
        """Explicit opt-in only; never silently mutate the original database."""
        return (not self.closing and self.failed_queue_readonly_db is not None
                and self.managed_downloads is not None and self.jobs is not None)

    async def read_failed_records(self):
        """Read the legacy failure table off-loop, without changing any job or DB."""
        if self.closing:
            raise RuntimeError("application services are closed")
        if self.failed_queue_readonly_db is not None:
            records = await asyncio.to_thread(self._read_immutable_failure_snapshot)
            if self.closing:
                raise RuntimeError('application services are closed')
            return records
        if self.data_mode == "synthetic":
            from .failed_records_fixtures import build_failed_records_fixture
            return build_failed_records_fixture()
        if self.library_readonly_db is None:
            raise RuntimeError("failure record database is not configured")
        from .failed_records_repository import read_failed_records
        records = await asyncio.to_thread(read_failed_records, self.library_readonly_db)
        if self.closing:
            raise RuntimeError("application services are closed")
        return records

    def _check_immutable_failure_source(self) -> None:
        from .failed_records_repository import immutable_copy_signature
        if (self._failed_copy_signature is None or
                immutable_copy_signature(self.failed_queue_readonly_db) != self._failed_copy_signature):
            raise ValueError('legacy failure copy changed; stop and prepare a new snapshot')

    def _read_immutable_failure_snapshot(self):
        from .failed_records_repository import read_failed_records
        self._check_immutable_failure_source()
        records = read_failed_records(self.failed_queue_readonly_db, immutable=True)
        self._check_immutable_failure_source()
        return records

    @staticmethod
    def _supported_failed_original(failure) -> bool:
        """Only the original Flet illust/manga rows can use managed v1 retry."""
        from .failed_records_state import FailureRecord
        return (isinstance(failure, FailureRecord)
                and failure.work_type in ('illust', 'manga')
                and type(failure.work_id) is str and re.fullmatch(r'[1-9][0-9]{0,19}', failure.work_id) is not None
                and type(failure.user_id) is str and re.fullmatch(r'[1-9][0-9]{0,19}', failure.user_id) is not None)

    def _assert_legacy_retry_row(self, failure):
        """Re-read the immutable source on worker thread before acceptance."""
        if not self.legacy_failed_retry_available or not self._supported_failed_original(failure):
            raise ValueError('legacy failure is not supported by managed downloads')
        rows = self._read_immutable_failure_snapshot()
        if not any(item == failure for item in rows):
            raise ValueError('legacy failure selection changed; refresh the list')

    async def lookup_legacy_failed_original(self, failure, *, owner):
        """No mutation: verify source row and remote work ID off the UI loop."""
        self._direct_owner(owner)
        await asyncio.to_thread(self._assert_legacy_retry_row, failure)
        record = await self.lookup_managed_work_id(failure.work_id, owner=owner, publish=False)
        if (not self._supported_failed_original(failure) or record.author_id != failure.user_id
                or record.illustration_type != (1 if failure.work_type == 'manga' else 0)):
            raise ValueError('legacy failure artwork identity changed')
        return record

    def enqueue_legacy_failed_original(self, failure, record, operation_id, *, owner):
        """Explicitly accept into new SQLite jobs; the old failed_queue stays intact."""
        from .bookmark_remote_adapter import RemoteBookmark
        self._direct_owner(owner)
        self._assert_legacy_retry_row(failure)
        if (not isinstance(record, RemoteBookmark) or record.remote_id != failure.work_id
                or record.author_id != failure.user_id
                or record.illustration_type != (1 if failure.work_type == 'manga' else 0)):
            raise ValueError('legacy failure artwork identity changed')
        # Author-batch pins protect UI preview selections until accepted. A
        # legacy failed-list retry, however, has no multi-item preview: its
        # frozen DTO lives in the accepted SQLite intent immediately after
        # enqueue. Drop this *view-exclusive* temporary owner on both success
        # and failure; otherwise 3 filtered batches can strand >128 pins.
        try:
            (authorized,) = self.remember_managed_author_batch(owner, (record,))
            return self.enqueue_managed_download(authorized, operation_id, owner=owner)
        finally:
            self.release_managed_owner(owner)

    @property
    def data_mode(self):
        if self.live_client is not None:
            return "pixiv-readonly"
        return "library-readonly" if self.library_repository is not None else "synthetic"

    async def refresh_library_snapshot(self) -> LibrarySnapshot:
        if not self.library_refresh_available:
            return self.library_snapshot()
        async with self._library_refresh_lock:
            if self.closing:
                raise RuntimeError("application services are closed")
            if self.managed_downloads is not None:
                from .managed_download_catalog import build_managed_library
                snapshot, catalog, local = await asyncio.to_thread(build_managed_library, self.managed_downloads)
                if self.closing:
                    catalog.close()
                    raise RuntimeError("application services are closed")
                try:
                    self.images.publish(catalog, self._remote_images(self._remote_records))
                except BaseException:
                    catalog.close()
                    raise
                self.library = snapshot
                self._managed_receipts = catalog.receipts
                self._cached_local_bookmarks = local
                return snapshot
            snapshot = await asyncio.to_thread(self.library_repository.library_snapshot)
            if self.closing:
                raise RuntimeError("application services are closed")
            self._validate_library_ids(snapshot)
            self._publish_images(snapshot, self._remote_records)
            self.library = snapshot
            return snapshot

    def bookmark_remote_snapshot(self):
        """Offline SQLite cache if opted in; otherwise synthetic demo entries."""
        if self.closing:
            raise RuntimeError("application services are closed")
        return self._cached_remote_bookmarks

    def bookmark_local_snapshot(self):
        """Downloaded badge fixtures are separate from remote bookmarks."""
        if self.closing:
            raise RuntimeError("application services are closed")
        return self._cached_local_bookmarks

    async def refresh_bookmark_cache(self):
        """Publish all routes atomically. A failed refresh leaves old records and images intact."""
        if self.library_repository is None and self.live_client is None:
            return self.bookmark_remote_snapshot(), self.bookmark_local_snapshot()
        async with self._library_refresh_lock:
            if self.closing:
                raise RuntimeError("application services are closed")
            if self.live_client is not None:
                from .bookmark_remote_adapter import fetch_all_bookmarks
                def read_all():
                    identity = self.live_client.verify_identity()
                    result = fetch_all_bookmarks(self.live_client, identity)
                    if not result.ok:
                        raise RuntimeError("bookmark routes incomplete")
                    return result.records
                records = await asyncio.to_thread(read_all)
                remote = tuple(record.as_artwork() for record in records)
                local = self._cached_local_bookmarks
                if self.library_repository is not None:
                    local = await asyncio.to_thread(self.library_repository.local_bookmark_snapshot)
            else:
                records, local = await asyncio.to_thread(self.library_repository.bookmark_bundle)
                remote = None if records is None else tuple(record.as_artwork() for record in records)
            if self.closing:
                raise RuntimeError("application services are closed")
            if remote is None:
                raise RuntimeError("bookmark cache unavailable")
            self._publish_images(self.library, records or ())
            self._cached_remote_bookmarks = remote
            self._cached_local_bookmarks = local
            with self._managed_target_lock:
                self._remote_records = records or ()
            self.bookmark_cache_failed = False
            return remote, local

    def close(self) -> None:
        with self._close_lock:
            if not self.closed:
                self.closing = True
                # Accepted jobs outlive views; join the worker before closing
                # the image provider it may still be using.
                if self.jobs is not None:
                    self.jobs.close()
                if self.download_writer is not None:
                    self.download_writer.close()
                if self.managed_downloads is not None:
                    if self.legacy_zip_exporter is not None:
                        self.legacy_zip_exporter.close()
                    self.managed_downloads.close()
                try:
                    if self.learning is not None:
                        self.learning.close()
                finally:
                    self.images.close()
                    if self.library_repository is not None:
                        self.library_repository.close()
                    if self.live_client is not None:
                        self.live_client.close()
                    self.closed = True


_services: ApplicationServices | None = None
_lock = Lock()
_APP_KEY = "_pixivvault_application_services"


def get_services(app=None, *, learning_sandbox: Path | None = None,
                 library_readonly_db: Path | None = None,
                 library_image_root: Path | None = None,
                 live_client=None, download_sandbox: Path | None = None,
                 pixiv_download_root: Path | None = None,
                 failed_queue_readonly_db: Path | None = None,
                 legacy_zip_export_root: Path | None = None) -> ApplicationServices:
    """Return resources scoped to the ASGI app; no-argument access is legacy."""
    global _services
    with _lock:
        if app is None:
            if _services is None:
                _services = ApplicationServices(learning_sandbox=learning_sandbox,
                    library_readonly_db=library_readonly_db, library_image_root=library_image_root,
                    live_client=live_client, download_sandbox=download_sandbox,
                    pixiv_download_root=pixiv_download_root,
                    failed_queue_readonly_db=failed_queue_readonly_db,
                    legacy_zip_export_root=legacy_zip_export_root)
            services = _services
        else:
            services = getattr(app.state, _APP_KEY, None)
            if services is None:
                services = ApplicationServices(learning_sandbox=learning_sandbox,
                    library_readonly_db=library_readonly_db, library_image_root=library_image_root,
                    live_client=live_client, download_sandbox=download_sandbox,
                    pixiv_download_root=pixiv_download_root,
                    failed_queue_readonly_db=failed_queue_readonly_db,
                    legacy_zip_export_root=legacy_zip_export_root)
                setattr(app.state, _APP_KEY, services)
        if services.closing:
            raise RuntimeError("application services are closed")
        if learning_sandbox is not None and services.learning_sandbox != learning_sandbox:
            raise RuntimeError("application services already use a different learning storage")
        if library_readonly_db is not None and services.library_readonly_db != library_readonly_db:
            raise RuntimeError("application services already use a different library DB")
        if library_image_root is not None and services.library_image_root != library_image_root:
            raise RuntimeError("application services already use a different image root")
        if live_client is not None and services.live_client is not live_client:
            raise RuntimeError("application services already use a different pixiv session")
        if download_sandbox is not None and services.download_sandbox != download_sandbox:
            raise RuntimeError("application services already use different download storage")
        if pixiv_download_root is not None and services.pixiv_download_root != pixiv_download_root:
            raise RuntimeError("application services already use different managed download storage")
        if failed_queue_readonly_db is not None and services.failed_queue_readonly_db != failed_queue_readonly_db:
            raise RuntimeError('application services already use a different failure source')
        if legacy_zip_export_root is not None and services.legacy_zip_export_root != legacy_zip_export_root:
            raise RuntimeError('application services already use a different ZIP export root')
        return services
