"""公開デモ専用の架空作品データ。"""

from .models import Artwork


def generate_demo_artworks(count: int = 50) -> tuple[Artwork, ...]:
    if count < 0:
        raise ValueError("count must be non-negative")
    ratios = (1.0, 0.75, 1.5, 0.8, 1.25)
    return tuple(
        Artwork(
            work_id=f"demo-{index:04d}",
            title=f"架空作品 {index:04d}",
            author=f"架空作家 {(index - 1) % 12 + 1:02d}",
            description=f"デモ表示用の創作イラスト {index:04d}。実在の投稿ではありません。",
            aspect_ratio=ratios[(index - 1) % len(ratios)],
        )
        for index in range(1, count + 1)
    )
