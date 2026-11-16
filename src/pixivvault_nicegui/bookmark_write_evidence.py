"""Fail-closed evidence rules for a future opt-in Pixiv bookmark mutation.

This module deliberately contains no HTTP client, credentials, file access,
database writes, or live mutation methods. It only interprets externally
provided API response structures and never turns an ambiguous response into
authorization to delete a bookmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import re


_ID_PATTERN = re.compile(r"[0-9]{1,20}\Z")
_RECEIPT_KEYS = ("last_bookmark_id", "bookmark_id", "bookmarkId", "id")


def positive_id(value: object) -> str | None:
    """Normalize only strictly positive decimal identifiers; never accept bool."""
    if type(value) not in (str, int):
        return None
    result = str(value)
    return result if _ID_PATTERN.fullmatch(result) and int(result) > 0 else None


def successful_api_response(status: object, payload: object) -> bool:
    """A successful HTTP code by itself is never proof of a successful API call."""
    return (type(status) is int and status == 200
            and isinstance(payload, Mapping) and payload.get("error") is False)


@dataclass(frozen=True, slots=True)
class AddEvidence:
    accepted: bool
    created_bookmark_id: str | None
    reason: str

    @property
    def authorizes_correlated_delete(self) -> bool:
        return self.accepted and self.created_bookmark_id is not None


def inspect_add_response(status: object, payload: object) -> AddEvidence:
    """Interpret a single add response without assuming an ID means success.

    A successful add without an unambiguous returned bookmark ID may already
    have changed the remote state, but it does NOT authorize a later delete.
    """
    if not successful_api_response(status, payload):
        return AddEvidence(False, None, "add_response_rejected")
    assert isinstance(payload, Mapping)
    body = payload.get("body")
    containers = (payload, body if isinstance(body, Mapping) else {})
    seen: set[str] = set()
    for container in containers:
        for key in _RECEIPT_KEYS:
            if key in container:
                candidate = positive_id(container[key])
                if candidate is not None:
                    seen.add(candidate)
                elif container[key] is not None:
                    return AddEvidence(True, None, "invalid_created_id")
    if len(seen) != 1:
        return AddEvidence(True, None, "ambiguous_or_missing_created_id")
    return AddEvidence(True, next(iter(seen)), "accepted_with_id")


def authorize_compensating_delete(
    *, initial_bookmark_id: object, observed_bookmark_id: object, add: AddEvidence
) -> bool:
    """Reject pre-existing, missing, changed, or unproven bookmark ownership."""
    if initial_bookmark_id is not None or not add.authorizes_correlated_delete:
        return False
    observed = positive_id(observed_bookmark_id)
    return observed is not None and observed == add.created_bookmark_id


@dataclass(frozen=True, slots=True)
class RestoreEvidence:
    """Remote state is separate from whether the deletion API itself succeeded."""
    restored: bool
    delete_response_confirmed: bool
    manual_attention_required: bool
    status: str

    @property
    def complete_roundtrip(self) -> bool:
        return self.restored and self.delete_response_confirmed


def inspect_final_state(
    *, delete_http_status: object, delete_response: object,
    final_state_known: bool, final_bookmark_id: object
) -> RestoreEvidence:
    """Only a successful delete API response AND observed absence is a pass.

    If the final GET is inconclusive, leave the user with an explicit manual
    recovery state. A failed delete plus observed absence is *not* a completed
    roundtrip, even though the server now shows the original state.
    """
    confirmed = successful_api_response(delete_http_status, delete_response)
    if type(final_state_known) is not bool or not final_state_known:
        return RestoreEvidence(False, confirmed, True, "final_state_unknown")
    if final_bookmark_id is not None:
        return RestoreEvidence(False, confirmed, True, "bookmark_still_present")
    if not confirmed:
        return RestoreEvidence(True, False, False, "restored_but_delete_unconfirmed")
    return RestoreEvidence(True, True, False, "roundtrip_restored")
