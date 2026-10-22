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

    def __post_init__(self) -> None:
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
