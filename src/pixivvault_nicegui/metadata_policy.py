"""Pure, offline policy for future opt-in managed metadata backfill.

No scheduler, HTTP client, credentials, legacy database, or filesystem is
opened by this module. An eventual transport adapter must translate HTTP
failures into the safe evidence types below without leaking request details.

Reference: original PixivVault core.py:560-587,608-716 and
database.py:1644-1695. The original database is never written.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Literal


MAX_META_ATTEMPTS = 3
MAX_UNKNOWN_STREAK = 5
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
FailureReason = Literal['http', 'authentication', 'transport', 'timeout',
                        'missing_bookmark_count', 'unknown']


class MetadataFailureClass(str, Enum):
    WORK = 'work'
    GLOBAL = 'global'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    """Sanitized input from a transport; never an URL or response body.

    An HTTP status must come from an *actual* status code. A Pixiv API JSON
    error without trustworthy status is 'unknown', not a fabricated 404.
    """

    reason: FailureReason
    http_status: int | None = None

    def __post_init__(self) -> None:
        allowed = {'http', 'authentication', 'transport', 'timeout',
                   'missing_bookmark_count', 'unknown'}
        if self.reason not in allowed:
            raise ValueError('invalid metadata failure reason')
        if self.reason == 'http':
            if type(self.http_status) is not int or not 100 <= self.http_status <= 599:
                raise ValueError('invalid metadata HTTP status')
        elif self.http_status is not None:
            raise ValueError('HTTP status requires HTTP failure reason')


@dataclass(frozen=True, slots=True)
class FailureDecision:
    classification: MetadataFailureClass
    increment_attempts: bool
    stop_run: bool
    consecutive_unknown: int


def classify_failure(evidence: FailureEvidence, *, consecutive_unknown: int = 0) -> FailureDecision:
    """Match the original policy without consuming attempts on global/unknown.

    A 404 or a successful response missing bookmarkCount is work-specific.
    Authentication, rate-limit, 5xx, deadline and transport faults stop the
    whole run without penalizing the work. Every other ambiguous failure
    consumes *no* work attempts, and five consecutive unknowns stop the run.
    """
    if not isinstance(evidence, FailureEvidence):
        raise TypeError('metadata failure evidence required')
    if type(consecutive_unknown) is not int or consecutive_unknown < 0:
        raise ValueError('invalid unknown streak')

    if evidence.reason == 'missing_bookmark_count':
        kind = MetadataFailureClass.WORK
    elif evidence.reason == 'http':
        status = evidence.http_status
        if status == 404:
            kind = MetadataFailureClass.WORK
        elif status in (401, 403, 429) or status >= 500:
            kind = MetadataFailureClass.GLOBAL
        else:
            kind = MetadataFailureClass.UNKNOWN
    elif evidence.reason in ('authentication', 'transport', 'timeout'):
        kind = MetadataFailureClass.GLOBAL
    else:
        kind = MetadataFailureClass.UNKNOWN

    unknown = consecutive_unknown + 1 if kind == MetadataFailureClass.UNKNOWN else 0
    return FailureDecision(
        classification=kind,
        increment_attempts=kind == MetadataFailureClass.WORK,
        stop_run=(kind == MetadataFailureClass.GLOBAL
                  or unknown >= MAX_UNKNOWN_STREAK),
        consecutive_unknown=unknown,
    )


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    """Synthetic/isolated snapshot; not a handle to the original database."""

    work_id: str
    user_id: str
    content_type: str
    is_deleted: bool
    bookmark_count: int | None
    meta_attempts: int
    meta_fetched_at: str | None = None

    def __post_init__(self) -> None:
        if (type(self.work_id) is not str or _ID.fullmatch(self.work_id) is None
                or type(self.user_id) is not str or _ID.fullmatch(self.user_id) is None):
            raise ValueError('invalid metadata work identity')
        if (type(self.content_type) is not str or type(self.is_deleted) is not bool
                or (self.bookmark_count is not None
                    and (type(self.bookmark_count) is not int or self.bookmark_count < 0))
                or type(self.meta_attempts) is not int or self.meta_attempts < 0):
            raise ValueError('invalid metadata work status')
        if self.meta_fetched_at is not None and type(self.meta_fetched_at) is not str:
            raise ValueError('invalid metadata fetch timestamp')

    @property
    def needs_backfill(self) -> bool:
        # meta_fetched_at is intentionally NOT an eligibility condition:
        # existing work may have partial metadata without bookmarkCount.
        return (self.content_type == 'illust' and not self.is_deleted
                and self.bookmark_count is None
                and self.meta_attempts < MAX_META_ATTEMPTS)


def select_candidates(rows: tuple[MetadataCandidate, ...]) -> tuple[MetadataCandidate, ...]:
    """One immutable pass, mirroring original ORDER BY attempts/user/work."""
    if type(rows) is not tuple or any(not isinstance(row, MetadataCandidate) for row in rows):
        raise ValueError('metadata candidates must be an immutable snapshot')
    return tuple(sorted((row for row in rows if row.needs_backfill),
                        key=lambda row: (row.meta_attempts, row.user_id, row.work_id)))
