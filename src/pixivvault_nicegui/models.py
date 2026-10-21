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

    def __post_init__(self) -> None:
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
