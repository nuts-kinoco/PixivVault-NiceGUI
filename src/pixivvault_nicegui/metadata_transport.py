"""Bounded metadata-detail adapter for explicit managed-root backfill.

Construction performs no IO. The production adapter only delegates to the
existing explicit-cookie GET-only client; all tests inject synthetic responses.
No retry loop lives here: HTTP status and unknown failures are classified by
the policy/runner, which owns stop behavior and attempt accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import re

from .metadata_policy import FailureEvidence, sanitize_work_meta
from .pixiv_read_client import (
    AuthenticationMissing, ClientClosed, CookieSourceRejected, HTTPStatusRejected,
    InvalidPayload, LivePixivReadClient, PixivReadError, ReadDeadlineExceeded,
    TransportFailed,
)


_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_MAX_NUMBER = 2_147_483_647
_PROMOTED = (
    ('bookmarkCount', 'bookmark_count'),
    ('likeCount', 'like_count'),
    ('viewCount', 'view_count'),
    ('commentCount', 'comment_count'),
    ('xRestrict', 'x_restrict'),
    ('aiType', 'ai_type'),
    ('width', 'illust_width'),
    ('height', 'illust_height'),
    ('uploadDate', 'upload_date'),
    ('userAccount', 'user_account'),
)
_PROMOTED_KEYS = frozenset(key for key, _field in _PROMOTED)
_EXCLUDED = frozenset({'userIllusts', 'zoneConfig', 'extraData'})


class MetadataPayloadInvalid(ValueError):
    """Sanitized response rejection; contains no URL, ID, body or cookie."""


@dataclass(frozen=True, slots=True)
class MetadataFetchResult:
    work_id: str
    author_id: str
    metadata: dict | None
    missing_bookmark_count: bool


class MetadataTransport(Protocol):
    def verify_identity(self) -> str: ...

    def fetch_detail(self, work_id: str, expected_author_id: str) -> MetadataFetchResult: ...


def _valid_id(value: object) -> str:
    if type(value) not in (str, int):
        raise MetadataPayloadInvalid('metadata identity rejected')
    result = str(value)
    if not _ID.fullmatch(result):
        raise MetadataPayloadInvalid('metadata identity rejected')
    return result


def parse_illust_detail(work_id: str, expected_author_id: str,
                        body: object) -> MetadataFetchResult:
    """Extract the original promoted columns and remaining safe work_meta keys.

    The caller must supply the expected IDs from the pinned managed receipt.
    Missing/null bookmarkCount is a per-work outcome; malformed fields are
    UNKNOWN and must never consume a work-specific attempt.
    """
    wid, author = _valid_id(work_id), _valid_id(expected_author_id)
    if type(body) is not dict or ('isMasked' in body and body['isMasked'] is not False):
        raise MetadataPayloadInvalid('metadata response rejected')
    if _valid_id(body.get('id')) != wid or _valid_id(body.get('userId')) != author:
        raise MetadataPayloadInvalid('metadata identity mismatch')

    if body.get('bookmarkCount') is None or 'bookmarkCount' not in body:
        return MetadataFetchResult(wid, author, None, True)

    result: dict = {'meta_source': 'illust_detail'}
    for source_key, target_key in _PROMOTED:
        if source_key not in body or body[source_key] is None:
            continue
        value = body[source_key]
        if target_key in ('upload_date', 'user_account'):
            limit = 128 if target_key == 'upload_date' else 64
            if (type(value) is not str or len(value) > limit
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise MetadataPayloadInvalid('metadata field rejected')
            if value:
                result[target_key] = value
            continue
        if type(value) is not int or not 0 <= value <= _MAX_NUMBER:
            raise MetadataPayloadInvalid('metadata field rejected')
        if target_key == 'x_restrict' and value not in (0, 1, 2):
            raise MetadataPayloadInvalid('metadata field rejected')
        if target_key in ('illust_width', 'illust_height') and value == 0:
            raise MetadataPayloadInvalid('metadata field rejected')
        result[target_key] = value

    work_meta = {
        key: value for key, value in body.items()
        if key not in _PROMOTED_KEYS and key not in _EXCLUDED
    }
    try:
        result['work_meta'] = sanitize_work_meta(work_meta)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise MetadataPayloadInvalid('metadata object rejected') from None
    return MetadataFetchResult(wid, author, result, False)


def failure_evidence(exc: BaseException) -> FailureEvidence:
    """Map typed client failures without parsing messages or exposing details."""
    if isinstance(exc, HTTPStatusRejected):
        return FailureEvidence('http', exc.status_code)
    if isinstance(exc, (AuthenticationMissing, CookieSourceRejected)):
        return FailureEvidence('authentication')
    if isinstance(exc, ReadDeadlineExceeded):
        return FailureEvidence('timeout')
    if isinstance(exc, (TransportFailed, ClientClosed)):
        return FailureEvidence('transport')
    # Invalid payload, API error envelopes, untyped response rejection and
    # unknown exceptions deliberately fail closed as UNKNOWN.
    return FailureEvidence('unknown')


class LivePixivMetadataTransport:
    """Thin adapter; caller owns explicit cookie/client lifecycle and consent."""

    def __init__(self, client: LivePixivReadClient):
        if not isinstance(client, LivePixivReadClient):
            raise TypeError('explicit Pixiv read client required')
        self._client = client

    def verify_identity(self) -> str:
        return self._client.require_identity()

    def fetch_detail(self, work_id: str, expected_author_id: str) -> MetadataFetchResult:
        body = self._client.get_artwork_metadata_raw(work_id, expected_author_id)
        return parse_illust_detail(work_id, expected_author_id, body)


__all__ = [
    'MetadataPayloadInvalid', 'MetadataFetchResult', 'MetadataTransport',
    'LivePixivMetadataTransport', 'parse_illust_detail', 'failure_evidence',
]
