"""合成ライブラリと将来の読取専用adapterが共有する不変DTO。"""

from __future__ import annotations

from dataclasses import dataclass, replace, field
from collections.abc import Mapping
from types import MappingProxyType

from .models import Artwork


@dataclass(frozen=True, slots=True)
class LibraryWork:
    """DL済み作品。ページ数の既知性は表示用Artworkとは別に保持する。"""

    artwork: Artwork
    author_id: str
    last_backup: str | None = None
    page_count_known: bool = True
    view_count: int = 0
    capacity_bytes: int | None = None
    work_type: str = "illustration"
    author_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artwork, Artwork):
            raise TypeError("artwork must be an Artwork")
        # Legacy works may legitimately contain NULL user_id, grouped as "".
        if not isinstance(self.author_id, str):
            raise ValueError("author_id must be a string")
        if self.author_name is not None and not isinstance(self.author_name, str):
            raise TypeError("author_name must be a string or None")
        if self.last_backup is not None and not isinstance(self.last_backup, str):
            raise TypeError("last_backup must be a string or None")
        if type(self.page_count_known) is not bool:
            raise TypeError("page_count_known must be bool")
        if type(self.view_count) is not int or self.view_count < 0:
            raise ValueError("view_count must be a non-negative integer")
        if self.capacity_bytes is not None and (
            type(self.capacity_bytes) is not int or self.capacity_bytes < 0
        ):
            raise ValueError("capacity_bytes must be non-negative or None")
        if not isinstance(self.work_type, str) or not self.work_type:
            raise ValueError("work_type must be a non-empty string")
        # viewerも同じ作者名を示し、ページ数不明なら1ページのみ選べるようにする。
        display_name = self.author_name or self.artwork.author
        displayed_pages = self.artwork.page_count if self.page_count_known else 1
        if self.artwork.author != display_name or self.artwork.page_count != displayed_pages:
            object.__setattr__(self, "artwork", replace(
                self.artwork, author=display_name, page_count=displayed_pages))

    @property
    def work_id(self) -> str:
        return self.artwork.work_id

    @property
    def page_count(self) -> int:
        return self.artwork.page_count

    @property
    def display_author_name(self) -> str:
        return self.author_name or self.artwork.author or self.author_id


@dataclass(frozen=True, slots=True)
class AuthorSummary:
    author_id: str
    name: str
    works: tuple[LibraryWork, ...]
    representative: LibraryWork
    view_count: int
    last_backup: str | None
    capacity_bytes: int | None

    @property
    def work_count(self) -> int:
        return len(self.works)


@dataclass(frozen=True, slots=True)
class PageRef:
    """0始まりの画像APIに対応するページ識別子。原版UIは1始まり。"""

    work: LibraryWork
    page_no: int

    def __post_init__(self) -> None:
        if type(self.page_no) is not int or not 0 <= self.page_no < self.work.page_count:
            raise ValueError("page_no out of range")

    @property
    def key(self) -> tuple[str, int]:
        return self.work.work_id, self.page_no

    @property
    def legacy_page_no(self) -> int:
        """原版の1始まりの表示番号への境界変換。"""
        return self.page_no + 1

    @classmethod
    def from_legacy(cls, work: LibraryWork, legacy_page_no: int) -> PageRef:
        """原版の1始まりページ番号を新画像APIの0始まりへ変換。"""
        if type(legacy_page_no) is not int:
            raise ValueError("legacy_page_no must be an integer")
        return cls(work, legacy_page_no - 1)


@dataclass(frozen=True, slots=True)
class LibrarySnapshot:
    """作品LIMITより前の全作品を収め、作者IDで集約する読取結果。"""

    works: tuple[LibraryWork, ...]
    author_capacities: Mapping[str, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        items = tuple(self.works)
        if any(not isinstance(item, LibraryWork) for item in items):
            raise TypeError("snapshot works must be LibraryWork instances")
        if len({item.work_id for item in items}) != len(items):
            raise ValueError("duplicate work_id")
        object.__setattr__(self, "works", items)
        capacities = dict(self.author_capacities)
        if any(not isinstance(key, str) or (value is not None and
               (type(value) is not int or value < 0)) for key, value in capacities.items()):
            raise ValueError("invalid author capacity cache")
        object.__setattr__(self, "author_capacities", MappingProxyType(capacities))

    @property
    def authors(self) -> tuple[AuthorSummary, ...]:
        by_author: dict[str, list[LibraryWork]] = {}
        for work in self.works:
            by_author.setdefault(work.author_id, []).append(work)
        result = []
        for author_id, items in by_author.items():
            works = tuple(items)
            # 安定なmax: 同日なら元の作品順。検索前の全作品から一度決める。
            representative = max(works, key=lambda work: work.last_backup or "")
            sizes = [work.capacity_bytes for work in works]
            result.append(AuthorSummary(
                author_id=author_id,
                name=works[0].display_author_name,
                works=works,
                representative=representative,
                view_count=sum(work.view_count for work in works),
                last_backup=representative.last_backup,
                capacity_bytes=(self.author_capacities[author_id]
                                if author_id in self.author_capacities else
                                (sum(sizes) if all(size is not None for size in sizes) else None)),
            ))
        return tuple(result)
