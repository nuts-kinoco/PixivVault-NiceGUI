"""Deterministic failure records for the disconnected demonstration only."""

from .failed_records_state import FailureRecord


def build_failed_records_fixture() -> tuple[FailureRecord, ...]:
    reasons = ("Connection: 合成の接続失敗", "404: 合成作品が見つかりません",
               "403: 合成の権限エラー", "No space: 合成の保存先エラー",
               "ページ不足: 合成の品質検査", "分類されていない合成エラー")
    return tuple(FailureRecord(
        work_id=f"failure-{index + 1:04d}", user_id="synthetic-author",
        title=f"架空の失敗作品 {index + 1:03d}" if index != 16 else "=SUM(1,2)",
        work_type="novel" if index % 4 == 0 else "illust",
        error_reason=reasons[index % len(reasons)] + (
            "\n二行目の診断は省略せず表示します。" if index == 0 else ""),
        failed_at=f"2026-01-{17 - index:02d}T12:00:00", retry_count=index % 3,
    ) for index in range(17))
