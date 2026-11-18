"""The bookmark run ("マイブックマーク" の実行 1 回) as the batch engine sees it.

The original's `download_bookmarks` walks the public list, then the private one, saves every bookmark that is
new, and never touches `last_downloaded`. The engine reuses its per-author rows for this: one row per chosen
visibility, under a fixed ID (`1` = 公開 / `2` = 非公開). A bookmarks run never mixes with real authors, and
the ledger never stamps a `bookmarks` run.

Only illustrations / manga (type 0 and 1) are bookmark-saved by the managed pipeline; ugoira and novel
bookmarks are not candidates here (novels have their own save screen).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .author_download import Difference

VISIBILITY_ROWS = {'show': '1', 'hide': '2'}
ROW_VISIBILITY = {row: name for name, row in VISIBILITY_ROWS.items()}
VISIBILITY_LABELS = {'show': '公開ブックマーク', 'hide': '非公開ブックマーク'}


@dataclass(frozen=True, slots=True)
class BookmarkCandidate:
    """One bookmark seen in a listing. Shaped like `AuthorMediaCandidate` for the engine's queue."""
    work_id: str
    author_id: str
    title: str
    visibility: str
    difference: Difference
    record: object = field(default=None, repr=False, compare=False)
    kind: str = 'illustration'
    page_count: int = 1
    update_date: str = ''
    prior_token: object = None
    work_type: int = 0

    @property
    def key(self):
        return self.work_id

    @property
    def eligible(self):
        return self.difference in (Difference.NEW, Difference.UPDATED, Difference.RESTORED, Difference.MISSING)


def classify(records, *, is_saved):
    """Candidates for the supported bookmarks: saved ones are SKIPPED, the rest NEW."""
    out = []
    for record in records:
        if (record.kind != 'illustration' or record.illustration_type not in (0, 1)
                or not getattr(record, 'page_count_known', True)):
            continue
        out.append(BookmarkCandidate(record.remote_id, record.author_id, record.title, record.visibility,
                                     Difference.SKIPPED if is_saved(record.remote_id) else Difference.NEW,
                                     record=record, page_count=record.page_count,
                                     work_type=record.illustration_type or 0))
    return tuple(out)
