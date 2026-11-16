"""Allowlisted job diagnostics: never serialize exception text or attributes."""
import errno


_MESSAGES = {
    "interrupted": "アプリの終了または異常終了で処理が中断されました。途中の保存は未完了です。再試行してください。",
    "permission_denied": "ファイル操作のアクセス権限がありません。保存先の権限を確認してください。",
    "operation_timeout": "処理が制限時間を超えました。通信状態などを確認してください。",
    "disk_full": "保存先の空き容量が不足しています。空き容量を確認してください。",
    "file_missing": "処理に必要なファイルが見つかりません。保存先や入力を確認してください。",
    "image_format_invalid": "画像形式に対応していないか、画像データが破損しています。",
    "image_size_limit": "画像サイズが保存可能な範囲外です。データ量または画素数の上限を確認してください。",
    "download_size_limit": "作品の合計サイズが保存上限を超えています。",
    "ugoira_mp4_failed": "MP4変換を完了できませんでした。画像の寸法などの変換条件と既存の動画変換環境を確認してください。",
    "novel_storage_io": "小説のファイル操作に失敗しました。保存先の状態を確認してください。",
    "novel_storage_integrity": "小説の保存データの整合性を確認できません。保存先を確認してください。",
    "novel_publication_unconfirmed": "小説の保存結果を確認できません。保存済みデータの照合が必要です。",
}


def job_failure_text(code):
    """Unknown/legacy values have no public detail; callers keep their fallback."""
    return _MESSAGES.get(code, "") if type(code) is str else ""


class JobFailure(Exception):
    """Explicit safe-code carrier across a product's sanitized error boundary.

    The message remains internal. Only an exact allowlisted reason code may
    reach job persistence/UI; never infer codes from str/repr, paths or causes.
    """
    def __init__(self, message="", *, reason_code="runner_failed"):
        super().__init__(message)
        self.reason_code = reason_code if job_failure_text(reason_code) else "runner_failed"


def job_failure_code(error):
    if isinstance(error, JobFailure):
        code = error.reason_code
        return code if job_failure_text(code) else "runner_failed"
    # Exact built-in types avoid interpreting third-party payload attributes.
    if type(error) is PermissionError:
        return "permission_denied"
    if type(error) is TimeoutError:
        return "operation_timeout"
    if type(error) is FileNotFoundError:
        return "file_missing"
    if type(error) is OSError and type(error.errno) is int and error.errno == errno.ENOSPC:
        return "disk_full"
    return "runner_failed"
