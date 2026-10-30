"""アプリ単位の資源。画面の再接続では作り直したり閉じたりしない。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
import re
from threading import Event, Lock, RLock

from .fixtures import generate_demo_artworks
from .media import DemoImageCatalog
from .library_fixtures import build_library_fixture
from .library_models import LibrarySnapshot
from .library_media_registry import LibraryImageRegistry
from .request_policy import job_request_runner, request_scope

_CURRENT_IMAGE_READER = object()


def _frozen_library_stat(path):
    from .library_repository import require_frozen_library_copy
    path = require_frozen_library_copy(path)
    info = path.stat()
    return info.st_size, info.st_mtime_ns, getattr(info, 'st_ino', 0)


def _legacy_managed_capabilities(path):
    """Inspect optional legacy capabilities without changing its schema."""
    import sqlite3
    from .fs_paths import sqlite_readonly_uri
    from .failed_records_repository import immutable_copy_signature, MAX_IMMUTABLE_COPY_BYTES
    from .failed_records_state import CSV_COLUMNS
    before = _frozen_library_stat(path)
    signature = immutable_copy_signature(path) if before[0] <= MAX_IMMUTABLE_COPY_BYTES else None
    conn = sqlite3.connect(sqlite_readonly_uri(path, immutable=True), uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        columns = {table: {row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')}
                   for table in ('works', 'failed_queue', 'file_ledger', 'following_users')}
    finally:
        conn.close()
    if _frozen_library_stat(path) != before:
        raise ValueError('frozen legacy source changed during inspection')
    if signature is not None and immutable_copy_signature(path) != signature:
        raise ValueError('frozen legacy source changed during inspection')
    capable = set()
    warnings = []
    if signature is None:
        warnings.append('旧DBは閲覧できますが、旧失敗再試行・差分・未判定重複の検証サイズ上限を超えています。')
    else:
        if set(CSV_COLUMNS) <= columns['failed_queue']:
            capable.add('failed')
        else:
            warnings.append('旧DBに対応する失敗記録の表がないため、旧失敗の再試行は利用できません。')
        required = {'work_id', 'user_id', 'title', 'update_date', 'is_deleted',
                    'missing_streak', 'page_count', 'content_type'}
        if 'failed' in capable and required <= columns['works']:
            capable.add('author_diff')
        else:
            warnings.append('旧DBの差分情報を検証できないため、旧保存に対する差分は未確認として扱います。')
        ledger = {'work_id', 'page', 'store_path', 'entry_name', 'reason',
                  'ambiguous_group', 'status', 'resolved_by'}
        if (ledger <= columns['file_ledger'] and {'work_id', 'title', 'user_id'} <= columns['works']
                and {'user_id', 'name'} <= columns['following_users']):
            capable.add('undecidable')
        else:
            warnings.append('旧DBに対応するfile_ledgerがないため、旧未判定重複の参照は利用できません。')
    return frozenset(capable), signature, before, tuple(warnings)


class ApplicationServices:
    def __init__(self, count: int = 120, *, learning_sandbox: Path | None = None,
                 library_readonly_db: Path | None = None,
                 library_image_root: Path | None = None,
                 live_client=None, download_sandbox: Path | None = None,
                 pixiv_download_root: Path | None = None,
                 failed_queue_readonly_db: Path | None = None,
                 legacy_zip_export_root: Path | None = None,
                 legacy_diff_image_root: Path | None = None,
                 novel_fixture_root: Path | None = None,
                 novel_fixture_source=None,
                 ugoira_root: Path | None = None, ugoira_source=None, ugoira_codec_python=None,
                 recommendation_preferences_root=None, preferences_root=None, discovery_root=None,
                 external_vault_root=None, image_cache_root=None, discovery_following=None,
                 external_vault_transport=None, external_vault_crypto=None, desktop=None, notification_backend=None,
                 following_sync_root=None, pixiv_actions_mode='readonly') -> None:
        if (type(pixiv_actions_mode) is not str or
                pixiv_actions_mode not in ('readonly', 'enabled', 'preferences')):
            raise ValueError('invalid Pixiv action policy')
        if (novel_fixture_root is None) != (novel_fixture_source is None):
            raise ValueError("novel fixture mode requires an explicit root and source")
        combined_managed = pixiv_download_root is not None and live_client is not None
        if novel_fixture_root is not None and not combined_managed and any(value is not None for value in (
                live_client, download_sandbox, pixiv_download_root, library_readonly_db,
                library_image_root, learning_sandbox, failed_queue_readonly_db,
                legacy_zip_export_root, legacy_diff_image_root)):
            raise ValueError("novel fixture mode requires an isolated configuration")
        self.legacy_capacity = None
        self._owned_legacy_capacity_cache = None
        self.preferences_root = preferences_root
        self.discovery_root = discovery_root
        self.following_sync_root = following_sync_root
        self.following_sync = None
        self.pixiv_actions_mode = pixiv_actions_mode
        self.pixiv_actions = None
        self.external_vault_root = external_vault_root
        self.image_cache_root = image_cache_root
        self.preferences = None
        self._failure_actions_lock = RLock()
        self._failure_record_actions = None
        self.discovery = None
        self.external_vault = None
        self.author_media = None
        self.shared_image_cache = None
        self.desktop = desktop
        self.notifications = None
        self.tray = None
        self.save_exports = None
        self.extension_commands = None
        self.extension_bridge = None
        self.extension_bridge_status = "disabled"
        self._startup_discovery_stop = Event()
        self._startup_discovery_task = None
        self._public_images = None
        self._vault_images = None
        self._discovery_following_source = discovery_following
        # None is unknown; a context holds (reader, account_id, rows). Publish
        # the account and rows together so concurrent readers cannot mix them.
        self._following_snapshot = None
        if discovery_root is not None and (live_client is None or learning_sandbox is not None):
            raise ValueError('discovery requires an explicit reader and its own managed root')
        if following_sync_root is not None and (live_client is None or learning_sandbox is not None):
            raise ValueError('following synchronization requires an explicit reader and its own managed root')
        if image_cache_root is not None and preferences_root is None:
            raise ValueError('persistent thumbnail cache requires explicit preferences')
        self.novel_fixture_root = novel_fixture_root
        self.novel_fixture_source = novel_fixture_source
        self.novel_jobs = None
        self.duplicate_inspection = None
        self.recommendations = None
        self.recommendation_preferences_root = recommendation_preferences_root
        self.ranking_source = None
        self.similarity_source = None
        self.similarity_index = None
        self._egara_model_path = None
        self._egara_provider = 'auto'
        self._egara_inference = None
        self._similarity_legacy_inputs = None
        self._similarity_run_lock = RLock()
        self._similarity_stop = Event()
        self._similarity_worker = None
        self.ugoira = None
        self.ugoira_root, self.ugoira_source = ugoira_root, ugoira_source
        if (ugoira_root is None) != (ugoira_source is None):
            raise ValueError('ugoira root/source must be explicit')
        if ugoira_root is not None and not combined_managed and any(v is not None for v in (learning_sandbox, library_readonly_db, library_image_root,live_client,download_sandbox,pixiv_download_root,failed_queue_readonly_db,legacy_zip_export_root,legacy_diff_image_root)):
            raise ValueError('ugoira requires isolated storage')
        if (download_sandbox is not None and learning_sandbox is not None
                and Path(download_sandbox).resolve() == Path(learning_sandbox).resolve()):
            raise ValueError("learning and download sandboxes must be separate")
        # A configuration that mixes mutually exclusive source modes is invalid
        # even before source-path existence/overlap checks. Retain the stricter
        # read-only root fence below for legitimate combined managed/legacy mode.
        if download_sandbox is not None and (library_readonly_db is not None or live_client is not None):
            raise ValueError("synthetic downloads cannot be combined with a read-only or live library")
        from .media_jobs import validate_media_roots, validate_readonly_boundaries
        validate_readonly_boundaries(
            (novel_fixture_root, ugoira_root, pixiv_download_root, download_sandbox,
             learning_sandbox, legacy_zip_export_root, preferences_root, discovery_root,
             external_vault_root, image_cache_root, recommendation_preferences_root, following_sync_root),
            (library_image_root, legacy_diff_image_root,
             Path(library_readonly_db).parent if library_readonly_db else None,
             Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None))
        validate_media_roots(novel_fixture_root, ugoira_root, pixiv_download_root,
                             download_sandbox, learning_sandbox, legacy_zip_export_root,
                             preferences_root, discovery_root, external_vault_root, image_cache_root,
                             recommendation_preferences_root, following_sync_root)
        for sidecar in (preferences_root,discovery_root,external_vault_root,image_cache_root,following_sync_root):
            if sidecar is not None:
                validate_readonly_boundaries((sidecar,),
                    (Path(library_readonly_db).parent if library_readonly_db else None,
                     library_image_root,
                     Path(failed_queue_readonly_db).parent if failed_queue_readonly_db else None,
                     legacy_diff_image_root))
        if recommendation_preferences_root is not None:
            if library_readonly_db is None and discovery_root is None:
                raise ValueError('recommendation preferences require a readonly library or managed discovery')
            from .media_jobs import validate_media_roots
            validate_readonly_boundaries((recommendation_preferences_root,),
                (Path(library_readonly_db).parent if library_readonly_db else None, library_image_root))
        if (library_readonly_db is None) != (library_image_root is None):
            raise ValueError("read-only library requires both explicit DB and image root")
        if library_readonly_db is not None:
            # A normal SQLite mode=ro handle may still create a WAL shared
            # memory file on Windows, even though all writes are query-only.
            # Reject an active/live WAL source before *any* service/cache can
            # open it, and use an immutable URI for this frozen snapshot.
            from .library_repository import require_frozen_library_copy
            library_readonly_db=require_frozen_library_copy(library_readonly_db)
        if pixiv_download_root is not None and (
                live_client is None or download_sandbox is not None or
                learning_sandbox is not None):
            raise ValueError("managed Pixiv downloads require an explicit live client and separate storage")
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
        if legacy_diff_image_root is not None:
            if failed_queue_readonly_db is None or pixiv_download_root is None or live_client is None:
                raise ValueError('legacy author diff requires a frozen old DB copy and managed Pixiv mode')
            image = Path(legacy_diff_image_root)
            source = Path(failed_queue_readonly_db).resolve()
            managed = Path(pixiv_download_root).resolve()
            if (not image.is_absolute() or not image.is_dir() or image.is_symlink()
                    or image.resolve() == managed
                    or image.resolve() in managed.parents
                    or managed in image.resolve().parents
                    or image.resolve() == source.parent
                    or source in image.resolve().parents):
                raise ValueError('legacy image root must be an existing separate read-only folder')
        diff_db = failed_queue_readonly_db if legacy_diff_image_root is not None else None
        diff_signature = failure_signature if diff_db is not None else None
        undecidable_db = failed_queue_readonly_db
        self._legacy_copy_signature = None
        self._legacy_initial_stat = None
        self.legacy_source_warnings = ()
        if combined_managed and library_readonly_db is not None:
            capabilities, old_signature, old_stat, warnings = _legacy_managed_capabilities(library_readonly_db)
            self._legacy_copy_signature = old_signature
            self._legacy_initial_stat = old_stat
            self.legacy_source_warnings = warnings
            if failed_queue_readonly_db is None and 'failed' in capabilities:
                failed_queue_readonly_db = library_readonly_db
                failure_signature = old_signature
            if legacy_diff_image_root is None and 'author_diff' in capabilities:
                legacy_diff_image_root = library_image_root
                diff_db = library_readonly_db
                diff_signature = old_signature
            if undecidable_db is None and 'undecidable' in capabilities:
                undecidable_db = library_readonly_db
        self.download_sandbox = download_sandbox
        self.pixiv_download_root = pixiv_download_root
        self.legacy_zip_export_root = legacy_zip_export_root
        self.legacy_zip_exporter = None
        self.legacy_diff_image_root = legacy_diff_image_root
        self.legacy_author_bridge = None
        self.managed_scheduler = None
        self.managed_schedule_runner = None
        # Phase10: metadata settings are explicit, OFF by default and do not
        # start any HTTP/backfill worker. Owned by the app, never by a tab.
        self.managed_metadata = None
        self._managed_metadata_lock = RLock()
        self._metadata_run_lock = Lock()
        self._metadata_cancel_event = Event()
        self._managed_scheduler_lock = Lock()
        self._scheduler_dispatch_lock = Lock()
        self._scheduler_cancel_event = Event()
        self._scheduler_unfinished_count = None
        self.failed_queue_readonly_db = failed_queue_readonly_db
        self._failed_copy_signature = failure_signature
        self.managed_downloads = None
        self._managed_requests = None
        self._managed_job_records = {}
        self._active_managed_record = None
        self._managed_target_lock = RLock()
        self._managed_enqueue_lock = Lock()
        self._media_admission_lock = RLock()
        # A completed remote crawl must be committed in the same order it was
        # initiated. Parallel clients otherwise could commit an older Pixiv
        # listing after a newer scan and invent a fresh deletion streak.
        self._legacy_inventory_lock = Lock()
        self._direct_download_candidates: dict[str, OrderedDict] = {}
        self._author_owned_pins: dict[str, frozenset[str]] = {}
        self._managed_receipts = ()
        self._managed_catalog_snapshot = None
        self._managed_library_snapshot = LibrarySnapshot(())
        self._managed_local_bookmarks = ()
        self._legacy_library_snapshot = None
        self._legacy_local_bookmarks = ()
        self._library_composition = None
        self.jobs = None
        self.static_jobs = None
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
                repository = ReadonlyLibraryRepository(library_readonly_db, immutable=True)
                snapshot = repository.library_snapshot()
                self._assert_legacy_library_source()
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
            self._legacy_library_snapshot = snapshot
            self._legacy_local_bookmarks = tuple(cached_local)
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
        self._viewer_page_lock = Lock()
        self._legacy_export_lock = Lock()
        self._legacy_export_stop = Event()
        if pixiv_download_root is not None:
            from .managed_downloads import ManagedPixivDownloads
            from .job_service import JobService
            from .managed_download_catalog import build_managed_library
            from .managed_download_requests import ManagedDownloadRequests
            try:
                self.managed_downloads = ManagedPixivDownloads(
                    Path(pixiv_download_root), live_client, resolve_record=self._resolve_managed_record)
                if legacy_diff_image_root is not None:
                    from .legacy_author_sync import LegacyAuthorBridge
                    self.legacy_author_bridge = LegacyAuthorBridge(
                        Path(diff_db), Path(legacy_diff_image_root),
                        self.managed_downloads.root, source_signature=diff_signature)
                if legacy_zip_export_root is not None:
                    from .legacy_zip_export import LegacyZipExporter
                    self.legacy_zip_exporter = LegacyZipExporter(
                        Path(legacy_zip_export_root), self.managed_downloads)
                self.static_jobs = JobService(job_request_runner(self._run_managed_job), self.managed_downloads.storage_path)
                self.jobs = self.static_jobs
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
                self._publish_managed_library(snapshot, catalog, local,
                    legacy_snapshot=self._legacy_library_snapshot,
                    legacy_local=self._legacy_local_bookmarks, records=self._remote_records)
            except BaseException:
                self.close()
                raise

        if self.managed_downloads is not None:
            from .duplicate_inspection import ManagedDuplicateInspection
            # The explicit, standalone legacy failed-queue copy is also the
            # only safe source for original file_ledger ambiguous groups.
            # Never infer unresolved legacy works from managed receipts.
            self.duplicate_inspection = ManagedDuplicateInspection(
                self.managed_downloads, undecidable_db=undecidable_db)
            self.duplicate_inspection.on_changed = self.refresh_library_snapshot

        if self.live_client is not None and all(callable(getattr(self.live_client, name, None)) for name in ('get_ranking','search_illusts','get_work_info')):
            from .ranking_source import RankingSource
            self.ranking_source = RankingSource(self.live_client)
        if library_readonly_db is not None:
            from .similar_saved import SavedSimilaritySource
            self.similarity_source = SavedSimilaritySource(library_readonly_db)
            from .recommendation_state import FrozenRecommendationSource, RecommendationPreferences, RecommendationController
            try:
                source = FrozenRecommendationSource(library_readonly_db)
                preferences = (RecommendationPreferences(recommendation_preferences_root)
                               if recommendation_preferences_root is not None else None)
                from .recommendation_acquisition import PixivRecommendationAdapter
                acquisition = PixivRecommendationAdapter(self.live_client) if self.live_client is not None else None
                self.recommendations = RecommendationController(source, preferences,
                    acquisition=acquisition, trend_provider=source.trend_rows)
            except BaseException:
                self.close()
                raise

        if novel_fixture_root is not None:
            from .managed_novel_jobs import ManagedNovelJobs
            try:
                self.novel_jobs = ManagedNovelJobs(Path(novel_fixture_root), novel_fixture_source)
                self.jobs = self.novel_jobs.jobs
            except BaseException:
                self.close()
                raise

        if ugoira_root is not None:
            from .ugoira_practical import PracticalUgoiraJobs
            try:
                self.ugoira = PracticalUgoiraJobs(ugoira_root,ugoira_source,codec_python=ugoira_codec_python)
                self.jobs = self.ugoira.jobs
            except BaseException:
                self.close()
                raise

        owners = tuple(owner for owner in (
            self.static_jobs, self.novel_jobs.jobs if self.novel_jobs else None,
            self.ugoira.jobs if self.ugoira else None) if owner is not None)
        if len(owners) > 1:
            from .media_jobs import MediaJobs
            self.jobs = MediaJobs(*owners)
        try:
            if following_sync_root is not None:
                from .following_sync import FollowingSyncStore
                self.following_sync = FollowingSyncStore(following_sync_root)
            if preferences_root is not None:
                from .preferences import PreferencesStore
                self.preferences = PreferencesStore(Path(preferences_root) / 'preferences.sqlite3')
                if library_image_root is not None:
                    from .capacity_cache import CapacityCache
                    from .legacy_capacity import LegacyCapacityReader
                    self._owned_legacy_capacity_cache = CapacityCache.open_owned(Path(preferences_root))
                    self.configure_legacy_capacity(LegacyCapacityReader(library_image_root), self._owned_legacy_capacity_cache)
                from .notifications import NotificationDispatcher
                self.notifications = NotificationDispatcher(self.preferences,backend=notification_backend)
                from .shared_image_cache import SharedImageCache, DiskThumbnailCache
                disk = (DiskThumbnailCache(image_cache_root,
                    max_bytes=int(self.preferences.get_setting('pixiv_disk_thumbnail_cache_size_gb','12'))*1024**3)
                    if image_cache_root is not None else None)
                self.shared_image_cache = SharedImageCache(
                    max_bytes=int(self.preferences.get_setting('pixiv_shared_image_cache_size_mb','1024'))*1024**2,
                    disk=disk)
                self.images.bind_shared_cache(self.shared_image_cache)
                self._configure_request_preferences()
            # The legacy Cookie CLI remains GET-only. Normal native startup
            # reads the explicit preference once; changing it never replaces
            # an owner with pending/indeterminate operations in this app.
            actions_enabled = pixiv_actions_mode == 'enabled'
            if pixiv_actions_mode == 'preferences' and self.preferences is not None:
                actions_enabled = self.preferences.get_setting('pixiv_actions_enabled', '0') == '1'
            if actions_enabled:
                from .pixiv_read_client import LivePixivReadClient
                if isinstance(self.live_client, LivePixivReadClient):
                    from .pixiv_actions import PixivActions
                    self.pixiv_actions = PixivActions(self.live_client, following_store=self.following_sync)
            if discovery_root is not None:
                from .discovery_learning import DiscoveryLearningService
                self.discovery = DiscoveryLearningService(discovery_root, self.live_client,
                    following=self.discovery_following_snapshot, saved_ids=self.saved_discovery_ids,
                    seed_readonly_db=library_readonly_db)
            if self.discovery is not None and recommendation_preferences_root is not None:
                from .recommendation_state import (DiscoveryRecommendationSource, CombinedRecommendationSource,
                    RecommendationPreferences, RecommendationController)
                from .recommendation_acquisition import PixivRecommendationAdapter
                source = DiscoveryRecommendationSource(self.discovery)
                if self.recommendations is not None:
                    source = CombinedRecommendationSource(self.recommendations.source, source)
                    self.recommendations.close()
                self.recommendations = RecommendationController(source,
                    RecommendationPreferences(recommendation_preferences_root),
                    acquisition=PixivRecommendationAdapter(self.live_client),
                    trend_provider=self.discovery.trend_rows,
                    judged_provider=lambda: self.discovery.recommendation_snapshot()['judged_ids'])
            if external_vault_root is not None:
                from .external_vault import ExternalVaultService
                from .external_vault_transport import default_transport
                self.external_vault = ExternalVaultService(external_vault_root,
                    transport=external_vault_transport if external_vault_transport is not None else default_transport(),
                    crypto=external_vault_crypto)
                from .pixiv_read_client import PublicImageReadClient, VaultImageReader
                self._public_images = PublicImageReadClient()
                self._vault_images = VaultImageReader(self.external_vault, self._public_images)
                if self.preferences is not None:
                    self._public_images.set_request_admission(self._request_admission)
                    self._public_images.set_retry_policy(self._request_retry)
                if self.ranking_source is not None:
                    self.ranking_source.vault = self.external_vault
                if self.external_vault.try_auto_unlock():
                    self.external_vault.start_keeper()
            if self.managed_downloads is not None and (self.novel_jobs is not None or self.ugoira is not None):
                from .author_media import AuthorMediaService
                self.author_media = AuthorMediaService(self)
        except BaseException:
            self.close()
            raise
        if self.managed_downloads is not None:
            from .managed_scheduler import ManagedScheduler
            if ManagedScheduler.configured(self.managed_downloads.root):
                # Start dispatch only after every configured media owner and
                # its durable history have been recovered.
                self.enable_managed_scheduler()

    def novel_catalog(self,author_id=None,rest='show'):
        rows=self.novel_fixture_source.catalog(author_id,rest)
        if author_id is None and rest in ('show','hide') and self.save_exports is not None:
            for wid,aid,_title in rows:self.save_exports.remember_bookmark('novel',wid,aid)
        return rows

    def configure_similarity_index(self, root, *, model_file=None, provider='auto'):
        """Explicit managed output. No model inference or API on configuration."""
        from .media_jobs import validate_media_roots, validate_readonly_boundaries
        from .managed_similarity import ManagedSimilarityIndex, CombinedSimilaritySource
        if self.closing or self.closed or self.similarity_index is not None:
            raise ValueError('similarity storage unavailable or already configured')
        if self.managed_downloads is None and self.library_readonly_db is None:
            raise ValueError('saved originals required for similarity indexing')
        if provider not in ('auto', 'cuda', 'cpu'):
            raise ValueError('invalid similarity inference provider')
        if model_file is not None:
            from .managed_downloads import _safe_path
            from .egara_inference import EGARA_BB_SHA256
            if not Path(model_file).is_absolute():
                raise ValueError('similarity model must be an absolute file')
            _safe_path(Path(model_file), file=True)
            if not Path(model_file).is_file():
                raise ValueError('similarity model unavailable')
        readonly = (self.library_image_root, self.legacy_diff_image_root,
                    Path(self.library_readonly_db).parent if self.library_readonly_db else None,
                    Path(self.failed_queue_readonly_db).parent if self.failed_queue_readonly_db else None,
                    Path(model_file).parent if model_file is not None else None)
        validate_readonly_boundaries((root,), readonly)
        validate_media_roots(root, self.managed_downloads.root if self.managed_downloads else None, self.novel_fixture_root,
            self.ugoira_root, self.preferences_root, self.discovery_root,
            self.external_vault_root, self.image_cache_root,
            self.legacy_zip_export_root, self.recommendation_preferences_root,
            self.following_sync_root, self.save_exports.root if self.save_exports else None)
        with self._close_lock:
            if self.closing or self.closed:
                raise ValueError('similarity storage unavailable')
            index = ManagedSimilarityIndex(root)
            legacy = None
            try:
                if self.library_readonly_db is not None:
                    from .legacy_similarity_inputs import LegacySimilarityInputs
                    legacy = LegacySimilarityInputs(self.library_image_root,
                        self._legacy_library_snapshot, self.similarity_source)
                combined = CombinedSimilaritySource(self.similarity_source, index,
                                                    writer=self.managed_downloads, legacy=legacy)
            except BaseException:
                if legacy is not None:
                    legacy.close()
                index.close()
                raise
            self.similarity_index = index
            self.similarity_source = combined
            self._similarity_legacy_inputs = legacy
            self._egara_model_path = Path(model_file) if model_file is not None else None
            self._egara_provider = provider

    @property
    def similarity_analysis_enabled(self):
        return (self.similarity_index is not None and self._egara_model_path is not None
                and (self.managed_downloads is not None or self._similarity_legacy_inputs is not None)
                and not self.closing and not self.closed)

    def analyze_saved_for_similarity(self, work_id, page, *, is_current=None):
        """Explicit UI action; call off the event loop. page is Flet's 1-based ID."""
        from .egara_inference import EgaraInference, EGARA_BB_SHA256
        if type(page) is not int or page < 1:
            raise ValueError('invalid similarity page')
        def current():
            return (not self._similarity_stop.is_set() and self.similarity_analysis_enabled
                    and (is_current is None or is_current() is True))
        # This lock is independent of close/configuration: closing marks stop
        # first and then joins, without blocking the worker on _close_lock.
        with self._similarity_run_lock:
            if not current():
                raise ValueError('similarity analyzer is unavailable')
            owner = self
            class LazyInference:
                model_sha256 = EGARA_BB_SHA256
                def infer(self, data):
                    if not current():
                        raise ValueError('similarity operation cancelled')
                    if owner._egara_inference is None:
                        owner._egara_inference = EgaraInference(
                            owner._egara_model_path, provider=owner._egara_provider)
                    return owner._egara_inference.infer(data)
            writer = self.managed_downloads
            if writer is not None and writer.verified_saved_receipt(str(work_id), verify_pages=False) is not None:
                return self.similarity_index.analyze_saved_page(
                    writer, LazyInference(), str(work_id), page - 1, is_current=current)
            if self._similarity_legacy_inputs is not None:
                return self._similarity_legacy_inputs.analyze(
                    self.similarity_index, LazyInference(), str(work_id), page, is_current=current)
            raise ValueError('saved image unavailable for analysis')

    def _similarity_pages(self):
        if not self.similarity_analysis_enabled:
            return ()
        pages, managed = [], set()
        if self.managed_downloads is not None:
            current = self.managed_downloads.catalog_snapshot(verify_pages=False)
            managed = set(current.source_pages)
            pages.extend((wid, page + 1) for wid, numbers in current.source_pages.items() for page in numbers)
        legacy = self._similarity_legacy_inputs
        if legacy is None:
            return tuple(pages)
        # A mandatory Windows deny-write read handle protects the entire
        # finite scan; one cancellable admission SHA replaces N per-page
        # hashes without allowing same-size/mtime-preserving rewrites.
        def cancelled():
            worker = self._similarity_worker
            return (self._similarity_stop.is_set() or
                    (worker is not None and worker.status()['phase'] in
                     ('paused', 'failed', 'stopped')))
        def scanned_pages():
            with legacy.verified_scan(cancelled=cancelled):
                yield from pages
                yield from ((wid,page) for wid,page in legacy.pages()
                            if wid not in managed)
        return scanned_pages()

    def start_similarity_analysis(self):
        from .similarity_worker import SimilarityWorker
        with self._close_lock:
            if not self.similarity_analysis_enabled:
                raise ValueError('similarity analyzer unavailable')
            if self._similarity_worker is None:
                self._similarity_worker = SimilarityWorker(self._similarity_pages, self.analyze_saved_for_similarity)
                self._similarity_worker.start()
            else:
                self._similarity_worker.wake()

    def pause_similarity_analysis(self):
        if self._similarity_worker is not None:
            self._similarity_worker.pause()

    def resume_similarity_analysis(self):
        if self._similarity_worker is None:
            self.start_similarity_analysis()
        elif not self.closing and not self.closed:
            self._similarity_worker.resume()

    def similarity_analysis_status(self):
        status = (self._similarity_worker.status() if self._similarity_worker is not None else
                  dict(phase='idle', indexed=0, resumed=0, errors=0, examined=0))
        engine = self._egara_inference
        return dict(status, provider=engine.provider_active if engine is not None else None,
                    started=self._similarity_worker is not None,
                    requested=self._egara_provider,
                    fallback_reason=engine.fallback_reason if engine is not None else None)

    def configure_save_exports(self,root,account_id=None):
        from .media_jobs import validate_media_roots, validate_readonly_boundaries, MediaJobs
        from .save_exports import SaveExports
        from .author_media import AuthorMediaService
        if self.closing or self.managed_downloads is None or self.preferences is None:
            raise ValueError('managed storage and preferences required for save exports')
        validate_readonly_boundaries((root,),
            (self.library_image_root, self.legacy_diff_image_root,
             Path(self.library_readonly_db).parent if self.library_readonly_db else None,
             Path(self.failed_queue_readonly_db).parent if self.failed_queue_readonly_db else None))
        validate_media_roots(root,self.managed_downloads.root,self.novel_fixture_root,self.ugoira_root,
            self.preferences_root,self.discovery_root,self.external_vault_root,self.image_cache_root,
            self.legacy_zip_export_root,self.recommendation_preferences_root,self.following_sync_root)
        if self.save_exports is not None:raise ValueError('save exports already configured')
        if self.author_media is None:self.author_media=AuthorMediaService(self)
        self.author_media.enable_zip_settings()
        self.save_exports=SaveExports(self,root,account_id)
        owners=self.jobs.owners if isinstance(self.jobs,MediaJobs) else (self.jobs,)
        self.jobs=MediaJobs(*owners,self.save_exports.jobs)

    def enable_extension_commands(self):
        """Provision only the managed command ledger; binding is separate opt-in."""
        from .extension_application import ExtensionApplication
        from .media_jobs import MediaJobs
        with self._close_lock:
            if self.closing or self.managed_downloads is None:
                raise RuntimeError('extension requires managed storage')
            if self.extension_commands is None:
                self.extension_commands = ExtensionApplication(self, self.managed_downloads.root / 'extension-commands')
                owners = self.jobs.owners if isinstance(self.jobs, MediaJobs) else (self.jobs,)
                self.jobs = MediaJobs(*owners, self.extension_commands)
            return self.extension_commands

    def start_extension_bridge(self):
        from .extension_bridge import ExtensionBridge
        if self.extension_commands is None or self.closing:return
        if self.extension_bridge is None:
            self.extension_bridge = ExtensionBridge(enqueue=self.extension_commands.enqueue,
                work_status=self.extension_commands.work_status,author_status=self.extension_commands.author_status)
        try:
            self.extension_bridge.start()
            self.extension_bridge_status = 'running'
        except Exception:
            self.extension_bridge_status = 'unavailable'

    def _configure_request_preferences(self):
        from .request_policy import RequestAdmission, RequestRetryPolicy
        from .preferences import RequestPreferences
        admission = RequestAdmission(lambda: float(self.preferences.get_setting('download_interval', '0.6')))
        self._request_admission = admission
        self._request_retry = RequestRetryPolicy(lambda: RequestPreferences.from_store(self.preferences))
        readers = (self.live_client, getattr(self.novel_fixture_source, '_reader', None),
                   getattr(self.ugoira_source, 'reader', None))
        for reader in readers:
            configure = getattr(reader, 'set_request_admission', None)
            if callable(configure):
                configure(admission)
            retry = getattr(reader, "set_retry_policy", None)
            if callable(retry):
                retry(self._request_retry)

    def preference_consumers(self):
        enabled = set()
        if self.pixiv_actions_mode == 'preferences' and self.preferences is not None:
            enabled.add('pixiv_actions_enabled')
        if self.save_exports is not None:
            enabled.add("zip_all_after_download")
            # These are local choices. An unbound normal profile can save
            # them offline; bookmark projection waits for explicit verified
            # account binding after a complete bookmark refresh.
            enabled.update(('bookmark_download_mode','use_work_folder'))
        if self.novel_jobs is not None:enabled.add('novel_save_format')
        if self.ugoira is not None:enabled.add('ugoira_save_format')
        if any(callable(getattr(r,'set_request_admission',None)) for r in (
            self.live_client,getattr(self.novel_fixture_source,'_reader',None),
            getattr(self.ugoira_source,'reader',None),self._public_images)):
            enabled.add('download_interval')
        if any(callable(getattr(r, 'set_retry_policy', None)) for r in (
                self.live_client, getattr(self.novel_fixture_source, '_reader', None),
                getattr(self.ugoira_source, 'reader', None), self._public_images)):
            enabled.update(('api_retry_count', 'api_retry_wait'))
        if self.shared_image_cache is not None:
            enabled.add('pixiv_shared_image_cache_size_mb')
            if self.discovery is not None:
                enabled.update(('discovery_cache_limit_mode','discovery_cache_size_mb','discovery_cache_size_count'))
            if self.shared_image_cache.disk is not None:enabled.add('pixiv_disk_thumbnail_cache_size_gb')
        if self.notifications is not None and self.notifications.available:enabled.add('enable_notifications')
        if self.tray is not None and self.tray.available:enabled.add('minimize_to_tray')
        return enabled

    def apply_preferences(self, snapshot=None):
        if self.closing or self.preferences is None:
            return
        if self.shared_image_cache is not None:
            self.shared_image_cache.configure(int(self.preferences.get_setting('pixiv_shared_image_cache_size_mb','1024'))*1024**2)
            if self.shared_image_cache.disk is not None:
                self.shared_image_cache.disk.configure(int(self.preferences.get_setting('pixiv_disk_thumbnail_cache_size_gb','12'))*1024**3)

    def discovery_following_snapshot(self):
        """Read the latest local account snapshot without an identity/list GET."""
        if self.closing:
            raise RuntimeError('managed discovery unavailable')
        source = self._discovery_following_source
        if source is not None:
            value = source() if callable(source) else source
            return None if value is None else tuple(value)
        reader = self.live_client
        cached = self._following_snapshot
        missing = object()
        account = getattr(reader, '_verified_identity', missing)
        if account is missing:
            # Compatibility with explicit adapters lacking a cached-identity
            # field: only a context acquired from this same reader is usable.
            account = cached[1] if cached is not None and cached[0] is reader else None
        if type(account) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', account) is None:
            return None
        value = None
        if self.following_sync is not None:
            synced = self.following_sync.snapshot(account)
            if synced.last_synced_at is not None:
                # () is verified zero, including a sync committed by another
                # client. It must not revive the one-time legacy seed.
                value = tuple(dict(user_id=row.user_id, name=row.name) for row in synced.following)
        if value is None and cached is not None and cached[0] is reader and cached[1] == account:
            value = cached[2]
        if self.live_client is not reader or getattr(reader, '_verified_identity', account) != account:
            raise RuntimeError('discovery account changed while reading following snapshot')
        return value

    def start_discovery_initial_load(self):
        """Start once on the application loop, without delaying HTTP startup."""
        if self.closing or self.discovery is None or self._startup_discovery_task is not None:
            return
        async def initialize():
            try:
                await self.discovery.initialize_once(self.refresh_discovery_context,
                    is_current=lambda: not self.closing and not self._startup_discovery_stop.is_set())
            except Exception:
                # Discovery retains its typed failure/status for the existing UI.
                return
        self._startup_discovery_task = asyncio.create_task(initialize())

    async def drain_startup_discovery(self):
        self.closing = True
        self._startup_discovery_stop.set()
        if self._startup_discovery_task is not None:
            await asyncio.shield(self._startup_discovery_task)

    async def refresh_discovery_context(self):
        if self.closing or self.discovery is None:
            raise RuntimeError('managed discovery unavailable')
        if self._discovery_following_source is not None:
            return
        reader = self.live_client
        def checkpoint():
            if self.closing or self._startup_discovery_stop.is_set() or self.live_client is not reader:
                raise RuntimeError("discovery context stopped")
        def fetch_scoped():
            with request_scope(checkpoint=checkpoint):
                return fetch()
        def fetch():
            checkpoint()
            uid = reader.require_identity()
            checkpoint()
            if self.following_sync is not None:
                synced = self.following_sync.snapshot(uid)
                checkpoint()
                if synced.last_synced_at is not None:
                    return uid, tuple(dict(user_id=row.user_id, name=row.name) for row in synced.following)
            found = {}
            for rest in ('show', 'hide'):
                checkpoint()
                for row in reader.get_following_users(uid, rest):
                    found[row['user_id']] = dict(user_id=row['user_id'], name=row['name'])
            return uid, tuple(found.values())
        account, value = await asyncio.to_thread(fetch_scoped)
        checkpoint()
        if getattr(reader, '_verified_identity', account) != account:
            raise RuntimeError('discovery account changed while acquiring following snapshot')
        self._following_snapshot = (reader, account, value)

    def saved_discovery_ids(self):
        if self.closing:
            return frozenset()
        source = self._legacy_library_snapshot or self.library
        saved={work.work_id for work in source.works}
        if self.managed_downloads is not None:
            current = self.managed_downloads.catalog_snapshot()
            saved.difference_update(current.source_pages)
            saved.update(key for key, pages in current.source_pages.items() if pages)
        if self.ugoira is not None:
            saved.update(entry['spec']['work_id'] for entry in self.ugoira.library())
        return frozenset(saved)

    def saved_folder_choices(self):
        from .saved_folders import saved_folder_choices
        return saved_folder_choices(self)

    async def open_saved_folder(self, choice, *, is_current=None):
        from .saved_folders import open_saved_folder
        return await open_saved_folder(self, choice, is_current=is_current)

    async def open_duplicate_quarantine(self):
        """Open only the existing, revalidated owned quarantine directory."""
        source = self.duplicate_inspection
        desktop = self.desktop
        if self.closing or source is None or desktop is None:
            raise RuntimeError('quarantine location unavailable')

        def open_target():
            target = source.quarantine_location()
            if (target is None or self.closing
                    or self.duplicate_inspection is not source
                    or self.desktop is not desktop):
                raise RuntimeError('quarantine location unavailable')
            return desktop.open_saved(target)

        return await asyncio.to_thread(open_target)

    async def open_external_saved(self, work_id, page=1, *, catalog_token=None):
        if self.closing or self.desktop is None or type(page) is not int or page<1 or not catalog_token:
            raise RuntimeError('external viewer unavailable')
        def open_target():
            target = self.images.external_target(catalog_token, work_id, page-1)
            if self.closing:
                raise RuntimeError('application closing')
            return self.desktop.open_saved(target)
        return await asyncio.to_thread(open_target)

    async def save_viewer_page(self, work_id, page_no, page_count, author_id=None, *, is_current=None,
                               source_identity=None):
        """Explicit current-page save; never enqueue a complete-work job.

        Acceptance freezes this selection on the UI loop. Once accepted, the
        worker drains even if the view leaves; only its late UI result is dropped.
        Shutdown joins the I/O barrier before closing the owner/HTTP client.
        """
        from .managed_viewer_page import selection
        selection(work_id, page_no, page_count, author_id)
        if source_identity is not None:
            from .managed_downloads import ManagedPageIdentity
            if (not isinstance(source_identity, ManagedPageIdentity) or
                    (source_identity.work_id, source_identity.source_page_no,
                     source_identity.source_page_count, source_identity.author_id) !=
                    (work_id, page_no, page_count, author_id)):
                raise ValueError('pinned page identity does not match selected source page')
        if self.closing or self.managed_downloads is None or self.live_client is None:
            raise RuntimeError('managed downloads unavailable')
        if is_current is not None and not is_current():return None
        def save():
            with self._viewer_page_lock:
                if self.closing:raise RuntimeError('application closing')
                def record():
                    return self.live_client.get_artwork_download_record(work_id)
                saved=(self.managed_downloads.save_pinned_page(source_identity)
                       if source_identity is not None else
                       self.managed_downloads.save_viewer_page(
                           work_id, page_no, page_count, author_id, record))
                return self._export_viewer_saved(saved)
        accepted = asyncio.create_task(asyncio.to_thread(save))
        try:return await asyncio.shield(accepted)
        except asyncio.CancelledError:
            await accepted
            raise

    def _export_viewer_saved(self,saved):
        if self.save_exports is None:return saved
        from dataclasses import replace
        from .viewer_exports import publish
        def original():
            from urllib.parse import urlsplit
            urls=self.live_client.get_artwork_original_pages(saved.work_id)
            if len(urls)!=saved.source_page_count:raise ValueError('original page metadata changed')
            return urlsplit(urls[saved.page_no]).path.rsplit('/',1)[-1]
        try:
            name=publish(self.save_exports,saved,
                use_work_folder=self.preferences.get_setting('use_work_folder','0')=='1',original_loader=original)
            return replace(saved,export_manifest=name)
        except Exception:
            # Source proof is already durable. Expose the separate projection /
            # history failure; explicit retry reuses the proof, never the GET.
            return replace(saved,export_error=True)

    async def save_viewer_gif(self,display,*,is_current=None):
        from .viewer_gif import save_gif,DisplayedGif
        if type(display) is not DisplayedGif or self.closing or self.managed_downloads is None:
            raise RuntimeError('displayed GIF unavailable')
        if is_current is not None and not is_current():return None
        def save():
            with self._viewer_page_lock:
                if self.closing:raise RuntimeError('application closing')
                return self._export_viewer_saved(save_gif(self.managed_downloads,display))
        accepted=asyncio.create_task(asyncio.to_thread(save))
        try:return await asyncio.shield(accepted)
        except asyncio.CancelledError:
            await accepted
            raise

    async def recent_viewer_backups(self):
        if self.closing or self.save_exports is None:return (),()
        from .viewer_exports import recent
        return await asyncio.to_thread(recent,self.save_exports)

    async def open_viewer_backup(self,manifest):
        if self.closing or self.save_exports is None or self.desktop is None:
            raise RuntimeError('saved destination unavailable')
        from .viewer_exports import saved_folder
        def open_target():
            target=saved_folder(self.save_exports,manifest)
            if self.closing:raise RuntimeError('application closing')
            return self.desktop.open_saved(target)
        return await asyncio.to_thread(open_target)

    @property
    def legacy_backup_history_available(self):
        return (not self.closing and self.library_repository is not None
                and self.library_image_root is not None)

    def legacy_metadata_export_available(self) -> bool:
        """E01 is meaningful only with an explicit old DB and owned settings."""
        return (not self.closing and self.library_readonly_db is not None
                and self.preferences is not None and self.preferences_root is not None)

    def export_legacy_metadata(self):
        """Explicit E01 action, with output inside the dedicated settings root."""
        with self._legacy_export_lock:
            if self._legacy_export_stop.is_set() or not self.legacy_metadata_export_available():
                raise RuntimeError('旧DBのエクスポート元と専用設定先が必要です。')
            from .legacy_metadata_export import export_frozen_legacy_metadata
            return export_frozen_legacy_metadata(
                Path(self.library_readonly_db), Path(self.preferences_root) / 'exports',
                cancelled=self._legacy_export_stop.is_set)

    async def recent_legacy_backup_authors(self):
        """Read the original H01 author history without touching its DB or files.

        Own the matching image snapshot until the caller closes the returned
        pin; old tabs keep their own URLs through any later library refresh.
        """
        if not self.legacy_backup_history_available:
            raise RuntimeError('legacy backup history unavailable')
        async with self._library_refresh_lock:
            if not self.legacy_backup_history_available:
                raise RuntimeError('legacy backup history unavailable')
            snapshot = await asyncio.to_thread(self._read_legacy_source, 'library_snapshot')
            from .local_media_adapter import ReadonlyLocalMediaCatalog
            catalog = ReadonlyLocalMediaCatalog(self.library_image_root, snapshot)
            try:
                if self.closing:
                    raise RuntimeError('application closing')
                images = self.images.acquire_catalog(catalog)
            except BaseException:
                catalog.close()
                raise
        from .legacy_backup_history import legacy_backup_authors
        try:
            rows, missing = await asyncio.to_thread(
                legacy_backup_authors, snapshot, images, self.library_image_root, catalog)
            if self.closing:
                raise RuntimeError('application closing')
            return rows, missing, images
        except BaseException:
            images.close()
            raise

    async def open_legacy_backup_author(self, row, images):
        """Recheck the selected author's actual saved target before launching."""
        if not self.legacy_backup_history_available or self.desktop is None:
            raise RuntimeError('saved destination unavailable')
        from .legacy_backup_history import LegacyBackupAuthor, author_storage_target
        if not isinstance(row, LegacyBackupAuthor):
            raise ValueError('invalid backup selection')
        def open_target():
            source = images.external_target(row.work_id, row.page_no)
            target = author_storage_target(self.library_image_root, row.author_id, source)
            if target is None or target != row.target or self.closing:
                raise RuntimeError('backup selection changed')
            return self.desktop.open_saved(target)
        return await asyncio.to_thread(open_target)

    async def save_explicit_work(self, work_id, operation_id=None, *, is_current=None):
        """Dispatch one explicit artwork save, retaining durable operation identity.

        Discovery IDs share the illustration/ugoira namespace. Novel IDs are
        deliberately handled by the separate novel form. No image or ZIP is
        fetched until the selected owner accepts its job.
        """
        from uuid import uuid4
        from .pixiv_read_client import _identifier
        wid = _identifier(work_id)
        operation = operation_id or uuid4().hex
        if self.closing or self.live_client is None:
            raise RuntimeError('managed downloads unavailable')
        if type(operation) is not str or not 1 <= len(operation) <= 128 or any(ord(c)<32 for c in operation):
            raise ValueError('invalid operation ID')
        def current():
            return not self.closing and (is_current is None or is_current())
        # Repeated acceptance (including after restart) returns the original job
        # before a changed default format or receipt predecessor can alter it.
        def replay():
            for owner, animated in ((self.static_jobs,False),(self.ugoira.jobs if self.ugoira else None,True)):
                if owner is None:continue
                for job in owner.snapshot():
                    if operation in owner.operation_ids(job.job_id):
                        saved_id = self.ugoira._spec(job.target_id)['work_id'] if animated else job.target_id
                        if saved_id != wid:raise ValueError('operation belongs to another work')
                        return job
            return None
        existing = await asyncio.to_thread(replay)
        if existing is not None:return existing
        if not current():return None
        if self.ugoira is not None:
            info = await asyncio.to_thread(self.live_client.get_discovery_work,wid)
            if not current():return None
            if str(info.get('id')) != wid:raise ValueError('work identity changed')
            kind=info.get('illust_type')
            if type(kind) is not int or kind not in (0,1,2):raise ValueError('unknown artwork type')
            if kind == 2:
                output=self.preferences.get_setting('ugoira_save_format','gif') if self.preferences else 'gif'
                output='frames' if output=='folder' else output
                selection=await asyncio.to_thread(self.ugoira.preview,wid,_identifier(info.get('user_id')),output)
                if not current():return None
                accepted=asyncio.create_task(asyncio.to_thread(self.enqueue_ugoira_selection,selection,operation))
                try:return await asyncio.shield(accepted)
                except asyncio.CancelledError:
                    await accepted
                    raise
        owner = uuid4().hex
        try:
            record = await self.lookup_managed_work_id(wid, owner=owner, publish=False)
            if not current():return None
            self.remember_managed_work_id(owner, record)
            accepted = asyncio.create_task(asyncio.to_thread(
                self.enqueue_managed_download, record, operation, owner=owner))
            try:
                return await asyncio.shield(accepted)
            except asyncio.CancelledError:
                await accepted
                raise
        finally:
            self.release_managed_owner(owner)

    def author_media_preview(self, author_id, target_type=None, *, novel_format=None, ugoira_format=None, new_only=False):
        if self.closing or self.author_media is None:
            raise RuntimeError('all-media author download unavailable')
        novel = self.preferences.get_setting('novel_save_format', 'epub') if self.preferences else 'epub'
        ugoira = self.preferences.get_setting('ugoira_save_format', 'gif') if self.preferences else 'gif'
        target_type = self.author_media.resolve_author_target(author_id) if target_type is None else target_type
        return self.author_media.preview(author_id, target_type,
            novel_format=novel_format or novel.upper(),
            ugoira_format=ugoira_format or ('frames' if ugoira=='folder' else ugoira), new_only=new_only)

    def enqueue_author_media(self, candidate, operation_id, *, origin='follow'):
        if self.closing or self.author_media is None:
            raise RuntimeError('all-media author download unavailable')
        with self._media_admission_lock:
            return self.author_media.enqueue(candidate, operation_id, origin=origin)

    def enqueue_novel_export(self, intent, operation_id):
        """Explicit fixture-only M3 acceptance; caller performs local I/O off-loop."""
        if self.closing or self.novel_jobs is None:
            raise RuntimeError("managed novel fixture mode is unavailable")
        with self._media_admission_lock:
            self._assert_media_capacity(self.novel_jobs.jobs, operation_id, 'novel-export', intent.target)
            return self.novel_jobs.enqueue(intent, operation_id)

    MAX_ACTIVE_MEDIA_JOBS = 50

    def _media_capacity_available(self, kind, target):
        from .job_service import UNFINISHED
        from .media_jobs import MediaJobs
        if not isinstance(self.jobs, MediaJobs):
            return True
        snapshot = self.jobs.snapshot()
        return (any(job.kind == kind and job.target_id == target and job.state in UNFINISHED
                    for job in snapshot)
                or sum(job.state in UNFINISHED for job in snapshot) < self.MAX_ACTIVE_MEDIA_JOBS)

    def _assert_media_capacity(self, owner, operation_id, kind, target):
        for job in owner.snapshot():
            if operation_id in owner.operation_ids(job.job_id):
                return
        if not self._media_capacity_available(kind, target):
            from .legacy_retry_errors import DownloadQueueCapacityReached
            raise DownloadQueueCapacityReached('download queue capacity reached')

    def enqueue_ugoira_selection(self, selection, operation_id):
        if self.closing or self.ugoira is None:
            raise RuntimeError('ugoira download unavailable')
        from .managed_ugoira import _json
        from .ugoira_practical import digest
        with self._media_admission_lock:
            self._assert_media_capacity(self.ugoira.jobs, operation_id,
                'ugoira-'+selection.spec['output'], digest(_json(selection.spec)))
            return self.ugoira.enqueue_selection(selection, operation_id)

    def personal_ranking_context(self):
        context = {'downloaded_ids': self.saved_discovery_ids()}
        if self.recommendations is not None:
            from .recommendation_state import RecommendationError
            try:
                snapshot = self.recommendations.refresh()
            except RecommendationError:
                # An unavailable read-only projection does not authorize empty
                # exclusion sets. The personal UI disables these filters.
                pass
            else:
                context.update(hidden_work_ids=set(snapshot['hidden_works']),
                               hidden_author_ids=set(snapshot['hidden_authors']))
        if self.discovery is not None:
            snapshot = self.discovery.recommendation_snapshot()
            if 'hated_ids' in snapshot:context['hated_ids']=snapshot['hated_ids']
            if 'author_seed_bookmarked' in snapshot:context['author_counts']=snapshot['author_seed_bookmarked']
            context['trend_rows']=self.discovery.trend_rows()
        elif self.recommendations is not None:
            source=self.recommendations.source
            provider=getattr(source,'trend_rows',None)
            if callable(provider):
                from .recommendation_state import RecommendationError
                try:context['trend_rows']=provider()
                except RecommendationError:pass
        return context

    def current_novel_export(self, work_id, output_format="TXT"):
        if self.closing or self.novel_jobs is None:
            raise RuntimeError("managed novel fixture mode is unavailable")
        return self.novel_jobs.current(work_id, output_format)

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

    def forget_accepted_managed_author_batch(self, owner, work_ids):
        """Unpin accepted author items so a single view can stage the next 128.

        Only the already accepted IDs are released. Frozen job intents remain
        durable in managed SQLite and no other client's authorization changes.
        """
        token = self._direct_owner(owner)
        if type(work_ids) not in (tuple, list):
            raise ValueError('invalid accepted author batch')
        with self._managed_target_lock:
            records = self._direct_download_candidates.get(token)
            pins = set(self._author_owned_pins.get(token, frozenset()))
            if records is None:
                return
            for work_id in work_ids:
                if work_id in pins:
                    records.pop(work_id, None)
                    pins.discard(work_id)
            if pins:
                self._author_owned_pins[token] = frozenset(pins)
            else:
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

    def pending_managed_author_work_ids(self):
        """Return accepted unfinished IDs without claiming their files are saved."""
        if self.jobs is None or self.closing:
            return frozenset()
        from .job_service import UNFINISHED
        return frozenset(job.target_id for job in self.jobs.snapshot()
                         if job.kind == 'pixiv-illust' and job.state in UNFINISHED)

    async def lookup_managed_author(self, user_id: str, *, owner: str,
                                    publish=False, saved_snapshot=None):
        """Preview verified author works and an optional frozen legacy snapshot.

        Existing v1 receipts have no update_date; they remain UNKNOWN rather
        than being silently treated as current or forcibly overwritten. The
        legacy SQLite and images are never written; deletion observations are
        durable only in a separate managed-root ledger after a complete GET.
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
            bridge = getattr(self, 'legacy_author_bridge', None)
            complete = True
            if bridge is not None:
                full_reader = getattr(self.live_client, 'get_author_artworks_complete', None)
                if not callable(full_reader):
                    raise RuntimeError('full author inventory proof is unavailable')
                from .pixiv_read_client import InvalidPayload
                try:
                    works = full_reader(uid)
                except InvalidPayload:
                    # A masked work or a partially visible bucket must NEVER
                    # advance remote deletion streaks. Retain the preexisting
                    # managed-only preview, with legacy-only works UNKNOWN.
                    works = self.live_client.get_author_artworks(uid)
                    complete = False
                if complete:
                    # Merely opening a preview does not increment old missing
                    # streaks. Only an explicit complete-crawl action may do so.
                    legacy_scan = bridge.observe_author(uid, works)
                else:
                    legacy_scan = None
            else:
                works = self.live_client.get_author_artworks(uid)
                legacy_scan = None
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
                legacy = legacy_scan.saved.get(work['id']) if legacy_scan is not None else None
                difference = decide_difference(
                    saved=prior is not None or legacy is not None,
                    remote_update_date=work['update_date'],
                    saved_update_date=(prior[1] if prior is not None else
                                       legacy.update_date if legacy is not None else None),
                    deleted=legacy.deleted if legacy is not None and prior is None else False,
                    files_present=(True if prior is not None else
                                   legacy.files_present if legacy is not None else None))
                if bridge is not None and not complete and prior is None:
                    difference = Difference.UNKNOWN
                if bridge is None and self.library_repository is not None and prior is None:
                    # Without legacy comparison data, deleted old rows cannot
                    # be distinguished from new work. Explicit saves remain.
                    difference = Difference.UNKNOWN
                output.append(({**work, 'expected_receipt_sha256': prior[0] if prior else None,
                                'expected_relative_receipt': prior[2] if prior else None,
                                'legacy_failed': bool(legacy is not None and legacy.failed),
                                'legacy_diff_source': bool(legacy is not None and prior is None)},
                               difference))
            return tuple(output), legacy_scan, complete

        items, scan, complete = await asyncio.to_thread(fetch)
        if self.closing:
            raise RuntimeError('managed downloads unavailable')
        from .author_download import AuthorPreview
        return AuthorPreview(items,
            remote_missing_pending=scan.remote_missing_pending if scan is not None else 0,
            remote_missing_confirmed=scan.remote_missing_confirmed if scan is not None else 0,
            remote_absent=getattr(scan, 'remote_absent', ()) if scan is not None else (),
            inventory_complete=complete)

    async def confirm_legacy_author_inventory(self, user_id: str, *, owner: str,
                                              scan_id: str):
        """Record one explicitly requested complete GET in the managed ledger."""
        from .pixiv_read_client import _identifier
        self._direct_owner(owner)
        uid = _identifier(user_id)
        if type(scan_id) is not str or re.fullmatch(r'[a-f0-9]{32}', scan_id) is None:
            raise ValueError('invalid author inventory operation')
        bridge = getattr(self, 'legacy_author_bridge', None)
        if self.closing or bridge is None or self.live_client is None:
            raise RuntimeError('legacy diff observation is not configured')

        def confirm():
            with self._legacy_inventory_lock:
                if self.closing:
                    raise RuntimeError('managed downloads unavailable')
                full_reader = getattr(self.live_client, 'get_author_artworks_complete', None)
                if not callable(full_reader):
                    raise RuntimeError('complete author inventory proof unavailable')
                self.live_client.verify_identity()
                works = full_reader(uid)
                if self.closing:
                    raise RuntimeError('managed downloads unavailable')
                return bridge.observe_author(uid, works, scan_id=scan_id)

        result = await asyncio.to_thread(confirm)
        if self.closing:
            raise RuntimeError('managed downloads unavailable')
        return result

    async def followed_download_authors(self, *, owner: str):
        """Use a completed account-scoped sync, or explicitly GET an unsynced list."""
        from .author_download import AuthorTarget
        self._direct_owner(owner)
        if self.closing or self.managed_downloads is None or self.live_client is None:
            raise RuntimeError('managed downloads unavailable')
        reader = self.live_client

        def checkpoint(account=None):
            if self.closing or self.live_client is not reader:
                raise RuntimeError('managed author source changed')
            if account is not None and getattr(reader, '_verified_identity', account) != account:
                raise RuntimeError('managed author account changed')

        def fetch():
            checkpoint()
            uid = reader.verify_identity()
            checkpoint(uid)
            if self.following_sync is not None:
                synced = self.following_sync.snapshot(uid)
                checkpoint(uid)
                if getattr(synced, 'account_id', uid) != uid:
                    raise RuntimeError('managed author snapshot account mismatch')
                if synced.last_synced_at is not None:
                    return uid, tuple(AuthorTarget(row.user_id, row.name, 'follow',
                                      is_favorite=row.is_favorite, follow_order=row.follow_order)
                                      for row in synced.following)
            # Favorites are local application choices, not inferred from a
            # public Pixiv following response. With no configured local ledger
            # retain None (unknown), rather than falsely reporting known zero.
            scheduler = getattr(self, 'managed_scheduler', None)
            favourite_ids = None if scheduler is None else frozenset(scheduler.favourites())
            seen = {}
            for visibility in ('show', 'hide'):
                checkpoint(uid)
                for entry in reader.get_following_users(uid, visibility):
                    checkpoint(uid)
                    if entry['user_id'] not in seen:
                        seen[entry['user_id']] = AuthorTarget(
                            entry['user_id'], entry['name'], 'follow',
                            is_favorite=(None if favourite_ids is None else entry['user_id'] in favourite_ids),
                            follow_order=len(seen))
            return uid, tuple(seen.values())

        account, result = await asyncio.to_thread(fetch)
        checkpoint(account)
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
            if self._similarity_worker is not None and not self.closing:
                self._similarity_worker.wake()

    def _reconcile_interrupted_author_jobs(self):
        """Recover v2 jobs after active-pointer commit but before SQLite done.

        The only positive evidence is the fully verified active receipt AND a
        job-specific accepted fingerprint. A valid but not yet published v2
        save remains retryable. Legacy v1 job recovery is left unchanged.
        """
        from .managed_download_requests import (ManagedAuthorIntent,
                                                accepted_author_fingerprint)
        interrupted = [(job, self._managed_job_records.get(job.job_id))
                       for job in self.static_jobs.snapshot()
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
                self.static_jobs.reconcile_interrupted(
                    job.job_id, receipt=match[0], page_count=match[1]['page_count'])
            else:
                self.static_jobs.reconcile_interrupted(job.job_id)

    def managed_job_author_names(self, jobs):
        """UI-only metadata from accepted immutable intents; no I/O or GET.

        Pin the published mapping reference; each DTO is immutable. A job
        published just before its accepted metadata is installed is retried
        by the existing UI refresh, rather than waiting for the enqueue lock.
        Restart reconstructs the same records from the accepted ledger.
        """
        from .managed_download_requests import ManagedAuthorIntent
        from .bookmark_remote_adapter import RemoteBookmark
        records = self._managed_job_records
        names = {}
        for job in jobs:
            accepted = records.get(job.job_id)
            record = accepted.record if isinstance(accepted, ManagedAuthorIntent) else accepted
            if (isinstance(record, RemoteBookmark) and job.kind == 'pixiv-illust'
                    and record.work_id == job.target_id):
                names[job.job_id] = record.author_name
        return names

    def enqueue_managed_download(self, record, operation_id, *, owner=None, return_created=False):
        selection = record.record if hasattr(record, 'record') else record
        if self.managed_downloads is None or not isinstance(getattr(selection, 'work_id', None), str):
            raise ValueError('invalid managed selection')
        with self._media_admission_lock, self.managed_downloads.work_lock(selection.work_id):
            self._assert_media_capacity(self.static_jobs, operation_id, 'pixiv-illust', selection.work_id)
            return self._enqueue_managed_download_locked(
                record, operation_id, owner=owner, return_created=return_created)

    def _enqueue_managed_download_locked(self, record, operation_id, *, owner=None, return_created=False):
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
            if self.closing or self.managed_downloads is None or self.static_jobs is None:
                raise RuntimeError("managed downloads unavailable")
            if self.legacy_author_bridge is not None:
                # A source DB replacement between preview and acceptance must
                # never authorize a job under a stale tombstone/ZIP verdict.
                self.legacy_author_bridge.assert_source_unchanged()
            with self._managed_target_lock:
                if selection not in self.download_candidates(owner=owner):
                    raise ValueError("download selection changed; refresh the candidates")
                existing = next((job for job in self.static_jobs.snapshot()
                                 if job.kind == "pixiv-illust" and job.target_id == selection.work_id
                                 and job.state in UNFINISHED), None)
                previous = self._managed_job_records.get(existing.job_id) if existing is not None else None
                if (existing is not None and previous is not None and
                        not self._managed_requests.equivalent(previous, record)):
                    raise ValueError("an earlier selection is still being processed")
            # A durable intent alone is not an accepted job. Only the committed
            # operations ledger above authorizes recovery of this exact input.
            self._managed_requests.store(operation_id, record)
            accepted = self.static_jobs.enqueue("pixiv-illust", selection.work_id,
                                         title=selection.title, operation_id=operation_id)
            self._managed_job_records.setdefault(accepted.job_id, record)
            live_ids = {job.job_id for job in self.static_jobs.snapshot()}
            self._managed_job_records = {key: value for key, value in self._managed_job_records.items()
                                         if key in live_ids}
            return (accepted, existing is None) if return_created else accepted

    def configure_legacy_capacity(self, reader, cache):
        """Attach explicit capacity dependencies; caller owns their lifecycle."""
        if self.closing:raise RuntimeError('application services are closed')
        from .legacy_capacity import LegacyCapacityService
        self.legacy_capacity=LegacyCapacityService(reader,cache)

    def library_snapshot(self) -> LibrarySnapshot:
        """Return the last successful immutable snapshot; never read SQLite on the UI loop."""
        if self.closing:
            raise RuntimeError("application services are closed")
        return self.library

    def _validate_library_ids(self, snapshot):
        demo_ids = {art.work_id for art in self.artworks}
        if any(work.work_id in demo_ids for work in snapshot.works):
            raise ValueError("library IDs collide with synthetic demo IDs")

    def _assert_legacy_library_source(self):
        if self.library_readonly_db is None:
            return
        current = _frozen_library_stat(self.library_readonly_db)
        if self._legacy_initial_stat is not None and current != self._legacy_initial_stat:
            raise ValueError('frozen legacy source changed; select a new snapshot')
        if self._legacy_copy_signature is not None:
            from .failed_records_repository import immutable_copy_signature
            if immutable_copy_signature(self.library_readonly_db) != self._legacy_copy_signature:
                raise ValueError('frozen legacy source changed; select a new snapshot')

    def _read_legacy_source(self, method):
        if self.library_repository is None:
            raise RuntimeError('legacy library unavailable')
        self._assert_legacy_library_source()
        result = getattr(self.library_repository, method)()
        self._assert_legacy_library_source()
        if method == 'library_snapshot':
            self._validate_library_ids(result)
        return result

    def _publish_managed_library(self, snapshot, catalog, local, *,
                                 legacy_snapshot, legacy_local, records,
                                 reader=_CURRENT_IMAGE_READER):
        """Publish one matching source selection, DTO snapshot and image owner."""
        provider = catalog
        legacy_catalog = remote = None
        composition = None
        try:
            receipts = catalog.receipts
            managed_version = catalog.snapshot
            visible_snapshot = snapshot
            visible_local = tuple(local)
            if legacy_snapshot is not None:
                from .composite_library import compose_library, CompositeLocalCatalog
                from .local_media_adapter import ReadonlyLocalMediaCatalog
                composition = compose_library(
                    legacy_snapshot, snapshot, legacy_local, local,
                    managed_source_pages=catalog.source_pages,
                    managed_original_page_counts={receipt['work_id']: receipt['page_count']
                                                  for _, receipt, _ in receipts})
                legacy_catalog = ReadonlyLocalMediaCatalog(self.library_image_root, legacy_snapshot)
                provider = CompositeLocalCatalog(composition,
                    legacy_catalog=legacy_catalog, managed_catalog=catalog)
                visible_snapshot = composition.snapshot
                visible_local = composition.local_bookmarks
            remote = self._remote_images(records, reader=reader)
            self.images.publish(provider, remote)
        except BaseException:
            try:
                provider.close()
            finally:
                if legacy_catalog is not None and provider is catalog:
                    legacy_catalog.close()
                if remote is not None:
                    remote.close()
            raise
        self.library = visible_snapshot
        self._library_composition = composition
        self._legacy_library_snapshot = legacy_snapshot
        self._legacy_local_bookmarks = tuple(legacy_local)
        self._managed_library_snapshot = snapshot
        self._managed_local_bookmarks = tuple(local)
        self._managed_receipts = receipts
        self._managed_catalog_snapshot = managed_version
        self._cached_local_bookmarks = visible_local

    def _remote_images(self, records, *, reader=_CURRENT_IMAGE_READER):
        if reader is _CURRENT_IMAGE_READER:
            reader = self.live_client
        if reader is None or not records:
            return None
        from .remote_media import RemoteBookmarkImages
        return RemoteBookmarkImages(reader, tuple(records))

    def _publish_images(self, snapshot, records, *, reader=_CURRENT_IMAGE_READER) -> None:
        from .local_media_adapter import ReadonlyLocalMediaCatalog
        if self.managed_downloads is not None:
            # The immutable publication cache was prepared off-loop. Reusing
            # its receipt data must not rescan files in this UI-thread method.
            from .managed_download_catalog import ManagedDownloadCatalog
            local = ManagedDownloadCatalog(self.managed_downloads, self._managed_receipts,
                                           snapshot=self._managed_catalog_snapshot)
            self._publish_managed_library(self._managed_library_snapshot, local,
                self._managed_local_bookmarks, legacy_snapshot=self._legacy_library_snapshot,
                legacy_local=self._legacy_local_bookmarks, records=records, reader=reader)
            return
        else:
            local = (ReadonlyLocalMediaCatalog(self.library_image_root, snapshot)
                     if self.library_image_root is not None else None)
        remote = self._remote_images(records, reader=reader)
        try:
            self.images.publish(local, remote)
        except BaseException:
            if local is not None:
                local.close()
            if remote is not None:
                remote.close()
            raise

    def acquire_ranking_images(self, records):
        if self.closing or (self.live_client is None and self._vault_images is None):
            raise RuntimeError("ranking image source unavailable")
        from .remote_media import RemoteBookmarkImages
        remote = RemoteBookmarkImages(self.live_client or self._vault_images, tuple(records))
        try:
            return self.images.acquire_catalog(remote)
        except BaseException:
            remote.close()
            raise

    def acquire_recommendation_images(self, records):
        """Keep local adoption/exclusion checks on every issued remote URL."""
        if self.closing or (self.live_client is None and self._vault_images is None):
            raise RuntimeError("recommendation image source unavailable")
        from .remote_media import RemoteBookmarkImages
        from .recommendation_media import RecommendationRemoteImages
        remote = RecommendationRemoteImages(
            RemoteBookmarkImages(self.live_client or self._vault_images, tuple(records)),
            self.images)
        try:
            return self.images.acquire_catalog(remote)
        except BaseException:
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

    @property
    def failed_record_removal_available(self):
        return (not self.closing and self.failed_records_available and
                (self.preferences is not None or self.managed_downloads is not None))

    def _failure_actions(self):
        with self._failure_actions_lock:
            if not self.failed_record_removal_available:
                raise ValueError('failure list local removal unavailable')
            if self._failure_record_actions is None:
                from .failure_record_actions import FailureRecordActions
                parent = (Path(self.preferences_root) if self.preferences is not None
                          else self.managed_downloads.root)
                self._failure_record_actions = FailureRecordActions(self, parent/'failure-dismissals')
            return self._failure_record_actions

    async def read_failed_records_view(self):
        """Visible snapshot is separate from the immutable raw retry source."""
        actions = self._failure_actions()
        result = await asyncio.to_thread(actions.snapshot)
        if self.closing:
            raise RuntimeError('application services are closed')
        return result

    def dismiss_failed_records(self, snapshot, selected):
        return self._failure_actions().dismiss(snapshot, selected)

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
        # This service's legacy source was admitted as a standalone immutable
        # copy. Do not reopen it in mode=ro, which can create a -shm sidecar
        # for old WAL-header databases even when every SQL statement is SELECT.
        records = await asyncio.to_thread(
            read_failed_records, self.library_readonly_db, immutable=True)
        if self.closing:
            raise RuntimeError("application services are closed")
        return records

    def _check_immutable_failure_source(self) -> None:
        from .failed_records_repository import immutable_copy_signature
        from .legacy_retry_errors import LegacyRetrySourceChanged
        if (self._failed_copy_signature is None or
                immutable_copy_signature(self.failed_queue_readonly_db) != self._failed_copy_signature):
            raise LegacyRetrySourceChanged('legacy failure copy changed; stop and prepare a new snapshot')

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
        from .legacy_retry_errors import LegacyRetrySourceChanged
        if not self.legacy_failed_retry_available or not self._supported_failed_original(failure):
            raise ValueError('legacy failure is not supported by managed downloads')
        from .legacy_retry_batch import CURRENT_RETRY_BATCH
        batch = CURRENT_RETRY_BATCH.get()
        if batch is not None:
            batch.check_row(self, failure)
            return
        rows = self._read_immutable_failure_snapshot()
        if not any(item == failure for item in rows):
            raise LegacyRetrySourceChanged('legacy failure selection changed; refresh the list')

    def prepare_legacy_retry_batch(self, rows, *, owner, is_current=None):
        from .legacy_retry_batch import LegacyRetryBatch
        self._direct_owner(owner)
        return LegacyRetryBatch.prepare(self, rows, owner, is_current=is_current)

    async def wait_legacy_retry_capacity(self, failure, *, is_current, deadline=None):
        """Wait before the metadata GET; never bypass the atomic 50-job gate."""
        from .legacy_retry_batch import RETRY_CAPACITY_WAIT_SECONDS
        from .legacy_retry_errors import LegacyRetryCancelled, DownloadQueueCapacityReached
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + RETRY_CAPACITY_WAIT_SECONDS
        while True:
            if (not is_current() or not self.legacy_failed_retry_available
                    or getattr(self.jobs, 'faulted', False)):
                raise LegacyRetryCancelled('legacy retry cancelled or unavailable')
            if self._media_capacity_available('pixiv-illust', failure.work_id):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise DownloadQueueCapacityReached('download queue capacity reached')
            await asyncio.sleep(0.2)

    async def lookup_legacy_failed_original(self, failure, *, owner):
        from .legacy_retry_batch import legacy_retry_lease
        with legacy_retry_lease(self, owner):
            return await self._lookup_legacy_failed_original(failure, owner=owner)

    async def _lookup_legacy_failed_original(self, failure, *, owner):
        """No mutation: verify source row and remote work ID off the UI loop."""
        self._direct_owner(owner)
        await asyncio.to_thread(self._assert_legacy_retry_row, failure)
        from .legacy_retry_errors import LegacyRetryRowUnavailable
        from .pixiv_read_client import ArtworkUnavailable
        try:
            record = await self.lookup_managed_work_id(failure.work_id, owner=owner, publish=False)
        except ArtworkUnavailable:
            raise LegacyRetryRowUnavailable('artwork cannot be saved') from None
        except ValueError as error:
            if str(error) == 'artwork cannot be saved':
                raise LegacyRetryRowUnavailable('artwork cannot be saved') from None
            raise
        if (not self._supported_failed_original(failure) or record.author_id != failure.user_id
                or record.illustration_type != (1 if failure.work_type == 'manga' else 0)):
            raise LegacyRetryRowUnavailable('legacy failure artwork identity changed')
        return record

    def enqueue_legacy_failed_original(self, failure, record, operation_id, *, owner):
        from .legacy_retry_batch import legacy_retry_lease
        with legacy_retry_lease(self, owner):
            return self._enqueue_legacy_failed_original(failure, record, operation_id, owner=owner)

    def _enqueue_legacy_failed_original(self, failure, record, operation_id, *, owner):
        """Explicitly accept into new SQLite jobs; the old failed_queue stays intact."""
        from .bookmark_remote_adapter import RemoteBookmark
        self._direct_owner(owner)
        self._assert_legacy_retry_row(failure)
        from .legacy_retry_errors import LegacyRetryRowUnavailable
        if (not isinstance(record, RemoteBookmark) or record.remote_id != failure.work_id
                or record.author_id != failure.user_id
                or record.illustration_type != (1 if failure.work_type == 'manga' else 0)):
            raise LegacyRetryRowUnavailable('legacy failure artwork identity changed')
        # Author-batch pins protect UI preview selections until accepted. A
        # legacy failed-list retry, however, has no multi-item preview: its
        # frozen DTO lives in the accepted SQLite intent immediately after
        # enqueue. Drop this *view-exclusive* temporary owner on both success
        # and failure; otherwise 3 filtered batches can strand >128 pins.
        try:
            (authorized,) = self.remember_managed_author_batch(owner, (record,))
            return self.enqueue_managed_download(authorized, operation_id, owner=owner)
        except ValueError as error:
            if str(error) == 'an earlier selection is still being processed':
                raise LegacyRetryRowUnavailable('earlier artwork selection still being processed') from None
            raise
        finally:
            self.release_managed_owner(owner)

    @property
    def data_mode(self):
        from .managed_novel_jobs import PinnedNovelSource
        if type(self.novel_fixture_source) is PinnedNovelSource:
            return "pixiv-readonly"
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
                legacy_snapshot = self._legacy_library_snapshot
                if self.library_repository is not None:
                    legacy_snapshot = await asyncio.to_thread(self._read_legacy_source, 'library_snapshot')
                snapshot, catalog, local = await asyncio.to_thread(build_managed_library, self.managed_downloads)
                if self.closing:
                    catalog.close()
                    raise RuntimeError("application services are closed")
                self._publish_managed_library(snapshot, catalog, local,
                    legacy_snapshot=legacy_snapshot, legacy_local=self._legacy_local_bookmarks,
                    records=self._remote_records)
                return self.library
            snapshot = await asyncio.to_thread(self._read_legacy_source, 'library_snapshot')
            if self.closing:
                raise RuntimeError("application services are closed")
            self._validate_library_ids(snapshot)
            self._publish_images(snapshot, self._remote_records)
            self.library = snapshot
            self._legacy_library_snapshot = snapshot
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
        """Publish complete bookmark routes and their verified export account."""
        if self.library_repository is None and self.live_client is None:
            return self.bookmark_remote_snapshot(), self.bookmark_local_snapshot()
        async with self._library_refresh_lock:
            if self.closing:
                raise RuntimeError("application services are closed")
            reader = self.live_client
            exports = self.save_exports
            account = None

            def check_current_read():
                if self.closing or self.live_client is not reader:
                    raise RuntimeError('bookmark reader changed')
                if reader is None:
                    return
                if getattr(reader, '_closed', False):
                    raise RuntimeError('bookmark reader closed')
                if exports is not self.save_exports:
                    raise RuntimeError('bookmark export owner changed')
                if exports is not None:
                    # Real LivePixivReadClient always has this cache scalar.
                    # Injected read clients may express the same verified
                    # contract only through verify_identity's return value.
                    if (type(account) is not str or
                            re.fullmatch(r'[1-9][0-9]{0,19}', account) is None or
                            getattr(reader, '_verified_identity', account) != account):
                        raise RuntimeError('bookmark account verification changed')
                    if exports.account not in (None, account):
                        raise RuntimeError('bookmark export account differs')

            if reader is not None:
                from .bookmark_remote_adapter import fetch_all_bookmarks

                def read_all():
                    identity = reader.verify_identity()
                    result = fetch_all_bookmarks(reader, identity)
                    if not result.ok:
                        raise RuntimeError("bookmark routes incomplete")
                    return result.records, identity

                records, account = await asyncio.to_thread(read_all)
                check_current_read()
                remote = tuple(record.as_artwork() for record in records)
                local = self._cached_local_bookmarks
                if self.library_repository is not None:
                    local = await asyncio.to_thread(self._read_legacy_source, 'local_bookmark_snapshot')
            else:
                records, local = await asyncio.to_thread(self._read_legacy_source, 'bookmark_bundle')
                remote = None if records is None else tuple(record.as_artwork() for record in records)
            check_current_read()
            if remote is None:
                raise RuntimeError("bookmark cache unavailable")

            prepared = None
            catalog = None
            adopted = False
            if self.managed_downloads is not None:
                from .managed_download_catalog import build_managed_library
                prepared = await asyncio.to_thread(build_managed_library, self.managed_downloads)
                catalog = prepared[1]
            try:
                check_current_read()
                from contextlib import nullcontext
                # The final guarded entry is the admission point for this
                # complete read. No network or await runs under this fence.
                # Later independent authentication/close cannot retrospectively
                # reject a registry version that was already published/pruned.
                # Publication failure still leaves the account unbound.
                binding = (exports.verified_account_binding(account)
                           if exports is not None and reader is not None
                           else nullcontext())
                with binding:
                    check_current_read()
                    if prepared is not None:
                        managed_snapshot, catalog, managed_local = prepared
                        self._publish_managed_library(managed_snapshot, catalog, managed_local,
                            legacy_snapshot=self._legacy_library_snapshot,
                            legacy_local=local if self.library_repository is not None else (),
                            records=records or (), reader=reader)
                        adopted = True
                        local = self._cached_local_bookmarks
                    else:
                        self._publish_images(self.library, records or (), reader=reader)
                        if self.library_repository is not None:
                            self._legacy_local_bookmarks = tuple(local)
                    self._cached_remote_bookmarks = remote
                    self._cached_local_bookmarks = local
                    with self._managed_target_lock:
                        self._remote_records = records or ()
                    self.bookmark_cache_failed = False
            except BaseException:
                if catalog is not None and not adopted:
                    catalog.close()
                raise
            return remote, local

    def close(self) -> None:
        with self._close_lock:
            if not self.closed:
                # The same lifecycle lock guards enable-vs-close. Once close
                # marks `closing`, no new runner can be published.
                with self._managed_scheduler_lock:
                    self.closing = True
                # Publish every cooperative cancellation signal before
                # joining any one worker. If CUDA/model shutdown times out,
                # leave owned resources open, but do not strand the unrelated
                # scheduler, discovery, export or metadata workers running.
                self._similarity_stop.set()
                self._startup_discovery_stop.set()
                self._legacy_export_stop.set()
                self._scheduler_cancel_event.set()
                self._metadata_cancel_event.set()
                if self._similarity_worker is not None:
                    self._similarity_worker.stop(timeout=150.0)
                # A synchronous close waits for action workers (including
                # local follow commits) without depending on the UI loop.
                self._close_pixiv_actions()
                if self.managed_schedule_runner is not None:
                    # No provider/SQLite resource may close while its GET
                    # dispatch worker still uses it. A bounded network read
                    # may legally outlive the runner's short stop timeout.
                    self.managed_schedule_runner.stop(timeout=150.0)
                # Manual dispatch and the timer share this barrier. Do not
                # close JobService/CAS/HTTP while either still uses them.
                with self._scheduler_dispatch_lock:
                    # Metadata GET is bounded by the explicit Pixiv transport;
                    # never close its client/ledger while a request is active.
                    with self._metadata_run_lock:
                        with self._viewer_page_lock:
                            with self._legacy_export_lock:
                                self._close_resources()

    def _close_pixiv_actions(self):
        actions = self.pixiv_actions
        if actions is None:
            return
        try:
            stopped = actions.close()
            if stopped is not True or actions.shutdown_unverified:
                raise RuntimeError()
        except Exception:
            # Retain the shared reader/store and action ledger so a later
            # close can finish draining. Never close borrowed owners first.
            self.closed = False
            raise RuntimeError('Pixiv action shutdown unverified') from None

    def _close_resources(self):
        with self._failure_actions_lock:
            if self._failure_record_actions is not None:
                self._failure_record_actions.close()
                self._failure_record_actions = None
        if self._owned_legacy_capacity_cache is not None:
            self._owned_legacy_capacity_cache.close()
            self._owned_legacy_capacity_cache = None
        if self.save_exports is not None:self.save_exports.close()
        if self.extension_bridge is not None:self.extension_bridge.close()
        if self.extension_commands is not None:self.extension_commands.close(timeout=150.0)
        # Fence concurrent metadata configuration before the storage provider
        # or shared Pixiv client is closed.
        with self._managed_metadata_lock:
            if self.managed_metadata is not None:
                self.managed_metadata.close()
                self.managed_metadata = None
        # Accepted jobs outlive views; join the worker before closing the
        # image provider it may still be using.
        if self.similarity_source is not None:
            self.similarity_source.close()
        with self._similarity_run_lock:
            if self._egara_inference is not None:
                self._egara_inference.close()
                self._egara_inference = None
            if self._similarity_legacy_inputs is not None:
                self._similarity_legacy_inputs.close()
                self._similarity_legacy_inputs = None
            if self.similarity_index is not None:
                self.similarity_index.close()
                self.similarity_index = None
        if self.duplicate_inspection is not None:
            self.duplicate_inspection.close()
        if self.recommendations is not None:
            self.recommendations.close()
        if self.discovery is not None:
            self.discovery.close()
        if self.following_sync is not None:
            self.following_sync.close()
        vault_shutdown_error = None
        if self.external_vault is not None:
            try:
                self.external_vault.close()
            except Exception as error:
                # The Vault owns an independent transport and writer lease.
                # Never report its unverified shutdown as success, but do not
                # strand the managed jobs, media, SQLite and Pixiv reader that
                # are owned independently by the application either.
                vault_shutdown_error = error
        if self.novel_jobs is not None:
            self.novel_jobs.close(timeout=150.0)
        if self.ugoira is not None:
            self.ugoira.close(timeout=150.0)
        if self.static_jobs is not None:
            self.static_jobs.close(timeout=150.0)
        elif self.novel_jobs is None and self.ugoira is None and self.jobs is not None:
            self.jobs.close(timeout=150.0)
        if self.author_media is not None:
            self.author_media.close()
        if self.download_writer is not None:
            self.download_writer.close()
        if self.managed_downloads is not None:
            if self.legacy_zip_exporter is not None:
                self.legacy_zip_exporter.close()
            self.managed_downloads.close()
        if self.legacy_author_bridge is not None:
            self.legacy_author_bridge.close()
        try:
            if self.learning is not None:
                self.learning.close()
        finally:
            self.images.close()
            if self.library_repository is not None:
                self.library_repository.close()
            if self.live_client is not None:
                self.live_client.close()
            if self._public_images is not None:
                self._public_images.close()
            if self.managed_scheduler is not None:
                self.managed_scheduler.close()
            if self.tray is not None:
                self.tray.close()
            if self.notifications is not None:
                self.notifications.close()
            if self.preferences is not None:
                self.preferences.close()
            if self.shared_image_cache is not None:
                self.shared_image_cache.close()
            self.closed = True
        if vault_shutdown_error is not None:
            raise vault_shutdown_error

    def enable_managed_scheduler(self):
        """Explicit opt-in, after managed-only application resources exist."""
        with self._close_lock, self._managed_scheduler_lock:
            if self.closing or self.managed_downloads is None or self.live_client is None:
                raise RuntimeError('isolated managed Pixiv mode is required')
            if self.managed_scheduler is None:
                from .managed_scheduler import ManagedScheduler, ManagedScheduleRunner, submit_author_batch
                ledger = ManagedScheduler(self.managed_downloads.root)

                def dispatch(slot, cancellation):
                    # Queue acceptance is not a verified saved receipt.
                    with self._scheduler_dispatch_lock:
                        submit_author_batch(self, ledger.favourites(), cancellation=cancellation,
                                            ledger=ledger, slot=slot)

                runner = ManagedScheduleRunner(ledger, dispatch,
                                               reconcile=self.reconcile_managed_scheduler)
                try:
                    runner.start()
                except BaseException:
                    ledger.close()
                    raise
                self.managed_scheduler = ledger
                self.managed_schedule_runner = runner
            return self.managed_scheduler

    def enable_managed_metadata(self):
        """Explicit provisioning only; never opens Pixiv, starts a timer or GET.

        A managed root is required. This intentionally does not restore an ON
        preference as an automatic network worker after application restart.
        """
        with self._close_lock, self._managed_metadata_lock:
            if self.closing:
                raise RuntimeError('application services are closing')
            if self.managed_downloads is None:
                raise RuntimeError('isolated managed Pixiv mode is required')
            if self.managed_metadata is None:
                from .metadata_ledger import ManagedMetadataLedger

                def active_receipt(work_id):
                    if self.closing or self.managed_downloads is None:
                        raise RuntimeError('managed receipt provider closed')
                    receipt = self.managed_downloads.verified_saved_receipt(work_id)
                    # receipt[2] is SHA256 of immutable receipt bytes. The
                    # optional receipt[4] is *job acceptance* fingerprint,
                    # which is a different concept and may be absent for v1.
                    return receipt[2] if receipt is not None else None

                self.managed_metadata = ManagedMetadataLedger(
                    self.managed_downloads.root,
                    active_receipt_sha256=active_receipt)
            return self.managed_metadata

    def managed_metadata_settings(self):
        """Read an already-open side-ledger only; no auto-provisioning or GET."""
        with self._managed_metadata_lock:
            if self.closing:
                raise RuntimeError('application services are closing')
            return (None if self.managed_metadata is None
                    else self.managed_metadata.settings())

    def managed_metadata_progress(self):
        """Local side-ledger state counts only; no Pixiv request or file scan."""
        with self._managed_metadata_lock:
            if self.closing:
                raise RuntimeError('application services are closing')
            return (None if self.managed_metadata is None
                    else self.managed_metadata.progress())

    def managed_metadata_entry(self, work_id: str):
        """Inspect one local side-ledger row without verifying or fetching Pixiv."""
        with self._managed_metadata_lock:
            if self.closing or self.managed_metadata is None:
                raise RuntimeError('managed metadata ledger is not open')
            return self.managed_metadata.entry(work_id)

    def acknowledge_managed_metadata_interrupted(self, work_id: str):
        """Explicitly retire only an older epoch's interrupted lease."""
        with self._managed_metadata_lock:
            if self.closing or self.managed_metadata is None:
                raise RuntimeError('managed metadata ledger is not open')
            self.managed_metadata.acknowledge_interrupted(work_id)

    def resume_managed_metadata_interrupted(self, work_id: str, *, expected_revision: int):
        """Explicit CAS reset to pending; does not schedule a request."""
        with self._managed_metadata_lock:
            if self.closing or self.managed_metadata is None:
                raise RuntimeError('managed metadata ledger is not open')
            self.managed_metadata.resume_interrupted(
                work_id, expected_revision=expected_revision)

    def resume_managed_metadata_blocked(self, work_id: str, *, expected_revision: int):
        """Explicit CAS reset after an outage; never hot-retries itself."""
        with self._managed_metadata_lock:
            if self.closing or self.managed_metadata is None:
                raise RuntimeError('managed metadata ledger is not open')
            self.managed_metadata.resume_blocked(
                work_id, expected_revision=expected_revision)

    def configure_managed_metadata(self, *, enabled: bool, expected_revision: int):
        """CAS settings change; never triggers metadata retrieval."""
        with self._managed_metadata_lock:
            if self.closing or self.managed_metadata is None:
                raise RuntimeError('managed metadata ledger is not open')
            return self.managed_metadata.configure(
                enabled=enabled, expected_revision=expected_revision)

    def stop_managed_metadata(self) -> bool:
        """Cancel the currently requested run; a bounded in-flight GET drains."""
        if self.closing or not self._metadata_run_lock.locked():
            return False
        self._metadata_cancel_event.set()
        return True

    def run_managed_metadata_one(self, work_id: str, *, transport=None,
                                 expected_revision: int | None = None):
        """Explicit one-work bounded GET; downloads win admission.

        The already-open, user-enabled managed-only side-ledger is mandatory.
        A snapshot is derived solely from a fully verified saved receipt, not
        from a user-supplied author ID. No background task, scan or retry is
        scheduled here; a second caller receives already_running immediately.
        """
        from .metadata_runner import ManagedMetadataBackfillRunner, MetadataRunSummary
        from .metadata_policy import MetadataCandidate
        from .metadata_transport import LivePixivMetadataTransport
        from .job_service import UNFINISHED

        if type(work_id) is not str or re.fullmatch(r'[1-9][0-9]{0,19}\Z', work_id) is None:
            raise ValueError('invalid metadata work identity')
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError('invalid metadata settings revision')
        if not self._metadata_run_lock.acquire(blocking=False):
            return MetadataRunSummary(stopped='already_running')
        try:
            if self.closing or self.managed_metadata is None or self.managed_downloads is None:
                raise RuntimeError('managed metadata mode is unavailable')
            if not self.managed_metadata.settings().enabled:
                return MetadataRunSummary(stopped='disabled')
            # The ordinary enqueue path also takes this same lock; while this
            # single bounded GET is in flight, no direct/scheduled enqueue can
            # be accepted behind our idle probe. Existing queued/running/paused
            # downloads always win and make us return without any auth GET.
            with self._managed_enqueue_lock:
                if self.closing:
                    return MetadataRunSummary(stopped='cancelled')
                if (expected_revision is not None and
                        self.managed_metadata.settings().revision != expected_revision):
                    return MetadataRunSummary(stopped='settings_changed')
                if self.jobs is None or any(j.state in UNFINISHED for j in self.jobs.snapshot()):
                    return MetadataRunSummary(stopped='download_busy')
                receipt = self.managed_downloads.verified_saved_receipt(work_id)
                if receipt is None:
                    return MetadataRunSummary(stopped='no_saved_receipt')
                author_id = receipt[1]['author_id']
                prior = self.managed_metadata.entry(work_id)
                candidate = MetadataCandidate(
                    work_id, author_id, 'illust', False, None,
                    prior.attempts if prior is not None else 0)

                def active(wid):
                    if self.closing:
                        raise RuntimeError('metadata source is closing')
                    current = self.managed_downloads.verified_saved_receipt(wid)
                    return current[2] if current is not None else None

                runner = ManagedMetadataBackfillRunner(
                    self.managed_metadata,
                    transport if transport is not None else LivePixivMetadataTransport(self.live_client),
                    active_receipt_sha256=active,
                    is_download_busy=lambda: self.closing or self.jobs is None
                    or any(j.state in UNFINISHED for j in self.jobs.snapshot()),
                )
                # Phase10 blocks on the first global failure, including identity GET.
                with request_scope(allow_retry=False):
                    return runner.run_once((candidate,),
                                           stop_event=self._metadata_cancel_event,
                                           wait_for_idle=False)
        finally:
            # Never erase a Stop received after run admission but before its
            # first auth GET. Reset only once this particular run has drained.
            if not self.closing:
                self._metadata_cancel_event.clear()
            self._metadata_run_lock.release()

    def reconcile_managed_scheduler(self):
        """Join JobService operation history to verified managed CAS receipts."""
        if (self.closing or self.managed_scheduler is None or self.jobs is None
                or self.managed_downloads is None):
            return False
        from .job_service import UNFINISHED
        jobs = self.jobs.snapshot()
        operation_to_job = {}
        operation_to_fingerprint = {}
        from .managed_download_requests import ManagedAuthorIntent, accepted_author_fingerprint
        for job in jobs:
            accepted = self._managed_job_records.get(job.job_id)
            fingerprint = (accepted_author_fingerprint(accepted, job.job_id)
                           if isinstance(accepted, ManagedAuthorIntent) else None)
            for operation_id in self.jobs.operation_ids(job.job_id):
                operation_to_job[operation_id] = job.job_id
                if fingerprint is not None:
                    operation_to_fingerprint[operation_id] = fingerprint
        receipt_fingerprints = {}
        for work_id, expected in self.managed_scheduler.reconcile_expected_fingerprints(
                operation_to_fingerprint).items():
            if not expected or not work_id.isdecimal():
                continue
            # Matching only by work ID would repeatedly hash/decode a valid
            # *old* generation for an unresolved newer operation every 5s.
            # The header/index-only check is bounded; only the exact
            # job-specific fingerprint warrants full page verification.
            cheap = self.managed_downloads.verified_saved_receipt(work_id, verify_pages=False)
            if cheap is None or cheap[4] not in expected:
                continue
            receipt = self.managed_downloads.verified_saved_receipt(work_id)
            if receipt is not None and receipt[4] in expected:
                receipt_fingerprints[receipt[1]['work_id']] = receipt[4]
        if getattr(self, 'author_media', None) is not None:
            _, extra_jobs, extra_fingerprints = self.author_media.reconcile()
            operation_to_job.update(extra_jobs)
            operation_to_fingerprint.update(extra_fingerprints)
            expected = self.managed_scheduler.reconcile_expected_fingerprints(operation_to_fingerprint)
            extra_receipts, _, _ = self.author_media.reconcile(expected)
            receipt_fingerprints.update(extra_receipts)
        newly_saved = self.managed_scheduler.reconcile_jobs(
            jobs, receipt_fingerprints, operation_to_job, operation_to_fingerprint)
        notifier=getattr(self,'notifications',None)
        if newly_saved and notifier is not None:
            # Reconciliation changes accepted -> saved once in the durable ledger.
            # OS toast is best effort; existing durable history/UI notice remains.
            notifier.notify('PixivVault',f'作者更新チェック: {len(newly_saved)}件の保存が完了しました。')
        unfinished_count = sum(1 for job in jobs if job.state in UNFINISHED)
        previous_count = self._scheduler_unfinished_count
        self._scheduler_unfinished_count = unfinished_count
        if (previous_count is not None and unfinished_count < previous_count
                and self.managed_scheduler.has_deferred()
                and self.managed_schedule_runner is not None):
            self.managed_schedule_runner.settings_changed()
        return unfinished_count < 50


_services: ApplicationServices | None = None
_lock = Lock()
_APP_KEY = "_pixivvault_application_services"


def get_services(app=None, *, learning_sandbox: Path | None = None,
                 library_readonly_db: Path | None = None,
                 library_image_root: Path | None = None,
                 live_client=None, download_sandbox: Path | None = None,
                 pixiv_download_root: Path | None = None,
                 failed_queue_readonly_db: Path | None = None,
                 legacy_zip_export_root: Path | None = None,
                 legacy_diff_image_root: Path | None = None,
                 novel_fixture_root: Path | None = None,
                 novel_fixture_source=None,
                 ugoira_root: Path | None = None, ugoira_source=None, ugoira_codec_python=None,
                 recommendation_preferences_root=None, preferences_root=None, discovery_root=None,
                 external_vault_root=None, image_cache_root=None, discovery_following=None,
                 external_vault_transport=None, external_vault_crypto=None, desktop=None, notification_backend=None,
                 following_sync_root=None, pixiv_actions_mode=None) -> ApplicationServices:
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
                    legacy_zip_export_root=legacy_zip_export_root,
                    legacy_diff_image_root=legacy_diff_image_root,
                    novel_fixture_root=novel_fixture_root,
                    novel_fixture_source=novel_fixture_source,
                    ugoira_root=ugoira_root,ugoira_source=ugoira_source,ugoira_codec_python=ugoira_codec_python,
                    recommendation_preferences_root=recommendation_preferences_root,
                    preferences_root=preferences_root, discovery_root=discovery_root,
                    external_vault_root=external_vault_root, image_cache_root=image_cache_root,
                    discovery_following=discovery_following, external_vault_transport=external_vault_transport,
                    external_vault_crypto=external_vault_crypto, desktop=desktop, notification_backend=notification_backend,
                    following_sync_root=following_sync_root,
                    pixiv_actions_mode='readonly' if pixiv_actions_mode is None else pixiv_actions_mode)
            services = _services
        else:
            services = getattr(app.state, _APP_KEY, None)
            if services is None:
                services = ApplicationServices(learning_sandbox=learning_sandbox,
                    library_readonly_db=library_readonly_db, library_image_root=library_image_root,
                    live_client=live_client, download_sandbox=download_sandbox,
                    pixiv_download_root=pixiv_download_root,
                    failed_queue_readonly_db=failed_queue_readonly_db,
                    legacy_zip_export_root=legacy_zip_export_root,
                    legacy_diff_image_root=legacy_diff_image_root,
                    novel_fixture_root=novel_fixture_root,
                    novel_fixture_source=novel_fixture_source,
                    ugoira_root=ugoira_root,ugoira_source=ugoira_source,ugoira_codec_python=ugoira_codec_python,
                    recommendation_preferences_root=recommendation_preferences_root,
                    preferences_root=preferences_root, discovery_root=discovery_root,
                    external_vault_root=external_vault_root, image_cache_root=image_cache_root,
                    discovery_following=discovery_following, external_vault_transport=external_vault_transport,
                    external_vault_crypto=external_vault_crypto, desktop=desktop, notification_backend=notification_backend,
                    following_sync_root=following_sync_root,
                    pixiv_actions_mode='readonly' if pixiv_actions_mode is None else pixiv_actions_mode)
                setattr(app.state, _APP_KEY, services)
        if services.closing:
            raise RuntimeError("application services are closed")
        if pixiv_actions_mode is not None and services.pixiv_actions_mode != pixiv_actions_mode:
            raise RuntimeError('application services already use a different Pixiv action policy')
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
        if legacy_diff_image_root is not None and services.legacy_diff_image_root != legacy_diff_image_root:
            raise RuntimeError('application services already use a different legacy image root')
        if novel_fixture_root is not None and services.novel_fixture_root != novel_fixture_root:
            raise RuntimeError("application services already use a different novel fixture root")
        if novel_fixture_source is not None and services.novel_fixture_source is not novel_fixture_source:
            raise RuntimeError("application services already use a different novel fixture source")
        if ugoira_root is not None and services.ugoira_root != ugoira_root:
            raise RuntimeError('different ugoira root')
        if ugoira_source is not None and services.ugoira_source is not ugoira_source:
            raise RuntimeError('different ugoira source')
        if recommendation_preferences_root is not None and services.recommendation_preferences_root != recommendation_preferences_root:
            raise RuntimeError('different recommendation preferences root')
        for name, value in (('preferences_root', preferences_root), ('discovery_root', discovery_root),
                            ('external_vault_root', external_vault_root), ('image_cache_root', image_cache_root),
                            ('following_sync_root', following_sync_root)):
            if value is not None and getattr(services, name) != value:
                raise RuntimeError('different application sidecar root')
        return services
