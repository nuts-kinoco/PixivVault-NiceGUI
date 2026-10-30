"""Legacy-compatible pure tag filters shared by library and bookmarks."""
from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata


def normalize_tag(tag: object) -> str | None:
    """NFKC, trim and lowercase ASCII letters only (legacy database.normalize_tag)."""
    if not tag:
        return None
    value = unicodedata.normalize("NFKC", str(tag)).strip()
    if not value:
        return None
    value = "".join(chr(ord(ch) + 32) if "A" <= ch <= "Z" else ch for ch in value)
    return value or None


def _normalize_tags(tags) -> frozenset[str]:
    values = frozenset(value for tag in tags if (value := normalize_tag(tag)))
    return values


@dataclass(frozen=True, slots=True)
class LibraryFilters:
    include: frozenset[str] = field(default_factory=frozenset)
    exclude: frozenset[str] = field(default_factory=frozenset)
    mode: str = "and"
    authors: frozenset[str] = field(default_factory=frozenset)
    date_from: str = ""
    date_to: str = ""
    bookmark_min: int = 0
    include_r18: bool = True
    exclude_ai: bool = False
    unread_only: bool = False
    unfetched_only: bool = False

    def __post_init__(self) -> None:
        if type(self.bookmark_min) is not int or self.bookmark_min < 0:
            raise ValueError("bookmark_min must be non-negative")
        if any(type(v) is not bool for v in (self.include_r18,self.exclude_ai,self.unread_only,self.unfetched_only)):
            raise ValueError("filter switches must be boolean")
        if any(not isinstance(v,str) for v in (self.date_from,self.date_to)):
            raise ValueError("date bounds must be strings")
        authors=frozenset(self.authors)
        if any(type(a) is not str or not a for a in authors):
            raise ValueError("author filters require IDs")
        object.__setattr__(self,"authors",authors)
        included = _normalize_tags(self.include)
        excluded = _normalize_tags(self.exclude)
        if self.mode not in {"and", "or"}:
            raise ValueError("mode must be 'and' or 'or'")
        if included & excluded:
            raise ValueError("a tag cannot be both included and excluded")
        object.__setattr__(self, "include", included)
        object.__setattr__(self, "exclude", excluded)

    def matches(self, tags) -> bool:
        actual = _normalize_tags(tags)
        if self.include:
            if self.mode == "and" and not self.include.issubset(actual):
                return False
            if self.mode == "or" and not (self.include & actual):
                return False
        return not bool(self.exclude & actual)


    def matches_artwork(self, artwork, *, author_id=None, downloaded=None, apply_unread=True) -> bool:
        """Original library_tab._matches semantics, including unknown fallbacks.

        Dates compare only the first ten characters. Missing bookmark count
        fails a positive minimum; missing restriction/AI/history is retained as
        unknown in the DTO and has the same permissive filter fallback as Flet.
        Unfetched applies only to bookmarks (downloaded is explicitly supplied).
        """
        if not self.matches(artwork.tags): return False
        identity=artwork.author_id if author_id is None else author_id
        if self.authors and identity not in self.authors: return False
        created=(artwork.create_date or '')[:10]
        if self.date_from and created<self.date_from: return False
        if self.date_to and created>self.date_to: return False
        if self.bookmark_min and (artwork.bookmark_count is None or artwork.bookmark_count<self.bookmark_min): return False
        if not self.include_r18 and (artwork.x_restrict or 0)!=0: return False
        if self.exclude_ai and (artwork.ai_type or 0)==2: return False
        if apply_unread and self.unread_only and artwork.viewed is True: return False
        if self.unfetched_only and downloaded is True: return False
        return True
