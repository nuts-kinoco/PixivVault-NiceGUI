"""Pure read-only bridge from the legacy Pixiv bookmark client to UI records.

This module never constructs an HTTP client, reads cookies, writes a database,
or downloads thumbnails. The caller supplies an authenticated, read-only
client. Four complete fetch routes are required before an existing cache can
be replaced; one failed route invalidates the entire candidate snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib.parse import urlsplit
import re

from .bookmark_state import BookmarkState
from .models import Artwork


_POSITIVE_ID = re.compile(r"[0-9]{1,20}\Z")
KINDS = ("illustration", "novel")
VISIBILITIES = ("show", "hide")
MAX_ITEMS_PER_ROUTE = 50000


class BookmarkReadClient(Protocol):
    def get_bookmarked_works(self, my_user_id: str, rest_type: str): ...
    def get_bookmarked_novels(self, my_user_id: str, rest_type: str): ...


def positive_id(value: object) -> str | None:
    if type(value) not in {str, int}:
        return None
    result = str(value)
    return result if _POSITIVE_ID.fullmatch(result) and int(result) > 0 else None


def thumbnail_url(value: object) -> str:
    if type(value) is not str:
        raise ValueError("thumbnail must be a string")
    if (value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("bookmark thumbnail contains unsafe whitespace/control characters")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "i.pximg.net"
            or not parsed.path.startswith("/") or parsed.username or parsed.password
            or parsed.port is not None or parsed.fragment):
        raise ValueError("bookmark thumbnail is not a permitted Pixiv CDN URL")
    return value


@dataclass(frozen=True, slots=True)
class RemoteBookmark:
    remote_id: str
    kind: str
    visibility: str
    title: str
    author_id: str
    author_name: str
    tags: tuple[str, ...]
    thumbnail: str
    created_at: str
    page_count: int
    illustration_type: int | None
    x_restrict: int
    page_count_known: bool = True

    @property
    def work_id(self) -> str:
        # Distinguish novel and artwork ID namespaces in NiceGUI state.
        return self.remote_id if self.kind == "illustration" else f"novel:{self.remote_id}"

    def as_artwork(self) -> Artwork:
        # Lossy rendering projection only. Thumbnail, raw ID, visibility,
        # novel kind and access restrictions remain in RemoteBookmark.
        return Artwork(self.work_id, self.title, self.author_name,
                       page_count=self.page_count, tags=self.tags)

    @property
    def legacy_identity(self) -> tuple[str, bool]:
        """DB adapters store raw id and separate is_novel, never novel:<id>."""
        return self.remote_id, self.kind == "novel"


@dataclass(frozen=True, slots=True)
class RouteFailure:
    kind: str
    visibility: str
    error_type: str


@dataclass(frozen=True, slots=True)
class BookmarkFetch:
    ok: bool
    records: tuple[RemoteBookmark, ...]
    failures: tuple[RouteFailure, ...] = ()

    def apply_to_state(self, state: BookmarkState, epoch: int) -> bool:
        # Never feed partially fetched records to the overwrite path.
        if self.ok:
            return state.complete_fetch(
                epoch, (record.as_artwork() for record in self.records), ok=True
            )
        return state.complete_fetch(epoch, (), ok=False)


def parse_bookmark(source: object, *, kind: str, visibility: str) -> RemoteBookmark | None:
    """Map a single legacy entry without modifying it or following its URL."""
    if kind not in KINDS or visibility not in VISIBILITIES:
        raise ValueError("invalid bookmark route")
    if not isinstance(source, dict):
        raise ValueError("invalid bookmark record")
    # The legacy view omits deleted/private entries without a preview URL.
    preview = source.get("thumb_url")
    if not preview:
        return None
    preview = thumbnail_url(preview)
    remote_id = positive_id(source.get("id"))
    if remote_id is None:
        raise ValueError("invalid remote bookmark ID")
    title = source.get("title") or ""
    name = source.get("user_name") or ""
    author_id = source.get("user_id")
    if type(title) is not str or type(name) is not str:
        raise ValueError("invalid bookmark metadata")
    if author_id is None:
        author_id = ""
    if type(author_id) not in {str, int}:
        raise ValueError("invalid author ID")
    raw_tags = source.get("tags") or []
    if not isinstance(raw_tags, (list, tuple)) or any(type(t) is not str for t in raw_tags):
        raise ValueError("invalid bookmark tags")
    count = 1 if kind == "novel" else source.get("page_count", 1)
    if (type(count) not in {str, int} or not str(count).isascii()
            or not str(count).isdecimal() or not (1 <= int(count) <= 1000)):
        raise ValueError("invalid page count")
    created = source.get("create_date") or ""
    if type(created) is not str:
        raise ValueError("invalid creation date")
    raw_type = source.get("type", 0) if kind == "illustration" else None
    if raw_type is not None and (type(raw_type) is not int or raw_type not in (0, 1, 2)):
        raise ValueError("invalid illustration type")
    raw_restriction = source.get("x_restrict", 0)
    if type(raw_restriction) is not int or raw_restriction not in (0, 1, 2):
        raise ValueError("invalid content restriction")
    return RemoteBookmark(remote_id, kind, visibility, title, str(author_id),
                          name, tuple(raw_tags), preview, created, int(count),
                          raw_type, raw_restriction)


def fetch_all_bookmarks(
    client: BookmarkReadClient, my_user_id: str, *, max_items_per_route: int = MAX_ITEMS_PER_ROUTE
) -> BookmarkFetch:
    """Read show/hide × illustration/novel; reject partial data on any error.

    A route is successful only if iteration finishes, not simply if it yields
    one entry. The client is never asked to add, remove, or update anything.
    """
    if positive_id(my_user_id) != my_user_id:
        return BookmarkFetch(False, (), (RouteFailure("identity", "", "InvalidUserId"),))
    if type(max_items_per_route) is not int or max_items_per_route < 1:
        raise ValueError("max_items_per_route must be positive")
    found: list[RemoteBookmark] = []
    failures: list[RouteFailure] = []
    for visibility in VISIBILITIES:
        for kind in KINDS:
            try:
                method = (client.get_bookmarked_works if kind == "illustration"
                          else client.get_bookmarked_novels)
                iterable: Iterable[object] = method(my_user_id, rest_type=visibility)
                for i, item in enumerate(iterable):
                    if i >= max_items_per_route:
                        raise OverflowError("bookmark result limit exceeded")
                    record = parse_bookmark(item, kind=kind, visibility=visibility)
                    if record is not None:
                        found.append(record)
            except Exception as exc:
                # Error classes, never exception strings: messages can contain
                # account identifiers, response bodies, or cookies.
                failures.append(RouteFailure(kind, visibility, type(exc).__name__))
    if failures:
        return BookmarkFetch(False, (), tuple(failures))
    unique: dict[str, RemoteBookmark] = {}
    for record in found:
        unique.setdefault(record.work_id, record)
    return BookmarkFetch(True, tuple(unique.values()))


__all__ = ["BookmarkFetch", "RemoteBookmark", "RouteFailure",
           "fetch_all_bookmarks", "parse_bookmark"]
