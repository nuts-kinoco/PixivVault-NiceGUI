"""Reference-counted image snapshots: one client's reload cannot change another's images."""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from uuid import uuid4
import re

from .media import ImageBlob

_ID = re.compile(r"(?:novel:)?[A-Za-z0-9_-]{1,128}\Z")


@dataclass
class _Version:
    local: object | None
    remote: object | None
    refs: int = 0

    def close(self) -> None:
        if self.local is not None:
            self.local.close()
        if self.remote is not None:
            self.remote.close()


class PinnedLibraryImages:
    def __init__(self, registry: LibraryImageRegistry, token: str):
        self.registry, self.token = registry, token
        self.closed = False

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        if self.closed:
            raise RuntimeError("image snapshot closed")
        return self.registry.url_for(self.token, work_id, page_no, variant)

    def is_animation(self,work_id):
        return not self.closed and self.registry.is_animation_versioned(self.token,work_id)

    def supports_original(self,work_id):
        return not self.closed and self.registry.supports_original_versioned(self.token,work_id)

    def display_animation(self,work_id):
        if self.closed:raise RuntimeError('image snapshot closed')
        return self.registry.display_animation_versioned(self.token,work_id)

    def can_open(self, work_id: str) -> bool:
        return not self.closed and self.registry.can_open(self.token, work_id)

    def configure_prefetch(self,mode,limit):
        if self.closed:raise RuntimeError('image snapshot closed')
        return self.registry.configure_prefetch(self.token,mode,limit)

    def prefetch_stats(self):
        if self.closed:raise RuntimeError('image snapshot closed')
        return self.registry.prefetch_stats(self.token)

    def peek(self,work_id,page_no=0,variant='view'):
        if self.closed:raise RuntimeError('image snapshot closed')
        try:return self.registry.metadata_versioned(self.token,work_id,page_no,variant)
        except RuntimeError:return None

    def get(self,work_id,page_no=0,variant='view'):
        if self.closed:raise RuntimeError('image snapshot closed')
        return self.registry.get_versioned(self.token,work_id,page_no,variant)

    def can_open_external(self,work_id):
        return not self.closed and self.registry.can_open_external(self.token,work_id)

    def external_target(self,work_id,page_no=0):
        if self.closed:raise RuntimeError('image snapshot closed')
        return self.registry.external_target(self.token,work_id,page_no)

    def source_page_identity(self, work_id, page_no=0):
        if self.closed:
            raise RuntimeError('image snapshot closed')
        return self.registry.source_page_identity_versioned(self.token, work_id, page_no)

    def needs_metadata(self, work_id: str) -> bool:
        return not self.closed and self.registry.needs_metadata(self.token, work_id)

    def prepare_artwork(self, work_id: str):
        if self.closed:
            raise RuntimeError("image snapshot closed")
        return self.registry.prepare_artwork(self.token, work_id)

    def artwork_for(self, work_id: str):
        return self.registry.artwork_for(self.token, work_id)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.registry.release(self.token)


class LibraryImageRegistry:
    """Short locks only; image I/O never holds this registry's lock.

    The catalog query identifies immutable content, not a per-request cache
    buster. Viewer request identity remains exclusively in the URL fragment.
    """
    def __init__(self, demo, *, max_versions: int = 16, shared_cache=None):
        self.demo = demo
        self.shared_cache=shared_cache
        if shared_cache is not None:demo.configure_cache(shared_cache)
        self._lock = RLock()
        self._versions: dict[str, _Version] = {}
        self._current: str | None = None
        self._closed = False
        self._max_versions = max_versions
        self.publish(None, None)

    def bind_shared_cache(self, shared_cache):
        """Bind existing and future pins once, after service resources initialize."""
        with self._lock:
            if self._closed: raise RuntimeError("image registry closed")
            if self.shared_cache is shared_cache:return
            if self.shared_cache is not None:raise RuntimeError("image cache already bound")
            providers=[self.demo]
            for version in self._versions.values():providers.extend((version.local,version.remote))
            unique={id(provider):provider for provider in providers if provider is not None}
            if any(not callable(getattr(provider,"configure_cache",None)) for provider in unique.values()):
                raise ValueError("provider does not support shared image cache")
            for provider in unique.values():provider.configure_cache(shared_cache)
            self.shared_cache=shared_cache

    @property
    def library(self):
        with self._lock:
            return self._versions[self._current].local

    def _prune(self) -> None:
        retired = [key for key, value in self._versions.items()
                   if key != self._current and value.refs == 0]
        for key in retired:
            self._versions.pop(key).close()

    def publish(self, local, remote) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("image registry closed")
            self._prune()
            current_free = self._current is not None and self._versions[self._current].refs == 0
            if len(self._versions) >= self._max_versions and not current_free:
                raise RuntimeError("too many active image snapshots; close an older view")
            if self.shared_cache is not None:
                for provider in (local,remote):
                    if provider is not None:provider.configure_cache(self.shared_cache)
            token = uuid4().hex
            self._versions[token] = _Version(local, remote)
            self._current = token
            self._prune()
            return token

    def acquire_catalog(self, remote) -> PinnedLibraryImages:
        """Own a non-current catalog; ranking refresh never replaces library media."""
        with self._lock:
            if self._closed:
                raise RuntimeError("image registry closed")
            self._prune()
            if len(self._versions) >= self._max_versions:
                raise RuntimeError("too many active image snapshots; close an older view")
            if self.shared_cache is not None:remote.configure_cache(self.shared_cache)
            token = uuid4().hex
            self._versions[token] = _Version(None, remote, refs=1)
            return PinnedLibraryImages(self, token)

    def acquire(self) -> PinnedLibraryImages:
        with self._lock:
            if self._closed:
                raise RuntimeError("image registry closed")
            token = self._current
            self._versions[token].refs += 1
            return PinnedLibraryImages(self, token)

    def release(self, token: str) -> None:
        with self._lock:
            version = self._versions.get(token)
            if version is not None:
                version.refs = max(0, version.refs - 1)
            self._prune()

    def _version(self, token: str | None) -> _Version:
        if self._closed:
            raise RuntimeError("image registry closed")
        version = self._versions.get(token or self._current)
        if version is None:
            raise ValueError("image snapshot not found")
        return version

    def _provider(self, version: _Version, work_id: str):
        if version.local is not None and work_id in getattr(version.local, 'source_pages', {}):
            # Empty page maps are managed tombstones, not permission to use
            # another local/remote copy of the same work.
            return version.local
        if version.local is not None and work_id in version.local.works:
            # Local still-image catalogs cannot decode novel text/animation.
            # A supported remote preview may serve those identities instead.
            work = version.local.works[work_id]
            if getattr(work, "work_type", None) not in {"novel", "ugoira"}:
                return version.local
        if version.remote is not None and work_id in version.remote.works:
            return version.remote
        return self.demo

    def is_animation_versioned(self,token,work_id):
        with self._lock:
            provider=self._provider(self._version(token),work_id)
            predicate=getattr(provider,'is_animation',None)
            return bool(predicate and predicate(work_id))

    def supports_original_versioned(self,token,work_id):
        with self._lock:
            provider=self._provider(self._version(token),work_id)
            predicate=getattr(provider,'supports_original',None)
            return bool(callable(predicate) and predicate(work_id))

    def supports_original(self,work_id):
        with self._lock:
            token=self._current
        return self.supports_original_versioned(token,work_id)

    def display_animation_versioned(self,token,work_id):
        with self._lock:
            version=self._version(token)
            provider=self._provider(version,work_id)
            method=getattr(provider,'display_animation',None)
            if method is None:raise ValueError('animation unavailable')
            version.refs+=1
        try:return method(work_id)
        finally:self.release(token)

    def is_animation(self,work_id):
        with self._lock:token=self._current
        return self.is_animation_versioned(token,work_id)

    def display_animation(self,work_id):
        with self._lock:token=self._current
        return self.display_animation_versioned(token,work_id)

    def can_open(self, token: str, work_id: str) -> bool:
        with self._lock:
            version = self._version(token)
            provider = self._provider(version, work_id)
            try:
                provider.url(work_id, 0, "view")
                return True
            except (ValueError, RuntimeError):
                return False

    def configure_prefetch(self,token,mode,limit):
        with self._lock:
            remote=self._version(token).remote
            if remote is None or not hasattr(remote,'configure_prefetch'):raise RuntimeError('learning cache unavailable')
            return remote.configure_prefetch(mode,limit)

    def prefetch_stats(self,token):
        with self._lock:
            remote=self._version(token).remote
            if remote is None or not hasattr(remote,'prefetch_stats'):raise RuntimeError('learning cache unavailable')
            return remote.prefetch_stats()

    def can_open_external(self,token,work_id):
        with self._lock:
            provider=self._provider(self._version(token),work_id)
            return callable(getattr(provider,'external_target',None)) and work_id in getattr(provider,'works',{})

    def external_target(self,token,work_id,page_no=0):
        with self._lock:
            version=self._version(token)
            provider=self._provider(version,work_id)
            method=getattr(provider,'external_target',None)
            if method is None:raise ValueError('saved target unavailable')
            version.refs+=1
        try:return method(work_id,page_no)
        finally:self.release(token)

    def source_page_identity_versioned(self, token, work_id, page_no=0):
        with self._lock:
            key = token or self._current
            version = self._version(key)
            provider = self._provider(version, work_id)
            method = getattr(provider, 'source_page_identity', None)
            if method is None:
                return None
            version.refs += 1
        try:
            return method(work_id, page_no)
        finally:
            self.release(key)

    def source_page_identity(self, work_id, page_no=0):
        with self._lock:
            token = self._current
        return self.source_page_identity_versioned(token, work_id, page_no)

    def url_for(self, token: str, work_id: str, page_no: int, variant: str) -> str:
        if (not isinstance(work_id, str) or not _ID.fullmatch(work_id) or
                type(page_no) is not int or not 0 <= page_no < 10000 or
                variant not in {"thumb", "view", "original"}):
            raise ValueError("invalid image identity")
        with self._lock:
            version = self._version(token)
            provider = self._provider(version, work_id)
            if provider is self.demo:
                try:
                    return self.demo.url(work_id, page_no, variant)
                except ValueError:
                    # Missing offline media is rendered as an HTTP error/placeholder,
                    # never an exception during construction of the entire grid.
                    return f"/media/{work_id}/{page_no}/{variant}.png?catalog={token}"
            return provider.url(work_id, page_no, variant) + f"?catalog={token}"

    def needs_metadata(self, token: str, work_id: str) -> bool:
        with self._lock:
            provider = self._provider(self._version(token), work_id)
            predicate = getattr(provider, "needs_metadata", None)
            return bool(predicate and predicate(work_id))

    def artwork_for(self, token: str, work_id: str):
        with self._lock:
            provider = self._provider(self._version(token), work_id)
            retrieve = getattr(provider, "artwork_for", None)
            return retrieve(work_id) if retrieve is not None else None

    def prepare_artwork(self, token: str, work_id: str):
        with self._lock:
            version = self._version(token)
            provider = self._provider(version, work_id)
            prepare = getattr(provider, "prepare_artwork", None)
            if prepare is None:
                raise ValueError("metadata provider unavailable")
            version.refs += 1
        try:
            return prepare(work_id)
        finally:
            self.release(token)

    def url(self, work_id: str, page_no: int = 0, variant: str = "view") -> str:
        with self._lock:
            token = self._current
        return self.url_for(token, work_id, page_no, variant)

    def get(self, work_id: str, page_no: int = 0, variant: str = "view") -> ImageBlob:
        # Internal service callers already own this app. HTTP callers must
        # present a version token for any non-demo media.
        with self._lock:
            token = self._current
        return self.get_versioned(token, work_id, page_no, variant)

    def get_versioned(self, token: str | None, work_id: str, page_no: int, variant: str) -> ImageBlob:
        if token is None:
            return self.demo.get(work_id, page_no, variant)
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("invalid image snapshot")
        with self._lock:
            key = token
            version = self._version(key)
            version.refs += 1  # Own the resource while a response is decoded.
            provider = self._provider(version, work_id)
        try:
            return provider.get(work_id, page_no, variant)
        finally:
            self.release(key)

    def metadata_versioned(self, token: str | None, work_id: str, page_no: int, variant: str):
        """HEAD must not start an authenticated metadata or image request."""
        if token is None:
            return self.demo.get(work_id, page_no, variant)
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("invalid image snapshot")
        with self._lock:
            provider = self._provider(self._version(token), work_id)
            if provider is self.demo:
                return self.demo.get(work_id, page_no, variant)
            peek = getattr(provider, "peek", None)
            if peek is None:
                raise RuntimeError("image metadata not cached")
            cached = peek(work_id, page_no, variant)
            if cached is None:
                raise RuntimeError("image metadata not cached")
            return cached

    def cache_control(self, work_id: str) -> str:
        # Browser caching stays disabled. Only the explicit application-owned thumbnail
        # cache may persist sanitized images; it never stores credentials or URLs.
        return "no-store"

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for version in self._versions.values():
                version.close()
            self._versions.clear()
            self.demo.close()
