"""画面と状態モデルが共有する不変データ。Flet/NiceGUI/DBに依存しない。"""

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class Artwork:
    work_id: str
    title: str
    author: str
    description: str = ""
    aspect_ratio: float = 1.0
    page_count: int = 1
    tags: tuple[str, ...] = ()
    # Optional source metadata; None means unavailable, not a measured zero.
    author_id: str | None = None
    create_date: str | None = None
    bookmark_count: int | None = None
    x_restrict: int | None = None
    ai_type: int | None = None
    viewed: bool | None = None

    def __post_init__(self) -> None:
        if self.author_id is not None and not isinstance(self.author_id, str):
            raise ValueError("author_id must be a string or None")
        if self.create_date is not None and not isinstance(self.create_date, str):
            raise ValueError("create_date must be a string or None")
        for value in (self.bookmark_count, self.x_restrict, self.ai_type):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("metadata counters must be non-negative integers or None")
        if self.viewed is not None and type(self.viewed) is not bool:
            raise ValueError("viewed must be bool or None")
        if not self.work_id or not isinstance(self.work_id, str):
            raise ValueError("work_id must be a non-empty string")
        if not math.isfinite(self.aspect_ratio) or self.aspect_ratio <= 0:
            raise ValueError("aspect_ratio must be finite and positive")
        if type(self.page_count) is not int or self.page_count < 1:
            raise ValueError("page_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class RequestToken:
    generation: int
    work_id: str
    page_no: int = 0
