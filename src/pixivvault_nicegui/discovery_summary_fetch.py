"""One explicitly selected discovery artwork description, never images or metadata.

This adapter owns no reader lifecycle. Only the pre-existing bounded, identity-
verified GET method is called, with both selected work/author IDs supplied.
"""
from __future__ import annotations

from .discovery_description import MAX_DESCRIPTION, layout


class SummaryFetchFailed(ValueError):
    """Public failure without source bytes, account details, URLs or HTTP body."""


def fetch_summary(reader, work_id: str, author_id: str) -> str:
    """Return verified HTML source text; a truly empty description is ``''``.

    Missing, None, non-string or oversized descriptions are failures, not
    equivalent to an artwork whose author deliberately supplied empty text.
    """
    if (type(work_id) is not str or not work_id.isascii() or not work_id.isdecimal()
            or not work_id or work_id[0] == '0' or
            type(author_id) is not str or not author_id.isascii()
            or not author_id.isdecimal() or not author_id or author_id[0] == '0'):
        raise SummaryFetchFailed('invalid selected work')
    method = getattr(reader, 'get_artwork_metadata_raw', None)
    if not callable(method):
        raise SummaryFetchFailed('description reader unavailable')
    try:
        raw = method(work_id, author_id)
        if (type(raw) is not dict or str(raw.get('id')) != work_id
                or str(raw.get('userId')) != author_id or 'description' not in raw):
            raise SummaryFetchFailed('description identity or field unavailable')
        description = raw['description']
        if type(description) is not str or len(description) > MAX_DESCRIPTION:
            raise SummaryFetchFailed('description invalid')
        # Validation uses the existing safe HTML-as-text/link parser; neither
        # the untrusted HTML nor arbitrary reader exceptions enter UI/logs.
        layout(description, 24.0, 12)
        return description
    except Exception:
        raise SummaryFetchFailed('description unavailable') from None
