"""実アカウント・DB・画像を使わない、作者ドリルダウンの合成データ。"""

from __future__ import annotations

from datetime import date, timedelta

from .library_models import LibrarySnapshot, LibraryWork
from .models import Artwork


def _author(index: int) -> tuple[str, str]:
    if index <= 60:
        return "demo-author-01", "架空作家 01"
    if index <= 80:
        return "demo-author-02", "架空作家 同名"
    if index <= 100:
        return "demo-author-03", "架空作家 同名"
    return "demo-author-04", "架空作家 04"


def build_library_fixture(artworks: tuple[Artwork, ...]) -> LibrarySnapshot:
    """既存合成Artworkから作者単位のsnapshotを生成（120件で4作者）。"""
    works: list[LibraryWork] = []
    for artwork in artworks:
        # 生成済みdemo-NNNN以外は扱わない。実Pixiv ID等を合成として取り込まない。
        prefix, sep, number = artwork.work_id.partition("-")
        if prefix != "demo" or sep != "-" or not number.isascii() or not number.isdecimal():
            raise ValueError("library fixture requires synthetic demo IDs")
        index = int(number)
        if index <= 0:
            raise ValueError("library fixture requires positive demo IDs")
        author_id, author_name = _author(index)
        works.append(LibraryWork(
            artwork=artwork,
            author_id=author_id,
            author_name=author_name,
            last_backup=(None if index in {7, 99}
                         else (date(2026, 1, 1) + timedelta(days=index)).isoformat()),
            # demo-0057は見かけ上3ページだが、DBメタデータ欠落を再現する。
            page_count_known=index != 57,
            view_count=(index * 10 if index <= 60 else
                        3000 if index <= 80 else 400 if index <= 100 else 600),
            capacity_bytes=None,
            work_type="manga" if artwork.page_count > 1 else "illustration",
        ))
    return LibrarySnapshot(tuple(works))
