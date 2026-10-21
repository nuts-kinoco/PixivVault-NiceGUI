"""画面と状態モデルが共有する不変データ。Flet/NiceGUI/DBに依存しない。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Artwork:
    work_id: str
    title: str
    author: str
    description: str = ""
    aspect_ratio: float = 1.0


@dataclass(frozen=True, slots=True)
class RequestToken:
    generation: int
    work_id: str
