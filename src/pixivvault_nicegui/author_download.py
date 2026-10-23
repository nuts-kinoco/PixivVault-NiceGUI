"""Pure author selection and conservative differential-download decisions.

This module never touches the legacy DB or filesystem.  Unknown saved
generations must not be interpreted as evidence of a current download.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


_AUTHOR_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


@dataclass(frozen=True, slots=True)
class AuthorTarget:
    user_id: str
    display_name: str
    origin: str

    def __post_init__(self):
        if not isinstance(self.user_id, str) or not _AUTHOR_ID.fullmatch(self.user_id):
            raise ValueError("invalid author ID")
        if self.origin not in ("id", "follow"):
            raise ValueError("invalid author origin")
        if not isinstance(self.display_name, str) or len(self.display_name) > 256:
            raise ValueError("invalid author name")


def select_author(targets: tuple[AuthorTarget, ...], candidate: AuthorTarget) -> tuple[AuthorTarget, ...]:
    """First selection wins, including its original provenance."""
    if not isinstance(candidate, AuthorTarget):
        raise TypeError("author required")
    if any(target.user_id == candidate.user_id for target in targets):
        return targets
    return (*targets, candidate)


def filter_authors(authors: tuple[AuthorTarget, ...], query: str) -> tuple[AuthorTarget, ...]:
    """Filter visible choices only, never the selected set."""
    normalized = str(query or "").strip().casefold()
    return tuple(author for author in authors if normalized in
                 f"{author.user_id} {author.display_name}".casefold())


class Difference(str, Enum):
    NEW = "new"
    UPDATED = "updated"
    RESTORED = "restored"
    MISSING = "missing"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class AuthorPreview(tuple):
    """Iterable `(work, status)` pairs with out-of-list legacy absence counts.

    A complete remote inventory may contain zero works, while the frozen old
    DB still has missing works; their counts must remain visible in the UI.
    """

    def __new__(cls, items, *, remote_missing_pending=0, remote_missing_confirmed=0,
                remote_absent=(), inventory_complete=True):
        result = super().__new__(cls, items)
        result.remote_missing_pending = remote_missing_pending
        result.remote_missing_confirmed = remote_missing_confirmed
        result.remote_absent = tuple(remote_absent)
        result.inventory_complete = inventory_complete
        return result


def decide_difference(*, saved: bool, remote_update_date: str | None,
                      saved_update_date: str | None, deleted: bool = False,
                      files_present: bool | None = None) -> Difference:
    """A missing date/physical state is unknown, never an implicit full fetch.

    The caller must establish whether an existing receipt is complete and
    verified; `files_present=None` means it cannot establish that fact.
    """
    if type(saved) is not bool or type(deleted) is not bool:
        raise TypeError("invalid saved/deleted state")
    if files_present is not None and type(files_present) is not bool:
        raise TypeError("invalid file state")
    if not saved:
        return Difference.NEW
    if (not isinstance(remote_update_date, str) or not remote_update_date
            or not isinstance(saved_update_date, str) or not saved_update_date):
        return Difference.UNKNOWN
    if remote_update_date != saved_update_date:
        return Difference.UPDATED
    if deleted:
        return Difference.RESTORED
    if files_present is False:
        return Difference.MISSING
    if files_present is None:
        return Difference.UNKNOWN
    return Difference.SKIPPED
