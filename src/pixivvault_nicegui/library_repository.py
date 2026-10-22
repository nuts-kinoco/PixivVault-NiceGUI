"""Explicit-path, read-only adapter for the legacy SQLite library schema."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import re
import sqlite3
from threading import RLock

from .bookmark_remote_adapter import RemoteBookmark, parse_bookmark
from .models import Artwork
from .library_models import LibrarySnapshot, LibraryWork


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
    def _tags(raw: object) -> tuple[str, ...]:
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError, UnicodeError):
            return ()
        return tuple(tag for tag in parsed if type(tag) is str) if isinstance(parsed, list) else ()

    @staticmethod
    def _ui_work_id(raw_id: object, content_type: object) -> str:
        # The legacy DB stores raw Pixiv IDs; the UI separates novel/illust IDs.
        raw = str(raw_id)
        return f"novel:{raw}" if content_type == "novel" else raw

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
            tags = self._tags(row["tags"])
            # page_count can be NULL in the historical schema; Artwork requires >=1.
            pages = row["page_count"]
            pages = pages if type(pages) is int and pages > 0 else 1
            artworks.append(Artwork(work_id=str(row["work_id"]), title=row["title"] or "",
                                    author=row["author_name"] or str(row["user_id"] or ""),
                                    page_count=pages, tags=tags))
        return tuple(artworks)

    def library_snapshot(self) -> LibrarySnapshot:
        """Read an entire legacy library in a single consistent read transaction.

        Never synthesize remote entries or silently interpret a failed read as empty.
        """
        with self._lock:
            self._ensure_open()
            self._conn.execute("BEGIN")
            try:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(works)")}
                optional = ("last_backup", "content_type")
                fields = [f"w.{name}" if name in columns else f"NULL AS {name}" for name in optional]
                tables = {row[0] for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                rows = self._conn.execute(
                    "SELECT w.work_id,w.title,w.page_count,w.tags,w.user_id,f.name AS author_name,"
                    + ",".join(fields)
                    + " FROM works w LEFT JOIN following_users f ON f.user_id=w.user_id"
                    " WHERE w.is_deleted=0 ORDER BY w.rowid"
                ).fetchall()
                view_counts = {}
                if "discovery_view_history" in tables:
                    try:
                        view_counts = dict(self._conn.execute(
                            "SELECT h.work_id, SUM(h.open_count) FROM discovery_view_history h "
                            "JOIN works w ON w.work_id=h.work_id WHERE w.is_deleted=0 "
                            "GROUP BY h.work_id").fetchall())
                    except sqlite3.OperationalError:
                        # Original UI keeps works visible when optional history fails.
                        view_counts = {}
                capacity_by_author = {}
                if "library_capacity_cache" in tables:
                    try:
                        cached = self._conn.execute(
                            "SELECT user_id,total_bytes FROM library_capacity_cache").fetchall()
                        capacity_by_author = {
                            str(author): value for author, value in cached
                            if author is not None and (value is None or
                               (type(value) is int and value >= 0))
                        }
                    except sqlite3.OperationalError:
                        capacity_by_author = {}
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.rollback()
                raise
        works = []
        for row in rows:
            if row["work_id"] is None:
                raise ValueError("legacy work without ID")
            tags = self._tags(row["tags"])
            pages = row["page_count"]
            known = type(pages) is int and pages > 0
            pages = pages if known else 1
            author_id = str(row["user_id"] or "")
            author_name = str(row["author_name"] or author_id)
            artwork = Artwork(self._ui_work_id(row["work_id"], row["content_type"]),
                              str(row["title"] or ""),
                              author_name, page_count=pages, tags=tags)
            works.append(LibraryWork(artwork, author_id,
                                     last_backup=row["last_backup"],
                                     page_count_known=known,
                                     view_count=max(0, int(view_counts.get(row["work_id"]) or 0)),
                                     work_type=str(row["content_type"] or "illust"),
                                     author_name=author_name))
        return LibrarySnapshot(tuple(works), author_capacities=capacity_by_author)

    def _bookmark_rows(self, *, include_local: bool = True, include_remote: bool = True
                       ) -> tuple[list[sqlite3.Row] | None, list[sqlite3.Row]]:
        """One read transaction; remote-only reads do not depend on local markers."""
        with self._lock:
            self._ensure_open()
            self._conn.execute("BEGIN")
            try:
                tables = {row[0] for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(works)")}
                rows = None
                if include_remote and "bookmark_remote_cache" in tables:
                    # Legacy cache has no type/page_count columns. Only a matching,
                    # non-deleted downloaded work can supply that metadata.
                    local_type = ("w.content_type" if "content_type" in columns
                                  else "NULL")
                    # Artwork and novel IDs have separate Pixiv namespaces even
                    # when the legacy SQLite table stores both as bare IDs.
                    kind_join = (
                        " AND ((b.is_novel=1 AND w.content_type='novel') OR "
                        "(b.is_novel=0 AND w.content_type!='novel'))"
                        if "content_type" in columns else ""
                    )
                    rows = self._conn.execute(
                        "SELECT b.work_id,b.is_novel,b.title,b.user_id,b.user_name,"
                        "b.tags,b.create_date,b.thumb_url,b.rest_type,b.x_restrict,"
                        f"w.page_count AS local_pages,{local_type} AS local_type "
                        "FROM bookmark_remote_cache b LEFT JOIN works w "
                        "ON w.work_id=b.work_id AND w.is_deleted=0"
                        + kind_join + " ORDER BY b.rowid"
                    ).fetchall()
                local_rows = []
                if include_local and "is_bookmarked" in columns:
                    local_type = ("w.content_type" if "content_type" in columns
                                  else "NULL")
                    local_rows = self._conn.execute(
                        "SELECT w.work_id,w.title,w.page_count,w.tags,w.user_id,f.name AS author_name "
                        + f",{local_type} AS content_type "
                        "FROM works w LEFT JOIN following_users f ON f.user_id=w.user_id "
                        "WHERE w.is_deleted=0 AND w.is_bookmarked=1 ORDER BY w.rowid"
                    ).fetchall()
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.rollback()
                raise
        return rows, local_rows

    @staticmethod
    def _cache_records(rows: list[sqlite3.Row] | None) -> tuple[RemoteBookmark, ...] | None:
        if rows is None:
            return None
        records: dict[str, RemoteBookmark] = {}
        for row in rows:
            if type(row["is_novel"]) is not int or row["is_novel"] not in (0, 1):
                raise ValueError("invalid cached bookmark kind")
            kind = "novel" if row["is_novel"] else "illustration"
            local_type = row["local_type"]
            illustration_type = {"illust": 0, "manga": 1, "ugoira": 2}.get(local_type, 0)
            pages = row["local_pages"]
            locally_known = (
                kind == "illustration" and local_type in {"illust", "manga", "ugoira"}
                and type(pages) is int and 1 <= pages <= 1000
            )
            if not locally_known:
                pages = 1  # The original cache has no page_count; never infer it.
            try:
                raw_tags = json.loads(row["tags"] or "[]")
            except (ValueError, TypeError, UnicodeError):
                raw_tags = []  # Legacy get_bookmark_remote_cache's documented fallback.
            payload = {
                "id": row["work_id"], "title": row["title"], "user_id": row["user_id"],
                "user_name": row["user_name"], "tags": raw_tags,
                "thumb_url": row["thumb_url"], "create_date": row["create_date"],
                "page_count": pages, "type": illustration_type,
                "x_restrict": (0 if row["x_restrict"] is None else row["x_restrict"]),
            }
            record = parse_bookmark(payload, kind=kind, visibility=row["rest_type"])
            if record is not None:
                record = replace(record, page_count_known=(kind == "novel" or locally_known))
                records.setdefault(record.work_id, record)
        return tuple(records.values())

    def bookmark_records(self) -> tuple[RemoteBookmark, ...] | None:
        """Validated legacy cache records, or None if the cache table is absent.

        A corrupt cache raises; it is never silently published as an empty one.
        URL validation is pure and does not fetch images or contact pixiv.
        """
        rows, _ = self._bookmark_rows(include_local=False)
        return self._cache_records(rows)

    def bookmark_bundle(self) -> tuple[tuple[RemoteBookmark, ...] | None, tuple[Artwork, ...]]:
        """Read remote cache and downloaded flags from one SQLite snapshot.

        A missing remote-cache table produces None; corrupt records propagate
        errors instead of replacing a previously successful cache with empty.
        """
        rows, local_rows = self._bookmark_rows()
        records = self._cache_records(rows)
        return records, self._local_bookmark_artwork(local_rows)

    @classmethod
    def _local_bookmark_artwork(cls, local_rows) -> tuple[Artwork, ...]:
        local = []
        for row in local_rows:
            work_id = row["work_id"]
            if type(work_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", work_id):
                continue
            work_id = cls._ui_work_id(work_id, row["content_type"])
            pages = row["page_count"]
            local.append(Artwork(work_id, str(row["title"] or ""),
                                 str(row["author_name"] or row["user_id"] or ""),
                                 page_count=pages if type(pages) is int and pages > 0 else 1,
                                 tags=cls._tags(row["tags"])))
        return tuple(local)

    def local_bookmark_snapshot(self) -> tuple[Artwork, ...]:
        """Downloaded flags do not depend on a stale or malformed remote cache."""
        _, local_rows = self._bookmark_rows(include_remote=False)
        return self._local_bookmark_artwork(local_rows)

    def bookmark_snapshots(self) -> tuple[tuple[Artwork, ...] | None, tuple[Artwork, ...]]:
        """Legacy UI projection of the same atomic remote/local bundle."""
        records, local = self.bookmark_bundle()
        return (None if records is None else tuple(record.as_artwork() for record in records),
                local)

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
