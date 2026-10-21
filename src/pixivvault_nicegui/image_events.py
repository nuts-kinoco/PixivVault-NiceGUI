"""ブラウザが絶対化した画像URLを、要求のパスと世代に照合する。"""

from urllib.parse import urlsplit


def source_matches(actual: object, expected: str) -> bool:
    """QImg load/currentSrc の違いを吸収する。クエリと世代fragmentは除去しない。"""
    if not isinstance(actual, str) or not actual or not expected:
        return False
    try:
        received, requested = urlsplit(actual), urlsplit(expected)
    except ValueError:
        return False
    if received.scheme not in ("", "http", "https"):
        return False
    return (received.path, received.query, received.fragment) == (
        requested.path, requested.query, requested.fragment,
    )


# error はURL文字列でなくDOM Eventなので、シリアライズ前に失敗したimgのURLを採取する。
# 最新の画面propsを読み直すと、古い画像の失敗を次の作品へ誤適用し得る。
ERROR_SOURCE_JS = "(event) => emit(event.target?.currentSrc || event.target?.src || '')"
