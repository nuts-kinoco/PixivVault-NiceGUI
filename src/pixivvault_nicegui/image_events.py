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
