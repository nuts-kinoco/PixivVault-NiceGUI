"""ギャラリー画面ごとに独立した、I/Oを持たない状態管理。"""

from collections.abc import Iterable

from .models import Artwork, RequestToken


class GalleryState:
    def __init__(self, artworks: Iterable[Artwork], page_size: int = 50) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self.artworks = tuple(artworks)
        self._by_id = {item.work_id: item for item in self.artworks}
        if len(self._by_id) != len(self.artworks):
            raise ValueError("duplicate work_id")
        self.page_size = page_size
        self.query = ""
        self.card_size = "small"
        self._limit = page_size
        self.selected_id: str | None = None
        self.generation = 0
        self.closed = False
        self._pending_token: RequestToken | None = None

    @property
    def matching_items(self) -> tuple[Artwork, ...]:
        needle = self.query.casefold()
        if not needle:
            return self.artworks
        return tuple(
            item for item in self.artworks
            if needle in item.title.casefold()
            or needle in item.author.casefold()
            or needle in item.description.casefold()
        )

    @property
    def visible_items(self) -> tuple[Artwork, ...]:
        return self.matching_items[:self._limit]

    @property
    def total_count(self) -> int:
        return len(self.matching_items)

    @property
    def shown_count(self) -> int:
        return len(self.visible_items)

    @property
    def has_more(self) -> bool:
        return self.shown_count < self.total_count

    @property
    def viewer_open(self) -> bool:
        return self.selected_id is not None

    @property
    def viewer_index(self) -> int | None:
        if self.selected_id is None:
            return None
        return next(
            (idx for idx, item in enumerate(self.matching_items)
             if item.work_id == self.selected_id), None
        )

    @property
    def selected_artwork(self) -> Artwork | None:
        return self._by_id.get(self.selected_id) if self.selected_id else None

    def _invalidate(self) -> None:
        self.generation += 1
        self._pending_token = None

    def set_query(self, query: str) -> None:
        if self.closed:
            return
        if not isinstance(query, str):
            raise TypeError("query must be str")
        if query != self.query:
            self.query = query
            self._limit = self.page_size
            self.selected_id = None
            self._invalidate()

    def load_more(self) -> None:
        if not self.closed:
            self._limit = min(self._limit + self.page_size, self.total_count)

    def set_card_size(self, size: str) -> None:
        if size not in ("small", "medium", "large"):
            raise ValueError("invalid card size")
        if not self.closed:
            self.card_size = size

    def open_artwork(self, work_id: str) -> bool:
        if self.closed or not any(item.work_id == work_id for item in self.matching_items):
            return False
        self.selected_id = work_id
        self._invalidate()
        return True

    def move_viewer(self, delta: int) -> bool:
        index = self.viewer_index
        if self.closed or index is None:
            return False
        target = index + delta
        matches = self.matching_items
        if target < 0 or target >= len(matches) or target == index:
            return False
        self.selected_id = matches[target].work_id
        self._invalidate()
        return True

    def close_viewer(self) -> None:
        if self.selected_id is not None:
            self.selected_id = None
            self._invalidate()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.selected_id = None
            self._invalidate()

    def begin_request(self, work_id: str) -> RequestToken:
        if self.closed or not self.viewer_open:
            raise RuntimeError("viewer is closed")
        if work_id != self.selected_id:
            raise ValueError("work_id is not the selected artwork")
        self._invalidate()
        token = RequestToken(self.generation, work_id)
        self._pending_token = token
        return token

    def accept_result(self, token: RequestToken) -> bool:
        if (self.closed or not self.viewer_open
                or not isinstance(token, RequestToken)
                or token.generation != self.generation
                or token.work_id != self.selected_id
                or token is not self._pending_token):
            return False
        self._pending_token = None
        return True
