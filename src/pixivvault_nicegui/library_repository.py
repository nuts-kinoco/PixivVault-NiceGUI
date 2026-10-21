"""Explicit-path, read-only adapter for the legacy SQLite library schema."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from threading import RLock

from .models import Artwork


class ReadonlyLibraryRepository:
    """A snapshot-per-query reader; never initializes or migrates the legacy DB."""

    def __init__(self, db_path: str | Path):
        if db_path is None or not str(db_path).strip():
            raise ValueError("explicit db_path required")
        path = Path(db_path).absolute()
        if not path.is_file():
            raise FileNotFoundError(path)
        self._lock = RLock()
        self._closed = False
        # mode=ro honors the WAL view; immutable=1 would ignore live WAL data.
        self._conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(works)")}
            required = {"work_id", "title", "page_count", "user_id", "tags", "is_deleted"}
            if not required <= columns:
                raise ValueError(f"unsupported works schema; missing {sorted(required - columns)}")
            authors = {row[1] for row in self._conn.execute("PRAGMA table_info(following_users)")}
            if not {"user_id", "name"} <= authors:
                raise ValueError("unsupported following_users schema")
        except BaseException:
            self._conn.close()
            raise

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("repository closed")

    @staticmethod
    def _query(query: str) -> tuple[str, tuple]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if not query:
            return "", ()
        # Literal substring search: escape LIKE wildcard characters.
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{escaped}%"
        return (" AND (w.title LIKE ? ESCAPE '\\' OR w.work_id LIKE ? ESCAPE '\\' "
                "OR f.name LIKE ? ESCAPE '\\')", (term, term, term))

    def count_artworks(self, query: str = "") -> int:
        condition, params = self._query(query)
        with self._lock:
            self._ensure_open()
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM works w LEFT JOIN following_users f ON f.user_id=w.user_id "
                "WHERE w.is_deleted=0" + condition, params).fetchone()[0])

    def list_artworks(self, query: str = "", offset: int = 0, limit: int = 50) -> tuple[Artwork, ...]:
        if type(offset) is not int or offset < 0 or offset > 1_000_000:
            raise ValueError("offset out of range")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit out of range")
        condition, params = self._query(query)
        with self._lock:
            self._ensure_open()
            rows = self._conn.execute(
                "SELECT w.work_id, w.title, w.page_count, w.tags, w.user_id, f.name AS author_name "
                "FROM works w LEFT JOIN following_users f ON f.user_id=w.user_id "
                "WHERE w.is_deleted=0" + condition + " ORDER BY w.work_id LIMIT ? OFFSET ?",
                (*params, limit, offset)).fetchall()
        artworks = []
        for row in rows:
            try:
                parsed = json.loads(row["tags"] or "[]")
                tags = tuple(tag for tag in parsed if isinstance(tag, str)) if isinstance(parsed, list) else ()
            except (TypeError, ValueError):
                tags = ()
            # page_count can be NULL in the historical schema; Artwork requires >=1.
            pages = row["page_count"]
            pages = pages if type(pages) is int and pages > 0 else 1
            artworks.append(Artwork(work_id=str(row["work_id"]), title=row["title"] or "",
                                    author=row["author_name"] or str(row["user_id"] or ""),
                                    page_count=pages, tags=tags))
        return tuple(artworks)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_):
        self.close()
