"""アプリ単位の資源。画面の再接続では作り直したり閉じたりしない。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock

from .fixtures import generate_demo_artworks
from .media import DemoImageCatalog
from .library_fixtures import build_library_fixture
from .library_models import LibrarySnapshot
from .library_media_registry import LibraryImageRegistry


class ApplicationServices:
    def __init__(self, count: int = 120, *, learning_sandbox: Path | None = None,
                 library_readonly_db: Path | None = None,
                 library_image_root: Path | None = None,
                 live_client=None) -> None:
        if (library_readonly_db is None) != (library_image_root is None):
            raise ValueError("read-only library requires both explicit DB and image root")
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
        self.closing = False
        self.closed = False
        self._close_lock = Lock()

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
        return self.data_mode == "synthetic" or self.library_readonly_db is not None

    async def read_failed_records(self):
        """Read the legacy failure table off-loop, without changing any job or DB."""
        if self.closing:
            raise RuntimeError("application services are closed")
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

    @property
    def data_mode(self):
        if self.live_client is not None:
            return "pixiv-readonly"
        return "library-readonly" if self.library_repository is not None else "synthetic"

    async def refresh_library_snapshot(self) -> LibrarySnapshot:
        if self.library_repository is None:
            return self.library_snapshot()
        async with self._library_refresh_lock:
            if self.closing:
                raise RuntimeError("application services are closed")
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
            self._remote_records = records or ()
            self.bookmark_cache_failed = False
            return remote, local

    def close(self) -> None:
        with self._close_lock:
            if not self.closed:
                self.closing = True
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
                 live_client=None) -> ApplicationServices:
    """Return resources scoped to the ASGI app; no-argument access is legacy."""
    global _services
    with _lock:
        if app is None:
            if _services is None:
                _services = ApplicationServices(learning_sandbox=learning_sandbox,
                    library_readonly_db=library_readonly_db, library_image_root=library_image_root,
                    live_client=live_client)
            services = _services
        else:
            services = getattr(app.state, _APP_KEY, None)
            if services is None:
                services = ApplicationServices(learning_sandbox=learning_sandbox,
                    library_readonly_db=library_readonly_db, library_image_root=library_image_root,
                    live_client=live_client)
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
        return services
