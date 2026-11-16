"""Pure view-only ordering and bounded selection of followed author targets.

Unknown favorites are distinct from unstarred authors. No selection action
updates a Pixiv account or implicitly admits a download job.
"""
from __future__ import annotations

from .author_download import AuthorTarget, filter_authors


# The read client allows up to 10,000 public and 10,000 private follows.
# Keep the entire filtered result selectable; 48/50 is only a render window.
MAX_SELECTED_AUTHORS = 20_000


def visible_authors(authors: tuple[AuthorTarget, ...], *, query: str = '',
                    favorites_only: bool = False, sort_key: str = 'follow_order',
                    descending: bool = False) -> tuple[AuthorTarget, ...]:
    """Filter and order without changing selection or remote follow state."""
    if sort_key not in ('follow_order', 'name', 'id') or type(descending) is not bool:
        raise ValueError('invalid author sort')
    if type(favorites_only) is not bool:
        raise ValueError('invalid favorite filter')
    matches = filter_authors(authors, query)
    if favorites_only:
        matches = tuple(a for a in matches if a.is_favorite is True)
    positions = {author.user_id: i for i, author in enumerate(authors)}
    def key(author):
        if sort_key == 'name':
            return (author.display_name.casefold(), author.user_id)
        if sort_key == 'id':
            return (int(author.user_id),)
        order = author.follow_order
        return (order if order is not None else positions[author.user_id], positions[author.user_id])
    return tuple(sorted(matches, key=key, reverse=descending))


def select_many_authors(selected: tuple[AuthorTarget, ...],
                        candidates: tuple[AuthorTarget, ...]) -> tuple[AuthorTarget, ...]:
    """All-or-nothing bounded selection; retain the first origin for each ID."""
    kept_ids = {author.user_id for author in selected}
    additions = []
    for author in candidates:
        if author.user_id in kept_ids:
            continue
        if len(selected) + len(additions) >= MAX_SELECTED_AUTHORS:
            raise ValueError('too many author selections')
        if not isinstance(author, AuthorTarget):
            raise TypeError('author required')
        kept_ids.add(author.user_id)
        additions.append(author)
    return (*selected, *additions) if additions else selected
