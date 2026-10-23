"""Durable opaque operation intents for accepted managed Pixiv downloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from .bookmark_remote_adapter import RemoteBookmark
from .managed_downloads import (MARKER, IDENTITY, ManagedDownloadError,
                                _safe_path, _read_bounded, _write, _json_unique, _fsync_dir)

LIMIT = 128 * 1024
MAX_INTENTS = 10000
ID = re.compile(r'[1-9][0-9]{0,19}\Z')
KEYS = frozenset({'remote_id', 'kind', 'visibility', 'title', 'author_id',
                  'author_name', 'tags', 'created_at', 'page_count',
                  'illustration_type', 'x_restrict', 'page_count_known'})


class ManagedRequestError(ValueError):
    """Fixed error text independent of credentials and operation contents."""


def _text(value, cap, nonempty=False):
    if (type(value) is not str or len(value) > cap or (nonempty and not value)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
        raise ManagedRequestError('managed intent rejected')
    return value


def _spec(value: RemoteBookmark) -> dict:
    if not isinstance(value, RemoteBookmark):
        raise ManagedRequestError('managed intent rejected')
    if (type(value.remote_id) is not str or not ID.fullmatch(value.remote_id)
            or type(value.author_id) is not str or not ID.fullmatch(value.author_id)
            or value.kind != 'illustration' or value.visibility not in ('show', 'hide')
            or type(value.page_count) is not int or not 1 <= value.page_count <= 1000
            or value.page_count_known is not True
            or type(value.illustration_type) is not int or value.illustration_type not in (0, 1)
            or type(value.x_restrict) is not int or value.x_restrict not in (0, 1, 2)
            or type(value.tags) is not tuple or len(value.tags) > 100):
        raise ManagedRequestError('managed intent rejected')
    _text(value.title, 256)
    _text(value.author_name, 256)
    _text(value.created_at, 128)
    for tag in value.tags:
        _text(tag, 128)
    return {'remote_id': value.remote_id, 'kind': value.kind,
            'visibility': value.visibility, 'title': value.title,
            'author_id': value.author_id, 'author_name': value.author_name,
            'tags': list(value.tags), 'created_at': value.created_at,
            'page_count': value.page_count, 'illustration_type': value.illustration_type,
            'x_restrict': value.x_restrict, 'page_count_known': True}


def _decode(data: bytes) -> dict:
    try:
        payload = json.loads(data, object_pairs_hook=_json_unique)
        if type(payload) is not dict or payload.keys() != KEYS:
            raise ValueError()
        if type(payload['tags']) is not list:
            raise ValueError()
        restored = RemoteBookmark(**{**payload, 'tags': tuple(payload['tags']), 'thumbnail': ''})
        if _spec(restored) != payload:
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ManagedRequestError('saved intent rejected') from None


class ManagedDownloadRequests:
    def __init__(self, root: Path):
        self.root = Path(root)
        try:
            if not self.root.is_absolute() or self.root.parent == self.root:
                raise ValueError()
            _safe_path(self.root)
            if not self.root.is_dir():
                raise ValueError()
            self.root = self.root.resolve()
            marker = self.root / MARKER
            raw = _read_bounded(marker, 4096)
            if json.loads(raw, object_pairs_hook=_json_unique) != IDENTITY:
                raise ValueError()
            self.folder = self.root / 'requests'
            _safe_path(self.folder)
            self.folder.mkdir(exist_ok=True)
            _safe_path(self.folder)
            self.incomplete = self.root / 'request-incomplete'
            _safe_path(self.incomplete)
            self.incomplete.mkdir(exist_ok=True)
            _safe_path(self.incomplete)
            self._lock = Lock()
        except Exception:
            raise ManagedRequestError('managed intent root rejected') from None

    @staticmethod
    def equivalent(a: RemoteBookmark, b: RemoteBookmark) -> bool:
        return _spec(a) == _spec(b)

    def _path(self, operation_id: str) -> Path:
        _text(operation_id, 128, nonempty=True)
        return self.folder / (hashlib.sha256(operation_id.encode('utf-8')).hexdigest() + '.json')

    def _read(self, path: Path) -> dict:
        try:
            return _decode(_read_bounded(path, LIMIT))
        except ManagedRequestError:
            raise
        except Exception:
            raise ManagedRequestError('saved intent rejected') from None

    def load(self, operation_id: str) -> RemoteBookmark:
        with self._lock:
            try:
                _safe_path(self.folder)
                payload = self._read(self._path(operation_id))
                return RemoteBookmark(**{**payload, 'tags': tuple(payload['tags']), 'thumbnail': ''})
            except ManagedRequestError:
                raise
            except Exception:
                raise ManagedRequestError('saved intent rejected') from None

    def store(self, operation_id: str, record: RemoteBookmark) -> None:
        spec = _spec(record)
        data = json.dumps(spec, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        if len(data) > LIMIT:
            raise ManagedRequestError('managed intent rejected')
        with self._lock:
            try:
                _safe_path(self.folder)
                target = self._path(operation_id)
                if target.exists() or target.is_symlink():
                    if self._read(target) != spec:
                        raise ManagedRequestError('operation intent collision')
                    return
                count = 0
                for item in self.folder.iterdir():
                    count += 1
                    if count >= MAX_INTENTS or not re.fullmatch(r'[0-9a-f]{64}\.json', item.name):
                        raise ManagedRequestError('managed intent directory rejected')
                    self._read(item)
                _safe_path(self.incomplete)
                temporary = self.incomplete / ('staging-' + uuid4().hex)
                _write(temporary, data)
                _fsync_dir(self.incomplete)
                if target.exists() or target.is_symlink():
                    raise ManagedRequestError('operation intent collision')
                temporary.rename(target)
                _fsync_dir(self.folder)
            except ManagedRequestError:
                raise
            except Exception:
                raise ManagedRequestError('managed intent save failed') from None
