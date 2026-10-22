"""1 clientに閉じるDL済み作者・全作品ページの状態モデル。I/O・UI依存なし。"""

from __future__ import annotations

from itertools import count

from .library_models import AuthorSummary, LibrarySnapshot, PageRef


_generations = count(1)  # 状態インスタンスを作り直しても古いUI callbackを再利用させない。
_SORT_KEYS = frozenset({"view_count", "last_backup"})


class LibraryState:
    def __init__(self, snapshot: LibrarySnapshot, page_size: int = 50) -> None:
        if not isinstance(snapshot, LibrarySnapshot):
            raise TypeError("snapshot must be LibrarySnapshot")
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive integer")
        self.snapshot = snapshot
        self.page_size = page_size
        self.query = ""
        self.sort_key = "view_count"
        self.sort_directions = {"view_count": True, "last_backup": True}
        self.selected_author_id: str | None = None
        self._all_authors = snapshot.authors
        self._by_author = {author.author_id: author for author in self._all_authors}
        self._matched_authors: tuple[AuthorSummary, ...] = ()
        self._pages: tuple[PageRef, ...] = ()
        self._page_limit = page_size
        self._author_limit = page_size
        self.generation = next(_generations)
        self.closed = False
        self._refresh_matches()

    @property
    def descending(self) -> bool:
        return self.sort_directions[self.sort_key]

    @property
    def total_author_count(self) -> int:
        return len(self._all_authors)

    @property
    def matched_author_count(self) -> int:
        return len(self._matched_authors)

    @property
    def visible_authors(self) -> tuple[AuthorSummary, ...]:
        return self._matched_authors[:self._author_limit]

    @property
    def has_more_authors(self) -> bool:
        return self.shown_author_count < self.matched_author_count

    def load_more_authors(self) -> bool:
        if self.closed or self.selected_author_id is not None or not self.has_more_authors:
            return False
        self._author_limit = min(self._author_limit + self.page_size, self.matched_author_count)
        return True

    @property
    def shown_author_count(self) -> int:
        return len(self.visible_authors)

    @property
    def selected_author(self) -> AuthorSummary | None:
        return self._by_author.get(self.selected_author_id) if self.selected_author_id else None

    @property
    def visible_pages(self) -> tuple[PageRef, ...]:
        return self._pages[:self._page_limit]

    @property
    def total_page_count(self) -> int:
        return len(self._pages)

    @property
    def shown_page_count(self) -> int:
        return len(self.visible_pages)

    @property
    def has_more_pages(self) -> bool:
        return self.shown_page_count < self.total_page_count

    @property
    def unknown_page_work_count(self) -> int:
        author = self.selected_author
        return sum(not work.page_count_known for work in author.works) if author else 0

    def _invalidate(self) -> None:
        self.generation = next(_generations)

    @staticmethod
    def _matches(author: AuthorSummary, needle: str) -> bool:
        if needle in author.name.casefold():
            return True
        return any(needle in value.casefold() for work in author.works for value in (
            work.work_id, work.artwork.title, work.display_author_name,
            work.artwork.description, *work.artwork.tags,
        ))

    def _refresh_matches(self) -> None:
        needle = self.query.strip().casefold()
        matching = (author for author in self._all_authors
                    if not needle or self._matches(author, needle))
        key = ((lambda author: author.view_count) if self.sort_key == "view_count"
               else (lambda author: author.last_backup or ""))
        self._matched_authors = tuple(sorted(matching, key=key, reverse=self.descending))

    def _leave_detail(self) -> None:
        self.selected_author_id = None
        self._pages = ()
        self._page_limit = self.page_size

    def set_query(self, query: str) -> bool:
        if not isinstance(query, str):
            raise TypeError("query must be str")
        if self.closed or self.query == query:
            return False
        self.query = query
        self._author_limit = self.page_size
        self._leave_detail()
        self._refresh_matches()
        self._invalidate()
        return True

    def select_sort(self, key: str) -> bool:
        if not isinstance(key, str) or key not in _SORT_KEYS:
            raise ValueError("unsupported library sort key")
        if self.closed:
            return False
        if self.sort_key == key:
            self.sort_directions[key] = not self.sort_directions[key]
        else:
            self.sort_key = key
        self._author_limit = self.page_size
        self._leave_detail()
        self._refresh_matches()
        self._invalidate()
        return True

    def open_author(self, author_id: str) -> bool:
        if (self.closed or self.selected_author_id is not None or not any(author.author_id == author_id
                                   for author in self.visible_authors)):
            return False
        author = self._by_author[author_id]
        # 代表と作者数は検索前の全作品。詳細も検索条件を引き継がない。
        ordered = sorted(author.works, key=lambda work: work.last_backup or "", reverse=True)
        self.selected_author_id = author_id
        self._pages = tuple(PageRef(work, page_no)
                            for work in ordered for page_no in range(work.page_count))
        self._page_limit = self.page_size
        self._invalidate()
        return True

    def load_more_pages(self) -> bool:
        if self.closed or self.selected_author is None or not self.has_more_pages:
            return False
        # 既存カードとその世代を維持する。追加分だけが描画対象になる。
        self._page_limit = min(self._page_limit + self.page_size, self.total_page_count)
        return True

    def close_author(self) -> bool:
        if self.closed or self.selected_author_id is None:
            return False
        self._leave_detail()
        self._invalidate()
        return True

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._leave_detail()
            self._invalidate()
